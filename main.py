import re
import random
import string
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from google_play_scraper import app as get_app_details
from google_play_scraper import search

import settings


PLAY_URL = "https://play.google.com/store/apps/details?id={app_id}"

CONSONANTS = "bcdfghjklmnpqrstvwxyz"
VOWELS = "aeiou"

# Words to skip when extracting tokens from game titles
STOPWORDS = {
    "the", "and", "for", "with", "your", "from", "this", "that",
    "are", "was", "have", "has", "its", "you", "all", "can",
    "new", "free", "game", "games", "play", "edition", "version",
    "lite", "pro", "plus", "hd", "vip", "mod",
}


def random_query() -> str:
    """Generate a short random query as a fallback seed."""
    style = random.choices(
        ["letters", "syllable", "double_syllable"],
        weights=[2, 3, 2],
        k=1,
    )[0]
    if style == "letters":
        return "".join(random.choices(string.ascii_lowercase, k=random.randint(2, 4)))
    if style == "syllable":
        return random.choice(CONSONANTS) + random.choice(VOWELS) + random.choice(CONSONANTS)
    return (
        random.choice(CONSONANTS) + random.choice(VOWELS)
        + random.choice(CONSONANTS) + random.choice(VOWELS)
    )


def parse_installs(value) -> int:
    if not value:
        return 0
    try:
        return int(str(value).replace(",", "").replace("+", "").strip())
    except ValueError:
        return 0


def fetch_search(query: str, lang: str, country: str, limit: int) -> list:
    try:
        return search(query, lang=lang, country=country, n_hits=limit)
    except Exception:
        return []


def fetch_details(app_id: str) -> dict | None:
    try:
        return get_app_details(app_id)
    except Exception:
        return None


def extract_tokens(app_ids: list[str], seen_tokens: set[str]) -> list[str]:
    """
    Sample already-discovered app IDs, fetch their titles,
    and extract new search tokens not yet in seen_tokens.
    This creates a self-replenishing query pool driven by real content.
    """
    if not app_ids:
        return []

    sample = random.sample(app_ids, min(40, len(app_ids)))
    new_tokens: list[str] = []

    with ThreadPoolExecutor(max_workers=settings.WORKERS) as executor:
        futures = {executor.submit(fetch_details, aid): aid for aid in sample}
        for future in as_completed(futures):
            details = future.result()
            if not details:
                continue
            title = details.get("title") or ""
            summary = details.get("summary") or ""
            # Extract alphabetic words of length 3-12 from title and summary
            for word in re.findall(r"[a-zA-Z]{3,12}", title + " " + summary):
                w = word.lower()
                if w not in STOPWORDS and w not in seen_tokens:
                    new_tokens.append(w)
                    seen_tokens.add(w)

    return new_tokens


def collect_candidates(seen: set[str], token_pool: list[str]) -> list[str]:
    """
    Build a query set from two sources:
      - half from the live token_pool (words extracted from known game titles)
      - half from random_query() as entropy to discover new clusters
    Collect app IDs from all queries across sampled locales.
    """
    half = max(1, settings.QUERIES_PER_RUN // 2)

    # Draw tokens from pool without removing them (pool is managed in main)
    pool_queries = set(random.sample(token_pool, min(half, len(token_pool)))) if token_pool else set()
    rand_queries = {random_query() for _ in range(settings.QUERIES_PER_RUN - len(pool_queries))}
    queries = pool_queries | rand_queries

    locales = random.sample(settings.SEARCH_LOCALES, k=min(4, len(settings.SEARCH_LOCALES)))
    tasks = [(q, lang, country) for q in queries for lang, country in locales]

    ids: list[str] = []
    done = 0
    total = len(tasks)
    print(f"  search: 0/{total}", end="\r", flush=True)

    with ThreadPoolExecutor(max_workers=settings.WORKERS) as executor:
        futures = {
            executor.submit(fetch_search, q, lang, country, settings.SEARCH_LIMIT): (q, lang, country)
            for q, lang, country in tasks
        }
        for future in as_completed(futures):
            for item in future.result():
                app_id = item.get("appId")
                # Only exclude already-seen IDs; filtering by installs/genre happens later
                if app_id and app_id not in seen:
                    ids.append(app_id)
            done += 1
            print(f"  search: {done}/{total}", end="\r", flush=True)

    print()
    return list(dict.fromkeys(ids))  # deduplicate while preserving order


def iter_games(app_ids: list[str]):
    """Fetch details for each candidate and yield only those matching genre/install filters."""
    done = 0
    total = len(app_ids)
    found = 0
    print(f"  details: 0/{total}", end="\r", flush=True)

    with ThreadPoolExecutor(max_workers=settings.WORKERS) as executor:
        futures = {executor.submit(fetch_details, aid): aid for aid in app_ids}
        for future in as_completed(futures):
            app_id = futures[future]
            details = future.result()
            done += 1
            print(f"  details: {done}/{total} (matched: {found})", end="\r", flush=True)

            if not details:
                continue

            genre = (details.get("genre") or "").lower()
            genre_id = (details.get("genreId") or "").upper()
            if "game" not in genre and not genre_id.startswith("GAME"):
                continue

            installs = parse_installs(details.get("installs"))
            if installs < settings.MIN_INSTALLS:
                continue
            if settings.MAX_INSTALLS and installs > settings.MAX_INSTALLS:
                continue

            found += 1
            yield app_id

    print()


def load_existing(path: Path) -> set[str]:
    if not path.exists():
        return set()
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "id=" in line:
            seen.add(line.rsplit("id=", 1)[-1])
    return seen


def append_links(path: Path, app_ids: list[str]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for app_id in app_ids:
            f.write(PLAY_URL.format(app_id=app_id) + "\n")


def init_sheet():
    if not (settings.GOOGLE_SHEET_ENABLED and settings.GOOGLE_SHEET_URL):
        return None
    try:
        from sheet import SheetWriter
        writer = SheetWriter(
            settings.GOOGLE_SHEET_URL,
            settings.SERVICE_ACCOUNT_FILE,
            settings.GOOGLE_SHEET_TAB,
        )
        print(f"Google Sheet: {len(writer.existing)} rows, next B{writer.next_row}")
        return writer
    except Exception as e:
        print(f"Google Sheet disabled (error): {e}")
        return None


def main() -> None:
    output = Path(settings.OUTPUT_FILE)
    seen = load_existing(output)

    sheet = init_sheet()
    if sheet:
        for url in sheet.existing:
            if "id=" in url:
                seen.add(url.rsplit("id=", 1)[-1])

    print(f"Already saved: {len(seen)}")
    print(f"Writing to {output}{' + Google Sheet' if sheet else ''}. Stop with Ctrl+C.\n")

    # Token pool: self-replenishing list of words extracted from known game titles
    token_pool: list[str] = []
    seen_tokens: set[str] = set(STOPWORDS)

    # Snapshot of known IDs used for token extraction (avoids modifying `seen` mid-loop)
    known_ids: list[str] = list(seen)

    buffer: list[str] = []
    total_saved = 0
    cycle = 0

    def flush() -> None:
        nonlocal total_saved
        if not buffer:
            return
        append_links(output, buffer)
        msg = f"  + txt: {len(buffer)}"
        if sheet:
            try:
                links = [PLAY_URL.format(app_id=aid) for aid in buffer]
                added = sheet.append(links)
                msg += f", sheet: {added}"
            except Exception as e:
                msg += f", sheet error: {e}"
        total_saved += len(buffer)
        print(msg + f" (session total: {total_saved})")
        buffer.clear()

    try:
        while True:
            cycle += 1

            # Replenish token pool when it runs low
            if len(token_pool) < settings.QUERIES_PER_RUN and known_ids:
                print(f"[Cycle {cycle}] extracting tokens from {min(40, len(known_ids))} known games...")
                new_tokens = extract_tokens(known_ids, seen_tokens)
                token_pool.extend(new_tokens)
                print(f"  token pool: {len(token_pool)} (+{len(new_tokens)} new)")

            print(f"[Cycle {cycle}] collecting candidates "
                  f"(pool: {len(token_pool)} tokens, seen: {len(seen)})...")

            candidates = collect_candidates(seen, token_pool)
            print(f"  candidates: {len(candidates)}")

            if not candidates:
                # Pool exhausted and random queries hit only known IDs — shuffle pool
                random.shuffle(token_pool)
                continue

            for app_id in iter_games(candidates):
                if app_id in seen:
                    continue
                seen.add(app_id)
                known_ids.append(app_id)  # feed new ID back into the extraction source
                buffer.append(app_id)
                if len(buffer) >= settings.BATCH_SIZE:
                    flush()

    except KeyboardInterrupt:
        print("\nStopping...")
        flush()
        print(f"Done. Session total: {total_saved}.")


if __name__ == "__main__":
    main()
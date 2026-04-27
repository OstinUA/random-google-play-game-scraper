# main.py
import re
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from google_play_scraper import app as get_app_details
from google_play_scraper import search

import settings


PLAY_URL = "https://play.google.com/store/apps/details?id={app_id}"

# ---------------------------------------------------------------------------
# Stopwords (TASK 6)
# ---------------------------------------------------------------------------
# Significantly expanded. Keeps generic English fillers, app/store boilerplate
# ("free", "edition", "update", "experience"), generic gaming jargon
# ("level", "score", "gameplay"), and small/junk tokens out of the dynamic
# token pool so extracted queries actually carry signal.
STOPWORDS = {
    # Articles, pronouns, conjunctions, prepositions
    "the", "and", "for", "with", "your", "from", "this", "that", "they",
    "them", "their", "there", "these", "those", "what", "when", "where",
    "while", "which", "will", "would", "could", "should", "been", "being",
    "more", "most", "some", "such", "into", "onto", "over", "under", "than",
    "then", "also", "even", "just", "only", "very", "much", "many", "each",
    "every", "both", "either", "neither", "another", "other", "about",
    "after", "before", "between", "through", "without", "within", "across",
    "around", "behind", "beyond", "during", "against",
    # Auxiliary verbs / common verbs
    "are", "was", "were", "have", "has", "had", "its", "you", "yours",
    "all", "any", "can", "may", "must", "shall", "did", "does", "doing",
    "make", "made", "take", "took", "come", "came", "give", "gave", "get",
    "got", "see", "saw", "say", "said", "use", "used", "try", "tried",
    "want", "need", "know", "feel", "look", "find", "show", "help",
    # App / store boilerplate
    "new", "free", "game", "games", "play", "playing", "edition", "version",
    "lite", "pro", "plus", "vip", "mod", "offline", "online", "premium",
    "deluxe", "ultimate", "official", "original", "classic",
    "app", "apps", "top", "best", "fun", "real", "world", "super",
    "download", "install", "update", "feature", "features", "store",
    "experience", "enjoy", "discover", "everyone", "anyone", "anywhere",
    "anytime", "available", "exclusive", "limited", "special",
    # Gaming jargon that doesn't carve out useful niches on its own
    "tap", "click", "swipe", "drag", "drop", "level", "levels", "score",
    "scores", "high", "easy", "hard", "simple", "amazing", "awesome",
    "exciting", "addictive", "challenging", "endless", "epic", "ultimate",
    "gameplay", "graphics", "controls", "modes", "mode", "stage", "stages",
    "mission", "missions", "achievements", "rewards", "bonus", "daily",
    "weekly", "event", "events", "season", "ranked", "rank",
    # Generic adjectives
    "good", "great", "nice", "fine", "cool", "neat", "clean", "smooth",
    "perfect", "incredible", "fantastic", "wonderful", "beautiful",
    "stunning", "gorgeous",
    # Numbers spelled out / ordinals
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "first", "second", "third", "next", "last", "final",
    # Time
    "now", "today", "tomorrow", "year", "years", "month", "week", "day",
    "hour", "minute", "second", "moment",
    # Misc filler
    "please", "thanks", "welcome", "hello", "ready", "start", "begin",
    "play", "pause", "stop", "exit", "menu", "screen", "page",
}

# How many cycles before resetting seen_tokens to allow re-extraction
# of words from games found in earlier rounds.
TOKEN_RESET_EVERY = 15


# ---------------------------------------------------------------------------
# Query generation (TASKS 3 + 4)
# ---------------------------------------------------------------------------
# Replaces the prior random_query() that produced "letters/syllable/double_syllable".
# Now draws from the curated SEED_WORDS list — every query is at least a real
# word — and occasionally combines 2-3 tokens into a long-tail phrase to dodge
# popular single-word results that exceed MAX_INSTALLS.
def random_query() -> str:
    pool = settings.SEED_WORDS
    # Distribution: heavy on single words for breadth, but ~half the queries are
    # 2-3 word combos that target the long tail.
    style = random.choices(
        ["single", "double", "triple"],
        weights=[5, 3, 2],
        k=1,
    )[0]
    if style == "single":
        return random.choice(pool)
    if style == "double":
        return f"{random.choice(pool)} {random.choice(pool)}"
    return f"{random.choice(pool)} {random.choice(pool)} {random.choice(pool)}"


def parse_installs(value) -> int:
    if not value:
        return 0
    try:
        return int(str(value).replace(",", "").replace("+", "").strip())
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Network primitives — every call swallows exceptions and returns an empty /
# None result so a transient timeout or rate-limit never crashes the main loop.
# ---------------------------------------------------------------------------
def fetch_search(query: str, lang: str, country: str, limit: int) -> list:
    try:
        return search(query, lang=lang, country=country, n_hits=limit)
    except Exception:
        return []


def fetch_details(app_id: str, lang: str = "en", country: str = "us") -> dict | None:
    try:
        return get_app_details(app_id, lang=lang, country=country)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Token extraction (TASK 6)
# ---------------------------------------------------------------------------
# Stricter validation: alphabetic-only, 4-15 letters, lowercase, not in
# the (now much larger) stopword set. Numbers, punctuation, and very short
# or absurdly long strings never enter the pool.
_VALID_TOKEN = re.compile(r"^[a-z]{4,15}$")


def is_valid_token(word: str) -> bool:
    return bool(_VALID_TOKEN.match(word)) and word not in STOPWORDS


def extract_tokens(app_ids: list[str], seen_tokens: set[str]) -> list[str]:
    """Sample known app IDs, fetch their text, harvest fresh high-quality tokens."""
    if not app_ids:
        return []

    sample = random.sample(app_ids, min(50, len(app_ids)))
    new_tokens: list[str] = []

    with ThreadPoolExecutor(max_workers=settings.WORKERS) as executor:
        futures = {executor.submit(fetch_details, aid): aid for aid in sample}
        for future in as_completed(futures):
            details = future.result()
            if not details:
                continue
            text = (details.get("title") or "") + " " + (details.get("summary") or "")
            for raw in re.findall(r"[A-Za-z]+", text):
                w = raw.lower()
                if w in seen_tokens:
                    continue
                seen_tokens.add(w)  # mark seen even if rejected — don't re-evaluate
                if is_valid_token(w):
                    new_tokens.append(w)

    return new_tokens


# ---------------------------------------------------------------------------
# Shared search-task runner — every discovery routine funnels through here so
# concurrency, error handling, and progress reporting are uniform.
# ---------------------------------------------------------------------------
def _run_search_tasks(
    tasks: list[tuple[str, str, str]],
    seen: set[str],
    label: str | None = None,
    limit: int | None = None,
) -> list[str]:
    if not tasks:
        return []
    n_hits = limit or settings.SEARCH_LIMIT
    total = len(tasks)
    done = 0
    if label:
        print(f"  {label}: 0/{total}", end="\r", flush=True)

    ids: list[str] = []
    with ThreadPoolExecutor(max_workers=settings.WORKERS) as executor:
        futures = {
            executor.submit(fetch_search, q, lang, country, n_hits): (q, lang, country)
            for q, lang, country in tasks
        }
        for future in as_completed(futures):
            for item in future.result():
                aid = item.get("appId")
                if aid and aid not in seen:
                    ids.append(aid)
            done += 1
            if label:
                print(f"  {label}: {done}/{total}", end="\r", flush=True)
    if label:
        print()
    return list(dict.fromkeys(ids))  # dedupe, preserve order


# ---------------------------------------------------------------------------
# DISCOVERY ROUTINES (modular per requirements)
# ---------------------------------------------------------------------------

def discover_by_search(seen: set[str], token_pool: list[str]) -> list[str]:
    """Primary keyword-search discovery (TASKS 3 + 4 + 7).

    Builds queries from a 50/50 mix of (a) dynamic tokens harvested from
    titles/summaries of already-found games and (b) random_query() draws from
    the curated SEED_WORDS list (single + long-tail combos). Each query runs
    against a random subset of the broad SEARCH_LOCALES so soft-launched
    titles in Tier-2/3 markets get surfaced.
    """
    half = max(1, settings.QUERIES_PER_RUN // 2)

    # Drain the token pool — keeps it shrinking so extract_tokens runs again.
    consumed: list[str] = []
    while token_pool and len(consumed) < half:
        consumed.append(token_pool.pop())
    pool_queries = set(consumed)

    rand_queries = {random_query() for _ in range(settings.QUERIES_PER_RUN - len(pool_queries))}
    queries = pool_queries | rand_queries

    locales = random.sample(settings.SEARCH_LOCALES, k=min(4, len(settings.SEARCH_LOCALES)))
    tasks = [(q, lang, country) for q in queries for lang, country in locales]

    return _run_search_tasks(tasks, seen, label="search")


def discover_by_developer(developer_name: str, developer_id: str, seen: set[str]) -> list[str]:
    """More-by-this-developer spidering (TASK 1).

    google-play-scraper has no first-class developer-listing endpoint, so we
    approximate: search the developer's name, then verify each candidate's
    `developerId` field matches before returning. Bounded by SPIDER_LIMIT.
    """
    if not developer_name or not developer_id:
        return []

    locales = random.sample(settings.SEARCH_LOCALES, k=min(2, len(settings.SEARCH_LOCALES)))
    tasks = [(developer_name, lang, country) for lang, country in locales]
    raw = _run_search_tasks(tasks, seen, label=None, limit=settings.SPIDER_LIMIT)
    if not raw:
        return []
    raw = raw[:settings.SPIDER_LIMIT]

    matching: list[str] = []
    with ThreadPoolExecutor(max_workers=settings.WORKERS) as executor:
        futures = {executor.submit(fetch_details, aid): aid for aid in raw}
        for future in as_completed(futures):
            aid = futures[future]
            d = future.result()
            if d and d.get("developerId") == developer_id:
                matching.append(aid)
    return matching


def discover_by_similar(app_details: dict, seen: set[str]) -> list[str]:
    """Similar / related apps expansion (TASK 2).

    google-play-scraper does not expose a `similar(app_id)` endpoint either, so
    we approximate it: take the most distinctive non-stopword tokens from the
    matched game's title and pair them with its `genre`. Searching that
    combination tightly clusters games in the same niche.
    """
    title = app_details.get("title") or ""
    genre = (app_details.get("genre") or "").lower().strip()

    title_tokens = [
        t.lower()
        for t in re.findall(r"[A-Za-z]{4,15}", title)
        if t.lower() not in STOPWORDS
    ]
    if not title_tokens:
        return []

    queries: set[str] = set()
    for tok in title_tokens[:3]:
        queries.add(f"{tok} {genre}".strip() if genre else tok)
        queries.add(tok)

    locales = random.sample(settings.SEARCH_LOCALES, k=min(2, len(settings.SEARCH_LOCALES)))
    tasks = [(q, lang, country) for q in queries for lang, country in locales]
    return _run_search_tasks(tasks, seen, label=None, limit=settings.SPIDER_LIMIT)


def discover_by_category(seen: set[str]) -> list[str]:
    """Category / top-chart discovery (TASK 5).

    Iterates the GAME_* category map in settings, picks one (sometimes two)
    seed keyword per category, and runs them across multiple locales. Surfaces
    trending titles in each genre that single-token text search rarely hits.
    """
    queries: set[str] = set()
    for keywords in settings.CATEGORY_SEEDS.values():
        queries.add(random.choice(keywords))
    # Add a "new" suffix variant for a subset to bias toward fresh listings —
    # standing in for the missing top-new-free chart endpoint.
    sample_cats = random.sample(
        list(settings.CATEGORY_SEEDS.values()),
        k=min(5, len(settings.CATEGORY_SEEDS)),
    )
    for keywords in sample_cats:
        queries.add(f"{random.choice(keywords)} new")

    locales = random.sample(settings.SEARCH_LOCALES, k=min(4, len(settings.SEARCH_LOCALES)))
    tasks = [(q, lang, country) for q in queries for lang, country in locales]
    return _run_search_tasks(tasks, seen, label="category")


# ---------------------------------------------------------------------------
# Filter pipeline — yields (app_id, details) tuples so callers can spider
# without a second details fetch.
# ---------------------------------------------------------------------------
def iter_games(app_ids: list[str]):
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
            yield app_id, details

    print()


# ---------------------------------------------------------------------------
# Persistence helpers (unchanged behavior)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
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

    token_pool: list[str] = []
    seen_tokens: set[str] = set(STOPWORDS)
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

    def process(candidates: list[str]) -> list[tuple[str, dict]]:
        """Run candidates through filtering, save matches, return matched details."""
        matched: list[tuple[str, dict]] = []
        if not candidates:
            return matched
        for aid, details in iter_games(candidates):
            if aid in seen:
                continue
            seen.add(aid)
            known_ids.append(aid)
            buffer.append(aid)
            matched.append((aid, details))
            if len(buffer) >= settings.BATCH_SIZE:
                flush()
        return matched

    try:
        while True:
            cycle += 1

            # Periodic token-filter reset so games found earlier can re-contribute
            # tokens (their titles may yield new words after the pool turned over).
            if cycle % TOKEN_RESET_EVERY == 0:
                seen_tokens.clear()
                seen_tokens.update(STOPWORDS)
                print(f"  [cycle {cycle}] token filter reset — ready for re-extraction")

            if len(token_pool) < settings.QUERIES_PER_RUN and known_ids:
                print(f"  [cycle {cycle}] extracting tokens from {min(50, len(known_ids))} known games...")
                new_tokens = extract_tokens(known_ids, seen_tokens)
                token_pool.extend(new_tokens)
                print(f"  token pool: {len(token_pool)} (+{len(new_tokens)} new)")

            print(f"[Cycle {cycle}] discovery (pool: {len(token_pool)}, seen: {len(seen)})")

            # 1. Primary keyword search
            search_candidates = discover_by_search(seen, token_pool)
            print(f"  search candidates: {len(search_candidates)}")

            # 2. Periodic category sweep — runs alongside search every Nth cycle
            if settings.CATEGORY_INTERVAL and cycle % settings.CATEGORY_INTERVAL == 0:
                try:
                    cat_candidates = discover_by_category(seen)
                    print(f"  category candidates: {len(cat_candidates)}")
                    seen_local = set(search_candidates)
                    for c in cat_candidates:
                        if c not in seen_local:
                            search_candidates.append(c)
                            seen_local.add(c)
                except Exception as e:
                    # Never let one failed routine kill the loop.
                    print(f"  category discovery error: {e}")

            if not search_candidates:
                continue

            # 3. Filter primary candidates
            matched = process(search_candidates)

            # 4. Spider into developer + similar for each freshly matched game
            if settings.SPIDER_ENABLED and matched:
                spider_pool: list[str] = []
                spider_seen: set[str] = set()
                for aid, details in matched:
                    dev_id = details.get("developerId")
                    dev_name = details.get("developer")
                    try:
                        for x in discover_by_developer(dev_name, dev_id, seen):
                            if x not in spider_seen:
                                spider_pool.append(x)
                                spider_seen.add(x)
                    except Exception as e:
                        print(f"  developer discovery error for {aid}: {e}")
                    try:
                        for x in discover_by_similar(details, seen):
                            if x not in spider_seen:
                                spider_pool.append(x)
                                spider_seen.add(x)
                    except Exception as e:
                        print(f"  similar discovery error for {aid}: {e}")

                if spider_pool:
                    print(f"  spider candidates: {len(spider_pool)}")
                    # depth=1 only — matches found via spidering are NOT spidered
                    # again this cycle, preventing runaway expansion.
                    process(spider_pool)

    except KeyboardInterrupt:
        print("\nStopping...")
        flush()
        print(f"Done. Session total: {total_saved}.")


if __name__ == "__main__":
    main()

# App-Finder

Python script that discovers games on Google Play and saves their links to `games.txt` and (optionally) a Google Sheet.

It runs in an endless loop: generates random search queries across multiple locales, scrapes app metadata via `google-play-scraper`, filters by install count, and flushes results in batches.

## Install

```
pip install -r requirements.txt
```

## Configure

Open `settings.py`:

- `MIN_INSTALLS` / `MAX_INSTALLS` — install range (`MAX_INSTALLS = 0` disables the upper bound)
- `OUTPUT_FILE` — text file with one Play Store URL per line
- `BATCH_SIZE` — how many new links to accumulate before flushing
- `QUERIES_PER_RUN`, `SEARCH_LIMIT`, `WORKERS` — search throughput tuning
- `SEARCH_LOCALES` — list of `(lang, country)` pairs

### Google Sheets (optional)

1. Drop your `service_account.json` into the project folder
2. Share the target sheet with the `client_email` from the JSON (Editor access)
3. In `settings.py`:
   ```python
   GOOGLE_SHEET_ENABLED = True
   GOOGLE_SHEET_URL = "https://docs.google.com/spreadsheets/d/<ID>/edit"
   GOOGLE_SHEET_TAB = 0
   SERVICE_ACCOUNT_FILE = "service_account.json"
   ```

Links are written to column `B` starting at `B2` (row 1 stays free for a header). Existing values in `B` are loaded on startup, so duplicates are never written.

To turn off the sheet integration, set `GOOGLE_SHEET_ENABLED = False` or leave `GOOGLE_SHEET_URL` empty.

## Run

```
python main.py
```

Stop with `Ctrl+C` — the remaining buffer is flushed before exit.

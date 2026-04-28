import re

import gspread
from google.oauth2.service_account import Credentials


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _extract_id(url_or_id: str) -> str:
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url_or_id)
    return match.group(1) if match else url_or_id


class SheetWriter:
    def __init__(self, url: str, key_path: str, tab_index: int = 0):
        creds = Credentials.from_service_account_file(key_path, scopes=SCOPES)
        client = gspread.authorize(creds)
        self.ws = client.open_by_key(_extract_id(url)).get_worksheet(tab_index)

        max_row = self.ws.row_count
        raw = self.ws.get(f"A1:A{max_row}") or []
        col = [row[0] if row else "" for row in raw]

        self.existing: set[str] = {v.strip() for v in col[1:] if v.strip()}
        self.next_row = max(len(col) + 1, 2)

    def _ensure_capacity(self, target_row: int) -> None:
        """Grow the sheet if we're about to write past its current row count."""
        if target_row > self.ws.row_count:
            self.ws.add_rows(target_row - self.ws.row_count + 100)

    def append(self, links: list[str]) -> int:
        new = [link for link in links if link not in self.existing]
        if not new:
            return 0
        end = self.next_row + len(new) - 1
        self._ensure_capacity(end)
        self.ws.update(
            range_name=f"A{self.next_row}:A{end}",
            values=[[link] for link in new],
        )
        self.existing.update(new)
        self.next_row = end + 1
        return len(new)
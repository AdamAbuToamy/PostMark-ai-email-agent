"""Historical paging policy. Page data and its checkpoint commit together."""
from typing import Protocol
from email_agent.domain.message import EmailMessage


class HistoryReader(Protocol):
    def list_history_page(self, account_id: str, page_token: str | None = None,
                          page_size: int = 100) -> tuple[list[EmailMessage], str | None]: ...


class HistoryStore(Protocol):
    def progress(self, account_id: str) -> dict: ...
    def commit_page(self, account_id: str, expected_token: str | None,
                    messages: list[EmailMessage], next_token: str | None) -> None: ...


class HistoricalSync:
    def __init__(self, reader: HistoryReader, store: HistoryStore):
        self.reader, self.store = reader, store

    def execute(self, account_id: str, max_pages: int = 1, page_size: int = 50) -> dict:
        if account_id not in ("personal", "university"):
            raise ValueError("Unknown account")
        if not 1 <= max_pages <= 100 or not 1 <= page_size <= 500:
            raise ValueError("Invalid sync bounds")
        for _ in range(max_pages):
            state = self.store.progress(account_id)
            if state["completed"]:
                break
            messages, token = self.reader.list_history_page(account_id, state["next_page_token"], page_size)
            if any(m.account_id != account_id for m in messages):
                raise ValueError("Account mismatch")
            if token is not None and token == state["next_page_token"]:
                raise ValueError("Gmail returned a non-advancing page")
            discovered = getattr(self.reader, "last_discovered_ids", None)
            observed_commit = getattr(self.store, "commit_observed_page", None)
            if isinstance(discovered, (list, tuple)) and all(isinstance(id, str) for id in discovered) and callable(observed_commit):
                observed_commit(account_id, state["next_page_token"], messages, token, discovered)
            else:
                self.store.commit_page(account_id, state["next_page_token"], messages, token)
        return self.store.progress(account_id)

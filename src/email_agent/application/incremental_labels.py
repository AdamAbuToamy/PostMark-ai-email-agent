"""Bounded refresh of cached special-label states, without listing old mail."""
from dataclasses import dataclass
from typing import Protocol
from email_agent.domain.message import EmailMessage


class LabelReader(Protocol):
    def get_metadata(self, account: str, id: str) -> EmailMessage: ...


class LabelRefreshStore(Protocol):
    def label_page(self, account: str, limit: int) -> tuple[int,list[tuple[int,str]]]: ...
    def commit_labels(self, account: str, cursor: int, rows: list[tuple[int,str]], messages: list[EmailMessage]) -> None: ...


@dataclass(frozen=True)
class LabelRefreshResult:
    messages: list[EmailMessage]
    unavailable: int
    errors: int


class IncrementalLabels:
    def __init__(self, reader: LabelReader, store: LabelRefreshStore):
        self.reader,self.store=reader,store

    def execute(self, account: str, limit: int = 5) -> LabelRefreshResult:
        if account not in ('personal','university') or not 1<=limit<=5:
            raise ValueError('Invalid incremental label bounds')
        cursor,rows=self.store.label_page(account,limit)
        messages=[];unavailable=0;errors=0
        for _,id in rows:
            try:
                message=self.reader.get_metadata(account,id)
                if not isinstance(message,EmailMessage) or message.id!=id or message.account_id!=account or message.label_ids is None:
                    raise ValueError('Invalid authoritative metadata')
                messages.append(message)
            except LookupError:unavailable+=1
            except Exception:errors+=1
        self.store.commit_labels(account,cursor,rows,messages)
        return LabelRefreshResult(messages,unavailable,errors)

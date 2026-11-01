"""Daily operational summary, independent of HTTP, storage and schedulers."""
from typing import Protocol


class ControlStore(Protocol):
    def sync_snapshot(self, account: str) -> dict: ...
    def counters(self, account: str) -> dict: ...
    def control_sync(self, account: str, action: str) -> dict: ...


class ControlCenter:
    def __init__(self, store: ControlStore):
        self.store = store

    @staticmethod
    def account(account: str):
        if account not in ('personal', 'university'):
            raise ValueError('Unknown account')

    def overview(self, account: str) -> dict:
        self.account(account)
        return dict(account=account, sync=self.store.sync_snapshot(account), **self.store.counters(account))

    def sync(self, account: str, action: str) -> dict:
        self.account(account)
        if action not in ('resume', 'pause', 'retry'):
            raise ValueError('Unknown sync action')
        return self.store.control_sync(account, action)

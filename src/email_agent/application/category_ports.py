from typing import Protocol


class CategoryRepository(Protocol):
    def resolve(self, category_id: str) -> str: ...
    def initialize(self) -> None: ...

    def ensure_category(
        self, category_id: str, name: str, source: str
    ) -> None: ...

    def list_categories(self, account_id: str | None = None) -> list[dict]: ...

    def list_proposals(self) -> list[dict]: ...

    def propose(
        self,
        category_id: str,
        name: str,
        reason: str,
        icon_key: str,
    ) -> bool: ...

    def approve(self, proposal_id: int) -> bool: ...

    def reject(self, proposal_id: int) -> bool: ...


class AnalysisReader(Protocol):
    def read_results(self) -> list[dict]: ...

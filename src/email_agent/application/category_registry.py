import re

from email_agent.application.category_ports import CategoryRepository
from email_agent.domain.category_icon_resolver import resolve_category_icon


DEFAULT_CATEGORIES = (
    ("bills", "Bills"),
    ("orders", "Orders"),
    ("job_applications", "Job Applications"),
    ("university", "University"),
    ("promotions", "Promotions"),
    ("other", "Other"),
)


class CategoryRegistry:
    def resolve(self, category_id: str) -> str:
        return self._repository.resolve(category_id)
    def __init__(self, repository: CategoryRepository):
        self._repository = repository

    def initialize(self) -> None:
        self._repository.initialize()

        for category_id, name in DEFAULT_CATEGORIES:
            self._repository.ensure_category(
                category_id, name, "system"
            )

    def categories(self, account_id: str | None = None) -> list[dict]:
        self.initialize()
        return self._repository.list_categories(account_id) if account_id else self._repository.list_categories()

    def proposals(self) -> list[dict]:
        self.initialize()
        return self._repository.list_proposals()

    def propose(
        self, category_id: str, name: str, reason: str
    ) -> bool:
        self.initialize()

        if not re.fullmatch(r"[a-z][a-z0-9_]{1,49}", category_id):
            raise ValueError("Invalid category ID")

        name = name.strip()
        reason = reason.strip()

        if not 1 <= len(name) <= 80:
            raise ValueError("Invalid category name")

        if not 1 <= len(reason) <= 500:
            raise ValueError("Invalid proposal reason")

        icon_key = resolve_category_icon(name, reason)

        return self._repository.propose(
            category_id, name, reason, icon_key
        )

    def approve(self, proposal_id: int) -> bool:
        self.initialize()
        return self._repository.approve(proposal_id)

    def reject(self, proposal_id: int) -> bool:
        self.initialize()
        return self._repository.reject(proposal_id)

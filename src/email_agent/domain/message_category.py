"""Local classification assignment for an email message."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MessageCategoryAssignment:
    account_id: str
    message_id: str
    category_id: str
    source: str
    status: str = "classified"
    reason: str = "legacy assignment"
    rule_version: str = "legacy"

    def __post_init__(self) -> None:
        if self.account_id not in ("personal", "university"):
            raise ValueError("Unknown account")

        if not self.message_id.strip():
            raise ValueError("Message ID cannot be empty")

        if not self.category_id.strip():
            raise ValueError("Category ID cannot be empty")

        if self.status not in ("classified", "needs_review"):
            raise ValueError("Invalid classification status")

        if self.source not in ("automatic", "manual"):
            raise ValueError("Invalid assignment source")

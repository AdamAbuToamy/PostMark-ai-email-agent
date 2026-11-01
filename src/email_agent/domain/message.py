from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """Represents an email independently of its provider."""

    id: str
    account_id: str
    sender: str
    subject: str
    internal_date_ms: int = 0
    label_ids: tuple[str, ...] | None = None
    list_headers: tuple[tuple[str, str], ...] | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Message ID cannot be empty")

        if not self.account_id.strip():
            raise ValueError("Account ID cannot be empty")

    @property
    def is_from_university(self) -> bool:
        return self.account_id == "university"

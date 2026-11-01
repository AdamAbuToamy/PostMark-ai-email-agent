from typing import Protocol

from email_agent.domain.message import EmailMessage


class EmailReader(Protocol):
    """Contract for reading emails from any provider."""

    def list_recent(
        self,
        account_id: str,
        limit: int = 10,
    ) -> list[EmailMessage]:
        ...

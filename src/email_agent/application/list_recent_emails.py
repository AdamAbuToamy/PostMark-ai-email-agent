from email_agent.application.ports import EmailReader
from email_agent.domain.message import EmailMessage


class ListRecentEmails:
    """Application use case for retrieving recent emails."""

    def __init__(self, reader: EmailReader):
        self._reader = reader

    def execute(
        self,
        account_id: str,
        limit: int = 10,
    ) -> list[EmailMessage]:

        if not 1 <= limit <= 100:
            raise ValueError("Limit must be between 1 and 100")

        return self._reader.list_recent(
            account_id=account_id,
            limit=limit,
        )

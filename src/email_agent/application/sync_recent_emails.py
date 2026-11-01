from email_agent.application.ports import EmailReader
from email_agent.domain.message import EmailMessage


class SyncRecentEmails:
    """Synchronize Gmail messages into local storage."""

    def __init__(self, reader: EmailReader, message_repository, classifier=None):
        self._classifier = classifier
        self._reader = reader
        self._repository = message_repository

    def execute(
        self,
        account_id: str,
        limit: int = 10,
    ) -> list[EmailMessage]:
        if not 1 <= limit <= 100:
            raise ValueError("Limit must be between 1 and 100")

        messages = self._reader.list_recent(
            account_id=account_id,
            limit=limit,
        )

        for message in messages:
            if message.account_id != account_id:
                raise ValueError("Account mismatch during sync")

            self._repository.save(message)
            if self._classifier is not None:
                self._classifier.classify(message)

        return messages

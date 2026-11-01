from googleapiclient.errors import HttpError
from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.gmail.client import GmailClient


class GmailEmailReader:
    """Converts Gmail API responses to domain entities."""

    def __init__(self, client: GmailClient):
        self._client = client

    def list_category(
        self,
        account_id: str,
        category: str,
        limit: int = 100,
    ) -> list[EmailMessage]:
        references = self._client.list_category_messages(
            category=category,
            limit=limit,
        )

        messages = []
        for reference in references:
            try:
                data = self._client.get_message(reference["id"])
            except HttpError as exc:
                if int(exc.resp.status) == 404:
                    # Deleted between list and get; do not erase local evidence.
                    continue
                raise
            messages.append(
                EmailMessage(
                    id=data["id"],
                    account_id=account_id,
                    sender=data["sender"],
                    subject=data["subject"],
                    internal_date_ms=data.get("internal_date_ms", 0),
                    label_ids=data.get("label_ids"),
                    list_headers=data.get("list_headers"),
                )
            )

        return sorted(
            messages,
            key=lambda message: message.internal_date_ms,
            reverse=True,
        )


    def list_history_page(
        self,
        account_id: str,
        page_token: str | None = None,
        page_size: int = 100,
    ) -> tuple[list[EmailMessage], str | None]:
        """Read one historical page, including Spam and Trash."""
        if account_id not in ("personal", "university"):
            raise ValueError("Unknown account")

        references, next_token = self._client.list_message_page(
            page_token=page_token,
            page_size=page_size,
            include_spam_trash=True,
        )

        messages = []
        self.last_skipped=0
        self.last_discovered_ids = [reference["id"] for reference in references]
        for reference in references:
            try:
                data = self._client.get_message(reference["id"])
            except HttpError as exc:
                if int(exc.resp.status) == 404:
                    # Deleted between list and get; do not erase local evidence.
                    self.last_skipped+=1
                    continue
                raise
            messages.append(
                EmailMessage(
                    id=data["id"],
                    account_id=account_id,
                    sender=data["sender"],
                    subject=data["subject"],
                    internal_date_ms=data.get("internal_date_ms", 0),
                    label_ids=data.get("label_ids"),
                    list_headers=data.get("list_headers"),
                )
            )

        return messages, next_token

    def get_metadata(self, account: str, id: str) -> EmailMessage:
        try:
            data=self._client.get_message(id,max_attempts=2)
        except HttpError as exc:
            if int(exc.resp.status)==404:raise LookupError('Message unavailable; cached state retained') from exc
            raise
        if data['id']!=id:raise ValueError('Gmail identifier mismatch')
        return EmailMessage(id,account,data['sender'],data['subject'],data.get('internal_date_ms',0),data.get('label_ids'),data.get('list_headers'))

    def list_recent(
        self,
        account_id: str,
        limit: int = 10,
    ) -> list[EmailMessage]:

        references = self._client.list_messages(limit=limit)

        messages = []

        for reference in references:
            try:
                data = self._client.get_message(reference["id"])
            except HttpError as exc:
                if int(exc.resp.status) == 404:
                    # Deleted between list and get; do not erase local evidence.
                    continue
                raise

            messages.append(
                EmailMessage(
                    id=data["id"],
                    account_id=account_id,
                    sender=data["sender"],
                    subject=data["subject"],
                    internal_date_ms=data.get("internal_date_ms", 0),
                    label_ids=data.get("label_ids"),
                    list_headers=data.get("list_headers"),
                )
            )

        return messages

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google_auth_httplib2 import AuthorizedHttp
import httplib2
from email_agent.infrastructure.gmail.request_policy import RequestGate, execute


class RawMessageTooLarge(ValueError):
    """The original includes large attachments; readable MIME may still fit."""


class GmailClient:
    """Handles read-only communication with Gmail API."""

    def __init__(self, credentials: Credentials, before_attempt=None,max_attempts=6,timeout=30):
        self._max_attempts=max_attempts
        self._before_attempt=before_attempt
        self._wire_budget=before_attempt is not None
        transport=httplib2.Http(timeout=30)
        scoped_credentials=credentials
        if before_attempt:
            from email_agent.infrastructure.gmail.bounded_http import BudgetHTTP
            transport=BudgetHTTP(before_attempt,timeout=timeout)
            # Expiry/401 never triggers hidden transport-side refresh. The
            # bounded authenticator refreshes before each selected message.
            scoped_credentials=Credentials(token=credentials.token,scopes=credentials.scopes)
        self._service = build(
            "gmail",
            "v1",
            http=AuthorizedHttp(scoped_credentials, http=transport,max_refresh_attempts=0 if before_attempt else 2),
            cache_discovery=False,
            **({'static_discovery':True} if before_attempt else {}),
        )

    def _execute(self, request, attempts=6):
        gate = getattr(self, "_gate", None)
        if gate is None:
            gate = self._gate = RequestGate()
        return execute(request, gate, attempts=min(attempts,getattr(self,'_max_attempts',6)),before_attempt=None if getattr(self,'_wire_budget',False) else getattr(self,'_before_attempt',None))

    def list_messages(self, limit: int = 10) -> list[dict]:
        """Return message references from Gmail."""

        if not 1 <= limit <= 100:
            raise ValueError("Limit must be between 1 and 100")

        response = self._execute(
            self._service.users()
            .messages()
            .list(
                userId="me",
                maxResults=limit,
                includeSpamTrash=True,
            )
        )

        return response.get("messages", [])


    def list_message_page(
        self,
        page_token: str | None = None,
        page_size: int = 100,
        include_spam_trash: bool = True,
        label_ids: list[str] | None = None,
    ) -> tuple[list[dict], str | None]:
        """Read one Gmail page without changing mailbox contents."""
        if not 1 <= page_size <= 500:
            raise ValueError("Page size must be between 1 and 500")

        parameters = {
            "userId": "me",
            "maxResults": page_size,
            "includeSpamTrash": include_spam_trash,
        }

        if label_ids is not None:
            if label_ids not in (["TRASH"],["SPAM"]):raise ValueError("Unsupported label page")
            parameters["labelIds"]=label_ids
        if page_token:
            parameters["pageToken"] = page_token

        response = self._execute(
            self._service.users()
            .messages()
            .list(**parameters)
        )

        return (
            response.get("messages", []),
            response.get("nextPageToken"),
        )

    def list_category_messages(
        self,
        category: str,
        limit: int = 100,
    ) -> list[dict]:
        categories = {
            "primary": "CATEGORY_PERSONAL",
            "updates": "CATEGORY_UPDATES",
            "forums": "CATEGORY_FORUMS",
            "promotions": "CATEGORY_PROMOTIONS",
            "social": "CATEGORY_SOCIAL",
        }

        if category not in categories:
            raise ValueError("Unknown Gmail category")
        if not 1 <= limit <= 100:
            raise ValueError("Invalid limit")

        response = self._execute(
            self._service.users()
            .messages()
            .list(
                userId="me",
                labelIds=["INBOX", categories[category]],
                maxResults=limit,
            )
        )
        return response.get("messages", [])


    def get_message(self, message_id: str, max_attempts: int = 6) -> dict:
        if not message_id:
            raise ValueError("Message ID is required")
        response = self._execute(self._service.users().messages().get(
            userId="me", id=message_id, format="metadata",
            metadataHeaders=["From", "Subject", "Message-ID", "References", "List-ID", "List-Unsubscribe", "List-Unsubscribe-Post", "In-Reply-To", "Auto-Submitted", "Precedence"],
        ), attempts=max_attempts)
        headers = {h["name"].lower(): h["value"] for h in response.get("payload", {}).get("headers", [])}
        return {
            "id": response["id"],
            "sender": headers.get("from", "Unknown"),
            "subject": headers.get("subject", "No subject"),
            "internal_date_ms": int(response.get("internalDate", 0)),
            # Gmail's successful metadata response omits labelIds when empty.
            "label_ids": tuple(response.get("labelIds", [])),
            "list_headers": tuple((key, headers[key][:4000]) for key in ("message-id","references","list-id", "list-unsubscribe", "list-unsubscribe-post", "in-reply-to", "auto-submitted", "precedence") if key in headers) + ((("thread-id", str(response["threadId"])[:256]),) if response.get("threadId") else ()),
        }

    def get_full_message(self, message_id: str) -> dict:
        """Fetch a complete message without modifying Gmail."""
        if not message_id:
            raise ValueError("Message ID is required")

        return self._execute(
            self._service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="full",
            )
        )

    def get_attachment(self, message_id: str, attachment_id: str) -> dict:
        if not message_id or not attachment_id:
            raise ValueError("Attachment context is required")
        return self._execute(self._service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id))

    def get_profile(self):
        return self._execute(self._service.users().getProfile(userId="me"))

    def list_history_changes(self,start_history_id,page_token=None):
        parameters=dict(userId='me',startHistoryId=start_history_id,maxResults=100)
        if page_token:parameters['pageToken']=page_token
        return self._execute(self._service.users().history().list(**parameters),attempts=2)

    def delete_permanently(self,message_id):
        return self._execute(self._service.users().messages().delete(userId="me",id=message_id),attempts=1)

    def trash(self, message_id):
        return self._execute(self._service.users().messages().trash(userId="me", id=message_id), attempts=1)

    def restore(self, message_id, labels):
        if "TRASH" in labels:
            return self._execute(self._service.users().messages().untrash(userId="me", id=message_id), attempts=1)
        if "SPAM" in labels:
            return self._execute(self._service.users().messages().modify(userId="me", id=message_id,
                body={"removeLabelIds": ["SPAM"], "addLabelIds": ["INBOX"]}), attempts=1)
        raise ValueError("Message is not in Spam or Trash")

    def get_raw_message(self, message_id):
        """Explicit bounded read for independent DKIM verification, never bulk sync."""
        import base64
        response = self._execute(self._service.users().messages().get(userId="me", id=message_id, format="raw"), attempts=2)
        encoded = response.get("raw", "")
        if not isinstance(encoded, str) or not encoded:
            raise ValueError("Original message unavailable")
        if len(encoded)>14_000_000:raise RawMessageTooLarge('Original exceeds the raw-message bound')
        try:
            raw=base64.b64decode(encoded + "=" * (-len(encoded) % 4),altchars=b"-_",validate=True)
        except (ValueError,TypeError) as exc:
            raise ValueError("Gmail original is not complete valid base64url data") from exc
        if not raw:raise ValueError('Gmail original is empty')
        if len(raw)>10_000_000:raise RawMessageTooLarge('Original exceeds the raw-message bound')
        return raw

"""Local mailbox policy; no provider writes or AI calls."""
import re
from typing import Protocol

from email_agent.application.category_registry import CategoryRegistry
from email_agent.application.classify_messages import ClassifyMessages, MessageStore


class MailboxStore(Protocol):
    def mailbox(self, account_id: str, category: str, state: str, search: str,
                sort: str, offset: int, limit: int, section: str = 'all') -> dict: ...
    def category_counts(self, account_id: str) -> dict[str, dict]: ...
    def set_local_state(self, account_id: str, message_id: str, state: str) -> None: ...
    def message_details(self, account_id: str, message_id: str) -> dict | None: ...


def mailbox_location(labels):
    if "TRASH" in (labels or ()):
        return "In Trash"
    if "SPAM" in (labels or ()):
        return "In Spam"
    return None


def order_notice(subject: str) -> str | None:
    """Describe explicit email subject evidence, never infer parcel tracking."""
    subject = subject.casefold()
    # Negative and uncertain wording must not become a fulfillment indicator.
    if re.search(r"\b(not|failed|unable|delay|delayed|pending|expected|estimated|will|scheduled|cancelled|canceled|reversed)\b|\?", subject):
        return None
    patterns = (
        (r"\b(?:your (?:order|package|parcel)(?: #?[\w-]*\d[\w-]*)? (?:(?:has been|was|is) )?delivered|delivery confirmation)\b", "Delivery confirmation"),
        (r"\b(?:your (?:order|package|parcel)(?: #?[\w-]*\d[\w-]*)? (?:(?:has been|has|was|is) )?shipped|shipping confirmation)\b", "Shipping confirmation"),
        (r"\brefund (?:issued|processed|confirmed)\b", "Refund update"),
        (r"\bpayment (?:received|confirmed|successful)\b", "Payment confirmation"),
    )
    return next((label for pattern, label in patterns if re.search(pattern, subject)), None)


class MessageManagement:
    def __init__(self, messages: MessageStore, store: MailboxStore,
                 registry: CategoryRegistry, classifier: ClassifyMessages):
        self.messages, self.store = messages, store
        self.registry, self.classifier = registry, classifier

    @staticmethod
    def validate_account(account_id: str) -> None:
        if account_id not in ("personal", "university"):
            raise ValueError("Unknown account")

    def categories(self, account_id: str) -> list[dict]:
        self.validate_account(account_id)
        counts = self.store.category_counts(account_id)
        return [{**category, "counts": counts.get(category["id"], {"new": 0, "done": 0, "all": 0})}
                for category in self.registry.categories(account_id)
                if category["source"] in ("system", "approved")]

    def mailbox(self, account_id: str, category: str, state: str = "new",
                search: str = "", sort: str = "newest", offset: int = 0,
                limit: int = 50, section: str = 'all') -> dict:
        self.validate_account(account_id)
        category=self.registry.resolve(category)
        if category not in {c["id"] for c in self.registry.categories(account_id)}:
            raise ValueError("Category is not active")
        if state not in ("new", "done", "all") or sort not in ("newest", "oldest"):
            raise ValueError("Invalid mailbox filter")
        if len(search) > 200 or offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid mailbox bounds")
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,29}', section):
            raise ValueError('Invalid category section')
        result = self.store.mailbox(account_id, category, state, search.strip(), sort, offset, limit, section)
        for item in result["items"]:
            item["gmail_location"] = mailbox_location(item.pop("_labels", None))
            item["order_notice"] = order_notice(item["subject"]) if category == "orders" and item["classification_status"] == "classified" else None
        return result

    def set_status(self, account_id: str, message_id: str, state: str) -> dict:
        self.validate_account(account_id)
        if state not in ("new", "done"):
            raise ValueError("Invalid local message state")
        self._require_message(account_id, message_id)
        self.store.set_local_state(account_id, message_id, state)
        return {"account_id": account_id, "message_id": message_id, "local_state": state}

    def change_category(self, account_id: str, message_id: str, category: str,
                        application_ref: str = "") -> dict:
        self.validate_account(account_id)
        self._require_message(account_id, message_id)
        self.classifier.manual(account_id, message_id, category,
                               "Category selected by user in Postmark", application_ref)
        return self.details(account_id, message_id)

    def details(self, account_id: str, message_id: str) -> dict:
        self.validate_account(account_id)
        message = self._require_message(account_id, message_id)
        result = self.store.message_details(account_id, message_id)
        return {"account_id": account_id, "message_id": message_id,
                "sender": message.sender, "subject": message.subject, "internal_date_ms": message.internal_date_ms,
                "label_ids": message.label_ids, **(result or {"local_state": "new"})}

    def _require_message(self, account_id, message_id):
        message = self.messages.get(account_id, message_id)
        if message is None:
            raise LookupError("Message not found in this account")
        return message

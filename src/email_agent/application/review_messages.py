"""Human decisions on classification uncertainty, separate from Gmail placement."""
from typing import Protocol
from email_agent.application.message_management import MessageManagement, mailbox_location


class ReviewStore(Protocol):
    def classification_queue(self, account, limit=100, after="", attention="all") -> list[dict]: ...
    def resolve_classification_review(self, account, message_id, fingerprint, decision, reason, assignment=None) -> None: ...
    def classification_candidate(self, account, message_id) -> dict | None: ...
    def classification_history(self, account) -> list[dict]: ...
    def label_coverage(self, account) -> dict: ...


class ReviewMessages:
    def __init__(self, store: ReviewStore, management: MessageManagement) -> None:
        self.store, self.management = store, management

    def listing(self, account: str, after: str = "", attention: str = "all") -> dict:
        self.management.validate_account(account)
        if attention not in ('all','required','untouched'):raise ValueError('Invalid attention filter')
        rows = self.store.classification_queue(account, 101, after, attention)
        for row in rows:
            row["gmail_location"] = mailbox_location(row.pop("_labels", None))
        return {"items": rows[:100], "next_after": rows[99]["message_id"] if len(rows)>100 else None,
                "history": self.store.classification_history(account), "coverage": self.store.label_coverage(account),
                "coverage_note": "Local messages only. Unknown labels and incomplete historical sync can hide placement candidates."}

    def decide(self, account: str, message_id: str, fingerprint: str, decision: str, category: str | None = None, application_ref: str = "") -> dict:
        self.management.validate_account(account)
        if decision not in ("assigned", "approved", "rejected", "reviewed"):
            raise ValueError("Unsupported review decision")
        self.management.details(account, message_id)
        current = self.store.classification_candidate(account, message_id)
        if current is None or current["fingerprint"] != fingerprint:
            raise ValueError("Review changed or already resolved; refresh before deciding")
        assignment = None
        if decision in ("assigned", "approved"):
            selected = category if decision == "assigned" else current["category_id"]
            if not selected or (decision == "approved" and selected == "other"):
                raise ValueError("Choose a meaningful approved category before assigning")
            assignment = self.management.classifier.manual_assignment(account, message_id, selected, "Explicit human classification review", application_ref)
        self.store.resolve_classification_review(account, message_id, fingerprint, decision,
            "Explicit human classification review; no Gmail state changed", assignment)
        return {"status": decision}

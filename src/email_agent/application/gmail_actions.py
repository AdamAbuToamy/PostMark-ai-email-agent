"""Remote operations require an immutable preview and separate approval."""
from contextlib import nullcontext
from typing import Protocol, Callable
import time
import re
from email_agent.application.classify_messages import decide


class ActionStore(Protocol):
    def preview(self, account, kind, payload, conversation_id=None) -> dict: ...
    def get(self, account, id) -> dict: ...
    def claim(self, account, id) -> bool: ...
    def finish(self, account, id, status, result) -> None: ...
    def event(self, id, event, detail) -> None: ...


class GmailOperations(Protocol):
    def labels(self, message_id: str) -> tuple[str, ...]: ...
    def apply(self, operation: str, message_id: str, labels: tuple[str, ...]) -> tuple[str, ...]:
        """Return authoritative post-operation labels after readback, or raise."""
        ...


def safe_promotional_selection(message, decision):
    """Conservative policy for implicit merchant/list selection, shared by actions."""
    category, _, reason = decide(message)
    sensitive = bool(re.search(r"\b(security alert|sign.in|password|verification code|one.time code|authentication|suspicious login)\b", message.subject.casefold()))
    return (decision.get("status") == "classified" and message.label_ids is not None
            and decision.get("category_id") == "promotions" and not sensitive
            and "IMPORTANT" not in message.label_ids and category not in ("orders", "bills", "university")
            and reason != "submitted_application_not_verified")


class GmailActions:
    def __init__(self, messages, assignments, actions: ActionStore,
                 provider: Callable[[str], GmailOperations], on_restored=None, cleanup_guard=None, operation_lock=None):
        self.messages, self.assignments, self.actions, self.provider = messages, assignments, actions, provider
        self.on_restored,self.cleanup_guard,self.operation_lock=on_restored,cleanup_guard,operation_lock

    def _context(self, account, id):
        if account not in ("personal", "university"):
            raise ValueError("Unknown account")
        message = self.messages.get(account, id)
        if message is None:
            raise LookupError("Message not found in this account")
        decision = self.assignments.message_details(account, id) or {}
        return message, {k: decision.get(k) for k in ("category_id", "status", "source")}

    def preview(self, account, operation, ids, category=None, individual_selection=False, conversation_id=None, keep_after_restore=False, cleanup_review=False):
        if operation not in ("trash", "restore") or not 1 <= len(ids) <= 20 or any(not isinstance(id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,256}", id) for id in ids) or len(set(ids)) != len(ids):
            raise ValueError("Select 1-20 distinct messages for a supported operation")
        if keep_after_restore and (operation!="restore" or self.on_restored is None):raise ValueError("Restore protection is unavailable for this operation")
        if cleanup_review and (operation!="trash" or self.cleanup_guard is None):raise ValueError("Cleanup review validation is unavailable")
        bulk = len(ids) > 1
        if bulk and (operation != "trash" or (category != "promotions" and not individual_selection)):
            raise ValueError("Bulk actions are restricted to reviewed Promotions; restore individually")
        items = []
        for id in ids:
            message, decision = self._context(account, id)
            if cleanup_review and not self.cleanup_guard(account,id):raise ValueError("A selected message is protected or no longer eligible")
            if category and decision["category_id"] != category:
                raise ValueError("Category membership changed; select messages again")
            if bulk and not self._safe_bulk(message, decision, individual_selection):
                raise ValueError("Bulk preview excludes uncertain, important and transactional mail. Review these messages individually.")
            if operation == "restore" and not set(message.label_ids or ()) & {"SPAM", "TRASH"}:
                raise ValueError("Refresh labels and select a message currently in Spam or Trash")
            items.append({"message_id": id, "sender": message.sender, "subject": message.subject, "classification": decision, "label_ids": message.label_ids})
        return self.actions.preview(account, f"gmail:{operation}", {"operation": operation, "category_id": category, "bulk": bulk, "individual_selection": individual_selection, "keep_after_restore":keep_after_restore,"cleanup_review":cleanup_review,"items": items}, conversation_id=conversation_id)

    @staticmethod
    def _safe_bulk(message, decision, individual_selection=False):
        if individual_selection:
            return decision.get("status") == "classified" and message.label_ids is not None
        return safe_promotional_selection(message, decision)

    def confirm(self,account,id,approved):
        with self.operation_lock(account) if self.operation_lock else nullcontext():
            return self._confirm(account,id,approved)

    def _confirm(self, account, id, approved):
        if approved is not True:
            raise ValueError("Explicit operation-specific confirmation is required")
        preview = self.actions.get(account, id)
        if preview["kind"] not in ("gmail:trash", "gmail:restore"):
            raise ValueError("Not a Gmail operation preview")
        if preview["status"] not in ("pending", "running"):
            return preview  # Replay never performs another remote operation.
        if preview["status"] == "running":
            raise ValueError("Operation already claimed; reconcile results before retrying")
        if preview["expires"] <= time.time():
            raise ValueError("Preview expired; request a new one")
        provider = self.provider(account)  # Fails closed unless separate modify credentials exist.
        if not self.actions.claim(account, id):
            raise ValueError("Preview expired or already claimed; make a new preview")
        results = []
        deadline = time.monotonic() + 120
        for item in preview["payload"]["items"]:
            message_id = item["message_id"]
            started = False
            try:
                if time.monotonic() >= deadline:
                    raise ValueError("Batch deadline reached; remaining messages were not sent")
                message, current = self._context(account, message_id)
                if current != item["classification"]:
                    raise ValueError("Classification changed since preview")
                if preview["payload"]["bulk"] and not self._safe_bulk(message, current, preview["payload"].get("individual_selection", False)):
                    raise ValueError("Message no longer eligible for bulk operation")
                if preview["payload"].get("cleanup_review") and not self.cleanup_guard(account,message_id,id):raise ValueError("Cleanup decision changed; no remote operation")
                labels = provider.labels(message_id)
                if preview["payload"]["operation"] == "restore" and set(labels) & {"SPAM", "TRASH"} != set(item["label_ids"] or ()) & {"SPAM", "TRASH"}:
                    raise ValueError("Restore location changed since preview")
                if preview["payload"]["bulk"] and not preview["payload"].get("individual_selection") and ("IMPORTANT" in labels or set(labels) & {"TRASH", "SPAM"}):
                    raise ValueError("Remote label state requires individual review")
                if preview["payload"]["operation"]=="trash" and "TRASH" in labels:
                    self.messages.update_labels(account,message_id,labels)
                    result={"message_id":message_id,"status":"succeeded","changed":False,"labels_after":list(labels),"verification":"authoritative_label_readback","message":"Already in Gmail Trash; no repeated move"}
                    self.actions.event(id,"succeeded",result);results.append(result);continue
                self.actions.event(id, "started", {"message_id": message_id})
                started = True
                updated = provider.apply(preview["payload"]["operation"], message_id, labels)
                operation=preview["payload"]["operation"]
                if updated is None or (operation=="trash" and "TRASH" not in updated) or (operation=="restore" and ("TRASH" in updated or ("TRASH" not in labels and "SPAM" in labels and ("SPAM" in updated or "INBOX" not in updated)))):
                    raise ValueError("Requested Gmail label change was not verified")
                self.messages.update_labels(account, message_id, updated)
                if preview["payload"]["operation"] == "restore" and not set(updated) & {"SPAM", "TRASH"}:
                    self.assignments.resolve_placement(account, message_id)
                if preview["payload"].get("keep_after_restore"):self.on_restored(account,message_id)
                result = {"message_id": message_id, "status": "succeeded", "kept_after_restore":bool(preview["payload"].get("keep_after_restore")), "labels_before":list(labels), "labels_after":list(updated), "verification":"authoritative_label_readback"}
            except ValueError:
                result = {"message_id": message_id, "status": "uncertain" if started else "skipped", "message": "Provider result needs reconciliation" if started else "State changed or action is not safe; review a new preview"}
            except Exception as exc:
                result = {"message_id": message_id, "status": "uncertain" if started else "skipped", "write_attempted":started,"error_kind":type(exc).__name__,"message": "Provider failure or outcome unknown; refresh labels before any retry" if started else "Operation could not start; no write was issued"}
            self.actions.event(id, result["status"], result)
            results.append(result)
        succeeded = sum(r["status"] == "succeeded" for r in results)
        status = "succeeded" if succeeded == len(results) else "partial" if succeeded else "failed"
        self.actions.finish(account, id, status, {"items": results, "succeeded": succeeded})
        return self.actions.get(account, id)

"""Exact-target, human-approved RFC 8058 requests with replay-safe audit."""
import time
from typing import Protocol, TypedDict
from email_agent.domain.message import EmailMessage
from email_agent.application.message_management import MessageManagement
from email_agent.application.subscriptions import observed_subscriptions
from email_agent.application.gmail_actions import ActionStore
from email_agent.application.classify_messages import decide
import re


class VerifiedList(TypedDict):
    identity: str
    url: str
    host: str
    signer: str
    author_domain: str
    alignment: str
    author_authorization: str
    signature_index: int
    policy_version: str
    raw_sha256: str
    message_id: str


def third_party_review(proof: VerifiedList) -> bool:
    """Cryptographic integrity does not establish an external signer's delegation."""
    if proof.get("policy_version")!="rfc8058-human-third-party-v1" or not proof.get("author_domain") or proof.get("alignment") not in ("exact","not_exact"):
        raise ValueError("Verification policy changed or evidence is incomplete; request a fresh eligibility check")
    if (proof["signer"]==proof["author_domain"]) != (proof["alignment"]=="exact"):
        raise ValueError("Inconsistent domain-authentication evidence")
    return proof["alignment"]!="exact"


def safe_subscription_selection(message, decision):
    """Unsubscribe changes a list preference, not Gmail labels; unknown labels are irrelevant."""
    if message is None: return False
    category,_,reason=decide(message)
    protected=bool(re.search(r"\b(security alert|sign.in|password|verification code|authentication)\b",message.subject.casefold()))
    return (decision.get("status")=="classified" and decision.get("category_id")=="promotions"
        and category not in ("orders","bills","university") and reason!="submitted_application_not_verified" and not protected)


class SubscriptionMessages(Protocol):
    def get(self, account: str, message_id: str) -> EmailMessage | None: ...
    def subscription_rows(self, account: str) -> list[dict]: ...


class UnsubscribeVerifier(Protocol):
    def verify(self, account: str, message_id: str, identity: str) -> VerifiedList: ...


class UnsubscribeSender(Protocol):
    def __call__(self, url: str, *, verified_dkim_coverage: bool) -> dict: ...


class UnsubscribeStore(ActionStore, Protocol):
    def subscription_states(self, account) -> dict: ...
    def start_subscription(self, account, action_id, subscription_id) -> bool: ...
    def finish_subscriptions(self, account, id, status, result) -> None: ...


class Unsubscribe:
    LIMIT = 5

    def __init__(self, messages: SubscriptionMessages, management: MessageManagement, store: UnsubscribeStore, verifier: UnsubscribeVerifier, sender: UnsubscribeSender) -> None:
        self.messages, self.management, self.store = messages, management, store
        self.verifier, self.sender = verifier, sender

    def preview(self, account: str, ids: list[str]) -> dict:
        self.management.validate_account(account)
        if not isinstance(ids, list) or not 1<=len(ids)<=self.LIMIT or any(not isinstance(id,str) for id in ids) or len(set(ids))!=len(ids):
            raise ValueError("Select 1-5 distinct subscriptions")
        groups = {g["id"]: g for g in observed_subscriptions(self.messages,account)}
        if any(id not in groups for id in ids):
            raise LookupError("Subscription not observed in the selected account")
        states = self.store.subscription_states(account)
        items = []
        deadline = time.monotonic()+60
        for id in ids:
            group = groups[id]
            candidates=group["candidate_message_ids"] or group["message_ids"]
            message_id=next((candidate for candidate in candidates if safe_subscription_selection(self.messages.get(account,candidate),self.management.details(account,candidate))),candidates[0])
            item = {"message_id": message_id, "subscription_id": id, "subject": group["display_name"], "display_name": group["display_name"], "identity": group["identity"], "eligible": False,"sender":self.messages.get(account,message_id).sender,"manual_destinations":group["manual_destinations"],"methods":group["methods"]}
            try:
                if not group["one_click_candidate"]:
                    raise ValueError("No cached RFC 8058 HTTPS one-click offer exists; inspect the original manually. No original was fetched and no request was sent.")
                if time.monotonic()>=deadline:
                    raise ValueError("Verification scheduling limit reached; check this list separately")
                if id in states:
                    raise ValueError("A request was already started for this list. Inspect its saved result; no automatic repeat is allowed.")
                details = self.management.details(account, message_id)
                if not safe_subscription_selection(self.messages.get(account, message_id), details):
                    raise ValueError("Promotional classification is not approved or this message has protected transactional/security evidence. Review its category in the original message, then request a fresh eligibility check.")
                item["proof"] = self.verifier.verify(account, message_id, group["identity"])
                external=third_party_review(item["proof"])
                item.update(eligible=True,requires_third_party_ack=external,host=item["proof"]["host"],
                    reason="DKIM and signed unsubscribe headers verified. Signing domain differs from From; authorization to represent the author is not established. Review both domains and explicitly acknowledge this external signer." if external else "Independent DKIM verified with exact From-domain match; sender legitimacy still requires human review")
            except ValueError as exc:
                item["reason"] = str(exc)
            except Exception:
                item["reason"] = "Original message or independent DNS verification unavailable; no request sent. Inspect manually."
            items.append(item)
        preview=self.store.preview(account, "unsubscribe:one_click", {"items": items,
            "description": "Verification reads at most five originals. Confirmation sends one POST per approved eligible list. Acceptance does not prove delivery has stopped."})
        if not any(item["eligible"] for item in items):
            self.store.finish(account,preview["id"],"manual_required",{"message":"No verified automatic target; use the reviewed manual options. Nothing was submitted.","items":[]})
            preview=self.store.get(account,preview["id"])
        return preview

    def confirm(self, account: str, id: str, approved: bool, approved_targets: list[str], approved_third_party_targets: list[str] | None = None) -> dict:
        self.management.validate_account(account)
        value = self.store.get(account, id)
        if value["kind"]!="unsubscribe:one_click":
            raise ValueError("Not an unsubscribe preview")
        if approved is not True or not isinstance(approved_targets,list) or not approved_targets or any(not isinstance(t,str) for t in approved_targets) or len(set(approved_targets))!=len(approved_targets):
            raise ValueError("Explicitly approve each exact eligible subscription")
        eligible = {item["subscription_id"] for item in value["payload"]["items"] if item["eligible"]}
        if not set(approved_targets)<=eligible:
            raise ValueError("Approval includes an unverified or unknown target")
        acknowledged=approved_third_party_targets or []
        if not isinstance(acknowledged,list) or any(not isinstance(target,str) for target in acknowledged) or len(set(acknowledged))!=len(acknowledged):
            raise ValueError("External signer acknowledgments must name distinct exact targets")
        selected=[item for item in value["payload"]["items"] if item["subscription_id"] in approved_targets]
        required_external={item["subscription_id"] for item in selected if third_party_review(item["proof"])}
        if set(acknowledged)!=required_external:
            raise ValueError("Explicit acknowledgment of every selected external signer is required; domain delegation is not established by DKIM alone")
        if value["status"] not in ("pending", "running"):
            return value
        if not self.store.claim(account, id):
            raise ValueError("Preview expired or already claimed; it will not be replayed")
        self.store.event(id,"unsubscribe_approved",{"subscription_ids":approved_targets,"external_signers_acknowledged":acknowledged,"authentication":[{"subscription_id":item["subscription_id"],"signer":item["proof"]["signer"],"author_domain":item["proof"]["author_domain"],"alignment":item["proof"]["alignment"],"author_authorization":item["proof"]["author_authorization"]} for item in selected]})
        results = []
        deadline = time.monotonic()+90
        for item in value["payload"]["items"]:
            result = {"message_id": item["message_id"], "subscription_id": item["subscription_id"], "status": "skipped"}
            started = False
            try:
                if item["subscription_id"] in approved_targets:
                    if time.monotonic()>=deadline:
                        raise ValueError("Scheduling deadline reached; request not sent")
                    details = self.management.details(account, item["message_id"])
                    if not safe_subscription_selection(self.messages.get(account, item["message_id"]), details):
                        raise ValueError("Message classification changed; request another preview")
                    proof = self.verifier.verify(account, item["message_id"], item["identity"])
                    if third_party_review(proof) and item["subscription_id"] not in acknowledged:
                        raise ValueError("External signer policy was not acknowledged")
                    if proof != item["proof"]:
                        raise ValueError("Signed target changed since preview; no request sent")
                    if not self.store.start_subscription(account,id,item["subscription_id"]):
                        raise ValueError("Another request was started for this list; inspect the saved result before retrying")
                    started = True
                    response = self.sender(proof["url"], verified_dkim_coverage=True)
                    if response.get("state")!="requested":
                        raise ValueError("Request acceptance was not verified")
                    result.update(status="requested", message="Request accepted; delivery cessation is not confirmed")
            except ValueError as exc:
                result.update(status="uncertain" if started else "skipped", message=str(exc))
            except Exception:
                result.update(status="uncertain" if started else "skipped", message="Result unavailable; no automatic retry. Inspect the list before a new request.")
            results.append(result)
            self.store.event(id, "unsubscribe_result", result)
        requested = sum(r["status"]=="requested" for r in results)
        self.store.finish_subscriptions(account,id,"succeeded" if requested==len(approved_targets) else "partial" if requested else "failed", {"items":results,"requested":requested})
        return self.store.get(account,id)

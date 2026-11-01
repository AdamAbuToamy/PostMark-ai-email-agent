"""Account-specific future-mail Trash policy. No SQL, SDK, AI or UI dependencies."""
import hashlib
import re
import time
from email.utils import parseaddr
from typing import Protocol, Callable
from email_agent.application.classify_messages import decide
from email_agent.application.gmail_actions import GmailOperations
from email_agent.application.subscriptions import subscription_identity
from email_agent.domain.search_text import normalize_search_text

VERSION = "conservative-ads-v1"
PROTECTED_LABELS = {"IMPORTANT", "STARRED", "SPAM", "TRASH", "SENT", "DRAFT", "CATEGORY_PERSONAL"}
SENSITIVE = re.compile(r"\b(order|purchase|shipping|delivery|refund|return|receipt|invoice|bill|payment|security|password|login|sign.in|verification|authentication|account recovery|account locked|account suspended|new device|one.time code|otp|verify|recovery|reset|application|interview|assessment|employer|university|college|campus|course|tuition|semester|lecture|exam|assignment|career|job)\b|طلب|شراء|فاتور|فواتير|ايصال|دفع|مدفوع|سداد|استرداد|ارجاع|مرتجع|شحن|توصيل|طرد|تتبع|امان|حساب|تحقق|رمز|تاكيد|استعاد|جامع|دراس|محاضر|امتحان|مقرر|تسجيل الدخول|مقابل|وظيف|توظيف|חשבון|תשלום|קבלה|משלוח|הזמנה|החזר|אבטחה|אוניברסיט|קורס|מבחן|סיסמ|ראיון")
ADVERTISING = re.compile(r"\b(sale|discount|coupon|special offer|deals?|shop now|save \d+)\b|تخفيض|خصم|عروض|מבצע|הנחה")


class CleanupStore(Protocol):
    def protection(self, account, message): ...
    def policy(self, account: str) -> dict: ...
    def configure(self, account: str, enabled: bool, mode: str, exclusions: list[str], approved: bool, expected_revision: str | None = None) -> None: ...
    def ingest(self, account: str) -> None: ...
    def pending(self, account: str, limit: int) -> list[dict]: ...
    def claim(self, account: str, id: str, revision: str, detail: dict) -> bool: ...
    def finish(self, account: str, id: str, status: str, detail: dict) -> None: ...
    def pause(self, account: str, reason: str) -> None: ...
    def history(self, account: str) -> dict: ...
    def item(self, account: str, id: str) -> dict: ...
    def add_exclusion(self, account: str, address: str) -> None: ...
    def acquire(self, account: str) -> str | None: ...
    def release(self, account: str, owner: str) -> None: ...
    def cancellation_status(self, account: str, subscription: str) -> str | None: ...
    def reconsider_future(self,account,id,revision,digest): ...


def advertising_purpose(message,body):
    """Independent current-body proof; transaction verbs alone are not invoices."""
    if not body:return False
    from email_agent.application.local_understanding import understand
    purpose=understand(message,body)
    if purpose:return purpose.category=='promotions' and not purpose.protected
    category,status,reason=decide(message,body)
    words={m.group(0).casefold() for m in SENSITIVE.finditer(normalize_search_text(message.subject+' '+body))}
    return category=='promotions' and status=='classified' and reason=='promotion_signal' and bool(ADVERTISING.search(normalize_search_text(body))) and words<={'order','purchase'}


def eligible_ad(message, decision, exclusions, enabled_at_ms, body=None):
    if message is None:return False,"Message unavailable"
    if message.internal_date_ms <= enabled_at_ms:return False,"Historical mail is excluded"
    sender=parseaddr(message.sender)[1].casefold()
    if sender in exclusions:return False,"Protected sender"
    if message.label_ids is None or set(message.label_ids) & PROTECTED_LABELS:
        return False,"Protected or unknown Gmail labels"
    if decision.get("source")=="manual":return False,"Manual classification protected"
    if decision.get("category_id")!="promotions" or decision.get("status")!="classified" or decision.get("source")!="automatic":
        return False,"Not confidently classified advertising"
    subject=normalize_search_text(message.subject)
    academic=bool(re.search(r"(?:\.edu(?:\.[a-z]{2})?|\.ac\.[a-z]{2})$",sender.split("@")[-1]))
    from email_agent.application.local_understanding import understand
    purpose=understand(message,body or '')
    promotional=advertising_purpose(message,body)
    if purpose and purpose.protected:return False,'Transactional or sensitive body evidence protected'
    if (SENSITIVE.search(subject) and not promotional) or academic or re.match(r"^(re|fw|fwd)\s*:",subject):
        return False,"Transactional, academic, personal or sensitive correspondence protected"
    if body is None or not body.strip():return False,'Message content not inspected; retained for content review'
    if body and SENSITIVE.search(normalize_search_text(body)) and not promotional:
        return False,'Sensitive content evidence; retained until its purpose is verified'
    category,status,_=decide(message,body or '')
    if category!="promotions" or status!="classified":return False,"Classification needs review"
    headers=dict(message.list_headers or ())
    list_signal=bool(headers.get("list-id") or headers.get("list-unsubscribe"))
    if not ADVERTISING.search(subject) or not ("CATEGORY_PROMOTIONS" in message.label_ids or list_signal):
        return False,"Insufficient independent advertising signals"
    return True,"Advertising subject, approved automatic Promotions classification and promotional metadata"


class PromotionalCleanup:
    LIMIT=10
    def __init__(self, messages, assignments, store:CleanupStore, provider:Callable[[str],GmailOperations], can_modify:Callable[[str],bool]):
        self.messages,self.assignments,self.store=messages,assignments,store
        self.provider,self.can_modify=provider,can_modify

    @staticmethod
    def validate(account):
        if account not in ("personal","university"):raise ValueError("Unknown account")

    def settings(self,account):
        self.validate(account)
        return {"policy":self.store.policy(account),"history":self.store.history(account),"gmail_modify":bool(self.can_modify(account)),"cycle_limit":self.LIMIT}

    def reconsider(self,account,id):
        """Queue improved evidence only under the existing future-mail policy."""
        self.validate(account);policy=self.store.policy(account)
        if not policy['enabled']:return False
        message=self.messages.get(account,id);decision=self.assignments.message_details(account,id) or {}
        body=self.assignments.cleanup_evidence(account,id)
        allowed,_=eligible_ad(message,decision,policy['exclusions'],policy['enabled_at_ms'],body)
        if not allowed or self.store.protection(account,message,ignore_system=True):return False
        return self.store.reconsider_future(account,id,policy['revision'],hashlib.sha256(body.encode()).hexdigest())

    def configure(self,account,enabled,mode,exclusions,approved,expected_revision=None):
        self.validate(account)
        if mode not in ("review","automatic") or not isinstance(enabled,bool):raise ValueError("Invalid cleanup mode")
        if not isinstance(exclusions,list) or len(exclusions)>50 or any(not isinstance(v,str) or not re.fullmatch(r"[^\s@]{1,100}@[^\s@]{1,150}\.[^\s@]{2,50}",v) for v in exclusions):
            raise ValueError("Use up to 50 exact sender email exclusions")
        exclusions=sorted(set(v.casefold() for v in exclusions))
        existing=self.store.policy(account)
        if expected_revision is not None and expected_revision!=existing["revision"]:raise ValueError("Cleanup settings changed; reload before saving")
        if enabled==existing["enabled"] and mode==existing["mode"] and exclusions==existing["exclusions"] and not existing["paused_reason"]:
            return self.settings(account)
        if enabled and mode=="automatic":
            if approved is not True:raise ValueError("Confirm future automatic Trash for this exact account")
            if not self.can_modify(account):raise PermissionError("Authorize this account with gmail.modify first; no permission is expanded automatically")
        self.store.configure(account,enabled,mode,exclusions,approved,expected_revision)
        return self.settings(account)

    def protect(self,account,id,scope="message"):
        self.validate(account)
        message=self.messages.get(account,id)
        if message is None:raise LookupError("Message not found in this account")
        address=parseaddr(message.sender)[1].casefold()
        if scope=='sender':
            if not re.fullmatch(r"[^\s@]{1,100}@[^\s@]{1,150}\.[^\s@]{2,50}",address):raise ValueError("Sender has no valid address; protect this message instead")
            self.store.add_exclusion(account,address)
        elif scope=='message':self.store.keep_message(account,id)
        else:raise ValueError('Choose this message or the explicitly named exact sender')
        return self.settings(account)

    def _annotation(self,account,message,reason):
        identity,_=subscription_identity(dict(message.list_headers or ()),message.sender)
        id=hashlib.sha256(identity.encode()).hexdigest()[:24]
        return {"reason":reason,"policy_version":VERSION,"subscription_id":id,"subscription_status":self.store.cancellation_status(account,id)}

    def run(self,account):
        self.validate(account)
        if not self.store.policy(account)["enabled"]:return
        owner=self.store.acquire(account)
        if owner is None:return
        try:
            self.store.ingest(account)
            deadline=time.monotonic()+45
            for item in self.store.pending(account,self.LIMIT):
                if time.monotonic()>=deadline:break
                policy=self.store.policy(account)
                if not policy["enabled"]:break
                id=item["message_id"]
                # An interrupted request is reconciled read-only, never submitted twice.
                if item["status"] in ("running","uncertain"):
                    self._reconcile(account,item)
                    if self.store.item(account,id)["status"]!="succeeded":
                        self.store.pause(account,"Review the interrupted operation before enabling future cleanup")
                        break
                    continue
                if item["revision"]!=policy["revision"]:continue
                message=self.messages.get(account,id)
                decision=self.assignments.message_details(account,id) or {}
                evidence=getattr(self.assignments,'cleanup_evidence',self.assignments.cached_evidence)
                allowed,reason=eligible_ad(message,decision,policy["exclusions"],policy["enabled_at_ms"],evidence(account,id))
                protected=self.store.protection(account,message) if message else None
                if protected:allowed,reason=False,protected
                if not allowed:
                    self.store.finish(account,id,"skipped",{"reason":reason});continue
                detail=self._annotation(account,message,reason)
                detail.update(sender=message.sender,subject=message.subject,operation="trash" if policy["mode"]=="automatic" else "propose_trash",classification={key:decision.get(key) for key in ("category_id","source","status","rule_version")})
                if policy["mode"]=="review":
                    self.store.finish(account,id,"review",detail);continue
                if not self.store.claim(account,id,policy["revision"],detail):continue
                attempted=False
                try:
                    if not self.can_modify(account):raise PermissionError("Missing modify permission")
                    provider=self.provider(account)
                    labels=provider.labels(id)
                    if labels is None:raise ValueError("Unknown remote labels")
                    labels=tuple(labels)
                    if set(labels)&PROTECTED_LABELS:
                        self.store.finish(account,id,"skipped",{**detail,"reason":"Remote labels protected"});continue
                    current=self.store.policy(account)
                    allowed,_=eligible_ad(self.messages.get(account,id),self.assignments.message_details(account,id) or {},current["exclusions"],current["enabled_at_ms"],evidence(account,id))
                    if self.store.protection(account,self.messages.get(account,id)):allowed=False
                    if not allowed or not current["enabled"] or current["revision"]!=item["revision"]:
                        self.store.finish(account,id,"skipped",{**detail,"reason":"Policy or classification changed"})
                        if not allowed:self.store.pause(account,"Classification changed during cleanup; review before continuing")
                        continue
                    attempted=True
                    updated=tuple(provider.apply("trash",id,labels))
                    if "TRASH" not in updated:raise ValueError("Trash label not verified")
                    self.messages.update_labels(account,id,updated)
                    self.store.finish(account,id,"succeeded",{**detail,"labels_before":labels,"labels_after":updated,"verification":"authoritative_label_readback"})
                except Exception:
                    self.store.finish(account,id,"uncertain" if attempted else "failed",{**detail,"reason":"Provider result requires reconciliation" if attempted else "Gmail permission or readback unavailable"})
                    self.store.pause(account,"Gmail cleanup paused; review permissions and recent actions")
                    break
        finally:self.store.release(account,owner)

    def _reconcile(self,account,item):
        id=item["message_id"]
        try:
            labels=tuple(self.provider(account).labels(id))
            if "TRASH" in labels:
                self.messages.update_labels(account,id,labels)
                self.store.finish(account,id,"succeeded",{**item.get("detail",{}),"reason":"Existing Trash label reconciled without another write","labels_after":labels,"verification":"authoritative_label_readback"})
            else:self.store.finish(account,id,"uncertain",{**item.get("detail",{}),"reason":"Remote outcome unresolved; no repeat write"})
        except Exception:self.store.finish(account,id,"uncertain",{**item.get("detail",{}),"reason":"Readback unavailable; no repeat write"})

    def reconcile(self,account,id):
        self.validate(account)
        item=self.store.item(account,id)
        if item["status"] not in ("running","uncertain"):return self.settings(account)
        owner=self.store.acquire(account)
        if owner is None:raise ValueError("Cleanup is running; wait for its result")
        try:self._reconcile(account,item)
        finally:self.store.release(account,owner)
        return self.settings(account)

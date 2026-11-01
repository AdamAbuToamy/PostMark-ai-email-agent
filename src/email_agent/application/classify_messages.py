"""Deterministic local policy; email text never authorizes an external action."""
import re
from typing import Protocol
from email_agent.domain.message import EmailMessage
from email_agent.domain.message_category import MessageCategoryAssignment
from email_agent.application.category_registry import CategoryRegistry

from email_agent.application.local_understanding import understand
from email_agent.application.job_evidence import employment_evidence, ADVERTISEMENT

VERSION = "local-v6-continuous"


class ClassificationStore(Protocol):
    def put(self, assignment: MessageCategoryAssignment) -> bool: ...
    def propose_review(self, message: EmailMessage, reason: str | None) -> None: ...
    def verified_application_thread(self, message: EmailMessage) -> bool: ...


class MessageStore(Protocol):
    def get(self, account_id: str, message_id: str) -> EmailMessage | None: ...
    def scan(self, account_id: str, after: str = "", limit: int = 100) -> list[EmailMessage]: ...


def decide(message: EmailMessage, body: str = '') -> tuple[str, str, str]:
    evidence = understand(message, body)
    if evidence and evidence.category in {'security','bills'}:
        return evidence.category, 'classified', evidence.reason
    job = employment_evidence(message.subject, message.sender, body)
    if job:
        return 'job_applications', 'classified', job.reason
    if re.search(ADVERTISEMENT, message.subject, re.IGNORECASE):
        return 'promotions', 'classified', 'recruitment_advertisement'
    if evidence:
        if evidence.category=='orders' and re.search(r'\b(sale|coupon|discount|special offer|shop now)\b',message.subject,re.IGNORECASE):
            return 'orders','needs_review','mixed_transaction_and_promotion'
        return evidence.category, "classified", evidence.reason
    subject = message.subject.casefold()
    if re.match(r"^(re|fw|fwd)\s*:", subject):
        return "other", "needs_review", "conversation_context_required"
    promo = re.search(r"\b(sale|coupon|discount|shop now|special offer|deals?|save \d+|off your next|place your order|order now)\b", subject)
    if promo and body.strip():
        from email_agent.application.message_text import purpose_text
        if not re.search(r'\b(sale|coupon|discount|shop now|special offer|offers|deals?|free trial|save\s*[$€£₪]?\s*\d+)\b|تخفيض|خصم|عروض|מבצע|הנחה',purpose_text(body)):
            return 'other','needs_review','body_purpose_unresolved'
    transaction = re.search(
        r"\b(order (?:confirmation|confirmed|received|shipped|delivered|cancelled|canceled)|"
        r"payment (?:received|confirmed|confirmation|successful)|purchase (?:receipt|confirmation)|"
        r"shipping (?:confirmation|update)|delivery (?:confirmation|update)|"
        r"refund (?:issued|processed|confirmed)|your (?:order|package|parcel).{0,50}"
        r"(?:confirmed|shipped|delivered|cancelled|canceled|on its way|out for delivery)|thank you for your (?:order|purchase))\b", subject)
    if transaction:
        return "orders", "needs_review" if promo else "classified", "transaction_subject" if not promo else "mixed_transaction_and_promotion"
    recruitment = re.search(r"\b(job alert|jobs? for you|we are hiring|career opportunities|hiring now|apply now)\b", subject)
    if recruitment:
        return "promotions", "classified", "recruitment_advertisement"
    job_response = re.search(r"\b(application|interview|assessment|offer letter|applying)\b", subject)
    if job_response:
        return "other", "needs_review", "submitted_application_not_verified"
    if promo or (not body.strip() and "CATEGORY_PROMOTIONS" in (message.label_ids or ())):
        return "promotions", "classified", "promotion_signal"
    if re.search(r"\b(invoice|bill (?:due|ready)|payment due|billing statement)\b", subject):
        return "bills", "classified", "billing_subject"
    if re.search(r"\b(order|purchase|payment|shipping|delivery|refund|receipt)\b", subject):
        return "orders", "needs_review", "possible_transaction"
    if message.account_id == "university" and re.search(r"\b(exam|semester|tuition|course registration|lecture|assignment due)\b", subject):
        return "university", "classified", "academic_subject"
    return "other", "needs_review", "insufficient_local_evidence"


class ClassifyMessages:
    def __init__(self, messages: MessageStore, assignments: ClassificationStore, registry: CategoryRegistry):
        self.messages, self.assignments, self.registry = messages, assignments, registry

    def classify(self, message: EmailMessage) -> None:
        message = self.messages.get(message.account_id, message.id) or message
        cached = getattr(self.assignments, 'cached_evidence', None)
        body = (cached(message.account_id, message.id) or '') if callable(cached) else ''
        category, status, reason = decide(message, body)
        automatic_allowed=getattr(self.assignments,'automatic_category_allowed',lambda account,category:True)
        current=self.assignments.message_details(message.account_id,message.id) or {}
        if current.get('source')=='automatic' and not automatic_allowed(message.account_id,current.get('category_id','other')):
            retained=getattr(self.assignments,'current_semantic_evidence',lambda msg:None)(message)
            if retained:return  # Revocation stops new autonomous changes; it is not Undo.
        # Learn only from a submitted application proven by explicit local
        # acknowledgment or user feedback in this exact provider thread.
        if category == 'other' and not re.search(ADVERTISEMENT+r'|\b(course|training|newsletter|discount|sale)\b',message.subject.casefold()):
            verify=getattr(self.assignments,'verified_application_thread',None)
            if callable(verify) and verify(message) is True:
                category,status,reason='job_applications','classified','verified_submitted_application_thread'
        pattern = self.assignments.approved_pattern(message, category, status, reason) if hasattr(self.assignments, "approved_pattern") else None
        if pattern:
            category, reason = pattern, "approved_recurring_pattern"
        semantic_policy=getattr(self.assignments,'approved_semantic_policy',None)
        target=semantic_policy(message,body) if callable(semantic_policy) else None
        if target and not (category in {'security','bills','orders','job_applications'} and category!=target and reason.startswith(('content_','explicit_submitted_application_'))):
            category,status,reason=target,'classified','approved_semantic_policy'
        complete=getattr(self.assignments,'has_complete_body',None)
        if category=='other' and callable(complete) and complete(message.account_id,message.id):
            purpose=understand(message,body)
            normalize=lambda value:re.sub(r'[_\s]+',' ',value.casefold()).strip()
            matches=[c['id'] for c in self.registry.categories(message.account_id) if c['source']=='approved' and automatic_allowed(message.account_id,c['id']) and purpose and normalize(c['name'])==normalize(purpose.name)]
            if len(matches)==1:
                category,status,reason=matches[0],'classified','existing_semantic_category_purpose'
        # Repeated sync must retain a consented full-text interpretation when
        # local evidence remains inconclusive. Changed evidence invalidates it.
        semantic_reader=getattr(self.assignments,'current_semantic_evidence',None)
        frame=semantic_reader(message) if category=='other' and status=='needs_review' and callable(semantic_reader) else None
        routine_threshold=getattr(self.assignments,'routine_semantic_threshold',lambda account:.9)(message.account_id)
        from email_agent.application.semantic_mail import validated_model_frame
        if frame and validated_model_frame(frame) and frame.get('understood') and not frame.get('analysis_reason') and frame.get('confidence',0)>=routine_threshold and frame.get('input_coverage',{}).get('body_complete'):
            target=frame.get('suggested_category_id')
            active={c['id'] for c in self.registry.categories(message.account_id) if c['source'] in {'system','approved'}}
            from email_agent.application.semantic_mail import supported_application_frame
            if target in active-{'other'} and automatic_allowed(message.account_id,target) and (target!='job_applications' or supported_application_frame(frame)):
                category,status,reason=target,'classified','semantic_full_message_evidence'
            elif target=='other':
                from email_agent.application.semantic_mail import supported_semantic_purpose
                if supported_semantic_purpose(frame):status,reason='classified','semantic_full_message_evidence'
        category=self.registry.resolve(category)
        self.assignments.put(MessageCategoryAssignment(message.account_id, message.id,
            category, "automatic", status, reason, VERSION))
        if hasattr(self.assignments, "update_attention"):
            self.assignments.update_attention(message)
        observe=getattr(self.assignments,'record_semantic_frame',None)
        if callable(observe):observe(message)
        # Importance and sender display name are deliberately not legitimacy evidence.
        misplaced = bool(set(message.label_ids or ()) & {"SPAM", "TRASH"})
        candidate = category in {"orders", "bills", "university", "job_applications"} or reason == "submitted_application_not_verified"
        self.assignments.propose_review(message,
            "Possible misplaced correspondence; verify sender and context manually. No Gmail change authorized."
            if misplaced and candidate else None)

    def run(self, account_id: str) -> int:
        if account_id not in ("personal", "university"):
            raise ValueError("Unknown account")
        after, count = "", 0
        while True:
            batch = self.messages.scan(account_id, after)
            if not batch:
                return count
            for message in batch:
                self.classify(message)
                count += 1
            after = batch[-1].id

    def manual_assignment(self, account_id: str, message_id: str, category_id: str,
               reason: str, application_ref: str = "") -> MessageCategoryAssignment:
        message = self.messages.get(account_id, message_id)
        if message is None:
            raise ValueError("Message not found in account")
        allowed = {c["id"] for c in self.registry.categories(message.account_id) if c["source"] in ("system", "approved")}
        if category_id not in allowed:
            raise ValueError("Category is not active")
        if not 1 <= len(reason.strip()) <= 500:
            raise ValueError("A review reason of 1-500 characters is required")
        if category_id == "job_applications":
            if not 1 <= len(application_ref.strip()) <= 200:
                raise ValueError("Reference to an actually submitted application is required")
            reason += " | Submitted application: " + application_ref.strip()
        return MessageCategoryAssignment(account_id, message_id,
            category_id, "manual", "classified", reason, VERSION)

    def manual(self, account_id: str, message_id: str, category_id: str,
               reason: str, application_ref: str = "") -> bool:
        return self.assignments.put(self.manual_assignment(account_id,message_id,category_id,reason,application_ref))

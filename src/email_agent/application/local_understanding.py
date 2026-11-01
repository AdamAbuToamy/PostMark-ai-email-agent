"""Message-level local evidence. Provider text is data, never an instruction.

These discrete evidence grades are not calibrated probabilities. Missing labels
never prevent organization, but still prevent unattended Gmail cleanup.
"""
from dataclasses import dataclass
from email.utils import parseaddr
import hashlib
import re
from functools import lru_cache
from email_agent.domain.message import EmailMessage
from email_agent.application.content_understanding import content_purpose


@dataclass(frozen=True)
class Understanding:
    kind: str
    name: str
    category: str
    reason: str
    protected: bool = False
    icon: str = 'folder-open'


def _understand(message: EmailMessage, body: str = '') -> Understanding | None:
    subject = message.subject.casefold()
    address = parseaddr(message.sender)[1].casefold()
    domain = address.rsplit('@', 1)[-1]
    github = domain == 'github.com' or domain.endswith('.github.com')
    purpose = content_purpose(message.subject, body)
    if purpose:
        if github and purpose.category == 'security':
            return Understanding('github_security','GitHub security alerts','security',purpose.reason,True,'shield')
        return purpose
    if re.search(r'\b(security alert|security warning|password reset|reset your password|verification code|one.time (?:code|password)|two.factor|new sign.in|account recovery|vulnerability|vulnerabilities|dependabot)\b|התראת אבטחה|קוד אימות|تنبيه أمني|رمز التحقق', subject):
        return Understanding('github_security' if github else 'security', 'GitHub security alerts' if github else 'Security alerts', 'security', 'security_evidence', True, 'shield')
    if github:
        if re.search(r'\b(workflow|github actions|run failed|run succeeded|build failed|build successful|ci failed|ci passed)\b', subject):
            failed = bool(re.search(r'\b(fail(?:ed|ure|ures)?|cancelled|canceled|timed out)\b', subject))
            return Understanding('github_ci_failed' if failed else 'github_ci', 'GitHub workflow failures' if failed else 'GitHub workflow notifications', 'other', 'github_workflow_structure', icon='bell')
        if re.search(r'\b(pull request|pull requests|pr #?\d+|review requested|merged (?:pr|pull))\b|\(#\d+\)', subject):
            return Understanding('github_pr', 'GitHub pull requests', 'other', 'github_pull_request_structure', icon='file-text')
    headers = dict(message.list_headers or ())
    # Explicit multilingual transaction and academic wording is category evidence,
    # without claiming current fulfillment or permitting cleanup.
    if re.search(r'קוד אימות|סיסמתך|שינוי סיסמ|איפוס סיסמ|رمز التحقق|إعادة تعيين كلمة',subject):
        return Understanding('security','Security alerts','security','security_evidence',True,'shield')
    if re.search(r'(?:הזמנתך|ההזמנה שלך|אישור הזמנה|פרטי הזמנה|החבילה שלך|המשלוח שלך|מספר מעקב|משלוח.{0,30}לאיסוף|החבילה.{0,30}(?:נשלחה|נמסרה|ממתינה))|(?:تم شحن طلبك|تم تسليم طلبك|تأكيد طلبك)|\b(?:your (?:order|package|parcel|shipment).{0,65}(?:update|tracking|arrived|ready|delivery)|track your (?:order|package|shipment)|shipment (?:notification|details)|order #?[a-z0-9-]*\d[a-z0-9-]*)\b',subject):
        return Understanding('transaction_updates','Order updates','orders','explicit_transaction_context',True,'package')
    academic_domain=bool(re.search(r'(?:\.edu(?:\.[a-z]{2})?|\.ac\.[a-z]{2})$',domain))
    if academic_domain and not body.strip() and not re.search(r'\b(application|interview|assessment|job|career|hiring|apply|sale|discount|coupon|special offer)\b|משרה|ראיון|דרושים|وظيف|مقابلة',subject):
        return Understanding('academic','University and courses','university','academic_domain_correspondence',True,'graduation-cap')
    if academic_domain and re.search(r'ציונ|הרשמ|לימוד|בחינ|שכר לימוד|מזכירות|תשלום|קבלה|מערכת שעות|\b(?:student|academic|campus|grades?|transcript|registration|tuition|deadline)\b',subject):
        return Understanding('academic','University and courses','university','academic_domain_and_subject',True,'graduation-cap')
    if re.search(r'חשבונית|קבלה על|אישור תשלום|תשלום התקבל|فاتورة|إيصال دفع',subject):
        return Understanding('billing','Bills and receipts','bills','billing_evidence',True,'receipt')
    if re.match(r'^(re|fw|fwd)\s*:|^השב:',subject) and (headers.get('in-reply-to') or headers.get('thread-id') or 'CATEGORY_PERSONAL' in (message.label_ids or ())):
        return Understanding('conversation','Conversations','other','threaded_conversation',True,'heart-pulse')
    if headers.get('list-id') and re.search(r'\b(newsletter|weekly digest|daily digest|weekly roundup)\b',subject):
        return Understanding('newsletter','Newsletters','other','identified_recurring_newsletter',True,'file-text')
    # Billing/security precede promotional words: cross-selling cannot turn a
    # receipt or account alert into a confidently promotional assignment.
    if re.search(r'\b(invoice|payment receipt|payment due|billing statement|bill ready|bill due)\b|קבלה על תשלום|חשבונית|فاتورة|إيصال دفع', subject):
        return Understanding('billing', 'Bills and receipts', 'bills', 'billing_evidence', True, 'receipt')
    if re.search(r'\b(account (?:alert|suspended|locked)|service (?:outage|disruption)|storage (?:full|warning))\b', subject):
        return Understanding('service_alert', 'Account and service alerts', 'other', 'account_service_alert', True, 'bell')
    if (re.search(r'(?:\.edu(?:\.[a-z]{2})?|\.ac\.[a-z]{2})$', domain) and
            re.search(r'\b(course|exam|semester|lecture|tuition|registration|assignment)\b|קורס|מבחן|הרצאה|סמסטר|לימוד|دراس|محاضر|امتحان', subject)):
        return Understanding('academic', 'University and courses', 'university', 'academic_domain_and_subject', True, 'graduation-cap')
    return None


def cluster_identity(message: EmailMessage, category: str, reason: str) -> tuple[str, str, str]:
    """Keep semantic subtypes apart; recurring lists also split by purpose."""
    if category == 'job_applications':
        from email_agent.application.job_evidence import employment_evidence
        job = employment_evidence(message.subject, message.sender)
        state = job.state if job else 'unknown'
        return 'applications_' + state, 'Applications · ' + (job.label if job else 'Replies'), 'briefcase-business'
    if reason == 'submitted_application_not_verified':
        return 'application_context_uncertain', 'Application context to verify', 'briefcase-business'
    evidence = understand(message)
    if evidence:
        return evidence.kind, evidence.name, evidence.icon
    headers = dict(message.list_headers or ())
    identity = headers.get('list-id') or headers.get('list-unsubscribe')
    address = parseaddr(message.sender)[1].casefold()
    # A subject template prevents grouping all mail from a mixed sender together.
    template = re.sub(r'\b\d+\b|\b[0-9a-f]{12,}\b', '#', message.subject.casefold())
    template = re.sub(r'\s+', ' ', template).strip()[:200]
    thread = headers.get('thread-id') or headers.get('in-reply-to')
    scope = identity or (address + ':' + (thread if thread and reason == 'conversation_context_required' else template))
    digest = hashlib.sha256((category + ':' + reason + ':' + scope).encode()).hexdigest()[:20]
    names = {'orders':'Order updates','bills':'Bills and receipts','university':'University mail','promotions':'Promotional messages'}
    if category=='other' and reason in ('insufficient_local_evidence','conversation_context_required'):
        return 'awaiting_semantic_analysis','Not yet understood','folder-open'
    return 'recurring_' + digest, names.get(category, 'Not yet understood'), 'megaphone' if category == 'promotions' else 'folder-open'


_cached_understand=lru_cache(maxsize=128)(_understand)


def understand(message: EmailMessage,body: str='') -> Understanding | None:
    # Classifiers and protection inspect the same immutable evidence repeatedly.
    # Changed content/headers automatically use a new key; large bodies bypass.
    return _cached_understand(message,body) if isinstance(body,str) and len(body)<=32768 else _understand(message,body)

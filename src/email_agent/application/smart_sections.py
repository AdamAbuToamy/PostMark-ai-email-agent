"""Local message groupings. These describe email evidence, not live parcel/account state."""
import re
from email.utils import parseaddr

SECTIONS = {
 'orders': [('pickup','Ready to collect'),('shipped','Shipped'),('delivered','Delivered'),('receipts','Receipts'),('refunds','Refunds / returns'),('processing','Pending / processing'),('other','Other')],
 'bills': [('due','Due soon'),('paid','Paid'),('receipts','Receipts'),('overdue','Overdue'),('other','Other')],
 'job_applications': [('applied','Applied'),('interview','Interview'),('rejected','Rejected'),('offer','Offer'),('alerts','Job alerts'),('other','Other')],
 'security': [('codes','Verification codes'),('login','Login alerts'),('password','Password changes'),('workflow','Workflow failures'),('other','Other')],
 'university': [('courses','Course updates'),('admin','Administration'),('grades','Grades'),('deadlines','Deadlines'),('other','Other')],
 'promotions': [('shopping','Shopping'),('tools','Tools'),('newsletters','Newsletters'),('travel','Travel'),('other','Other')],
 'other': [('personal','Conversations'),('development','Development'),('updates','Service updates'),('recurring','Newsletters'),('other','Other')],
 'github_ci': [('failed','Failed runs'),('successful','Successful runs'),('other','Other')],
 'github_pr': [('merged','Merged'),('review','Review requested'),('other','Other')],
}


def match(pattern, text):
    return bool(re.search(pattern, text, re.IGNORECASE))


def section_for(category: str, subject: str, sender: str = '', body: str = '') -> str:
    # Prefer the subject. A bounded cached body is only a fallback for grouping;
    # it never authorizes a category change, cleanup, or external operation.
    from email_agent.application.message_text import purpose_text
    result = _section(category, subject.casefold(), sender)
    if body:
        text=purpose_text(body)
        body_result=_section(category,text,sender)
        if category=='orders' and body_result=='pickup':return 'pickup'
        if category=='bills' and result in ('other','due') and body_result in ('receipts','paid','overdue'):return body_result
        if result=='other':result=body_result
    return result


def _section(category, text, sender):
    delayed = match(r'\b(not|failed|unable|delay(?:ed)?|expected|estimated|will be|scheduled|cancelled|canceled)\b|לא נמסר|טרם|(?:לא|אינו|אינה) (?:מוכן|מוכנה|ממתין)|لم يتم|لم يصل|غير جاهز|ليست جاهزة|\?',text)
    if category == 'orders':
        if match(r'\b(refund (?:has been |was |is )?(?:issued|processed|confirmed|approved|requested|completed)|return (?:approved|requested|label|instructions)|returned item)\b|החזר כספי.{0,30}(?:בוצע|אושר)|تم (?:استرداد|رد) المبلغ',text):return 'refunds'
        if match(r'\b(cancelled|canceled)\b|בוטל|ملغي',text):return 'other'
        if not delayed and match(r'\b(ready for (?:pickup|collection)|arrived at (?:your |the )?(?:pickup|collection) (?:point|location)|available (?:for|to) (?:pickup|collect)|collect your|pick up your)\b|ממתי[נן].{0,25}לאיסוף|מוכ[נן].{0,25}לאיסוף|הגיע.{0,25}לאיסוף|جاهز.{0,25}للاستلام',text):return 'pickup'
        if not delayed and match(r'\b(delivered|delivery confirmation)\b|נמסר|נמסרה|تم التسليم',text):return 'delivered'
        if not delayed and match(r'\b(shipped|dispatched|on (?:its|the) way|shipping confirmation|out for delivery)\b|נשלח|נשלחה|בדרך אלי|تم الشحن',text):return 'shipped'
        if match(r'\b(receipt|payment (?:confirmed|received|successful)|purchase confirmation)\b|קבלה|אישור תשלום|إيصال',text):return 'receipts'
        if delayed or match(r'\b(order (?:confirmation|confirmed|received|processing)|awaiting confirmation|processing|pending|preparing)\b|הזמנתך התקבלה|ההזמנה אושרה|قيد المعالجة|تأكيد الطلب',text):return 'processing'
    elif category == 'bills':
        if match(r'\b(overdue|past due|late payment)\b|פיגור|متأخر',text):return 'overdue'
        if match(r'\b(receipt|payment confirmation)\b|קבלה|إيصال',text):return 'receipts'
        if not delayed and match(r'\b(paid|payment (?:received|successful)|balance settled)\b|שולם|تم الدفع',text):return 'paid'
        if match(r'\b(due|pay by|upcoming payment)\b|לתשלום|מועד תשלום|مستحق',text):return 'due'
    elif category == 'job_applications':
        for key,pattern in [('alerts',r'\b(job alert|we are hiring|career opportunities)\b'),('rejected',r'\b(not moving forward|unsuccessful|not selected|rejected)\b|לצערנו|رفض'),('offer',r'\b(offer letter|employment offer|job offer)\b'),('interview',r'\b(interview|assessment invitation)\b|ראיון|مقابلة'),('applied',r'\b(application (?:received|submitted)|thank you for applying)\b')]:
            if match(pattern,text):return key
    elif category == 'security':
        for key,pattern in [('codes',r'\b(verification code|one.time|otp|confirmation code)\b|קוד אימות|رمز التحقق'),('password',r'\b(password|credential)\b|סיסמ|كلمة المرور'),('workflow',r'\b(workflow|build failed|ci failed)\b'),('login',r'\b(sign.in|login|new device|access alert)\b|התחבר|تسجيل الدخول')]:
            if match(pattern,text):return key
    elif category == 'university':
        for key,pattern in [('grades',r'\b(grades?|exam results?|transcript)\b|ציונ|תוצאות|درجات|نتائج'),('deadlines',r'\b(deadline|due date|assignment due|exam schedule)\b|מועד הגשה|תאריך אחרון|موعد نهائي'),('admin',r'\b(registration|tuition|student (?:office|services)|administration)\b|הרשמ|שכר לימוד|מזכירות|قبول|تسجيل'),('courses',r'\b(course|lecture|semester|class|exam)\b|קורס|מבחן|בחינ|הרצא|לימוד|סמסטר|دراس|محاضر|امتحان')]:
            if match(pattern,text):return key
    elif category == 'promotions':
        domain=parseaddr(sender)[1].rsplit('@',1)[-1].casefold()
        for key,pattern in [('travel',r'\b(flight|hotel|travel|holiday booking)\b'),('tools',r'\b(software|developer|api|hosting|productivity|subscription plan)\b'),('shopping',r'\b(sale|coupon|discount|shop|deals?|offer)\b|מבצע|הנחה|خصم|عروض'),('newsletters',r'\b(newsletter|digest|weekly|roundup)\b')]:
            if match(pattern,text):return key
        if domain.endswith(('aliexpress.com','amazon.com')):return 'shopping'
    elif category in ('other','inbox'):
        if match(r'^(re|fw|fwd)\s*:|^השב:',text):return 'personal'
        if match(r'\b(workflow|pull request|github|build failed|ci passed)\b',text):return 'development'
        if match(r'\b(newsletter|digest|weekly roundup)\b',text):return 'recurring'
        if match(r'\b(account|service|notification|storage|subscription)\b',text):return 'updates'
    elif category == 'github_ci':
        if match(r'\b(failed|failure|cancelled|timed out)\b',text):return 'failed'
        if match(r'\b(successful|succeeded|passed)\b',text):return 'successful'
    elif category == 'github_pr':
        if match(r'\bmerged\b',text):return 'merged'
        if match(r'\breview requested\b',text):return 'review'
    return 'other'


def attention_state(message, decision):
    """A retained uncertain transaction is not a mandatory manual decision."""
    if decision.get('source') == 'manual':return 'protected'
    if decision.get('status') == 'classified':return 'organized'
    if decision.get('reason') == 'submitted_application_not_verified':return 'review'
    if decision.get('category_id') in {'orders','bills','security','university'}:return 'protected'
    if set(message.label_ids or ()) & {'STARRED','IMPORTANT','SENT','DRAFT'}:return 'protected'
    if decision.get('reason') in {'insufficient_local_evidence','conversation_context_required'}:return 'untouched'
    return 'review'

"""Local purpose evidence from bounded, already-decoded message text.

This is purpose classification, not sender authentication or permission to act.
Quoted correspondence and footer instructions cannot authorize anything.
"""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ContentEvidence:
    kind: str
    name: str
    category: str
    reason: str
    protected: bool = True
    icon: str = 'folder-open'


def primary_text(body: str) -> str:
    from email_agent.application.message_text import purpose_text
    return purpose_text(body)


def content_purpose(subject: str, body: str = '') -> ContentEvidence | None:
    subject = subject.casefold()
    text = primary_text(body)
    security = r'\b(?:your password.{0,35}(?:changed|reset|updated)|password (?:change|reset) (?:confirmation|successful)|(?:changed|reset|updated) your password|verification code|one.time (?:code|password)|new (?:sign.in|login)|(?:unusual|suspicious) (?:sign.in|login|activity)|security (?:alert|warning)|account recovery|vulnerability|dependabot)\b|(?:סיסמ[התך].{0,35}(?:שונ|שונה|הוחלפ|עודכנ)|(?:שינוי|איפוס|החלפת|עדכון) (?:ה)?סיסמ|קוד (?:אימות|חד פעמי)|התראת אבטחה)|(?:تم (?:تغيير|إعادة تعيين|تحديث) كلمة المرور|رمز (?:التحقق|التأكيد)|تسجيل دخول (?:جديد|مريب)|تنبيه أمني)'
    security+=r'|\b(?:password change detected|(?:change|reset|update) your (?:[a-z]+ )?password|your (?:organizational |account )?password.{0,100}(?:was |has been )changed|password (?:expires|expiry|expiration))\b|(?:הוחלפ[הו]?|עודכנ[הו]?|שונתה) (?:ה)?סיסמ'
    if re.search(security,subject):
        # Advice/advertisements mentioning security are protected but do not
        # establish an actual password-change or login event.
        return ContentEvidence('security','Security / authentication','security','content_security_purpose',icon='shield')
    receipt_subject = bool(re.search(r'\b(?:receipt|invoice|tax invoice|payment (?:receipt|confirmation|received|successful)|proof of payment)\b|חשבונית|קבלה (?:מס|על|עבור|מספר)|^קבלה$|אישור תשלום|فاتورة|إيصال|تأكيد الدفع',subject))
    authentication_passcode = bool(re.search(r'\bpasscode\b',text) and re.search(r'\b(?:verify your identity|account (?:creation|registration)|sign.in|log.?in)\b',text))
    if not receipt_subject and (re.search(security,text) or authentication_passcode):
        return ContentEvidence('security','Security / authentication','security','content_security_purpose',icon='shield')
    product_ad=bool(re.search(r'\b(?:receipt|invoice|billing) (?:software|templates?|management|automation|solutions?)\b|תוכנה להפקת חשבוניות|תבניות חשבונית|برنامج الفواتير|قوالب الفواتير',subject))
    promotion=bool(re.search(r'\b(?:save|discount|sale|free trial|special offer)\b|\bsave\s*\d+\s*%|הנחה|מבצע|خصم|عرض خاص',subject))
    actual_document=bool(re.match(r'(?:your (?:invoice|receipt)|receipt for your|payment (?:receipt|confirmation))\b',subject))
    if product_ad and promotion and not actual_document:
        return ContentEvidence('promotion','Promotional offers','promotions','content_financial_product_advertisement',icon='megaphone')
    money = bool(re.search(r'(?:[$€£₪]\s*\d|\d[\d,.]*\s*(?:usd|eur|ils|nis|gbp|ש["״]?ח|دولار))',text))
    financial_document = bool(re.search(r'\b(?:receipt|invoice|amount paid|total paid|payment (?:received|confirmed|successful)|transaction (?:id|reference))\b|חשבונית|סכום (?:ששולם|לתשלום)|אישור תשלום|رقم الفاتورة|المبلغ المدفوع',text))
    document_structure = bool(re.search(r'\b(total|amount|paid|number|invoice|reference)\b|סכום|שולם|מספר|المبلغ|مدفوع',text))
    if receipt_subject or (money and financial_document and document_structure):
        return ContentEvidence('billing','Bills / receipts','bills','content_receipt_or_payment',icon='receipt')
    refund = r'\brefund (?:has been |was )?(?:issued|processed|confirmed|completed)\b|החזר כספי.{0,30}(?:בוצע|אושר)|تم (?:استرداد|رد) المبلغ'
    if re.search(refund,subject) or re.search(refund,text):
        return ContentEvidence('refund','Refunds / returns','orders','content_refund_confirmation',icon='package')
    # A substantial cached body can establish delivery even with a generic
    # subject; marketing discounts alone cannot establish a transaction.
    transaction = r'\b(?:your (?:order|parcel|package|shipment).{0,65}(?:shipped|delivered|ready for (?:pickup|collection)|confirmed)|order (?:number|confirmation)|tracking (?:number|id))\b|החבילה שלך.{0,35}(?:נמסרה|נשלחה|לאיסוף)|מספר (?:הזמנה|מעקב)|تم (?:شحن|تسليم) طلبك'
    # A pickup state needs parcel/order context; collecting an identity card
    # or an official document is not an order merely because it is ready.
    from email_agent.application.smart_sections import section_for
    parcel_context=bool(re.search(r'\b(?:parcel|package|shipment|order)\b|משלוח|חבילה|طرد|شحنة',text))
    pickup=parcel_context and section_for('orders','', '',text)=='pickup'
    if re.search(transaction,text) or pickup:
        return ContentEvidence('transaction_updates','Order updates','orders','content_transaction_context',icon='package')
    academic = r'\b(?:your (?:lecture|course|exam)|lecture (?:schedule|cancelled|canceled|materials)|course (?:materials|timetable|registration)|assignment (?:due|deadline)|exam (?:schedule|results))\b|עדכון (?:הרצאה|בקורס)|חומרי (?:הקורס|הרצאה)|מערכת שעות|موعد (?:المحاضرة|الامتحان)'
    if re.search(academic,subject) or re.search(academic,text):
        return ContentEvidence('academic','University and courses','university','content_academic_purpose',icon='graduation-cap')
    if re.search(r'\b(?:out of credits|quota (?:exhausted|exceeded)|usage limit reached|storage full)\b',subject) and re.search(r'\b(?:credits|quota|limit|storage)\b',text):
        return ContentEvidence('usage_limit','Service usage alerts','other','content_service_usage_limit',icon='bell')
    return None

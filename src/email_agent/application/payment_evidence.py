"""Conservative current-message payment facts, independent of sender identity."""
import re
from email_agent.application.message_text import purpose_text

NO_DUE = (
    r'\b(?:no|not any)\s+(?:(?:further|additional|outstanding)\s+)?(?:payment|balance|amount|money)(?:\s+is)?\s+(?:due|required|outstanding)\b'
    r'|\bnothing\s+(?:is\s+)?(?:due|owed|outstanding)\b|\bnot a payment request\b'
    r'|אין\s+(?:יתרה|צורך)[^.\n]{0,35}(?:לתשלום|לשלם)|לא\s+נדרש\s+תשלום'
    r'|لا\s+(?:توجد|يوجد|تتبقى)\s+(?:أي\s+)?(?:دفعة|مبالغ|مبلغ|رصيد)[^.\n]{0,35}مستحق\w*'
)
UNPAID = (
    r'\b(?:not\s+(?:yet\s+)?(?:been\s+)?paid|has not been paid|have not been paid|unpaid)\b'
    r'|(?:לא|טרם)\s+(?:שולם|שולמה|שילמת|התקבל\s+התשלום)'
    r'|لم\s+(?:يتم\s+)?(?:دفع|الدفع|سداد|السداد|تسديد)|غير\s+(?:مدفوع\w*|مسدد\w*)'
)
PAID = (
    r'\b(?:amount paid|total paid|paid in full|payment\s+(?:(?:has been|was|is)\s+)?(?:received|confirmed|successful|completed)|received\s+(?:your|the)\s+payment)\b'
    r'|\b(?:you (?:have )?paid|we (?:have )?received your payment)\b'
    r'|סכום\s+(?:ששולם|ששילמת)|(?<!\w)(?:שולם|שולמה|שילמת)(?!\w)|התשלום\s+(?:התקבל|בוצע|אושר)|קיבלנו\s+את\s+התשלום'
    r'|(?<!\w)تم(?:ت)?\s+(?:دفع|الدفع|سداد|السداد|تسديد)|(?:استلمنا|تلقينا)\s+(?:دفعتك|الدفع|مدفوعاتك)'
)
DUE = (
    r'\b(?:(?:payment|amount|balance)\s+(?:of\s+[^.\n]{1,35}\s+)?(?:is\s+)?due|payment required|please pay|must pay)\b'
    r'|(?<!\w)לתשלום(?!\w)|יש\s+לשלם'
    r'|مستحق\w*|يجب\s+(?:دفع|الدفع|سداد|السداد)'
)


def _facts(text):
    # Conditions and quoted history cannot establish a completed transaction.
    sentences=re.split(r'[.!?;؛\n]+',text.casefold())
    paid=due=negative_due=negative_paid=False
    for sentence in sentences:
        if re.search(r'\b(?:if|whether|unless)\b|(?:^|\s)(?:אם|إذا|في حال)(?:\s|$)',sentence):continue
        negative_due=negative_due or bool(re.search(NO_DUE,sentence))
        current=re.sub(NO_DUE,' ',sentence)
        negated=re.search(r'\b(?:no payment (?:has been |was |is )?received|payment (?:has |was |is )?not (?:been )?(?:received|confirmed|successful|completed)|did not receive (?:your |the )?payment)\b',current)
        negative_paid=negative_paid or bool(negated)
        if negated:current=current[:negated.start()]+' '+current[negated.end():]
        unpaid=bool(re.search(UNPAID,current))
        due=due or unpaid or bool(re.search(DUE,current))
        # Remove explicit negative payment clauses before looking for payment.
        current=re.sub(UNPAID,' ',current)
        paid=paid or bool(re.search(PAID,current))
    return ('Paid' if paid and not due and not negative_paid else 'Payment due' if due and not paid else None),paid or due or negative_due or negative_paid


def payment_state(subject,body):
    current=purpose_text(body)
    state,has_facts=_facts(current)
    if has_facts:return state
    return _facts(subject)[0]

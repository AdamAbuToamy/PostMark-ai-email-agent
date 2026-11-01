"""Orthogonal semantic facets; normalization is not sender-based categorization."""
from dataclasses import asdict,dataclass
import re

ALIASES={
 'receipt':{'receipt','receipts','payment_receipt','purchase_receipt','קבלה','إيصال','إيصال_دفع'},
 'invoice':{'invoice','bill','unpaid_invoice','חשבונית','فاتورة'},
 'payment_notice':{'payment_notice','payment_confirmation','payment','אישור_תשלום','تأكيد_دفع'},
 'refund':{'refund','refund_notice','refund_confirmation','החזר','استرداد'},
 'order_confirmation':{'order_confirmation','purchase_confirmation','הזמנה','تأكيد_طلب'},
 'pickup_notice':{'pickup_notice','pickup_notification','pickup','ready_for_pickup','awaiting_pickup','הודעת_איסוף','إشعار_استلام'},
 'application_acknowledgment':{'application_acknowledgment','application_acknowledgement','application_confirmation','application_received','acknowledgment'},
 'interview_invitation':{'interview_invitation','interview','job_interview_invitation'},
 'rejection':{'rejection','application_rejection','job_rejection'},
 'job_offer':{'job_offer','offer','employment_offer','offer_letter','employment_offer_letter'},
 'security_notice':{'security_notice','security_alert','password_reset','password_change','password_change_confirmation','verification_code','authentication','security_notification','security','authentication_alert'},
 'academic_notice':{'academic_notice','course_update','lecture_notice','lecture_update','course_notice','course_notification'},
 'shipment_notice':{'shipment_notice','shipping_notice','shipment_notification','shipping_notification','shipping_update','shipment_update'},
 'delivery_notice':{'delivery_notice','delivery_confirmation','delivery_notification','delivered_notification'},
 'promotion':{'promotion','advertisement','marketing'},
 'newsletter':{'newsletter'},'correspondence':{'correspondence','conversation','personal_correspondence','personal_message'},
 'field_report':{'field_report','research_report','research_note','field_research_report'},
}


def canonical(value):
    return re.sub(r'[^\w]+','_',str(value or '').casefold(),flags=re.UNICODE).strip('_')


def document_type(value):
    key=canonical(value)
    return next((label for label,terms in ALIASES.items() if key in terms),key or None)


@dataclass(frozen=True)
class SemanticDimensions:
    primary_category:str|None
    document_type:str|None
    entity:str|None
    workflow_stage:str|None
    payment_status:str|None


def dimensions(frame):
    from email_agent.application.semantic_mail import status_key
    attrs=frame.get('attributes') or {}
    if isinstance(attrs,list):attrs={a['key']:a['value'] for a in attrs}
    def normalized(value):
        known={'שולם':'paid','تم_الدفع':'paid','مدفوع':'paid','مدفوعة':'paid','לא_שולם':'payment_due','غير_مدفوع':'payment_due','مستحق':'payment_due','הוחזר':'refunded','مسترد':'refunded','تم_الاسترداد':'refunded','נדחה':'rejected','נדחתה':'rejected','مرفوض':'rejected','تم_الرفض':'rejected','מוכן_לאיסוף':'pickup','جاهز_للاستلام':'pickup'}
        return known.get(canonical(value)) or status_key(value)
    status=normalized(frame.get('status'))
    payment=normalized(attrs.get('payment_status')) or (status if status in ('paid','payment_due','refunded','unpaid') else None)
    workflow=normalized(attrs.get('workflow_stage')) or (status if status and status not in ('paid','payment_due','refunded','unpaid') else None)
    return SemanticDimensions(frame.get('category_id',frame.get('suggested_category_id')),document_type(attrs.get('canonical_message_type') or frame.get('message_type')),frame.get('entity'),workflow,payment)


def dimension_payload(frame):return asdict(dimensions(frame))

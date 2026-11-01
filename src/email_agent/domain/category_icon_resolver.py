"""Semantic icon selection for local email categories."""

from email_agent.domain.category_icons import (
    DEFAULT_CATEGORY_ICON,
    validate_category_icon,
)


ICON_RULES = {
    "message-circle": ("conversation", "correspondence", "discussion", "محادث", "مراسلات", "שיחות", "התכתבות"),
    "shield": (
        "security", "account protection", "password",
        "authentication", "privacy", "أمان", "حماية",
        "كلمة مرور", "אבטחה", "סיסמה",
    ),
    "plane": (
        "travel", "flight", "airline", "trip",
        "سفر", "رحلات", "طيران", "טיסה", "נסיעות",
    ),
    "receipt": (
        "bill", "invoice", "receipt", "payment",
        "فاتورة", "فواتير", "חשבונית", "תשלום",
    ),
    "briefcase": (
        "job application", "recruitment", "interview",
        "طلب توظيف", "مقابلة عمل", "מועמדות", "ראיון עבודה",
    ),
    "graduation-cap": (
        "university", "scholarship", "education",
        "جامعة", "منحة دراسية", "אוניברסיטה", "מלגה",
    ),
    "megaphone": (
        "promotion", "advertisement", "marketing",
        "إعلان", "ترويج", "פרסומת",
    ),
    "calendar-days": (
        "schedule", "appointment", "calendar",
        "موعد", "مواعيد", "פגישה", "לוח שנה",
    ),
    "wallet": (
        "finance", "banking", "bank account",
        "بنك", "مالية", "בנק", "כספים",
    ),
    "package": (
        "delivery", "shipping", "order tracking",
        "شحنة", "توصيل", "משלוח",
    ),
    "heart-pulse": (
        "health", "medical", "hospital",
        "صحة", "طبي", "בריאות", "רפואי",
    ),
    "file-text": (
        "document", "contract", "certificate",
        "مستند", "عقد", "מסמך", "חוזה",
    ),
    "bell": (
        "notification", "alert", "reminder",
        "تنبيه", "تذكير", "התראה", "תזכורת",
    ),
    "book-open": (
        "course", "learning", "lecture",
        "دورة", "محاضرة", "קורס", "הרצאה",
    ),
    "building-2": (
        "company", "organization", "office",
        "شركة", "مؤسسة", "חברה", "משרד",
    ),
}



def resolve_category_icon(
    category_name: str,
    description: str = "",
) -> str:
    """Resolve a category icon with name-first semantic scoring."""
    import re

    name = category_name.casefold().replace("_", " ").replace("-", " ")
    description = description.casefold()

    scores: dict[str, int] = {}

    for icon_key, keywords in ICON_RULES.items():
        score = 0

        for keyword in keywords:
            token = keyword.casefold()

            # Match complete words or phrases, not arbitrary substrings.
            pattern = r"(?<!\\w)" + re.escape(token) + r"(?!\\w)"

            name_match = re.search(pattern, name)

            if name_match:
                # Earlier name terms generally carry more category intent.
                word_position = len(name[:name_match.start()].split())
                score += max(10 - 2 * word_position, 4)

            elif re.search(pattern, description):
                score += 1

        if score:
            scores[icon_key] = score

    if not scores:
        return DEFAULT_CATEGORY_ICON

    highest = max(scores.values())
    winners = [
        key for key, score in scores.items()
        if score == highest
    ]

    if len(winners) != 1:
        return DEFAULT_CATEGORY_ICON

    return validate_category_icon(winners[0])

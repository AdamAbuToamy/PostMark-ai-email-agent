"""Supported local category icon identifiers."""

ALLOWED_CATEGORY_ICONS = frozenset({
    "message-circle",
    "shield",
    "plane",
    "receipt",
    "briefcase",
    "graduation-cap",
    "megaphone",
    "folder-open",
    "bell",
    "heart-pulse",
    "file-text",
    "calendar-days",
    "wallet",
    "package",
    "book-open",
    "building-2",
})

DEFAULT_CATEGORY_ICON = "folder-open"

BUILTIN_CATEGORY_ICONS = {
    "bills": "receipt",
    "orders": "package",
    "job_applications": "briefcase",
    "university": "graduation-cap",
    "promotions": "megaphone",
    "other": "folder-open",
    "security": "shield",
    "travel": "plane",
    "scholarships": "graduation-cap",
}


def validate_category_icon(icon_key: str) -> str:
    if icon_key not in ALLOWED_CATEGORY_ICONS:
        raise ValueError("Unsupported category icon")
    return icon_key


def default_category_icon(category_id: str) -> str:
    return BUILTIN_CATEGORY_ICONS.get(
        category_id, DEFAULT_CATEGORY_ICON
    )

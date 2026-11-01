
from email_agent.application.category_registry import CategoryRegistry
from email_agent.application.category_ports import AnalysisReader


# Initial conservative discovery rules.
# These rules are not a substitute for semantic AI analysis.
DISCOVERY_RULES = {
    "security": {
        "name": "Security",
        "keywords": (
            "security alert",
            "security notification",
            "security warning",
            "تنبيه أمني",
            "إشعار أمني",
            "تحذير أمني",
            "התראת אבטחה",
        ),
        "reason": "Messages concerning account security.",
    },
    "travel": {
        "name": "Travel",
        "keywords": (
            "flight booking",
            "flight reservation",
            "travel itinerary",
            "حجز الرحلات",
            "حجز طيران",
            "تذاكر الطيران",
            "הזמנת טיסה",
        ),
        "reason": "Messages concerning travel and reservations.",
    },
    "scholarships": {
        "name": "Scholarships",
        "keywords": (
            "scholarship application",
            "scholarship award",
            "طلب منحة",
            "منحة دراسية",
            "מלגה",
        ),
        "reason": "Messages concerning scholarships.",
    },
}


class CategoryDiscovery:
    def __init__(
        self,
        registry: CategoryRegistry,
        analysis_reader: AnalysisReader,
    ):
        self.registry = registry
        self.analysis_reader = analysis_reader

    def discover(self) -> dict:
        rows = self.analysis_reader.read_results()

        known = {
            item["id"]
            for item in self.registry.categories()
        }

        # Avoid re-proposing previously rejected or approved topics.
        previously_proposed = {
            item["category_id"]
            for item in self.registry.proposals()
        }

        created = 0
        scanned = 0

        for result in rows:
            if not isinstance(result, dict):
                continue

            scanned += 1

            # Only propose for messages currently classified as
            # 'other'. Do not reinterpret established categories.
            if result.get("category") != "other":
                continue

            text = " ".join(
                str(result.get(field) or "")
                for field in ("summary", "action")
            ).casefold()

            for category_id, rule in DISCOVERY_RULES.items():
                if category_id in known:
                    continue

                if category_id in previously_proposed:
                    continue

                if not any(
                    keyword.casefold() in text
                    for keyword in rule["keywords"]
                ):
                    continue

                if self.registry.propose(
                    category_id,
                    rule["name"],
                    rule["reason"],
                ):
                    created += 1

                previously_proposed.add(category_id)

        return {
            "scanned": scanned,
            "created_proposals": created,
        }

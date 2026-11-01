"""Trusted local planning policy, separate from untrusted email fields."""
ORGANIZATION_PLANNING = (
    'Observe purpose and compare existing catalog meanings before assigning. Reuse a category only when its purpose fits. '
    'For a coherent multi-message specialized purpose without a suitable existing category, propose a concise create action '
    'covering the supported aliases. Do not bury specialized document collections in generic personal correspondence or Other. '
    'Use refine for useful document subdivisions of a fitting category. Never create one category per sender. '
    'Keep company, document type, workflow and payment state independent. Return typed changes; only the backend can execute, '
    'authorize and verify them. For create actions choose icon_key semantically from the schema enum according to the category purpose; use null only when no supported icon fits. Never derive an icon from the sender alone. Never claim a tool ran. Preserve manual choices and uncertainty when evidence conflicts.'
)




class PublicationBusy(ValueError):
    """Transient local contention; retain the plan and retry without spent attempts."""

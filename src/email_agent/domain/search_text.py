"""Shared literal-search normalization; no provider or UI dependencies."""
import re
import unicodedata


def normalize_search_text(text: str) -> str:
    value=unicodedata.normalize("NFKC",text).casefold()
    value="".join(c for c in value if unicodedata.category(c)!="Cf")
    value=re.sub(r"[\u064b-\u065f\u0670ـ]","",value)
    value=value.translate(str.maketrans("أإآٱى","ااااي"))
    value=re.sub(r"(?<!\w)ali[\s_-]*express(?!\w)","aliexpress",value)
    value=re.sub(r"(?<!\w)(?:الي|علي)\s*اكس(?:برس|بريس)(?!\w)","aliexpress",value)
    return re.sub(r"\s+"," ",value).strip()

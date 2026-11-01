"""Deterministic bilingual intent parsing; no email text is treated as permission."""
import hashlib
import re
from typing import Protocol
from email_agent.domain.search_text import normalize_search_text


class IntentSearch(Protocol):
    def __call__(self, term: str, limit: int, category: str | None = None) -> list[dict]: ...


def normalize(text: str) -> str:
    value=normalize_search_text(text).rstrip(".!؟?")
    value=re.sub(r"^(?:اعطني|اعطيني|هات(?:لي)?|ورجيني|وريني|فرجيني|جيبلي)\s+","اعرض ",value)
    value=re.sub(r"^(?:حط|حطلي|ضع)\s+","انقل ",value)
    value=re.sub(r"^((?:اعرض|ابحث عن|انقل)\s+)(?:ميلات|ايميلات|اميلات|ايميلاتي|ميلاتي)\s+",r"\1رسائل ",value)
    return value


def plan_intent(text: str, context: list[str], categories: list[dict], search: IntentSearch) -> list[dict]:
    value = normalize(text)
    if re.search(r"^(?:send\b|reply\b|forward\b|ارسل\b|ارسال\b|رد علي\b)|permanently|نهائيا", value):
        raise ValueError("الإرسال والحذف النهائي غير مدعومين. Sending mail and permanent deletion are unavailable. Select a message to preview reversible Gmail Trash instead.")
    aliases = {normalize(c["name"]): c["id"] for c in categories}
    aliases.update({c["id"]: c["id"] for c in categories})
    aliases.update({"الطلبات": "orders", "الامان": "security", "اعلانات": "promotions", "الاعلانات": "promotions", "university": "university"})
    def call(tool, **args):
        return [{"tool": tool, "args": args}]
    if any(word in value for word in ("subscriptions", "unsubscribe", "اشتراك", "الاشتراكات")):
        return call("list_subscriptions")
    if "غير المصنف" in value or "unclassified" in value:
        return call("search_messages", classification="unclassified")
    if "spam" in value or "trash review" in value:
        return call("review_spam")
    create = re.search(r"(?:انشئ|انشي|انشاء)\s+(?:قسما?|فئة)\s+(?:اسمه|باسم)\s+(.+)$", value)
    create = create or re.search(r"create (?:an? )?(.+?) category$", value)
    create = create or re.search(r"create (?:an? )?category (?:called|named) (.+)$", value)
    if create:
        name = text[create.start(1):create.end(1)].strip() if len(text) == len(value) else create[1].strip()
        slug = re.sub(r"[^a-z0-9]+", "_", normalize(name)).strip("_")
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,49}", slug):
            slug = "custom_" + hashlib.sha256(name.encode()).hexdigest()[:12]
        return call("create_category", category_id=slug, name=name.title() if name.isascii() else name, reason="Category explicitly requested in Chat")
    mutation = bool(re.match(r"^(?:move\b|انقل\b|نقل\b|احذف\b|trash\b|delete\b|restore\b|استعد\b|mark\b|return to new\b)", value))
    if mutation:
        if any(word in value for word in ("trash", "delete", "احذف", "المهملات")):
            promotional = re.search(r"^(?:احذف|trash|delete)\s+(?:اعلانات|promotions(?: from)?)\s+(.+?)(?:\s+الي المهملات|\s+to trash)?$", value)
            if promotional:
                term=re.sub(r"\s+الي المهملات$|\s+to trash$", "", promotional[1]).strip()
                rows=search(term,21)
                if len(rows)>20:
                    raise ValueError("More than 20 messages match; narrow the sender search before previewing Trash.")
                selected=[row["message_id"] for row in rows if row.get("category_id")=="promotions" and row.get("classification_status")=="classified"]
                if not selected:
                    raise ValueError("لا توجد إعلانات مصنفة مطابقة في هذا الحساب. No classified promotions match this sender.")
                return call("trash_messages",message_ids=selected,category_id="promotions")
            if not context:
                raise ValueError("حدد الرسالة أولًا من نتائج البحث، ثم اطلب نقلها إلى المهملات. Select a message first.")
            return call("trash_messages", message_ids=context)
        if "restore" in value or "استعد" in value:
            if not context:
                raise ValueError("Select the exact message to restore first.")
            return call("restore_message", message_ids=context)
        if "done" in value or "return to new" in value:
            if not context:
                raise ValueError("Select messages before changing their local status.")
            return call("set_status", message_ids=context, state="new" if "return" in value else "done")
        match = re.search(r"(?:\bto\s+|الي\s+|في\s+)(.+)$", value)
        if not match or match[1].strip() not in aliases:
            raise ValueError("حدد القسم المقصود باسمه المعتمد. Choose an active category by name.")
        target = aliases[match[1].strip()]
        if context:
            return call("assign_messages", message_ids=context, category_id=target)
        source = re.sub(r"^(?:انقل|نقل|move)\s+(?:(?:رسائل|messages|emails)\s+(?:from\s+)?)?", "", value[:match.start()].strip())
        source = re.sub(r"\s+(?:emails|messages)$", "", source)
        if not source or source in ("هذه الرسالة", "this message"):
            raise ValueError("حدد الرسالة أو اسم المرسل أولًا. Select a message or specify a sender.")
        source_category=aliases.get(source)
        rows = search("",21,source_category) if source_category else search(source,21)
        if not rows:
            raise ValueError("لم أجد رسائل مطابقة في الحساب المحدد. No matching messages in this account.")
        if len(rows) > 20:
            raise ValueError("More than 20 messages match. Narrow your search before previewing a change.")
        return call("assign_messages", message_ids=[r["message_id"] for r in rows], category_id=target)
    if "aliexpress" in value and ("classify" in value or "orders category" in value):
        return call("classify_orders", sender="AliExpress")
    if ("categor" in value and ("list" in value or "show" in value)) or "الاقسام" in value:
        return call("list_categories")
    args = {"state": "new" if any(w in value for w in ("لم انته", "not done", "haven't", "haven’t", "new emails", "الجديدة", "الجديد")) else "all"}
    if "اعلانات" in value or "promotions" in value:
        args["category"] = "promotions"
    elif "university" in value or "الجامعة" in value:
        args["category"] = "university"
    elif "application" in value:
        args["category"] = "job_applications"
    elif "orders" in value:
        args["category"] = "orders"
    else:
        args["search"] = re.sub(r"\s+(?:emails|messages)$", "", re.sub(r"^(?:اعرض|ابحث عن|find|search|show)\s+(?:رسائل\s+|messages\s+|emails from\s+)?", "", value))[:200]
    if "important" in value:
        args["label"] = "IMPORTANT"
    return call("search_messages", **args)


def normalize_call(call):
    """Accept documented provider envelopes without relaxing argument validation."""
    if not isinstance(call, dict):
        raise ValueError("A tool call needs a tool name and an arguments object")
    if set(call) == {"tool", "args"}:
        return call
    if set(call) == {"name", "arguments"} and isinstance(call["arguments"], dict):
        return {"tool": call["name"], "args": call["arguments"]}
    raise ValueError("Use {tool: supported_name, args: {...}}. No actions have been executed.")


def deterministic_intent(text: str) -> bool:
    """Only explicit supported command shapes bypass the model, never arbitrary prose."""
    value=normalize(text)
    if re.search(r"\b(?:then|and|why|compare|summarize|analyse|analyze|which|ثم|بعدها|وبعدين|قارن|لخص|حلل|لماذا|كيف)\b",value):
        return False
    return bool(re.match(r"^(?:اعرض|ابحث عن|انقل|نقل|احذف|انشئ|انشي|انشاء|ارسل|ارسال|رد علي|find\b|search\b|show\b|list\b|move\b|mark\b|return to new\b|restore\b|trash\b|delete\b|create\b|classify\b|send\b|reply\b|forward\b)",value))

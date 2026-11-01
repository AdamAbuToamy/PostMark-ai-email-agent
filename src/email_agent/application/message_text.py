"""Bounded current-message text and evidence windows; no provider effects."""
import re
from functools import lru_cache
import unicodedata

# Quotation/signature boundaries are message formats, not sender policies.
BOUNDARY = re.compile(r'^(?:on .{0,180}wrote:|בתאריך .{0,180}(?:כתב|כתבה)|في .{0,180}كتب|(?:from|מאת|من):|[-_]{2,}\s*(?:original message|forwarded message|הודעה מקורית|رسالة أصلية)|begin forwarded message:|sent from my |נשלח מ|تم الإرسال من|unsubscribe\b|manage (?:your )?preferences\b)', re.I)
FOCUS = re.compile(r'application|applying|position|employment|interview|rejected|not moving|offer|accepted|received|receipt|invoice|payment|paid|refund|password|verification|sign.in|shipped|delivered|pickup|tracking|מועמד|משרה|ראיון|לצער|הצעת עבודה|חשבונית|קבלה|תשלום|סיסמ|אימות|לאיסוף|طلب|توظيف|وظيف|مقابل|نأسف|رفض|قبول|فاتور|إيصال|الدفع|المرور|التحقق', re.I)

OUTCOME=re.compile(r'not moving forward|not (?:be )?proceeding|not selected|offer letter|pleased to offer|application.{0,25}(?:received|rejected)|total paid|payment (?:received|confirmed|due)|password.{0,30}changed|לא נוכל להתקדם|לא התקבלת|הצעת עבודה|קיבלנו את מועמדותך|סכום ששולם|נאסר|נשלחה|נמסרה|نأسف|تم قبولك|تم رفض|استلام طلبك|تم (?:الدفع|تغيير كلمة المرور)',re.I)


def current_text(body: str) -> str:
    text=unicodedata.normalize('NFKC',str(body or '')).replace('\r\n','\n').replace('\r','\n')
    text=''.join(c for c in text if unicodedata.category(c)!='Cf')
    lines=[]
    for line in text.splitlines():
        stripped=line.strip()
        if stripped=='--' or BOUNDARY.match(stripped):break
        if stripped.startswith('>'):continue
        lines.append(re.sub(r'[\t \u00a0]+',' ',line).strip())
    return '\n'.join(lines).strip()


def full_sections(body: str) -> dict:
    """Complete text, with historical context separated from current evidence."""
    text=unicodedata.normalize('NFKC',str(body or '')).replace('\r\n','\n').replace('\r','\n')
    if len(text)>2_000_000:raise ValueError('Full body exceeds local safety bound; not clipped')
    text=''.join(c for c in text if unicodedata.category(c)!='Cf')
    current=[];context=[];past=False
    for line in text.splitlines():
        if line.strip()=='--' or BOUNDARY.match(line.strip()):past=True
        if past or line.lstrip().startswith('>'):context.append(line)
        else:current.append(line)
    return dict(current='\n'.join(current).strip(),history='\n'.join(context).strip())


def evidence_excerpt(body: str, limit: int = 600) -> str:
    """Keep context around decisive passages beyond long preheaders/legal text.

    Keywords only select windows; classification still requires contextual
    evidence. Adjacent words/negation remain; omitted regions are marked.
    """
    text=current_text(body)
    if len(text)<=limit:return text
    found=list(FOCUS.finditer(text));matches=found[:200]+found[-200:]
    outcomes=list(OUTCOME.finditer(text));matches+=outcomes[:100]+outcomes[-100:]
    if not matches:return text[:limit]
    width=max(80,limit-min(120,limit//5)-12)
    candidates=[]
    for match in matches:
        start=max(0,match.start()-width//3);end=min(len(text),start+width)
        score=sum(start<=other.start()<end for other in matches)+100*sum(start<=other.start()<end for other in outcomes)
        candidates.append((-score,start,end))
    _,start,end=min(candidates)
    if start<=min(120,limit//5):return text[:limit]
    prefix=text[:min(120,limit//5)].rstrip()
    return (prefix+'\n[…]\n'+text[start:end])[:limit]


def _purpose_text(body: str) -> str:
    return evidence_excerpt(body,8000).casefold()


_cached_purpose_text=lru_cache(maxsize=128)(_purpose_text)


def purpose_text(body: str) -> str:
    # Exact immutable input, bounded in-memory reuse; no disk persistence.
    return _cached_purpose_text(body) if isinstance(body,str) and len(body)<=32768 else _purpose_text(body)


def content_template(body: str) -> str | None:
    """Exact current-text pattern; preserve words, order, negation and punctuation."""
    import hashlib
    text=current_text(body).casefold()
    if len(text)<64 or len(re.findall(r'[^\W\d_]+',text))<10:return None
    text=re.sub(r'https?://[^\s<>]+','[url]',text)
    text=re.sub(r'[\w.+-]+@[\w.-]+','[email]',text)
    text=re.sub(r'\d+','[number]',text)
    text=' '.join(text.split())
    return hashlib.sha256(text.encode()).hexdigest()

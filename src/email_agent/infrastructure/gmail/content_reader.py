"""Bounded Gmail MIME parsing and allowlist-only email HTML."""
import base64
import binascii
import html
import re
import unicodedata
import ipaddress
from html.parser import HTMLParser
from urllib.parse import urlsplit

MAX_ATTACHMENT = 10 * 1024 * 1024
MAX_TEXT = 2 * 1024 * 1024
SAFE_IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

class MIMECharsetError(ValueError):pass
class MIMETextTooLarge(ValueError):pass


def decode_data(data, limit=MAX_ATTACHMENT):
    if not isinstance(data, str) or len(data) > (limit * 4 // 3 + 8):
        raise ValueError("Attachment exceeds the size limit")
    try:
        raw = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Malformed attachment encoding") from exc
    if len(raw) > limit:
        raise ValueError("Attachment exceeds the size limit")
    return raw


def safe_filename(value):
    value = "".join("_" if unicodedata.category(c) in {"Cc", "Cf", "Cs"} else c for c in str(value))
    value = re.sub(r'[\x00-\x1f\x7f/\\:]', '_', str(value))
    return value.strip(' .')[:150] or "attachment"


def walk_parts(payload):
    result = []
    def walk(part, key, depth):
        if depth > 20 or len(result) >= 200 or not isinstance(part, dict):
            raise ValueError("Invalid or excessive MIME nesting")
        if not isinstance(part.get("body", {}), dict) or not isinstance(part.get("parts", []), list):
            raise ValueError("Malformed MIME part")
        result.append((key, part))
        for index, child in enumerate(part.get("parts", [])):
            walk(child, f"{key}.{index}", depth + 1)
    walk(payload, "0", 0)
    return result


def headers_of(part):
    values = part.get("headers", [])
    if not isinstance(values, list) or any(not isinstance(h, dict) for h in values):
        raise ValueError("Malformed MIME headers")
    return {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in values}


class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.ignored_depth = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "svg", "iframe", "form", "object"):
            self.ignored_depth += 1
        elif not self.ignored_depth and tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "svg", "iframe", "form", "object"):
            self.ignored_depth = max(0, self.ignored_depth - 1)

    def handle_data(self, data):
        if not self.ignored_depth:
            self.parts.append(data)

    def get_text(self):
        return "\n".join(line.strip() for line in "".join(self.parts).splitlines() if line.strip())


class SafeEmailHTML(HTMLTextExtractor):
    ALLOWED = set("p div br span b strong i em u table tbody thead tr td th ul ol li blockquote h1 h2 h3 h4 h5 h6 pre code hr a img".split())
    def __init__(self, cids):
        super().__init__()
        self.output, self.cids, self.external_images = [], cids, 0
        self.external_hosts = set()

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if self.ignored_depth or tag not in self.ALLOWED:
            return
        attributes = ""
        values = dict(attrs)
        if tag == "img":
            src = (values.get("src") or "").strip()
            alt = html.escape((values.get("alt") or "Email image")[:200], quote=True)
            if src.lower().startswith("cid:") and src[4:].strip("<>") in self.cids:
                attributes = f' data-attachment="{html.escape(self.cids[src[4:].strip("<>")], quote=True)}"'
            elif src.startswith("https://"):
                try:
                    parsed = urlsplit(src)
                except ValueError:
                    return
                host = parsed.hostname or ""
                try:
                    public_host = ipaddress.ip_address(host).is_global
                except ValueError:
                    public_host = bool(host) and host != "localhost" and not host.endswith((".localhost", ".local", ".internal", ".lan"))
                if public_host and parsed.username is None and parsed.password is None and len(src) <= 2000 and not any(ord(c) < 33 for c in src):
                    attributes = f' data-external-src="{html.escape(src, quote=True)}"'
                    self.external_images += 1
                    self.external_hosts.add(host)
            if not attributes:
                return
            attributes += f' alt="{alt}" loading="lazy" referrerpolicy="no-referrer"'
        self.output.append(f"<{tag}{attributes}>")

    def handle_endtag(self, tag):
        was_ignored = self.ignored_depth
        super().handle_endtag(tag)
        if not was_ignored and tag in self.ALLOWED and tag not in ("img", "br", "hr"):
            self.output.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.ignored_depth:
            self.output.append(html.escape(data))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in ("img", "br", "hr"):
            self.handle_endtag(tag)


class GmailContentReader:
    def __init__(self, client):
        self._client = client

    @staticmethod
    def _decode(data):
        return decode_data(data, MAX_TEXT).decode("utf-8", errors="replace")

    def _body_data(self, message_id, part, limit):
        body = part.get("body", {})
        if not 0 <= int(body.get("size", 0)) <= limit:
            raise ValueError("Attachment exceeds the size limit")
        if "data" in body:
            return decode_data(body["data"], limit)
        if body.get("attachmentId"):
            response = self._client.get_attachment(message_id, body["attachmentId"])
            if int(response.get("size", 0)) > limit:
                raise ValueError("Attachment exceeds the size limit")
            return decode_data(response.get("data", ""), limit)
        return b""

    def get_content(self, message_id):
        response = self._client.get_full_message(message_id)
        parts = walk_parts(response.get("payload", {}))
        headers = headers_of(response.get("payload", {}))
        attachments, cids = [], {}
        plain, formatted = None, None
        excluded=[]
        for key, part in parts:
            if any(key.startswith(prefix+'.') for prefix in excluded):continue
            mime = str(part.get("mimeType", "application/octet-stream")).lower()
            h = headers_of(part)
            if str(part.get('mimeType','')).lower()=='message/rfc822' or part.get('filename') or h.get('content-disposition','').lower().startswith('attachment'):excluded.append(key)
            cid = h.get("content-id", "").strip("<>")
            is_attachment = bool(part.get("filename") or cid or h.get("content-disposition", "").strip().lower().startswith("attachment") or (not mime.startswith("multipart/") and mime not in ("text/plain", "text/html") and ("data" in part.get("body", {}) or part.get("body", {}).get("attachmentId"))))
            if is_attachment:
                size = int(part["body"]["size"]) if "size" in part.get("body", {}) else None
                attachments.append({"part_id": key, "filename": safe_filename(part.get("filename") or ("inline-image" if cid else "attachment")), "mime_type": mime, "size": size, "inline": bool(cid), "image": mime in SAFE_IMAGES, "available": size is None or 0 <= size <= MAX_ATTACHMENT})
                if cid and mime in SAFE_IMAGES:
                    cids[cid] = key
            elif mime in ("text/plain", "text/html"):
                from email.message import Message
                charset_header=Message()
                charset_header['Content-Type']=h.get('content-type',mime)
                try:text = self._body_data(message_id, part, MAX_TEXT).decode(charset_header.get_content_charset() or 'utf-8', errors="replace")[:100000]
                except LookupError as exc:raise ValueError('Unknown MIME charset') from exc
                if mime == "text/plain" and plain is None:
                    plain = text
                elif mime == "text/html" and formatted is None:
                    formatted = text
        parser = SafeEmailHTML(cids)
        if formatted:
            parser.feed(formatted)
        classification_body=plain
        evidence=EvidenceHTMLText();evidence.feed(formatted or '')
        if formatted and (not plain or (len(plain.split())<3 and len(evidence.text().split())>=8)):
            classification_body=evidence.text()
            if plain:plain=classification_body
        if not plain:
            extractor = HTMLTextExtractor()
            extractor.feed(formatted or "")
            plain = extractor.get_text() or str(response.get("snippet", ""))
            evidence=EvidenceHTMLText()
            evidence.feed(formatted or '')
            classification_body=evidence.text()
        return {"id": response["id"], "sender": headers.get("from", ""), "recipient": headers.get("to", ""),
            "cc": headers.get("cc", ""), "subject": headers.get("subject", ""), "date": headers.get("date", ""),
            "body": plain[:100000], "_classification_body": (classification_body or '')[:100000], "label_ids": tuple(response.get("labelIds", ())), "html": "".join(parser.output), "external_images": parser.external_images, "external_image_hosts": sorted(parser.external_hosts),
            "attachments": attachments,
            "list_headers": {k: headers[k][:4000] for k in ("list-id", "list-unsubscribe", "list-unsubscribe-post") if k in headers}}

    def attachment(self, message_id, part_id, inline=False):
        if not re.fullmatch(r"0(?:\.\d{1,3}){0,20}", part_id):
            raise ValueError("Invalid MIME part ID")
        parts = dict(walk_parts(self._client.get_full_message(message_id).get("payload", {})))
        part = parts.get(part_id)
        if part is None or part.get("parts"):
            raise LookupError("Attachment not found")
        mime = str(part.get("mimeType", "application/octet-stream")).lower()
        data = self._body_data(message_id, part, MAX_ATTACHMENT)
        if inline:
            signatures = {"image/png": data.startswith(b"\x89PNG\r\n\x1a\n"), "image/jpeg": data.startswith(b"\xff\xd8\xff"),
                "image/gif": data.startswith((b"GIF87a", b"GIF89a")), "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP"}
            if not signatures.get(mime, False):
                raise ValueError("This attachment cannot be rendered as an image")
        safe_type = mime if mime in SAFE_IMAGES | {"application/pdf", "text/plain", "text/csv", "application/zip"} else "application/octet-stream"
        return data, safe_type, safe_filename(part.get("filename", "attachment"))


class EvidenceHTMLText(HTMLParser):
    """Visible text only: no hidden preheaders, quoted replies, images or forms."""
    VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    BLOCKED = {'script','style','head','svg','iframe','form','object','blockquote','template','noscript'}

    def __init__(self,include_quotes=False):
        self.include_quotes=include_quotes
        super().__init__(convert_charrefs=True)
        self.stack, self.parts = [], []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        style = re.sub(r'\s+', '', (attributes.get('style') or '').lower())
        quoted=tag=='blockquote' or bool(re.search(r'\b(?:gmail_quote|yahoo_quoted|moz-cite-prefix)\b',(attributes.get('class') or '')))
        inherited_quote=bool(self.stack and self.stack[-1][2])
        hidden = bool(self.stack and self.stack[-1][1]) or (tag in self.BLOCKED and not (self.include_quotes and tag=='blockquote')) or 'hidden' in attributes or attributes.get('aria-hidden')=='true' or 'display:none' in style or 'visibility:hidden' in style or bool(re.search(r'\bgmail_signature\b',(attributes.get('class') or ''))) or (quoted and not self.include_quotes)
        if tag not in self.VOID:self.stack.append((tag,hidden,quoted or inherited_quote))
        if not hidden and tag in {'p','div','br','li','tr','h1','h2','h3'}:self.parts.append('\n')

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not self.stack or not self.stack[-1][1]:
            quoted=bool(self.stack and self.stack[-1][2])
            self.parts.append(('\n> '+data.replace('\n','\n> ')) if quoted else data)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag,attrs)
        if tag not in self.VOID:self.handle_endtag(tag)

    def text(self):
        return '\n'.join(line.strip() for line in ''.join(self.parts).splitlines() if line.strip())


def decode_evidence_text(data,charset=None):
    """Prefer declared encoding; repair only measured, non-lossy alternatives."""
    declared=charset or 'utf-8'
    try:return data.decode(declared,errors='strict'),declared
    except (UnicodeDecodeError,LookupError) as original:
        # Broken UTF-8 mail often has a Windows punctuation byte among valid
        # Unicode. Preserve all valid UTF-8 runs rather than reinterpreting them.
        mixed=data.decode('utf-8',errors='surrogateescape');out=[];repaired=False
        for c in mixed:
            if 0xDC80<=ord(c)<=0xDCFF:
                try:value=bytes([ord(c)-0xDC00]).decode('cp1252')
                except UnicodeDecodeError:break
                if unicodedata.category(value)[0] not in {'P','Z'} and value not in '€£©®™':break
                out.append(value);repaired=True
            else:out.append(c)
        else:
            return ''.join(out),'utf-8+windows-punctuation' if repaired else 'utf-8'
        # HTML sometimes supplies a correct in-document declaration while its
        # MIME charset is wrong. Never drop undecodable bytes with replacement.
        meta=re.search(br'<meta\b[^>]{0,300}charset\s*=\s*["\x27]?([a-zA-Z0-9_-]+)',data[:4096],re.I)
        if meta:
            try:return data.decode(meta[1].decode('ascii'),errors='strict'),'html-declared:'+meta[1].decode('ascii')
            except (LookupError,UnicodeDecodeError):pass
        from charset_normalizer import from_bytes
        match=from_bytes(data).best() if len(data)>=128 else None
        if match is not None and match.chaos<=.05 and match.coherence>=.4:
            return str(match),'detected:'+match.encoding
        raise MIMECharsetError('MIME charset cannot be decoded reliably') from original


def parse_evidence_message(raw):
    """Decode RFC5322 + MIME charset/transfer encoding with Python's parser.

    Nothing here renders HTML, follows links, downloads images or opens files.
    """
    from email import policy
    from email.parser import BytesParser
    if not isinstance(raw,bytes) or not raw or len(raw)>10_000_000:
        raise ValueError('Message exceeds the bounded MIME evidence limit')
    message=BytesParser(policy=policy.default).parsebytes(raw)
    plain,formatted=[],[]
    seen=0;mime_types={};decodings=[]

    def visit(part,depth=0):
        nonlocal seen
        seen+=1
        mime_types[part.get_content_type()]=mime_types.get(part.get_content_type(),0)+1
        if depth>20 or seen>200:raise ValueError('Excessive MIME nesting')
        if part.get_content_disposition()=='attachment' or part.get_filename() or part.get_content_type()=='message/rfc822':return
        if part.is_multipart():
            for child in part.iter_parts():visit(child,depth+1)
            return
        mime=part.get_content_type()
        if mime not in ('text/plain','text/html'):return
        data=part.get_payload(decode=True) or b''
        if len(data)>MAX_TEXT:raise MIMETextTooLarge('MIME text exceeds the evidence bound')
        text,encoding=decode_evidence_text(data,part.get_content_charset());decodings.append(encoding)
        if sum(map(len,plain))+sum(map(len,formatted))+len(text)>2_000_000:raise MIMETextTooLarge('Decoded body exceeds local safety bound')
        if mime=='text/plain':plain.append(text)
        else:
            parser=EvidenceHTMLText(include_quotes=True);parser.feed(text);formatted.append(parser.text())
    visit(message)
    text='\n'.join(plain).strip()
    rich='\n'.join(formatted).strip()
    if not text or (len(text.split())<3 and len(rich.split())>=8):text=rich or text
    return dict(subject=str(message.get('subject','')),sender=str(message.get('from','')),body=text,complete=True,mime_types=mime_types,decodings=decodings,source='gmail_raw',
        headers={name:str(message.get(name,''))[:4000] for name in ('message-id','in-reply-to','references','list-id','list-unsubscribe') if message.get(name)})


class GmailEvidenceReader:
    """At most one raw-message operation per selected email (two HTTP attempts)."""
    def __init__(self, client):self.client=client

    def read(self, message_id):
        from email_agent.infrastructure.gmail.client import RawMessageTooLarge
        try:return parse_evidence_message(self.client.get_raw_message(message_id))
        except RawMessageTooLarge:return self._read_text_parts(message_id)

    def _read_text_parts(self,message_id):
        """Large binary attachments do not impose a limit on readable body text."""
        from email.message import Message
        response=self.client.get_full_message(message_id);payload=response.get('payload',{})
        plain=[];formatted=[];types={};decodings=[];excluded=[];total=0;text_reads=0
        for key,part in walk_parts(payload):
            if any(key.startswith(prefix+'.') for prefix in excluded):continue
            mime=part.get('mimeType','application/octet-stream');types[mime]=types.get(mime,0)+1
            headers=headers_of(part)
            if part.get('filename') or headers.get('content-disposition','').lower().startswith('attachment') or mime=='message/rfc822':excluded.append(key);continue
            if mime not in ('text/plain','text/html'):continue
            body=part.get('body',{})
            if int(body.get('size',0))>MAX_TEXT:raise MIMETextTooLarge('MIME text exceeds the evidence bound')
            if body.get('attachmentId'):
                text_reads+=1
                if text_reads>3:raise ValueError('Text-part request bound reached')
                body=self.client.get_attachment(message_id,body['attachmentId'])
            raw=decode_data(body.get('data',''),MAX_TEXT)
            info=Message();info['Content-Type']=headers.get('content-type',mime)
            text,encoding=decode_evidence_text(raw,info.get_content_charset());decodings.append(encoding);total+=len(text)
            if total>2_000_000:raise MIMETextTooLarge('Decoded body exceeds local safety bound')
            if mime=='text/plain':plain.append(text)
            else:
                parser=EvidenceHTMLText(include_quotes=True);parser.feed(text);formatted.append(parser.text())
        text='\n'.join(plain).strip();rich='\n'.join(formatted).strip()
        if not text or len(text.split())<3 and len(rich.split())>=8:text=rich or text
        headers=headers_of(payload)
        return dict(body=text,complete=True,mime_types=types,decodings=decodings,source='gmail_full_text_parts',headers={k:v[:4000] for k,v in headers.items() if k in ('message-id','in-reply-to','references','list-id','list-unsubscribe')})

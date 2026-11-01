"""Independent DKIM verification. Gmail Authentication-Results is never trusted.

Only RSA-SHA256 signatures with full body coverage and signed, unambiguous
list headers qualify. Unsupported profiles retain the manual workflow.
"""
import hashlib
import re
import ipaddress
import time
from email import policy
from email.parser import BytesHeaderParser
from urllib.parse import urlsplit
from email_agent.application.unsubscribe import VerifiedList
from email_agent.application.subscriptions import subscription_identity


def dns_txt(name, timeout=3):
    import dns.resolver
    value = name.decode("ascii").rstrip(".")
    if len(value)>253 or not all(re.fullmatch(r"[a-zA-Z0-9_-]{1,63}", label) for label in value.split(".")):
        raise ValueError("Invalid signing-domain DNS name")
    answer = dns.resolver.resolve(value, "TXT", lifetime=min(float(timeout), 3), search=False)
    records = [b"".join(item.strings) for item in answer]
    if len(records) != 1 or len(records[0])>8192:
        raise ValueError("Ambiguous or oversized DKIM public key")
    return records[0]


class DKIMUnsubscribeVerifier:
    def __init__(self, client_factory, dns_lookup=dns_txt):
        self.clients, self.dns_lookup = client_factory, dns_lookup

    def verify(self, account: str, message_id: str, identity: str) -> VerifiedList:
        try:
            import dkim
        except ImportError as exc:
            raise ValueError("Independent DKIM verifier is not installed. Install requirements-unsubscribe.txt; use manual review meanwhile.") from exc
        raw = self.clients(account).get_raw_message(message_id)
        if not isinstance(raw, bytes) or len(raw)>10_000_000 or b"\r\n\r\n" not in raw:
            raise ValueError("Original RFC message is unavailable or exceeds the verification limit")
        header_block = raw.split(b"\r\n\r\n", 1)[0]
        if len(header_block)>128_000:
            raise ValueError("Message headers exceed the verification limit")
        headers = BytesHeaderParser(policy=policy.default).parsebytes(header_block + b"\r\n\r\n")
        required = ("from", "list-unsubscribe", "list-unsubscribe-post") + (("list-id",) if headers.get_all("list-id",[]) else ())
        if headers.defects or any(len(headers.get_all(name, []))!=1 for name in required):
            raise ValueError("Missing, duplicate or malformed list headers; inspect the original manually")
        if any(getattr(headers[name], "defects", ()) for name in required):
            raise ValueError("Malformed list headers")
        observed,_=subscription_identity({name:str(headers[name]) for name in required},str(headers["from"]))
        if observed!=identity:
            raise ValueError("The signed list identity differs from the selected subscription")
        post = re.sub(r"\s+", "", str(headers["list-unsubscribe-post"]))
        if post != "List-Unsubscribe=One-Click":
            raise ValueError("This list does not offer RFC 8058 one-click POST; use its manual mechanism")
        destinations = re.findall(r"<([^<>]+)>", str(headers["list-unsubscribe"]))
        urls = [value for value in destinations if value.casefold().startswith("https://")]
        if len(urls)!=1:
            raise ValueError("A single signed HTTPS destination is required")
        url = urls[0]
        parsed = urlsplit(url)
        if len(url)>2000 or any(ord(c)<33 for c in url) or parsed.scheme!="https" or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.port not in (None,443) or parsed.fragment:
            raise ValueError("Unsubscribe destination is not safe HTTPS")
        host=parsed.hostname.lower().rstrip(".")
        if host=="localhost" or host.endswith((".localhost",".local",".internal")):
            raise ValueError("Unsubscribe destination is local; manual review required")
        try:
            address=ipaddress.ip_address(host)
        except ValueError:
            address=None
        if address is not None and (not address.is_global or address.is_reserved or address.is_multicast):
            raise ValueError("Unsubscribe destination is not a public address")
        signatures=headers.get_all("dkim-signature",[])
        if not signatures:
            raise ValueError("No DKIM signature exists; automatic unsubscribe is unavailable")
        authors=getattr(headers["from"],"addresses",())
        if len(authors)!=1 or not authors[0].domain:
            raise ValueError("A single signed From identity is required")
        author=authors[0].domain.encode("idna").decode("ascii").lower()
        candidate=None
        reasons=[]
        deadline=time.monotonic()+15
        cryptographic_attempts=0
        dns_cache={}
        dns_failures=[]
        def lookup(name,timeout=3):
            if name in dns_cache:return dns_cache[name]
            try: value=self.dns_lookup(name,timeout=min(timeout,3))
            except Exception as exc:
                dns_failures.append(type(exc).__name__)
                raise
            dns_cache[name]=value
            return value
        # RFC8058 requires at least one applicable valid signature, not necessarily the first.
        for index,signature in enumerate(signatures[:32]):
            if time.monotonic()>=deadline:
                reasons.append("verification deadline reached")
                break
            try:
                tags=dkim.util.parse_tag_value(str(signature).encode("ascii"))
                covered={part.strip().lower() for part in tags.get(b"h",b"").split(b":")}
                if not {name.encode() for name in required}<=covered:
                    reasons.append("required headers not signed");continue
                if b"l" in tags or tags.get(b"a")!=b"rsa-sha256":
                    reasons.append("unsupported algorithm or partial body coverage");continue
                domain=tags.get(b"d",b"").decode("ascii").lower()
                if not domain or len(domain)>253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",part) for part in domain.split(".")):
                    reasons.append("malformed signing domain");continue
                if cryptographic_attempts>=8:
                    reasons.append("eight applicable cryptographic checks exhausted; later signatures remain unverified")
                    break
                cryptographic_attempts+=1
                before=len(dns_failures)
                verifier=dkim.DKIM(raw,minkey=2048,timeout=3)
                if not verifier.verify(idx=index,dnsfunc=lookup):
                    reasons.append("DNS lookup failed: "+dns_failures[-1] if len(dns_failures)>before else "signature or public key invalid");continue
                proof={"identity":identity,"url":url,"host":parsed.hostname,"signer":domain,
                       "author_domain":author,"alignment":"exact" if author==domain else "not_exact",
                       "author_authorization":"signed_author_domain" if author==domain else "not_established",
                       "signature_index":index,"policy_version":"rfc8058-human-third-party-v1",
                       "raw_sha256":hashlib.sha256(raw).hexdigest(),"message_id":message_id}
                if author==domain:
                    return proof
                if candidate is None:
                    candidate=proof
            except Exception as exc:
                detail=str(exc).lower()
                if dns_failures:reason="DNS lookup failed: "+dns_failures[-1]
                elif "x= value is past" in detail:reason="DKIM signature expired (x=); use a newer message or a manual method"
                elif "key too small" in detail:reason="RSA key below this installation's 2048-bit safety policy; this is a local policy rejection, not proof of sender fraud"
                elif "body hash" in detail:reason="Original body does not match its signed hash; verification cannot establish message integrity"
                elif "t= value is in the future" in detail:reason="Signature timestamp is in the future; check the local clock"
                else:reason="Independent cryptographic verification failed ("+type(exc).__name__+")"
                reasons.append(reason)
        if candidate is not None:
            return candidate
        if len(signatures)>32:reasons.append("header scan bound reached; additional signatures remain unverified")
        raise ValueError(f"No independently valid applicable DKIM signature verified: {len(signatures)} DKIM signatures observed, {cryptographic_attempts} applicable signatures checked. "+"; ".join(dict.fromkeys(reasons))+". Use a reviewed manual method; no unsubscribe request was submitted.")

"""Observed header offers and unverified senders; observations are not authentication."""
import hashlib
import json
import re
import time
from email import policy
from email.parser import HeaderParser
from email.utils import parseaddr
from urllib.parse import urlsplit
from typing import Protocol
from email_agent.domain.message import EmailMessage
from email_agent.application.classify_messages import decide


def normalized_headers(headers):
    result={}
    for name,value in headers.items():
        if not isinstance(value,str): continue
        value=re.sub(r"\r?\n[ \t]+"," ",value).strip()
        if "\n" in value or "\r" in value: continue
        parsed=HeaderParser(policy=policy.default).parsestr(name+": "+value+"\n\n")
        if not parsed.defects and parsed.get(name) is not None:
            result[name.lower()]=str(parsed[name])
    return result


def subscription_identity(headers, sender=""):
    headers=normalized_headers(headers)
    raw_id=headers.get("list-id","")
    match=re.search(r"<([a-zA-Z0-9_.-]{3,250})>",raw_id) if raw_id.count("<")==raw_id.count(">")==1 else None
    if match: return match[1].lower(),"mailing_list"
    destinations=sorted(re.findall(r"<([^<>]+)>",headers.get("list-unsubscribe","")))
    address=parseaddr(sender)[1].casefold()
    if "@" not in address: address=""
    if destinations:
        # Preserve the entire offered target: dropping query/list tokens could merge unrelated lists.
        identity="headers:"+hashlib.sha256(json.dumps([address,destinations],sort_keys=True).encode()).hexdigest()
        return identity,"header_offer"
    return "sender:"+hashlib.sha256((address or sender.casefold()).encode()).hexdigest(),"promotional_sender"


def subscriptions(rows):
    groups={}
    for row in rows:
        if isinstance(row,dict):
            message_id,headers,sender=row["message_id"],row.get("headers") or {},row["sender"]
            # Do not mistake a manually misfiled transaction for a marketing list.
            category,_,reason=decide(EmailMessage(message_id,row["account_id"],sender,row["subject"],label_ids=tuple(row.get("label_ids") or ())))
            if category in ("orders","bills","university") or reason=="submitted_application_not_verified":
                continue
        else:
            message_id,headers=row
            sender=""
            if not headers.get("list-id") and not headers.get("list-unsubscribe"): continue
        headers=normalized_headers(headers)
        identity,kind=subscription_identity(headers,sender)
        key=hashlib.sha256(identity.encode()).hexdigest()[:24]
        address=parseaddr(sender)[1]
        display=identity if kind=="mailing_list" else (address if "@" in address else sender or "Observed unsubscribe offer")
        group=groups.setdefault(key,{"id":key,"identity":identity,"display_name":display,"kind":kind,"message_ids":[],"candidate_message_ids":[],"one_click_candidate":False,"methods":[],"manual_destinations":[],"automatic_available":False,"verification":"not_verified","state":"verification_required" if kind!="promotional_sender" else "manual_review_required","reason":"Headers are observed, not authenticated. Check eligibility before approving any request." if kind!="promotional_sender" else "Unverified promotional sender; no mailing-list identity or unsubscribe header observed. Read missing metadata or inspect the original manually."})
        group["latest_message_ms"]=max(group.get("latest_message_ms",0),row.get("internal_date_ms",0) if isinstance(row,dict) else 0)
        group["message_ids"].append(message_id)
        offered=re.sub(r"\s+","",headers.get("list-unsubscribe-post",""))=="List-Unsubscribe=One-Click" and any(value.casefold().startswith("https://") for value in re.findall(r"<([^<>]+)>",headers.get("list-unsubscribe","")))
        if offered:
            group["one_click_candidate"]=True
            group["candidate_message_ids"].append(message_id)
        for value in re.findall(r"<([^<>]+)>",headers.get("list-unsubscribe","")):
            method=None
            if value.casefold().startswith("mailto:"): method="Manual email option; Postmark never sends it"
            elif value.casefold().startswith("https://"):
                try:
                    parsed=urlsplit(value)
                    if parsed.hostname and parsed.username is None and parsed.password is None and parsed.port in (None,443) and not parsed.fragment:
                        method="HTTPS option; independent verification required"
                except ValueError: pass
            if method:
                if value not in group["manual_destinations"]: group["manual_destinations"].append(value)
                if method not in group["methods"]: group["methods"].append(method)
    for group in groups.values():
        if not group["one_click_candidate"]:
            group["state"]="manual_review_required"
            if group["kind"]!="promotional_sender":
                group["reason"]="No RFC 8058 HTTPS one-click offer is present in cached headers. Inspect the original for a manual option; no request has been sent."
    return [{**group,"observed_count":len(group["message_ids"])} for group in groups.values()]


class SubscriptionObservations(Protocol):
    def subscription_rows(self, account: str, limit: int = 1000) -> list[dict]: ...


class DiscoveryStore(Protocol):
    def get(self, account: str, id: str) -> EmailMessage | None: ...
    def claim_discovery(self, account: str) -> str | None: ...
    def release_discovery(self, account: str, owner: str) -> None: ...
    def discovery_candidates(self, account: str, limit: int) -> list[str]: ...
    def record_discovery(self, account: str, id: str, status: str, headers: dict | None) -> None: ...
    def discovery_status(self, account: str) -> dict: ...


class MetadataReader(Protocol):
    def read_list_headers(self, id: str) -> dict[str,str]: ...


def observed_subscriptions(messages: SubscriptionObservations, account: str) -> list[dict]:
    return subscriptions(messages.subscription_rows(account))


class RefreshSubscriptions:
    """One explicit resumable metadata page; successful empty headers remain cached."""
    def __init__(self, messages: DiscoveryStore, reader: MetadataReader) -> None:
        self.messages,self.reader=messages,reader

    def execute(self, account: str) -> dict:
        if account not in ("personal","university"): raise ValueError("Unknown account")
        # A lease protects simultaneous web/CLI invocations; interrupted pages resume by item.
        owner=self.messages.claim_discovery(account)
        if not owner:
            raise ValueError("Metadata discovery is already running; refresh progress instead")
        results=[]
        deadline=time.monotonic()+60
        try:
            for id in self.messages.discovery_candidates(account,20):
                if time.monotonic()>=deadline: break
                message=self.messages.get(account,id)
                category,_,reason=decide(message)
                if category in ("orders","bills","university") or reason=="submitted_application_not_verified":
                    self.messages.record_discovery(account,id,"skipped",None)
                    results.append({"message_id":id,"status":"skipped"});continue
                try:
                    headers=self.reader.read_list_headers(id)
                    if not isinstance(headers,dict) or set(headers)-{"list-id","list-unsubscribe","list-unsubscribe-post"} or any(not isinstance(v,str) or len(v)>4000 for v in headers.values()):
                        raise ValueError("Invalid list metadata")
                    self.messages.record_discovery(account,id,"succeeded",normalized_headers(headers))
                    results.append({"message_id":id,"status":"succeeded"})
                except Exception:
                    self.messages.record_discovery(account,id,"failed",None)
                    results.append({"message_id":id,"status":"failed","message":"Metadata unavailable; bounded retry deferred, no headers inferred"})
        finally:
            self.messages.release_discovery(account,owner)
        return {"items":results,"limit":20,"progress":self.messages.discovery_status(account)}

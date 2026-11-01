"""Present durable cancellation facts without equating receipt with cessation."""
from datetime import datetime, timezone
import time

STATUS = {"running":"in_progress", "requested":"submitted", "cancelled":"unsubscribed",
          "needs_action":"uncertain", "uncertain":"uncertain", "failed":"failed"}
LABELS = {"active":"Active", "in_progress":"Unsubscribe in progress", "submitted":"Request submitted",
          "unsubscribed":"Unsubscribed", "uncertain":"Needs verification", "failed":"Failed"}


def tracking(store, account, group, state):
    manual=group.get('manual_state')
    if manual and manual['status']=='manual_completed':
        cutoff=datetime.fromisoformat(manual['updated_at']).replace(tzinfo=timezone.utc).timestamp()*1000+1000
        later=max([group.get('latest_message_ms') or 0,*group.get('message_dates',[])])>cutoff
        return dict(state='manual_completed',label='Manual unsubscribe completed',later_message=later,
            completed_at=manual['updated_at'],updated_at=manual['updated_at'],method='manual',
            evidence='Completed by the user; future delivery is not independently verified')
    if not state:
        return {"state":"active", "label":"Active", "later_message":False}
    value=store.get(account,state["action_id"])
    result=value.get("result") or {}
    status=STATUS.get(state["status"],"uncertain")
    if status=="in_progress" and value["expires"]<=time.time():status="uncertain"
    if result.get("submission_attempted") and status=="failed":status="uncertain"
    # An RFC8058 HTTP acknowledgement is submitted, never confirmed cessation.
    if status=="unsubscribed" and (value["kind"]!="unsubscribe:browser" or result.get("state")!="cancelled" or result.get("reason")!="website_confirmation" or not isinstance(result.get("evidence"),str) or not result["evidence"].strip()):
        status="uncertain"
    audit=store.audit(account,value["id"])
    event=next((item for item in reversed(audit) if item["event"]=="browser_cancelled"),None)
    completed_at=event["created_at"] if status=="unsubscribed" and event else None
    if status=="unsubscribed" and not completed_at:status="uncertain"
    later=False
    if completed_at:
        cutoff=datetime.fromisoformat(completed_at).replace(tzinfo=timezone.utc).timestamp()*1000+1000
        later=max([group.get("latest_message_ms",0),*group.get("message_dates",[])])>cutoff
    return {"state":status,"label":LABELS[status],"action_id":value["id"],
            "updated_at":state["updated_at"],"completed_at":completed_at,
            "method":"website" if value["kind"]=="unsubscribe:browser" else "one_click",
            "evidence":result["evidence"][:500] if completed_at else "See saved request; no confirmed cancellation",
            "later_message":later}


def include_saved_lists(store, account, groups, states):
    """Keep existing audit identities visible after category changes or window expiry."""
    known={group["id"] for group in groups}
    for id,state in states.items():
        if id in known:continue
        value=store.get(account,state["action_id"])
        payload=value["payload"]
        item=next((i for i in payload.get("items",[]) if i.get("subscription_id")==id),{})
        identity=payload.get("identity") or item.get("identity")
        if not identity:continue
        message_id=payload.get("message_id") or item.get("message_id")
        groups.append({"id":id,"identity":identity,"display_name":payload.get("display_name") or item.get("display_name") or identity,
          "kind":"promotional_sender" if identity.startswith("sender:") else "header_offer" if identity.startswith("headers:") else "mailing_list","message_ids":[message_id] if message_id else [],"candidate_message_ids":[],"one_click_candidate":False,
          "methods":[],"manual_destinations":[],"automatic_available":False,"verification":"saved_request",
          "state":"saved_request","reason":"Saved cancellation history; current local observations may be incomplete","observed_count":0})
    return groups

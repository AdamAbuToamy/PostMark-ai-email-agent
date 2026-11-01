"""Bounded typed local tools. A model may propose mutations, never approve them."""
import json
import re
import time
from dataclasses import dataclass
from typing import Protocol
from email_agent.application.chat_intents import plan_intent, normalize_call
from email_agent.application.classify_messages import decide
from email_agent.application.gmail_actions import safe_promotional_selection
from email_agent.application.subscriptions import observed_subscriptions


class ChatStore(Protocol):
    def chat(self, account, role, content) -> None: ...
    def history(self, account) -> list[dict]: ...
    def preview(self, account, kind, payload, conversation_id=None) -> dict: ...
    def get(self, account, id) -> dict: ...
    def claim(self, account, id) -> bool: ...
    def finish(self, account, id, status, result) -> None: ...
    def current_conversation(self, account) -> str: ...


@dataclass(frozen=True)
class Tool:
    fields: dict
    mutation: bool = False


TOOLS = {
    "search_messages": Tool({"search": str, "category": str, "state": str, "label": str, "classification": str, "offset": int, "limit": int}),
    "list_categories": Tool({}),
    "review_spam": Tool({}),
    "list_subscriptions": Tool({}),
    "classify_orders": Tool({"sender": str}, True),
    "propose_category": Tool({"category_id": str, "name": str, "reason": str}, True),
    "create_category": Tool({"category_id": str, "name": str, "reason": str}, True),
    "approve_category": Tool({"proposal_id": int}, True),
    "assign_messages": Tool({"message_ids": list, "category_id": str, "application_ref": str}, True),
    "set_status": Tool({"message_ids": list, "state": str}, True),
    "trash_messages": Tool({"message_ids": list, "category_id": str}, True),
    "restore_message": Tool({"message_ids": list}, True),
}


def validate_tool(name, args):
    if not isinstance(name, str) or name not in TOOLS or not isinstance(args, dict):
        raise ValueError("Unsupported tool")
    tool = TOOLS[name]
    if set(args) - set(tool.fields):
        raise ValueError("Unknown tool arguments")
    for field, value in args.items():
        if type(value) is not tool.fields[field]:
            raise ValueError(f"Invalid argument type: {field}")
        if isinstance(value, str) and len(value) > 500:
            raise ValueError("Tool text is too long")
    if "message_ids" in args:
        ids = args["message_ids"]
        if not 1 <= len(ids) <= 20 or len(set(str(i) for i in ids)) != len(ids) or any(not isinstance(i, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,256}", i) for i in ids):
            raise ValueError("Select 1-20 distinct message IDs")
    required = {"propose_category": {"category_id", "name", "reason"}, "create_category": {"category_id", "name", "reason"}, "approve_category": {"proposal_id"}, "assign_messages": {"message_ids", "category_id"}, "set_status": {"message_ids", "state"}, "trash_messages": {"message_ids"}, "restore_message": {"message_ids"}}
    if required.get(name, set()) - set(args):
        raise ValueError("Required tool argument missing")
    if "state" in args and args["state"] not in (("new", "done") if name == "set_status" else ("new", "done", "all")):
        raise ValueError("Invalid message state")
    if "category_id" in args and not re.fullmatch(r"[a-z][a-z0-9_]{1,49}", args["category_id"]):
        raise ValueError("Invalid category ID")
    if "proposal_id" in args and args["proposal_id"] < 1:
        raise ValueError("Invalid proposal ID")
    if "offset" in args and not 0<=args["offset"]<=1000000:
        raise ValueError("Invalid search offset")
    if "limit" in args and not 1<=args["limit"]<=100:
        raise ValueError("Search page limit must be 1–100")
    return tool




class Chat:
    MAX_TOOLS = 4

    def __init__(self, messages, management, assignments, store: ChatStore, remote=None):
        self.messages, self.management, self.assignments, self.store = messages, management, assignments, store
        self.remote = remote

    def run(self, account, text, context=None, plan=None, processor="local", reply=None):
        self.management.validate_account(account)
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
            raise ValueError("Enter 1-2000 characters")
        context = context or []
        for id in context:
            self.management.details(account, id)
        try:
            calls = plan if plan is not None else plan_intent(text, context, self.management.categories(account), lambda term, limit, category=None: self.messages.search_local(account, term, category=category, limit=limit))
        except ValueError as exc:
            content = {"text": str(exc), "results": [], "status": "clarification", "processor":processor,"ai_needed":processor=="gemini"}
            self.store.chat(account, "user", {"text": text})
            self.store.chat(account, "assistant", content)
            return content
        if not isinstance(calls, list) or not 1 <= len(calls) <= self.MAX_TOOLS:
            raise ValueError("Plan exceeds the four-tool limit")
        calls = [normalize_call(call) for call in calls]
        # Validate the entire plan before executing even its first tool.
        for call in calls:
            if not isinstance(call, dict) or set(call) != {"tool", "args"}:
                raise ValueError("Invalid tool call")
            validate_tool(call["tool"], call["args"])
        self.store.chat(account, "user", {"text": text})
        deadline, results = time.monotonic() + 10, []
        for call in calls:
            if time.monotonic() >= deadline:
                results.append({"status": "failed", "message": "Local tool deadline reached"})
                break
            try:
                result = self.execute(account, call["tool"], call["args"], deadline=deadline)
                results.append({"tool": call["tool"], "status": "pending" if "preview" in result else "succeeded", **result})
            except (ValueError, LookupError) as exc:
                results.append({"tool": call["tool"], "status": "failed", "message": str(exc)})
            except Exception:
                results.append({"tool": call["tool"], "status": "failed", "message": "Local tool unavailable; no automatic retry was attempted"})
        content = {"text": "Local tools completed. Review pending actions before confirming." if any(r["status"] == "pending" for r in results) else "Results from your selected account. All changes require separate confirmation.", "results": results, "processor":processor,"ai_needed":processor=="gemini"}
        if reply:
            content["text"] = reply
        self.store.chat(account, "assistant", content)
        return content

    def reply(self, account, text, reply, processor, ai_needed=None):
        self.store.chat(account,"user",{"text":text})
        content={"text":reply,"results":[],"processor":processor,"ai_needed":ai_needed if ai_needed is not None else processor in ("gemini","gemini_failed")}
        self.store.chat(account,"assistant",content)
        return content

    def execute(self, account, name, args, confirmed=False, deadline=None):
        if deadline is not None and time.monotonic() >= deadline:
            raise ValueError("Tool scheduling deadline reached")
        tool = validate_tool(name, args)
        if name == "list_categories":
            return {"categories": self.management.categories(account)}
        if name == "search_messages":
            state, label = args.get("state", "all"), args.get("label")
            if state not in ("new", "done", "all") or label not in (None, "SPAM", "TRASH", "IMPORTANT"):
                raise ValueError("Unsupported search filter")
            if args.get("category") and args["category"] not in {c["id"] for c in self.management.categories(account)}:
                raise ValueError("Category is not active")
            query={"search":args.get("search","")[:200],"category":args.get("category"),"state":state,"label":label,"classification":args.get("classification"),"limit":args.get("limit",50)}
            query={key:value for key,value in query.items() if value is not None}
            return {**self.messages.search_local_page(account,**query,offset=args.get("offset",0)),"query":query}
        if name == "review_spam":
            return {"reviews": self.assignments.reviews(account), "message": "These are uncertain proposals, not proof that a sender is legitimate."}
        if name == "list_subscriptions":
            return {"subscriptions": observed_subscriptions(self.messages,account), "message": "Observed mailing headers and unverified promotional senders are separate. Metadata discovery is available in Promotions; counts may be incomplete."}
        if name in ("trash_messages", "restore_message"):
            if self.remote is None:
                raise ValueError("Gmail actions are unavailable")
            ids=args["message_ids"]
            if name=="trash_messages" and args.get("category_id")=="promotions":
                ids=[id for id in ids if safe_promotional_selection(self.messages.get(account,id),self.management.details(account,id))]
                if not ids: raise ValueError("No safely reviewed promotional messages remain; inspect protected messages individually")
            return {"preview": self.remote.preview(account, "trash" if name == "trash_messages" else "restore", ids, args.get("category_id"), conversation_id=self.store.current_conversation(account)),"message":f"{len(ids)} exact messages in this preview; {len(args['message_ids'])-len(ids)} protected messages excluded."}
        if name == "classify_orders":
            candidates = self.messages.search_local(account, args.get("sender", "AliExpress"), limit=1000)
            selected = [r["message_id"] for r in candidates if r["source"] != "manual" and decide(self.messages.get(account, r["message_id"]))[:2] == ("orders", "classified")][:20]
            if not selected:
                return {"message": "No high-confidence automatic order candidates found. Promotions and manual overrides were excluded."}
            return self.execute(account, "assign_messages", {"message_ids": selected, "category_id": "orders"}, deadline=deadline)
        if name == "assign_messages":
            if args["category_id"] not in {c["id"] for c in self.management.categories(account)}:
                raise ValueError("Category is not active")
            if args["category_id"] == "job_applications" and not args.get("application_ref", "").strip():
                raise ValueError("Provide a reference to an actually submitted application")
        if tool.mutation and not confirmed:
            if "message_ids" in args:
                items = [self.management.details(account, id) for id in args["message_ids"]]
            else:
                items = []
            description = None
            if name == "approve_category":
                proposal = next((p for p in self.management.registry.proposals() if p["id"] == args["proposal_id"] and p["status"] == "pending"), None)
                if proposal is None:
                    raise ValueError("Category proposal is not pending")
                description = f"Approve {proposal['name']}: {proposal['reason']}"
            preview = self.store.preview(account, f"local:{name}", {"args": args, "items": items, "description": description}, conversation_id=self.store.current_conversation(account))
            return {"preview": preview}
        if name in ("propose_category", "create_category"):
            registry = self.management.registry
            registry.propose(args["category_id"], args["name"], args["reason"])
            if name == "create_category":
                proposal = next((p for p in registry.proposals() if p["category_id"] == args["category_id"] and p["status"] == "pending"), None)
                if proposal:
                    registry.approve(proposal["id"])
                elif args["category_id"] not in {c["id"] for c in registry.categories(account)}:
                    raise ValueError("A rejected category needs manual review in Settings")
            return {"categories": self.management.categories(account)}
        if name == "approve_category":
            if not self.management.registry.approve(args["proposal_id"]):
                raise ValueError("Proposal is no longer pending")
            return {"categories": self.management.categories(account)}
        if name in ("assign_messages", "set_status"):
            outcomes = []
            for id in args["message_ids"]:
                try:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise ValueError("Scheduling deadline reached; message not changed")
                    if name == "assign_messages":
                        self.management.change_category(account, id, args["category_id"], args.get("application_ref", ""))
                    else:
                        self.management.set_status(account, id, args["state"])
                    outcomes.append({"message_id": id, "status": "succeeded"})
                except (ValueError, LookupError) as exc:
                    outcomes.append({"message_id": id, "status": "failed", "message": str(exc)})
                except Exception:
                    outcomes.append({"message_id": id, "status": "uncertain", "message": "Local result needs reconciliation; inspect the message before retrying"})
            return {"items": outcomes}
        raise ValueError("Unsupported tool")

    def confirm(self, account, id, approved):
        if not approved:
            raise ValueError("Explicit local action confirmation is required")
        preview = self.store.get(account, id)
        if not preview["kind"].startswith("local:"):
            raise ValueError("Not a local Chat action")
        if preview["status"] not in ("pending", "running"):
            return preview
        if not self.store.claim(account, id):
            raise ValueError("Preview expired or already claimed")
        try:
            for item in preview["payload"]["items"]:
                current = self.management.details(account, item["message_id"])
                if any(current.get(k) != item.get(k) for k in ("category_id", "source", "status", "local_state")):
                    raise ValueError("Message changed since preview; request another preview")
            result = self.execute(account, preview["kind"][6:], preview["payload"]["args"], confirmed=True, deadline=time.monotonic() + 10)
            outcomes = result.get("items", [])
            succeeded = sum(item["status"] == "succeeded" for item in outcomes)
            status = "succeeded" if not outcomes or succeeded == len(outcomes) else "partial" if succeeded else "failed"
            self.store.finish(account, id, status, result)
        except (ValueError, LookupError) as exc:
            self.store.finish(account, id, "failed", {"message": str(exc)})
        return self.store.get(account, id)

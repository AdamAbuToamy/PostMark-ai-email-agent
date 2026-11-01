"""Consent, deterministic routing, model planning and safe reuse of read plans."""
import hashlib
import json
import uuid
from typing import Protocol
from email_agent.application.chat import TOOLS, validate_tool
from email_agent.application.chat_intents import deterministic_intent, normalize
from email_agent.application.agent import ToolAgent, conversation_context, PROMPT_VERSION
import re


class ChatPreferences(Protocol):
    def preferences(self, account: str) -> dict: ...
    def save_preferences(self, account: str, enabled: bool, developer_local: bool) -> None: ...
    def begin_request(self, account: str, request_id: str, fingerprint: str) -> dict | None: ...
    def finish_request(self, account: str, request_id: str, result: dict) -> None: ...
    def cached_plan(self, account: str, fingerprint: str) -> dict | None: ...
    def request_active(self, account: str, request_id: str) -> bool: ...
    def save_plan(self, account: str, fingerprint: str, plan: dict) -> None: ...
    def history(self, account: str) -> list[dict]: ...
    def current_conversation(self, account: str) -> str: ...
    def change_conversation(self, account: str, operation: str, target=None) -> list[dict]: ...
    def save_history_preference(self, account: str, enabled: bool) -> None: ...


class AIQuota(Protocol):
    def status(self) -> dict: ...
    def configure(self, limit: int) -> None: ...
    def reserve(self, account: str, purpose: str) -> bool: ...
    def attest_provider(self, free: bool, rpd: int | None, paid: bool, model: str) -> None: ...


class QuotaExhausted(ValueError):
    pass


class ChatAssistant:
    def __init__(self, chat, store: ChatPreferences, quota: AIQuota, model_factory, configured: bool, model_name="gemini-3.5-flash-lite", configuration=None):
        self.configuration=configuration
        self._model_name=model_name
        self.chat,self.store,self.quota,self.models,self._configured=chat,store,quota,model_factory,configured

    @property
    def configured(self):return self.configuration.configured if self.configuration else self._configured

    @property
    def model_name(self):return self.configuration.model if self.configuration else self._model_name

    @model_name.setter
    def model_name(self,value):self._model_name=value

    def settings(self, account: str) -> dict:
        self.chat.management.validate_account(account)
        preferences=self.store.preferences(account)
        return {**preferences,"configured":self.configured,"default_mode":"gemini" if self.configured and preferences["enabled"] and not preferences["developer_local"] else "local","usage":self.quota.status(),"model":self.model_name,"agent_limits":{"requests_per_turn":3,"tools_per_turn":4,"context_minutes":10},"connection":self.configuration.status(preferences) if self.configuration else None,"provider_limits":{"rpm":None,"tpm":None,"rpd":None,"verified":False,"source":"Check your active project/model in Google AI Studio; application UTC reset is independent"}}

    def history_preference(self, account, save_history):
        self.chat.management.validate_account(account)
        if type(save_history) is not bool:raise ValueError('History preference must be a boolean')
        if self.store.preferences(account)['save_history']!=save_history:
            self.store.change_conversation(account,'new')
            self.store.save_history_preference(account,save_history)
        return self.settings(account)

    def configure(self, account, enabled, developer_local, daily_limit, cost_approved, save_history=None, free_tier_verified=None, verified_rpd=None, paid_approved=False, processing_consent=None):
        self.chat.management.validate_account(account)
        if any(type(value) is not bool for value in (enabled,developer_local,cost_approved)):
            raise ValueError("Consent and preferences must be booleans")
        if daily_limit is not None and (type(daily_limit) is not int or not 0<=daily_limit<=100):
            raise ValueError("Daily request limit must be 0–100")
        if processing_consent is not None and type(processing_consent) is not bool: raise ValueError("Processing consent must be a boolean")
        if processing_consent is True and cost_approved is not True and not self.store.preferences(account).get("processing_consent"):raise ValueError("Explicit data-processing consent required")
        if save_history is not None and type(save_history) is not bool: raise ValueError("History preference must be a boolean")
        current=self.settings(account)
        if (enabled and not current["enabled"] or daily_limit is not None and daily_limit>current["usage"]["limit"]) and cost_approved is not True:
            raise ValueError("Explicit consent to sending prompts and possible charges is required")
        if enabled and not self.configured:
            raise ValueError("Gemini is not configured; no API request was made")
        policy=current["usage"].get("provider_policy",{})
        if free_tier_verified is not None:
            if type(free_tier_verified) is not bool or type(paid_approved) is not bool: raise ValueError("Provider attestations must be booleans")
            changed=any(policy.get(key)!=value for key,value in {"free_tier_attested":free_tier_verified,"verified_rpd":verified_rpd if free_tier_verified else None,"paid_approved":paid_approved,"model":self.model_name}.items())
            if changed and (free_tier_verified or paid_approved) and cost_approved is not True: raise ValueError("Explicit provider and processing consent required")
            if free_tier_verified and (type(verified_rpd) is not int or not 1<=verified_rpd<=100000): raise ValueError("Enter the RPD you verified in AI Studio")
            policy={"free_tier_attested":free_tier_verified,"verified_rpd":verified_rpd,"paid_approved":paid_approved}
        requested_limit=daily_limit if daily_limit is not None else current["usage"]["limit"]
        if not current["usage"].get("provider_managed_chat") and policy.get("free_tier_attested") and not policy.get("paid_approved") and requested_limit>policy["verified_rpd"]:
            raise ValueError("Application limit exceeds your attested free-tier RPD; verify your project/model in AI Studio")
        if save_history is not None:
            if save_history != current.get("save_history",True):
                self.store.change_conversation(account,"new")
            self.store.save_history_preference(account,save_history)
        if free_tier_verified is not None: self.quota.attest_provider(free_tier_verified,verified_rpd,paid_approved,self.model_name)
        if daily_limit is not None: self.quota.configure(daily_limit)
        if processing_consent is None:self.store.save_preferences(account,enabled,developer_local)
        else:self.store.save_preferences(account,enabled,developer_local,processing_consent)
        return self.settings(account)

    def send(self, account, text, context=None, mode="auto", ai_approved=False, tool_call=None, request_id=None):
        self.chat.management.validate_account(account)
        if mode not in ("auto","local","gemini"):
            raise ValueError("Unsupported chat mode")
        if not isinstance(text,str) or not 1<=len(text.strip())<=2000:
            raise ValueError("Enter 1–2000 characters")
        context=context or []
        for id in context: self.chat.management.details(account,id)
        history=conversation_context(self.store,account,text)
        ordinal=re.search(r"(?:الثانيه|الثاني|second|الاولي|الاول|first|الثالثه|الثالث|third)\b",normalize(text).replace("ة","ه"))
        if ordinal:
            position=1 if ordinal[0] in ("الثانيه","الثاني","second") else 2 if ordinal[0] in ("الثالثه","الثالث","third") else 0
            from datetime import datetime,timezone
            recent=[]
            for turn in self.store.history(account)[-20:]:
                try: age=datetime.now(timezone.utc).timestamp()-datetime.fromisoformat(turn.get("created_at","")).replace(tzinfo=timezone.utc).timestamp()
                except ValueError: continue
                if age<=600: recent.append(turn)
            latest=next((item.get("messages") for turn in reversed(recent) for item in reversed(turn["content"].get("results",[])) if "messages" in item),[])
            if len(latest)<=position:
                return self.chat.reply(account,text,"لا توجد نتيجة حديثة بهذا الترتيب. ابحث أولاً أو حدد الرسالة. No recent result at that position; search or select a message first.","local")
            context=[latest[position]["message_id"]]
        preferences=self.store.preferences(account)
        categories=self.chat.management.categories(account)
        data={"routing_version":PROMPT_VERSION,"conversation":self.store.current_conversation(account),"history":history,"text":text,"context":context,"mode":mode,"tool":tool_call,"categories":[(c["id"],c["name"]) for c in categories]}
        fingerprint=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        request_id=request_id or str(uuid.uuid4())
        saved=self.store.begin_request(account,request_id,fingerprint)
        if saved is not None:
            for result in saved.get("results",[]):
                if "preview" in result:
                    result["preview"]=self.chat.store.get(account,result["preview"]["id"])
            return {**saved,"deduplicated":True}
        try:
            if tool_call is not None and mode!="local":
                raise ValueError("Explicit developer tools require Local mode")
            if mode=="local" or tool_call is None and deterministic_intent(text) or mode=="auto" and preferences["developer_local"]:
                result=self.chat.run(account,text,context,[tool_call] if tool_call else None)
            elif mode=="auto" and (not preferences["enabled"] or preferences.get("processing_consent") is False):
                result=self.chat.reply(account,text,"Gemini isn’t connected yet. Open AI Settings to finish setup.","not_processed",ai_needed=True)
                result["reason_code"]="missing_key" if not self.configured else "consent_required" if preferences.get("model_enabled") else "model_disabled"
            else:
                if not self.configured:
                    raise ValueError("Gemini is not configured. Local searches and previews remain available; no AI request was sent.")
                if (not preferences["enabled"] or preferences.get("processing_consent") is False) and ai_approved is not True:
                    raise ValueError("Gemini requires explicit consent before a request")
                policy=self.quota.status().get("provider_policy",{})
                if (self.configuration and "policy_required" in self.configuration.status(preferences)["blocking_reasons"]) or policy.get("model")!=self.model_name or not policy.get("free_tier_attested") and not policy.get("paid_approved"):
                    result=self.chat.reply(account,text,"Gemini isn’t connected yet. Open AI Settings to verify your project policy.","not_processed",ai_needed=True)
                    self.store.finish_request(account,request_id,result)
                    return result
                plan_fingerprint=hashlib.sha256((fingerprint+":"+self.model_name).encode()).hexdigest()
                plan=self.store.cached_plan(account,plan_fingerprint)
                if mode=="gemini" and plan is None and not self.quota.status().get("provider_managed_chat") and self.quota.status()["remaining"]==0:
                    raise QuotaExhausted("Postmark application daily cap exhausted; this is not evidence of a Google provider quota limit")
                result=ToolAgent(self.chat,self.quota,self.models).run(account,text,context,history,
                    lambda:self.store.request_active(account,request_id),plan)
                cache=result.pop("cache_plan",None)
                if not self.store.request_active(account,request_id):
                    raise ValueError("Request expired or its conversation changed; no more tools executed")
                if cache is not None: self.store.save_plan(account,plan_fingerprint,cache)
                result["model"]=self.model_name
                if self.configuration:self.configuration.observe(result)
                self.store.chat(account,"user",{"text":text})
                self.store.chat(account,"assistant",result)
            self.store.finish_request(account,request_id,result)
            return result
        except Exception:
            self.store.finish_request(account,request_id,{"text":"Request failed before completion; inspect history before retrying.","results":[],"processor":"local","status":"failed"})
            raise

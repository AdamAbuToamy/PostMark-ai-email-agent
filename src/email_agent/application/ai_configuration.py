"""Consumer-owned AI setup and explicit one-request diagnostics; no SDK or SQL."""
from datetime import datetime, timezone
from typing import Protocol
from email_agent.application.model_errors import ModelFailure,classify_error,actionable

CATALOG={"gemini-3.5-flash-lite":"Gemini 3.5 Flash Lite"}

class ConfigurationStore(Protocol):
    def runtime(self)->dict: ...
    def status(self)->dict: ...
    def reserve(self,account:str,purpose:str)->bool: ...
    def select_model(self,model:str)->None: ...
    def begin_test(self,account:str,request_id:str,model:str)->dict|None: ...
    def finish_test(self,account:str,request_id:str,result:dict)->None: ...
    def retarget_policy(self,model:str)->None: ...
    def set_simple_policy(self,free:bool,paid:bool,model:str)->None: ...
    def configuration_event(self,event:str,detail:dict)->None: ...
    def record_connection(self,result:dict)->None: ...
    def record_provider_usage(self,account:str,model:str,metrics:dict)->None: ...
    def last_verified(self)->dict|None: ...
    def mark_verified(self,model:str,source:str)->None: ...
    def model_check(self,account:str,request_id:str,result:dict|None=None)->dict|None: ...

ProviderFailure=ModelFailure  # Compatibility name for the existing probe contract.

class AIConfiguration:
    RECOMMENDED_CAP=20  # proposed Postmark ceiling; never a claimed Google entitlement
    def __init__(self,store:ConfigurationStore,configured:bool,default_model:str,probe):
        self.store,self.configured,self.default_model,self.probe=store,configured,default_model,probe

    @property
    def model(self):return self.store.runtime()["model"] or self.default_model

    def status(self,preferences):
        usage=self.store.status();policy=usage["provider_policy"];model=self.model
        eligible=not self.store.runtime().get("policy_recheck") and policy.get("model")==model and (policy.get("free_tier_attested") or policy.get("paid_approved"))
        last=self.store.runtime()["connection"]
        if last and (last.get("model")!=model or usage.get("provider_managed_chat") and last.get("state")=="application_cap_reached"):last=None
        reasons=[]
        if not self.configured:reasons.append("missing_key")
        if not preferences.get("model_enabled",preferences["enabled"]):reasons.append("model_disabled")
        if not preferences.get("processing_consent",preferences["enabled"]):reasons.append("consent_required")
        if not eligible:reasons.append("policy_required")
        if not usage.get("provider_managed_chat") and usage["remaining"]==0:reasons.append("application_cap_reached")
        if usage.get("chat_safety_ceiling_reached"):reasons.append("internal_safety_ceiling")
        if usage.get("short_term_throttled"):reasons.append("short_term_throttle")
        state=reasons[0] if reasons else last["state"] if last else "test_required"
        return {"state":state,"blocking_reasons":reasons,"last_test":last,"model":model,"display_name":CATALOG.get(model,model),
            "plan":"Paid" if eligible and policy.get("paid_approved") else "Free" if eligible else "Unverified",
            "key_configured":self.configured,"recommended_application_cap":self.RECOMMENDED_CAP,
            "project_limits_url":"https://aistudio.google.com/usage?tab=rate-limit",
            "project_help":"Select the project owning this configured key and the exact model in AI Studio. Postmark cannot infer your project or quota from the key.",
            "last_verified":self.store.last_verified(),"models":[{"id":id,"name":name,"provider":"Google Gemini","free_tier_eligibility":"user_attested" if policy.get("model")==id and policy.get("free_tier_attested") else "unknown","availability":"Project availability not checked","billing":"Separate approval required unless verified free-only"} for id,name in {**CATALOG,**({model:CATALOG.get(model,model)} if model!="gemini-3.8-flash" else {})}.items()]}

    def change_model(self,model,approved):
        if approved is not True or not __import__("re").fullmatch(r"gemini-[a-zA-Z0-9][a-zA-Z0-9._-]{0,92}",model):raise ValueError("Confirm a valid model identifier; availability requires an explicit provider check")
        if model!=self.model:
            self.store.select_model(model)
            if self.store.status().get("provider_managed_chat"):self.store.retarget_policy(model)
        return {"model":self.model,"policy_recheck_required":not self.store.status().get("provider_managed_chat",False)}

    def test(self,account,preferences,approved,request_id):
        if approved is not True:raise ValueError("Explicit consent to exactly one bounded Gemini connection request is required")
        import re
        if not isinstance(request_id,str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}",request_id):raise ValueError("A unique bounded test identifier is required")
        saved=self.store.begin_test(account,request_id,self.model)
        if saved is not None:return {**saved,"deduplicated":True}
        status=self.status(preferences)
        result={"model":self.model,"state":status["state"],"request_count":0,"response":None,"error":None,"tested_at":datetime.now(timezone.utc).isoformat()}
        try:
            if status["blocking_reasons"]:
                result["error"]="Please wait briefly, or review an optional safety stop you enabled under Advanced. No provider request was sent." if any(reason in status["blocking_reasons"] for reason in ("short_term_throttle","internal_safety_ceiling")) else "Finish AI setup first. No provider request was sent."
            elif not self.store.reserve(account,"connection-test"):
                result.update(state="short_term_throttle" if self.store.status().get("provider_managed_chat") else "application_cap_reached",error="Please wait briefly before trying again. Check any optional internal safety ceiling in Advanced; local tools still work.")
            else:
                result["request_count"]=1
                try:
                    response=self.probe(self.model)
                    if isinstance(response,dict):
                        metrics=response.get('usage')
                        if isinstance(metrics,dict):self.store.record_provider_usage(account,self.model,metrics)
                        response=response.get('text')
                    if not isinstance(response,str) or not response.strip():raise ProviderFailure("model_request_failed","Provider returned an empty response")
                    result.update(state="connected",response=response[:2000])
                except ProviderFailure as exc:result.update(state=exc.state,error=exc.detail,provider_code=exc.code,diagnostic=exc.diagnostic())
                except Exception as exc:
                    failure=classify_error(exc);result.update(state=failure.state,error=actionable(failure),diagnostic=failure.diagnostic(),provider_code=failure.code)
        finally:self.store.finish_test(account,request_id,result)
        return result

    def observe(self,result):
        if not result.get("agent_requests"):return
        if result.get("status")!="failed" and result.get("processor")=="gemini":self.store.mark_verified(self.model,"chat_request")
        self.store.record_connection({"model":self.model,"state":result.get("reason_code") or ("model_request_failed" if result.get("status")=="failed" else "connected"),"request_count":result["agent_requests"],"tested_at":datetime.now(timezone.utc).isoformat(),"source":"chat_request","diagnostic":result.get("diagnostic")})

    def setup(self,assistant,account,plan,processing_consent,verified_minimum,paid_approved):
        if processing_consent is not True:raise ValueError("Confirm what will be shared with Gemini before connecting")
        if plan not in ("free","paid"):raise ValueError("Choose Free only or separately approved Paid")
        if plan=="free" and verified_minimum is not True:raise ValueError("Check the exact project/model in AI Studio; do not guess its quota. Advanced settings supports a smaller verified quota.")
        if plan=="paid" and paid_approved is not True:raise ValueError("Paid requests require separate explicit authorization")
        current=self.store.status();policy=current["provider_policy"]
        verified=max(self.RECOMMENDED_CAP,policy.get("verified_rpd") or 0) if policy.get("model")==self.model else self.RECOMMENDED_CAP
        limit=max(self.RECOMMENDED_CAP,current["limit"])
        if plan=="free":limit=min(limit,verified)
        assistant.configure(account,True,False,limit,True,free_tier_verified=plan=="free",verified_rpd=verified if plan=="free" else None,paid_approved=plan=="paid",processing_consent=True)
        self.store.configuration_event("guided_setup",{"model":self.model,"plan":plan,"application_limit":limit,"quota_source":"user_confirmed_lower_bound" if plan=="free" else "paid_explicitly_approved","previous_verified_rpd":policy.get("verified_rpd")})
        return assistant.settings(account)

    def simple_setup(self,assistant,account,enabled,plan,free_confirmed,paid_approved,processing_consent,save_history):
        if enabled and not self.configured:raise ValueError("Configure a Gemini key privately first")
        if enabled and processing_consent is not True:raise ValueError("Allow Gemini processing once before enabling AI")
        if plan not in ("free","paid"):raise ValueError("Choose Free only or Paid")
        if enabled and plan=="free" and free_confirmed is not True:raise ValueError("Confirm this Google project is free with billing disabled")
        if enabled and plan=="paid" and paid_approved is not True:raise ValueError("Paid requests need separate explicit authorization")
        preferences=assistant.store.preferences(account)
        if save_history!=preferences["save_history"]:
            assistant.store.change_conversation(account,"new")
            assistant.store.save_history_preference(account,save_history)
        if (plan=="free" and free_confirmed) or (plan=="paid" and paid_approved):self.store.set_simple_policy(plan=="free",plan=="paid",self.model)
        assistant.store.save_preferences(account,enabled,preferences["developer_local"],processing_consent)
        return assistant.settings(account)

    def validate_model(self,account,approved,request_id,list_models):
        if approved is not True:raise ValueError("Confirm up to three provider model-list metadata requests; no generation")
        if not self.configured:raise ValueError("Configure the private API key first")
        if not __import__('re').fullmatch(r'[a-zA-Z0-9_-]{1,100}',request_id):raise ValueError("A bounded unique request ID is required")
        saved=self.store.model_check(account,request_id)
        if saved is not None:return {**saved,'deduplicated':True}
        selected=self.model;result={'model':selected,'state':'not_checked','generation_requests':0}
        try:
            data=list_models(lambda:self.store.reserve(account,'chat-model-validation'))
            found=next((item for item in data['models'] if item['id']==selected),None)
            result.update(pages=data['pages'],complete=data['complete'],supported_models=[item['id'] for item in data['models'] if 'generateContent' in item['actions']])
            result['state']='generate_content_supported' if found and 'generateContent' in found['actions'] else 'generate_content_unsupported' if found else 'model_unavailable' if data['complete'] else 'not_found_in_bounded_scan'
            result['reason']=('The selected model is absent from this key/project model list. Choose a listed model or return to the last verified working model. ' if result['state']=='model_unavailable' else 'This listed model does not support generateContent; choose a compatible listed model. ' if result['state']=='generate_content_unsupported' else '')+'Provider model-list metadata only; this does not verify structured generation, free billing or successful inference.'
        except Exception as exc:
            failure=classify_error(exc);result.update(state=failure.state,reason=actionable(failure),diagnostic=failure.diagnostic())
        self.store.model_check(account,request_id,result)
        self.store.configuration_event('model_metadata_check',{'model':selected,'state':result['state']})
        return result

    def return_to_verified(self,approved):
        if approved is not True:raise ValueError("Confirm return to the named verified model")
        saved=self.store.last_verified()
        if not saved:raise ValueError("No verified working model is recorded")
        return self.change_model(saved['model'],True)

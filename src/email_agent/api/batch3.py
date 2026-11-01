"""Composition and transport contracts for Chat, approvals and review."""
import os
from pathlib import Path
from contextlib import asynccontextmanager, contextmanager
from typing import Literal
from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from email_agent.api.message_management import authorize_local_change
from email_agent.api.cleanup import register_cleanup_routes
from email_agent.cleanup import build_cleanup
from email_agent.application.mail_management import assess
from email_agent.infrastructure.gmail.request_policy import sync_lock,SyncBusyError
from email_agent.application.unsubscribe import Unsubscribe
from email_agent.application.browser_unsubscribe import BrowserUnsubscribe
from email_agent.infrastructure.gmail.unsubscribe_methods import GmailUnsubscribeMethods
from email_agent.infrastructure.http.website_browser import PlaywrightUnsubscribeBrowser
from email_agent.application.manual_subscription import ManualSubscriptions
from email_agent.infrastructure.http.dkim_unsubscribe import DKIMUnsubscribeVerifier
from email_agent.infrastructure.http.safe_unsubscribe import post_verified_one_click, public_destination
from email_agent.application.review_messages import ReviewMessages
from email_agent.application.chat import Chat
from email_agent.application.ai_configuration import AIConfiguration
from email_agent.application.chat_assistant import ChatAssistant, QuotaExhausted
from email_agent.application.gmail_actions import GmailActions
from email_agent.application.message_management import MessageManagement
from email_agent.application.subscription_tracking import tracking, include_saved_lists
from email_agent.application.subscriptions import observed_subscriptions, RefreshSubscriptions
from email_agent.infrastructure.gmail.capabilities import GmailListHeaderReader
from email_agent.infrastructure.ai.usage import SQLiteAIQuota
from email_agent.infrastructure.ai.gemini_model import GeminiTextModel
from email_agent.infrastructure.database.action_store import SQLiteActionStore
from email_agent.infrastructure.database.chat_sessions import ChatSessionPool
from email_agent.intelligence import build_classifier


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["trash", "restore"]
    message_ids: list[str] = Field(min_length=1, max_length=20)
    category_id: str | None = None
    individual_selection: StrictBool = False


class HistoryPreference(BaseModel):
    model_config=ConfigDict(extra="forbid")
    save_history: StrictBool


class Approval(BaseModel):
    approved: StrictBool = False


class SubscriptionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscription_ids: list[str] = Field(min_length=1, max_length=5)


class SubscriptionApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: StrictBool = False
    approved_targets: list[str] = Field(min_length=1, max_length=5)
    approved_third_party_targets: list[str] = Field(default_factory=list,max_length=5)


class ManualMethodRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    method_index: StrictInt = Field(ge=0,le=20)
    approved: StrictBool = False


class ManualHandledRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    approved: StrictBool = False
    note: str = Field(min_length=1,max_length=500)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    mode: Literal["auto", "local", "gemini"] = "auto"
    ai_approved: StrictBool = False
    context_ids: list[str] = Field(default_factory=list, max_length=20)
    tool_call: dict | None = None
    request_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,100}$")


class ChatSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool = False
    developer_local: StrictBool = False
    daily_limit: StrictInt | None = Field(default=None, ge=0, le=100)
    cost_approved: StrictBool = False
    save_history: StrictBool | None = None
    free_tier_verified: StrictBool | None = None
    verified_rpd: StrictInt | None = Field(default=None,ge=1,le=100000)
    paid_approved: StrictBool = False
    processing_consent: StrictBool | None = None


class SimpleAIRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    enabled: StrictBool=False
    plan: Literal["free","paid"]="free"
    free_project_confirmed: StrictBool=False
    paid_approved: StrictBool=False
    processing_consent: StrictBool=False
    save_history: StrictBool=True


class ChatCeilingRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    limit: StrictInt | None=Field(default=None,ge=1,le=10000)
    approved: StrictBool=False


class CancellationRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    subscription_id: str=Field(min_length=1,max_length=100)
    parent_action_id: str | None=Field(default=None,max_length=100)


class BrowserResumeRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    submit_recipient: StrictBool=False
    allow_all: StrictBool=False
    topic_ids: list[str]=Field(default_factory=list,max_length=30)
    inspect_only: StrictBool=False


class FlowContinueRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    approved: StrictBool=False
    allow_all: StrictBool=False
    topic_ids: list[str]=Field(default_factory=list,max_length=30)
    change_destination: StrictBool=False

class ManualPageRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    action_id: str|None=Field(default=None,max_length=100)

class AISetupRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    plan: Literal["free","paid"]="free"
    processing_consent: StrictBool=False
    verified_minimum: StrictBool=False
    paid_approved: StrictBool=False


class ConnectionTestRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    approved: StrictBool=False
    request_id: str=Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")


class ModelChoiceRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    model: str=Field(max_length=100)
    approved: StrictBool=False


class ConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["new","clear","clear_all","select"]
    confirmed: StrictBool = False
    conversation_id: str | None = Field(default=None,max_length=100)


class ClassificationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str = Field(max_length=4000)
    decision: Literal["assigned", "approved", "rejected", "reviewed"]
    category_id: str | None = None
    application_ref: str = Field(default="", max_length=200)


class ReviewDecision(BaseModel):
    decision: Literal["dismissed", "confirmed_for_manual_action"]
    reason: str = Field(min_length=1, max_length=500)


def register_batch3_routes(app, messages, root, gmail, model_factory=None, unsubscribe_verifier=None, unsubscribe_sender=None, manual_validator=None, unsubscribe_browser=None, unsubscribe_reader=None, unsubscribe_recipient=None):
    classifier = build_classifier(messages, root / "data" / "categories.db")
    app.state.message_classifier = classifier
    assignments = classifier.assignments
    management = MessageManagement(messages, assignments, classifier.registry, classifier)
    store = SQLiteActionStore(messages.database_path)
    cleanup_service=build_cleanup(messages,root/"data"/"worker_state.db",gmail)
    @contextmanager
    def gmail_operation_lock(account):
        with sync_lock(account,messages.database_path.parent):
            owner=cleanup_service.store.acquire(account)
            if owner is None:raise ValueError("Account task is running; wait before confirming")
            try:yield
            finally:cleanup_service.store.release(account,owner)
    def cleanup_guard(account,id,ignore_action=None):
        message=messages.get(account,id)
        return message is not None and assess(message,assignments.message_details(account,id) or {},cleanup_service.store.policy(account)['exclusions'],(cleanup_service.store.protection(account,message,ignore_action,ignore_system=True) or cleanup_service.store.protection(account,message,ignore_action)),assignments.cleanup_evidence(account,id))['verdict']=='eligible'
    actions = GmailActions(messages, assignments, store, gmail.actions,on_restored=cleanup_service.store.keep_message,cleanup_guard=cleanup_guard,operation_lock=gmail_operation_lock)
    register_cleanup_routes(app,messages,root,gmail,actions,service=cleanup_service)
    unsubscribe = Unsubscribe(messages, management, store, unsubscribe_verifier or DKIMUnsubscribeVerifier(gmail.client), unsubscribe_sender or post_verified_one_click)
    manual=ManualSubscriptions(messages,management,store,manual_validator or public_destination)
    classification_review = ReviewMessages(assignments, management)
    chat = Chat(messages, management, assignments, store, actions)
    model_name=os.environ.get("GEMINI_MODEL","gemini-3.5-flash-lite")
    def model_for(name):return model_factory() if model_factory else GeminiTextModel(os.environ["GEMINI_API_KEY"],name)
    configured=bool(os.getenv("GEMINI_API_KEY") or model_factory is not None)
    quota=SQLiteAIQuota(root/"data"/"ai_usage.db",provider_managed_chat=True)
    def probe_model(name):
        model=model_for(name);text=model.test_connection()
        return {"text":text,"usage":getattr(model,"last_usage",{})}
    configuration=AIConfiguration(quota,configured,model_name,probe_model)
    browser=unsubscribe_browser or PlaywrightUnsubscribeBrowser(Path(__file__).resolve().parents[3],keep_sessions=True)
    cancellation=BrowserUnsubscribe(messages,management,store,unsubscribe,unsubscribe_reader or GmailUnsubscribeMethods(gmail.client),browser,browser.transport.validate,unsubscribe_recipient or (lambda account:gmail.client(account).get_profile()["emailAddress"]),validate_manual=manual_validator or public_destination)
    models=lambda:model_for(configuration.model)
    assistant=ChatAssistant(chat,store,quota,models,configured,model_name,configuration)
    from email_agent.api.organization import register_semantic_routes
    from email_agent.application.semantic_organization import SemanticOrganization
    from email_agent.infrastructure.database.organization_store import SQLiteOrganizationStore
    semantic=SemanticOrganization(messages,SQLiteOrganizationStore(messages.database_path,cleanup_service.store.management),
        assistant.settings,quota.reserve,models,quota.record_provider_usage)
    register_semantic_routes(app,semantic,messages.database_path.parent)
    from email_agent.application.semantic_organizer import SemanticOrganizer
    from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore
    from email_agent.api.semantic_organizer import register_organizer_routes
    organizer_store=SQLiteSemanticOrganizerStore(messages.database_path,root/'data'/'categories.db')
    organizer_store.initialize()
    organizer=SemanticOrganizer(organizer_store,messages,classifier,assistant.settings,quota.reserve,models,quota.record_provider_usage)
    from email_agent.infrastructure.database.automation_ai_store import AutomationAIStore
    from email_agent.infrastructure.ai.bounded_organizer import BoundedOrganizerModel
    from email_agent.application.semantic_dispatch import SemanticDispatch
    automation=AutomationAIStore(messages.database_path,messages)
    from email_agent.infrastructure.database.sqlite_history_store import SQLiteHistoryStore
    SQLiteHistoryStore(messages.database_path)
    automation.initialize()
    automation.initialize_ai()
    def bounded_factory(grant):
        holder={}
        def verify():
            if not automation.enabled(grant['account_id']):raise ValueError('Background organization paused')
            engine._free_policy(grant['account_id'],grant['model'])
        def bounded_model():return BoundedOrganizerModel(models(),automation,grant['id'],holder['id'],holder['ids'],lambda:quota.reserve(grant['account_id'],'organizer-token-count'),verify)
        engine=SemanticOrganizer(organizer_store,messages,classifier,assistant.settings,quota.reserve,bounded_model,quota.record_provider_usage,on_scope=organizer.on_scope)
        return engine,holder
    dispatcher=SemanticDispatch(automation,bounded_factory)
    from email_agent.api.hybrid_ai import register_hybrid_routes
    hybrid,hybrid_store=register_hybrid_routes(app,root,organizer,dispatcher,automation,assistant.settings,quota)
    from email_agent.api.control_center import register_control_center
    register_control_center(app,messages)
    def content_step(account):
        from email_agent.workers.fill_content import build_fill,fill_lock
        import uuid
        with fill_lock(root/'data'):
            fill=build_fill(root)
            rows=organizer_store.snapshot(account)
            targets=[(account,row['message_id']) for row in sorted(rows,key=lambda r:(r['decision']['category_id']!='job_applications',r['decision']['category_id']!='bills',r['message_id'])) if not row['content_complete']][:5]
            if not targets:return dict(states={'done':0},message='No unverified bodies in this local scope',gemini_requests=0,gmail_mutations=0)
            id='ui-content-'+uuid.uuid4().hex
            fill.store.create_fill(id,targets,10)
            return fill.run(id,5)
    register_organizer_routes(app,organizer,messages.database_path.parent,content_step)
    from email_agent.api.message_management import authorize_local_change
    @app.post('/api/organizer/{account}/automation-step')
    def automation_step(account:str,request:Request):
        authorize_local_change(request);organizer.account(account)
        from email_agent.workers.fill_content import fill_lock
        from email_agent.infrastructure.gmail.request_policy import sync_lock
        if not automation.enabled(account):return dict(processed=0,semantic={'status':'paused'})
        try:
            with fill_lock(messages.database_path.parent),sync_lock(account,messages.database_path.parent):
                local=organizer.organize_local(account,100)
                organizer_store.supersede_redundant_local_proposals(account)
            # AI has one continuous owner below; the sync timer never dispatches a second batch.
            semantic_result={'status':'continuous_worker'}
            if getattr(app.state,'mail_management',None):app.state.mail_management.refresh_changed(account)
        except (ValueError,SyncBusyError) as exc:raise HTTPException(409,str(exc)) from exc
        return dict(processed=local['processed'],semantic=semantic_result)
    app.state.semantic_organizer=organizer
    app.state.ai_configuration=configuration
    app.state.ai_quota=quota
    app.state.chat_store=store
    sessions=ChatSessionPool(store)
    prior_lifespan=app.router.lifespan_context
    @asynccontextmanager
    async def chat_lifespan(application):
        async with prior_lifespan(application) as state:
            import asyncio
            async def recurring_loop():
                import logging,time
                from email_agent.workers.analysis_worker import single_worker
                logger=logging.getLogger(__name__)
                while True:
                    try:
                        with single_worker(messages.database_path.parent/'ai_worker.lock'):
                            async def lane(provider):
                                while True:
                                    advanced=False
                                    try:
                                        for account in ('personal','university'):
                                            if not automation.enabled(account):continue
                                            choices=await asyncio.to_thread(hybrid_store.candidates,account)
                                            if not any(m.provider==provider and hybrid_store.healthy(m.provider,m.model) for m in choices):continue
                                            before=await asyncio.to_thread(hybrid_store.attempt_version,account,provider)
                                            result=await asyncio.to_thread(hybrid.batch,account,max_items=1,provider=provider)
                                            after=await asyncio.to_thread(hybrid_store.attempt_version,account,provider)
                                            advanced=advanced or after!=before or bool(result.get('accepted',0))
                                        await asyncio.sleep(0 if advanced else 10)
                                    except asyncio.CancelledError:raise
                                    except Exception:
                                        logger.exception('AI provider lane waiting: %s',provider)
                                        await asyncio.sleep(30)
                            async def maintenance():
                                while True:
                                    try:
                                        await asyncio.to_thread(app.state.provider_qualification.step)
                                        for account in ('personal','university'):
                                            if automation.enabled(account):await asyncio.to_thread(hybrid.refresh_eligibility,account)
                                            if organizer_store.recurring(account).get('enabled'):await asyncio.to_thread(organizer.recurring_step,account)
                                    except asyncio.CancelledError:raise
                                    except Exception:logger.warning('AI maintenance waiting; persisted scopes retained')
                                    await asyncio.sleep(60)
                            # One owner; independent provider lanes, one wire call per provider.
                            from email_agent.application.hybrid_ai import ACTIVE_PROVIDERS
                            async with asyncio.TaskGroup() as tasks:
                                for provider in ACTIVE_PROVIDERS:tasks.create_task(lane(provider))
                                tasks.create_task(maintenance())
                    except asyncio.CancelledError:raise
                    except Exception:
                        logger.exception('Continuous AI owner waiting; checkpoints retained')
                        await asyncio.sleep(30)
            recurring_task=asyncio.create_task(recurring_loop())
            try: yield state
            finally:
                recurring_task.cancel()
                await asyncio.gather(recurring_task,return_exceptions=True)
                sessions.close()
                if callable(getattr(browser,"close",None)):browser.close()
    app.router.lifespan_context=chat_lifespan

    def services(account,request):
        if store.preferences(account)["save_history"]: return chat,assistant,actions,store
        try: transient=sessions.get(account,request.headers.get("X-Postmark-Chat-Session"))
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
        remote=GmailActions(messages,assignments,transient,gmail.actions,on_restored=cleanup_service.store.keep_message,cleanup_guard=cleanup_guard,operation_lock=gmail_operation_lock)
        local=Chat(messages,management,assignments,transient,remote)
        return local,ChatAssistant(local,transient,quota,models,configured,model_name,configuration),remote,transient

    def action_store(account,request,id):
        try:store.get(account,id);return store
        except LookupError:return services(account,request)[3]

    def enrich_preview(account,value):
        if value["kind"]=="unsubscribe:one_click":
            groups={item["id"]:item for item in observed_subscriptions(messages,account)}
            for item in value["payload"]["items"]:
                group=groups.get(item["subscription_id"])
                if group:item.setdefault("manual_destinations",group["manual_destinations"])
        return value

    messages.initialize_discovery()

    def execute(account, function, *args):
        try:
            management.validate_account(account)
            return function(account, *args)
        except SyncBusyError as exc:
            raise HTTPException(409,"Account sync is running; retry after it finishes") from exc
        except QuotaExhausted as exc:
            raise HTTPException(429,str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else "Invalid operation arguments") from exc

    @app.get("/api/gmail/{account}/capabilities")
    def capabilities(account: str):
        return execute(account, gmail.capabilities)

    @app.post("/api/gmail/{account}/preview")
    def preview(account: str, payload: PreviewRequest, request: Request):
        authorize_local_change(request)
        return execute(account, actions.preview, payload.operation, payload.message_ids, payload.category_id, payload.individual_selection)

    @app.get("/api/actions/{account}")
    def action_list(account: str):
        return execute(account, store.list)

    @app.get("/api/actions/{account}/{action_id}/audit")
    def action_audit(account: str, action_id: str, request:Request):
        return execute(account, action_store(account,request,action_id).audit, action_id)

    @app.get("/api/actions/{account}/{action_id}")
    def action(account: str, action_id: str, request:Request):
        return enrich_preview(account,execute(account, action_store(account,request,action_id).get, action_id))

    @app.post("/api/gmail/{account}/actions/{action_id}/confirm")
    def confirm_remote(account: str, action_id: str, payload: Approval, request: Request):
        authorize_local_change(request)
        return execute(account, GmailActions(messages,assignments,action_store(account,request,action_id),gmail.actions,on_restored=cleanup_service.store.keep_message,cleanup_guard=cleanup_guard,operation_lock=gmail_operation_lock).confirm, action_id, payload.approved)

    @app.post("/api/chat/{account}/actions/{action_id}/confirm")
    def confirm_local(account: str, action_id: str, payload: Approval, request: Request):
        authorize_local_change(request)
        return execute(account, services(account,request)[0].confirm, action_id, payload.approved)

    @app.post("/api/chat/{account}/deactivate")
    def deactivate_chat(account:str,request:Request):
        authorize_local_change(request)
        return execute(account,services(account,request)[3].cancel_chat_approvals)

    @app.get("/api/chat/{account}/history")
    def history(account: str,request:Request):
        selected=services(account,request)[3]
        rows = execute(account, selected.history)
        for row in rows:
            for result in row["content"].get("results", []):
                if "preview" in result:
                    result["preview"] = selected.get(account, result["preview"]["id"])
                    result["status"] = result["preview"]["status"]
        return rows

    @app.post("/api/chat/{account}")
    def send(account: str, payload: ChatRequest, request: Request):
        authorize_local_change(request)
        if account not in ("personal", "university"):
            raise HTTPException(400, "Unknown account")
        return execute(account,services(account,request)[1].send,payload.text,payload.context_ids,payload.mode,payload.ai_approved,payload.tool_call,payload.request_id)

    @app.get("/api/chat/{account}/messages")
    def chat_messages(account: str,search: str=Query("",max_length=200),category: str | None=None,
                      state: Literal["new","done","all"]="all",label: Literal["SPAM","TRASH","IMPORTANT"] | None=None,
                      classification: Literal["unclassified"] | None=None,offset: int=Query(0,ge=0,le=1000000),limit: int=Query(50,ge=1,le=100)):
        args={"search":search,"state":state,"offset":offset,"limit":limit}
        args.update({k:v for k,v in {"category":category,"label":label,"classification":classification}.items() if v is not None})
        return execute(account,lambda selected:chat.execute(selected,"search_messages",args))

    @app.get("/api/chat/{account}/settings")
    def chat_settings(account: str):
        return execute(account,assistant.settings)

    @app.post('/api/chat/{account}/history-preference')
    def history_preference(account:str,payload:HistoryPreference,request:Request):
        authorize_local_change(request)
        return execute(account,assistant.history_preference,payload.save_history)

    @app.post("/api/chat/{account}/settings")
    def save_chat_settings(account: str,payload: ChatSettingsRequest,request: Request):
        authorize_local_change(request)
        if payload.save_history is not None and payload.save_history!=store.preferences(account)["save_history"]:
            execute(account,lambda selected:sessions.end(selected,request.headers.get("X-Postmark-Chat-Session")))
        return execute(account,assistant.configure,payload.enabled,payload.developer_local,payload.daily_limit,payload.cost_approved,payload.save_history,payload.free_tier_verified,payload.verified_rpd,payload.paid_approved,payload.processing_consent)

    @app.post("/api/chat/{account}/simple-settings")
    def simple_ai(account:str,payload:SimpleAIRequest,request:Request):
        authorize_local_change(request)
        return execute(account,lambda selected:configuration.simple_setup(assistant,selected,payload.enabled,payload.plan,payload.free_project_confirmed,payload.paid_approved,payload.processing_consent,payload.save_history))

    @app.post("/api/chat/{account}/safety-ceiling")
    def chat_ceiling(account:str,payload:ChatCeilingRequest,request:Request):
        authorize_local_change(request)
        execute(account,lambda selected:quota.set_chat_ceiling(payload.limit,payload.approved))
        return execute(account,assistant.settings)

    @app.get("/api/chat/{account}/provider-diagnostics")
    def provider_diagnostics(account:str):return execute(account,quota.provider_usage)

    @app.post("/api/subscriptions/{account}/cancellation")
    def cancellation_plan(account:str,payload:CancellationRequest,request:Request):
        authorize_local_change(request)
        return execute(account,cancellation.describe,payload.subscription_id,payload.parent_action_id)

    @app.post("/api/subscriptions/{account}/browser/{action_id}/confirm-flow")
    def cancellation_flow(account:str,action_id:str,payload:Approval,request:Request):
        authorize_local_change(request)
        return execute(account,cancellation.authorize_flow,action_id,payload.approved)

    @app.post("/api/subscriptions/{account}/browser/{action_id}/dismiss-flow")
    def cancellation_dismiss_flow(account:str,action_id:str,payload:Approval,request:Request):
        authorize_local_change(request)
        if payload.approved is not True:raise HTTPException(400,"Confirm dismissal of this local workflow")
        return execute(account,cancellation.dismiss_flow,action_id)

    @app.post("/api/subscriptions/{account}/browser/{action_id}/continue-flow")
    def cancellation_continue_flow(account:str,action_id:str,payload:FlowContinueRequest,request:Request):
        authorize_local_change(request)
        return execute(account,cancellation.continue_flow,action_id,payload.approved,payload.allow_all,payload.topic_ids,payload.change_destination)

    @app.post("/api/subscriptions/{account}/{subscription_id}/manual-page")
    def cancellation_manual_page(account:str,subscription_id:str,payload:ManualPageRequest,request:Request):
        authorize_local_change(request)
        return execute(account,cancellation.manual_page,subscription_id,payload.action_id)

    @app.post("/api/subscriptions/{account}/browser/{action_id}/confirm")
    def cancellation_confirm(account:str,action_id:str,payload:Approval,request:Request):
        authorize_local_change(request)
        return execute(account,cancellation.confirm,action_id,payload.approved)

    @app.post("/api/subscriptions/{account}/browser/{action_id}/resume-preview")
    def cancellation_resume(account:str,action_id:str,payload:BrowserResumeRequest,request:Request):
        authorize_local_change(request)
        return execute(account,cancellation.prepare_resume,action_id,payload.submit_recipient,payload.allow_all,payload.topic_ids,payload.inspect_only)

    @app.post("/api/subscriptions/{account}/browser/{action_id}/recovery-preview")
    def cancellation_recovery(account:str,action_id:str,payload:Approval,request:Request):
        authorize_local_change(request)
        if payload.approved is not True:raise HTTPException(400,"Confirm review of a pre-submission restart")
        return execute(account,cancellation.recover_before_submission,action_id)

    @app.post("/api/subscriptions/{account}/browser/{action_id}/close-session")
    def cancellation_close(account:str,action_id:str,payload:Approval,request:Request):
        authorize_local_change(request)
        if payload.approved is not True:raise HTTPException(400,"Confirm ending this isolated session locally")
        return execute(account,cancellation.close_session,action_id)

    @app.post("/api/chat/{account}/validate-model")
    def validate_model(account:str,payload:ConnectionTestRequest,request:Request):
        authorize_local_change(request)
        return execute(account,lambda selected:configuration.validate_model(selected,payload.approved,payload.request_id,lambda reserve:model_for(configuration.model).list_models(reserve)))

    @app.post("/api/chat/{account}/return-verified-model")
    def return_model(account:str,payload:ModelChoiceRequest,request:Request):
        authorize_local_change(request)
        if (quota.last_verified() or {}).get("model")!=payload.model:raise HTTPException(400,"Verified model changed; review the named target again")
        execute(account,lambda selected:configuration.return_to_verified(payload.approved))
        return execute(account,assistant.settings)

    @app.post("/api/chat/{account}/setup")
    def ai_setup(account:str,payload:AISetupRequest,request:Request):
        authorize_local_change(request)
        return execute(account,lambda selected:configuration.setup(assistant,selected,payload.plan,payload.processing_consent,payload.verified_minimum,payload.paid_approved))

    @app.post("/api/chat/{account}/connection-test")
    def connection_test(account:str,payload:ConnectionTestRequest,request:Request):
        authorize_local_change(request)
        return execute(account,lambda selected:configuration.test(selected,store.preferences(selected),payload.approved,payload.request_id))

    @app.post("/api/chat/{account}/model")
    def choose_model(account:str,payload:ModelChoiceRequest,request:Request):
        authorize_local_change(request)
        execute(account,lambda selected:configuration.change_model(payload.model,payload.approved))
        return execute(account,assistant.settings)

    @app.post("/api/actions/{account}/{action_id}/reject")
    def reject_action(account:str,action_id:str,payload:Approval,request:Request):
        authorize_local_change(request)
        if payload.approved is not True:raise HTTPException(400,"Explicit rejection required")
        return execute(account,action_store(account,request,action_id).reject,action_id)

    @app.get("/api/chat/{account}/conversations")
    def conversations(account: str,request:Request):
        return execute(account,services(account,request)[3].sessions)

    @app.post("/api/chat/{account}/conversations")
    def conversation_action(account: str,payload: ConversationRequest,request: Request):
        authorize_local_change(request)
        if payload.operation in ("clear","clear_all") and payload.confirmed is not True:
            raise HTTPException(400,"Confirm deletion of local conversation history; Gmail is never affected")
        selected=services(account,request)[3]
        if payload.operation=="clear_all" and selected is not store:
            execute(account,store.change_conversation,"clear_all")
        return execute(account,selected.change_conversation,payload.operation,payload.conversation_id)

    @app.post("/api/chat/{account}/session/end")
    def end_chat_session(account:str,request:Request):
        authorize_local_change(request)
        return execute(account,lambda selected:sessions.end(selected,request.headers.get("X-Postmark-Chat-Session")))

    @app.get("/api/classification-review/{account}")
    def classification_queue(account: str, after: str = Query("", max_length=256), attention: Literal["all","required","untouched"]="all"):
        return execute(account, classification_review.listing, after, attention)

    @app.post("/api/classification-review/{account}/{message_id}/decision")
    def classification_decision(account: str, message_id: str, payload: ClassificationDecision, request: Request):
        authorize_local_change(request)
        return execute(account, classification_review.decide, message_id, payload.fingerprint, payload.decision, payload.category_id, payload.application_ref)

    @app.get("/api/review/{account}")
    def review(account: str, after: int = Query(0, ge=0), queue: Literal["all","spam","trash","history"] = "all"):
        if account not in ("personal", "university"):
            raise HTTPException(400, "Unknown account")
        rows = assignments.reviews(account, after, 100, queue)
        return {"counts": assignments.review_counts(account), "coverage": assignments.label_coverage(account), "items": [{**row, "message": management.details(account, row["message_id"])} for row in rows if messages.get(account, row["message_id"])], "next_after": rows[-1]["id"] if len(rows) == 100 else None}

    @app.post("/api/review/{account}/{review_id}/decision")
    def decide_review(account: str, review_id: int, payload: ReviewDecision, request: Request):
        authorize_local_change(request)
        execute(account, assignments.review, review_id, payload.decision, payload.reason)
        return {"status": payload.decision}

    @app.post("/api/subscriptions/{account}/preview")
    def unsubscribe_preview(account: str, payload: SubscriptionPreview, request: Request):
        authorize_local_change(request)
        return execute(account, unsubscribe.preview, payload.subscription_ids)

    @app.post("/api/subscriptions/{account}/actions/{action_id}/confirm")
    def unsubscribe_confirm(account: str, action_id: str, payload: SubscriptionApproval, request: Request):
        authorize_local_change(request)
        return execute(account, unsubscribe.confirm, action_id, payload.approved, payload.approved_targets, payload.approved_third_party_targets)

    @app.get("/api/subscriptions/{account}")
    def list_subscriptions(account: str):
        if account not in ("personal", "university"):
            raise HTTPException(400, "Unknown account")
        groups = observed_subscriptions(messages,account)
        states = store.subscription_states(account)
        groups = include_saved_lists(store,account,groups,states)
        known={g['id'] for g in groups}
        groups += [g for id,g in store.manual_snapshots(account).items() if id not in known]
        verified = store.verified_subscriptions(account)
        manual_states=store.manual_states(account)
        checks=store.subscription_checks(account)
        for group in groups:
            group["manual_state"]=manual_states.get(group["id"])
            group["display_status"]="Manual Action" if group["manual_destinations"] else "Method not checked"
            if group["one_click_candidate"]: group["display_status"]="Unsubscribe option"
            if group["id"] in verified and verified[group["id"]].get("policy_version")=="rfc8058-human-third-party-v1" and verified[group["id"]]["message_id"] in group["message_ids"]:
                group["display_status"]="Verified"
                group["verification"]="dkim_verified"
                group["automatic_available"]=True
                group["requires_third_party_ack"]=verified[group["id"]]["alignment"]!="exact"
            check=checks.get(group["id"])
            if check and not check["eligible"]:
                group["verification"]="ineligible"
                group["automatic_available"]=False
                group["display_status"]="Manual Action" if group["manual_destinations"] else "Method not checked"
                group["reason"]=check["reason"]
            state = states.get(group["id"])
            if state:
                group["automatic_available"]=False
                group["display_status"]={"requested":"Request Submitted","cancelled":"Cancelled","needs_action":"Needs your action","failed":"Failed"}.get(state["status"],"Check saved result")
                group["request_state"] = state
                group["reason"] = "Request accepted; delivery cessation is not confirmed." if state["status"] == "requested" else "A prior request may have been sent; inspect the audit. No automatic repeat."
            if group.get("manual_state") and not state:group["display_status"]="Handled Locally"
        for group in groups:
            group["tracking"]=tracking(store,account,group,states.get(group["id"]))
        return {"items": groups, "coverage": "Local promotional observations; header offers and unverified senders are separate. Up to 1000 messages are grouped; historical counts may be incomplete", "progress": messages.discovery_status(account), "one_click_available": any(group["automatic_available"] for group in groups), "verification_required": True, "verification_limit": 5}

    @app.post("/api/subscriptions/{account}/{subscription_id}/manual-method")
    def manual_method(account:str,subscription_id:str,payload:ManualMethodRequest,request:Request):
        authorize_local_change(request)
        return execute(account,manual.method,subscription_id,payload.method_index,payload.approved)

    @app.post("/api/subscriptions/{account}/{subscription_id}/handled")
    def manual_handled(account:str,subscription_id:str,payload:ManualHandledRequest,request:Request):
        authorize_local_change(request)
        return execute(account,manual.handled,subscription_id,payload.approved,payload.note)

    @app.post('/api/subscriptions/{account}/{subscription_id}/manual-complete')
    def manual_complete(account:str,subscription_id:str,payload:ManualHandledRequest,request:Request):
        authorize_local_change(request)
        return execute(account,manual.complete,subscription_id,payload.approved,payload.note)

    @app.get("/api/subscriptions/{account}/{subscription_id}/manual-history")
    def manual_history(account:str,subscription_id:str):
        return execute(account,manual.audit,subscription_id)

    @app.get("/api/subscriptions/{account}/{subscription_id}/history")
    def subscription_history(account:str,subscription_id:str):
        if subscription_id not in store.subscription_states(account):
            execute(account,manual.group,subscription_id)
        return execute(account,store.subscription_history,subscription_id)

    @app.post("/api/subscriptions/{account}/refresh")
    def refresh_subscriptions(account: str, request: Request):
        authorize_local_change(request)
        if account not in ("personal", "university"):
            raise HTTPException(400, "Unknown account")
        if not messages.discovery_candidates(account,1):
            return {"items":[],"limit":20,"progress":messages.discovery_status(account)}
        class LazyMetadata:
            def __init__(self):
                self.reader=None
            def read_list_headers(self,id):
                if self.reader is None:
                    self.reader=GmailListHeaderReader(gmail.client(account))
                return self.reader.read_list_headers(id)
        service=RefreshSubscriptions(messages,LazyMetadata())
        return execute(account,lambda selected:service.execute(selected))

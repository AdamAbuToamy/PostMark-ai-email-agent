"""Provider-neutral semantic routing. Email/model text never grants permission."""
from dataclasses import dataclass, field
import json
import time
import re
from typing import Protocol
from copy import deepcopy
from email_agent.application.model_errors import ModelFailure
from email_agent.application.semantic_mail import validate_batch

# Keep historical identifiers and secrets readable; retired routes cannot execute.
ACTIVE_PROVIDERS=('ollama','gemini','groq','mistral','zai')
SETUP_PROVIDERS=ACTIVE_PROVIDERS
PROVIDERS=('ollama','gemini','groq','openrouter','cerebras','mistral','zai','cloudflare','sambanova','openai','nvidia','siliconflow')


def organization_schema(data,schema):
    """Bind identifiers to this exact scope, never repair invented identities."""
    bound=deepcopy(schema);aliases=[m['id'] for m in data.get('messages',[])];categories=[c['id'] for c in data.get('catalog',[])]
    bound['properties']['frames'].update(minItems=len(aliases),maxItems=len(aliases))
    frame=bound['properties']['frames']['items']['properties']
    frame['id']={'type':'string','enum':aliases,'description':'EXACT input message id, never an invented frame id'}
    frame['category_id']={'type':['string','null'],'enum':[*categories,None]}
    frame['related_ids']['items']={'type':'string','enum':aliases};frame['related_ids']['uniqueItems']=True
    changes=bound['properties']['changes']
    if len(aliases)<2:
        changes['maxItems']=0
        changes['items']={'type':'object','properties':{},'required':[],'additionalProperties':False}
    else:
        fields=changes['items']['properties']
        fields['sources']['items']={'type':'string','enum':categories}
        fields['message_ids']['items']={'type':'string','enum':aliases};fields['message_ids']['uniqueItems']=True
        fields['dimension']['pattern']='^[a-z][a-z0-9_]{0,31}$'
    return bound


@dataclass(frozen=True)
class ModelCapability:
    provider:str
    model:str
    context:int
    structured:bool=False
    generation:bool=True
    tools:bool=False
    free:bool=False
    validated:bool=False
    quality:dict[str,float]=field(default_factory=dict)
    workflow_quality:dict[str,float]=field(default_factory=dict)
    latency:float=60
    fingerprint:str=''
    paid_authorized:bool=False
    output_mode:str='json_schema'
    reasoning:bool=False
    task_quality:dict=field(default_factory=dict)
    evaluation_version:str=''


@dataclass(frozen=True)
class InferenceResult:
    text:str
    provider:str
    model:str
    usage:dict
    seconds:float


class AIProvider(Protocol):
    def discover(self)->list[dict]: ...
    def analyze(self,model:ModelCapability,system:str,data:dict,schema:dict,timeout:float=85)->InferenceResult: ...


class InferenceBudgetManager(Protocol):
    def reserve(self,account:str,model:ModelCapability,key:str,ids:list[str],input_bound:int,output_bound:int)->int: ...
    def finish(self,id:int,status:str,result:InferenceResult|None=None,error:ModelFailure|None=None)->None: ...


class ModelHealthMonitor(Protocol):
    def healthy(self,provider:str,model:str)->bool: ...


class ModelRegistry(InferenceBudgetManager,ModelHealthMonitor,Protocol):
    def candidates(self,account:str)->list[ModelCapability]: ...
    def saved_result(self,account:str,model:ModelCapability,key:str)->InferenceResult|None: ...
    def replay_candidates(self,account:str)->list[ModelCapability]: ...
    def model_load(self,account:str)->dict: ...


def message_language(body):
    # Script detection chooses evaluated coverage, never substitutes for meaning.
    counts={'ar':sum('\u0600'<=c<='\u06ff' for c in body),'he':sum('\u0590'<=c<='\u05ff' for c in body),'en':sum(c.isascii() and c.isalpha() for c in body)}
    nonlatin=sum(c.isalpha() and not c.isascii() and not '\u0590'<=c<='\u06ff' for c in body)
    if nonlatin>max(counts.values(),default=0):return 'other'
    return max(counts,key=counts.get)


def message_languages(body):
    body=re.sub(r'\[(?:link|address|redacted|number)\]','',body)
    languages=[]
    if sum('\u0600'<=c<='\u06ff' for c in body)>=4:languages.append('ar')
    if sum('\u0590'<=c<='\u05ff' for c in body)>=4:languages.append('he')
    if sum(c.isascii() and c.isalpha() for c in body)>=12:languages.append('en')
    if sum(c.isalpha() and not c.isascii() and not '\u0590'<=c<='\u06ff' for c in body)>=4:languages.append('other')
    return tuple(languages) or (message_language(body),)


class ModelRouter:
    def __init__(self,registry:ModelRegistry):self.registry=registry

    def select(self,account,language,input_bound,preference='auto',complexity='routine',excluded=()):
        languages=language if isinstance(language,(tuple,list)) else (language,)
        models=[m for m in self.registry.candidates(account) if (m.provider,m.model) not in excluded
            and m.generation and m.structured and m.validated and (m.free or m.paid_authorized)
            and all(m.quality.get(lang,0)>=.9 for lang in languages) and input_bound+2048<=m.context
            and self.registry.healthy(m.provider,m.model) and getattr(self.registry,'capacity',lambda _:True)(m.provider) and (preference!='local' or m.provider=='ollama')]
        # Measured quality wins within each task/preference class; no fixed
        # provider order or round-robin credentials substitutes for evaluation.
        load=getattr(self.registry,'model_load',lambda _:{})(account)
        if not isinstance(load,dict):load={}
        def score(m):
            local=m.provider=='ollama'
            preference_score=not local
            # Every candidate already passes consent/cost/quality gates. Auto
            # ranks measured quality, then latency, rather than forcing local.
            measured=load.get((m.provider,m.model),{})
            latency=measured.get('mean_seconds') or m.latency
            utility=min(m.quality[lang] for lang in languages)/(max(1,latency)*(1+measured.get('recent_requests',0)/4))
            return preference_score,min(m.quality[lang] for lang in languages),utility
        return sorted(models,key=score,reverse=True)


class ClassificationQualityGate:
    def accept(self,raw,summaries,rows,catalog,capability):
        from email_agent.application.semantic_mail import local_frame,SEMANTIC_VERSION
        frames,changes,issues=validate_batch(raw,summaries,{alias:local_frame(row) for alias,row in rows.items()},[c['id'] for c in catalog])
        for alias,frame in frames.items():
            summary=next(s for s in summaries if s['id']==alias)
            languages=message_languages(summary.get('excerpt','')+' '+summary.get('history_context',''))
            calibrated=min(capability.quality.get(language,0) for language in languages)
            frame['raw_model_confidence']=frame['confidence']
            frame['confidence']=min(frame['confidence'],calibrated)
            frame.update(source=capability.provider,provider=capability.provider,model=capability.model,
                model_fingerprint=capability.fingerprint,prompt_version=SEMANTIC_VERSION,
                quality_verified=capability.validated and calibrated>=.9,quality_score=calibrated)
            if not frame['quality_verified'] or not frame['input_coverage']['body_complete'] or not any(e.get('field')=='excerpt' for e in frame.get('evidence',[])):
                frame.update(understood=False,analysis_reason='model_quality_or_content_not_verified')
            elif frame['confidence']<.9:
                frame.update(understood=False,analysis_reason='cloud_escalation_required')
            if capability.task_quality and not all(capability.task_quality.get(lang,{}).get('document',0)>=.9 for lang in languages):
                baseline=local_frame(rows[alias])
                frame['message_type']=baseline.get('message_type') if baseline.get('understood') else 'unknown'
                frame.setdefault('dimensions',{})['document_type']=baseline.get('dimensions',{}).get('document_type')
                frame['document_quality_verified']=False
            workflow_score=min(capability.workflow_quality.get(language,0) for language in languages)
            frame['workflow_quality_score']=workflow_score
            if capability.task_quality:
                frame['taxonomy_quality_verified']=all(capability.task_quality.get(lang,{}).get('taxonomy',0)>=.9 for lang in languages)
            if workflow_score<.95:
                baseline=local_frame(rows[alias])
                from email_agent.application.semantic_dimensions import dimension_payload
                safe_dimensions=dimension_payload(dict(baseline,category_id=frame.get('suggested_category_id')))
                frame['dimensions']={**frame.get('dimensions',{}),'workflow_stage':safe_dimensions['workflow_stage'],'payment_status':safe_dimensions['payment_status']}
                attrs=frame.get('attributes') or {}
                if isinstance(attrs,list):attrs={a['key']:a['value'] for a in attrs}
                frame['attributes']={k:v for k,v in attrs.items() if k not in ('workflow_stage','payment_status')}
                for key in ('workflow_stage','payment_status'):
                    if safe_dimensions[key]:frame['attributes'][key]=safe_dimensions[key]
                if baseline.get('status'):
                    frame.update(status=baseline['status'],status_source='local',status_note='Current-body deterministic evidence; model workflow not qualified')
                    if frame.get('analysis_reason')=='conflicting_status_evidence':frame['analysis_reason']=None
                else:
                    promotional=set(frame.get('sensitivities',[]))=={'promotional'}
                    needs=bool(frame.get('status')) and not promotional or baseline.get('category_hint') in ('job_applications','orders') or baseline.get('message_type')=='Invoice'
                    frame.update(status=None,status_source='unknown',workflow_needs_escalation=needs)
        # Structural plans use the same grounded multi-message tool policy;
        # valid JSON alone never permits a low-quality model to act.
        if any(not f['quality_verified'] or f.get('taxonomy_quality_verified') is False for f in frames.values()):changes=[]
        return frames,changes,issues


class HybridInference:
    def __init__(self,registry:ModelRegistry,providers):
        self.registry,self.providers=registry,providers;self.router=ModelRouter(registry)

    def options(self,account,key,language,bound,preference='auto',complexity='routine',excluded=()):
        languages=language if isinstance(language,(tuple,list)) else (language,)
        replay=[m for m in self.registry.replay_candidates(account) if m.validated and m.structured and (m.provider,m.model) not in excluded and all(m.quality.get(lang,0)>=.9 for lang in languages) and self.registry.saved_result(account,m,key)]
        normal=self.router.select(account,language,bound,preference,complexity,excluded)
        return replay+[m for m in normal if (m.provider,m.model) not in {(r.provider,r.model) for r in replay}]

    def run(self,account,key,ids,system,data,schema,language,preference='auto',complexity='routine',excluded=(),budget_seconds=85,provider=None,purpose=None):
        schema=organization_schema(data,schema)
        # UTF-8 bytes conservatively bound tokens for all supported scripts.
        bound=len((system+json.dumps(data,ensure_ascii=False)+json.dumps(schema)).encode())+512
        errors=[]
        deadline=time.monotonic()+max(0,min(85,budget_seconds))
        candidates=self.options(account,key,language,bound,preference,complexity,excluded)
        if provider:candidates=[m for m in candidates if m.provider==provider]
        for model in candidates[:5]:
            saved=self.registry.saved_result(account,model,key)
            if saved:return saved,model
            remaining=deadline-time.monotonic()
            if remaining<max(1,min(model.latency,80)):continue
            try:attempt=self.registry.reserve(account,model,key,ids,bound,2048)
            except ValueError:continue
            try:
                record=getattr(self.registry,'record_route',None)
                if record:record(attempt,account,model,language,bound,excluded)
                result=self.providers(model.provider).analyze(model,system,data,schema,timeout=remaining)
                byte_size=lambda value:len(json.dumps(value,ensure_ascii=False).encode())
                messages=data.get('messages',[])
                result.usage['purpose']=purpose or ('taxonomy' if key.startswith('group:') else 'production')
                result.usage['input_components_bytes']=dict(system=len(system.encode()),current_body=sum(len(m.get('excerpt','').encode()) for m in messages),quoted_history=sum(len(m.get('history_context','').encode()) for m in messages),metadata=max(0,byte_size(messages)-sum(len(m.get('excerpt','').encode())+len(m.get('history_context','').encode()) for m in messages)),catalog=byte_size(data.get('catalog',[])),schema=byte_size(schema),tool_planning=byte_size(data.get('local_organization',{})))
                self.registry.finish(attempt,'done',result)
                return result,model
            except Exception as exc:
                from email_agent.application.model_errors import classify_error
                failure=classify_error(exc);self.registry.finish(attempt,'failed',error=failure);errors.append(failure.state)
                # One attempt per provider/model/fingerprint. A different
                # independently consented provider may handle an availability
                # failure; malformed semantics is not an availability verdict.
                if failure.state not in ('provider_rate_limited','provider_unavailable','network_unavailable','model_unavailable','authentication_failed','context_exceeded','local_memory_pressure') and failure.http_status!=400:break
            except BaseException:
                self.registry.finish(attempt,'interrupted')
                raise
        raise ModelFailure('no_eligible_model','AI analysis is paused; saved tasks resume when an authorized evaluated model is available'+(' ('+', '.join(errors)+')' if errors else ''))

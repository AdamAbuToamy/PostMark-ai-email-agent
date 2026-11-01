"""Bounded restart-safe provider qualification, independent of HTTP/SQLite/SDKs."""
from typing import Callable,Protocol
import json
from email_agent.application.hybrid_ai import AIProvider,InferenceResult,ModelCapability,organization_schema
from email_agent.application.semantic_mail import SYSTEM,SCHEMA,SEMANTIC_VERSION,digest,public_summary,local_frame,validate_batch
from email_agent.application.model_errors import ModelFailure,classify_error
from email_agent.application.qualification_rubric import CATALOG,RUBRIC_VERSION,score_frame
from email_agent.application.local_organization_agent import LocalOrganizationAgent,ORGANIZATION_PLANNING


class QualificationInferencePort(Protocol):
    def saved_result(self,account:str,model:ModelCapability,key:str)->InferenceResult|None: ...
    def synthetic_scope(self,id:str,account:str,model:ModelCapability)->None: ...
    def reserve(self,account:str,model:ModelCapability,key:str,ids:list[str],input_bound:int,output_bound:int,synthetic_scope:str|None=None)->int: ...
    def finish(self,id:int,status:str,result:InferenceResult|None=None,error:ModelFailure|None=None)->None: ...


class QualificationPort(Protocol):
    hybrid:QualificationInferencePort
    def pause(self,plan_id:str,reason:str)->None: ...
    def runnable(self)->dict|None: ...
    def claim(self,plan:dict)->dict|None: ...
    def state(self,plan_id:str)->dict: ...
    def finish_job(self,job:dict,result:dict,attempt_id:int|None)->None: ...
    def fail_job(self,job:dict,failure:ModelFailure,attempt_id:int|None)->None: ...
    def defer_job(self,job:dict,reason:str)->None: ...
    def certify(self,plan:dict,job:dict)->None: ...
    def discovery(self,plan:dict,provider:str,adapter)->None: ...
    def admit(self,job:dict,bound:int)->None: ...
    def bind(self,job:dict,attempt:int)->None: ...


def request_data(cases):
    rows={};summaries=[]
    for i,case in enumerate(cases):
        row=dict(account_id='evaluation',message_id=case['id'],subject=case['subject'],sender='independent@synthetic.invalid',body=case['body'],headers={'thread-id':'synthetic-'+case['id']},labels=[],internal_date_ms=0,content_complete=True,decision={})
        alias='m'+str(i);rows[alias]=row;summaries.append(public_summary(row,alias,'full'))
    data=dict(messages=summaries,catalog=CATALOG,organization_scope=dict(synthetic=True,mailbox_access=False,local_tools_only=True),local_organization=dict(tools=sorted(LocalOrganizationAgent.READS|LocalOrganizationAgent.WRITES),planning=ORGANIZATION_PLANNING))
    schema=organization_schema(data,SCHEMA)
    bound=len((SYSTEM+json.dumps(data,ensure_ascii=False)+json.dumps(schema)).encode())+512
    return rows,summaries,data,schema,bound


def qualification_contract(cases):
    # Hash the exact supplied public catalog/data/schema, not merely version names.
    return digest([SEMANTIC_VERSION,SYSTEM,SCHEMA,RUBRIC_VERSION,cases,[request_data(cases[i:i+3])[2:4] for i in range(0,len(cases),3)]])


def evaluate_batch(cases,response):
    rows,summaries,data,schema,bound=request_data(cases)
    frames,changes,issues=validate_batch(response.text,summaries,{a:local_frame(r) for a,r in rows.items()},[c['id'] for c in CATALOG])
    raw=json.loads(response.text);original={f.get('id'):f for f in raw.get('frames',[]) if isinstance(f,dict)}
    cluster=cases[0].get('cluster') if all(c.get('cluster')==cases[0].get('cluster') for c in cases) else None
    taxonomy=None
    if cluster:
        terms=cases[0]['gold'].get('category_name_terms',[])
        taxonomy=any(c['action']=='create' and set(c['message_ids'])==set(rows) and any(t in c['name'].casefold() for t in terms) for c in changes)
    outcomes=[score_frame(case,original.get('m'+str(i),{}),'m'+str(i) in frames,[issue['code'] for issue in issues if issue.get('id') in (None,'m'+str(i))],source_texts=[summaries[i].get('sender_domain','')],new_category_plan=bool(taxonomy)) for i,case in enumerate(cases)]
    if cluster:taxonomy=bool(taxonomy and all(o['category_correct'] and o['schema_valid'] and o['document_correct'] and o['evidence_grounded'] and o['protection_recalled'] and o['entity_grounded'] for o in outcomes))
    return dict(outcomes=outcomes,seconds=response.seconds,usage=response.usage,taxonomy=taxonomy,language=cases[0]['language'],issues=[i['code'] for i in issues])


class ProviderQualification:
    def __init__(self,store:QualificationPort,providers:Callable[[str],AIProvider],cases:list[dict]):self.store,self.providers,self.cases=store,providers,cases

    def step(self):
        plan=self.store.runnable()
        if not plan:return dict(status='idle')
        if plan['contract']!=qualification_contract(self.cases):
            self.store.pause(plan['id'],'Evaluation contract changed; review a new immutable plan');return dict(status='contract_changed')
        # Discovery is separately bounded metadata work; it never submits mail.
        pending=next((p for p in plan['providers'] if p['stage']=='metadata'),None)
        if pending:
            self.store.discovery(plan,pending['provider'],self.providers(pending['provider']));return dict(status='discovered',provider=pending['provider'])
        job=self.store.claim(plan)
        if not job:return self.store.state(plan['id'])
        cap=ModelCapability(**job['capability']);cases=[self.cases[i] for i in job['case_indexes']]
        rows,summaries,data,schema,bound=request_data(cases)
        if bound>16384 or bound+2048>cap.context:
            self.store.fail_job(job,ModelFailure('context_exceeded','Complete synthetic input cannot fit; not clipped'),None);return dict(status='context_blocked')
        operation='qual:'+plan['id']+':'+str(job['id'])+':try:'+str(job['attempts'])
        attempt=None
        try:
            saved=self.store.hybrid.saved_result('personal',cap,operation)
            if saved:response=saved
            else:
                self.store.admit(job,bound)
                self.store.hybrid.synthetic_scope(operation,'personal',cap)
                try:attempt=self.store.hybrid.reserve('personal',cap,operation,[],bound,2048,synthetic_scope=operation)
                except ValueError as exc:
                    self.store.defer_job(job,str(exc));return dict(status='waiting',reason=str(exc))
                self.store.bind(job,attempt)
                response=self.providers(cap.provider).analyze(cap,SYSTEM,data,schema,timeout=75)
                self.store.hybrid.finish(attempt,'done',response)
            result=evaluate_batch(cases,response)
            self.store.finish_job(job,result,attempt);self.store.certify(plan,job)
            return dict(status='processed',provider=cap.provider,model=cap.model,cases=len(cases))
        except Exception as exc:
            failure=classify_error(exc)
            if isinstance(exc,(ValueError,TypeError,KeyError)):failure=ModelFailure('malformed_output','Structured benchmark response failed local validation')
            if attempt:self.store.hybrid.finish(attempt,'failed',error=failure)
            self.store.fail_job(job,failure,attempt)
            return dict(status='provider_waiting',provider=cap.provider,reason=failure.state)

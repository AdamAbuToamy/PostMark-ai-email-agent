"""Bounded multi-message planning through the existing verified local tools."""
import json
import time
from typing import Protocol
from email_agent.application.hybrid_ai import message_languages,organization_schema
from email_agent.application.semantic_mail import SYSTEM,SCHEMA,SEMANTIC_VERSION,digest,public_summary
from email_agent.application.semantic_organizer import row_fingerprint
from email_agent.application.organization_contract import ORGANIZATION_PLANNING
from email_agent.application.local_organization_agent import LocalOrganizationAgent


class GroupWorkPort(Protocol):
    def preference(self,account:str)->str: ...
    def resolve_semantic(self,account:str,id:str,complete:bool)->None: ...
    def group_candidates(self,account:str)->list[list[str]]: ...
    def claim_group(self,account:str,key:str)->bool: ...
    def claim_messages(self,account:str,ids:list[str])->bool: ...
    def release_messages(self,account:str,ids:list[str])->None: ...
    def finish_group(self,account:str,key:str,status:str,result:dict)->None: ...
    def task(self,account,id,status,reason,provider=None,model=None,**kwargs): ...


class HybridGroupOrganizer:
    def __init__(self,work:GroupWorkPort,organizer,inference,gate):
        self.work,self.organizer,self.inference,self.gate=work,organizer,inference,gate

    def step(self,account,budget_seconds=85,provider=None):
        if budget_seconds<5 or not self.organizer.store.local_policy(account)['enabled']:return None
        if not callable(getattr(self.work,'group_candidates',None)):return None
        catalog=self.organizer.store.model_catalog(account)
        for ids in self.work.group_candidates(account):
            rows=[self.organizer.store.message_row(account,id) for id in ids]
            if len(rows)<3 or any(not r or not r['content_complete'] or r['decision']['source']=='manual' or r['input_fingerprint']!=row_fingerprint(r) for r in rows):continue
            mapped={'m'+str(i):r for i,r in enumerate(rows)}
            summaries=[public_summary(r,alias,'full') for alias,r in mapped.items()]
            data=dict(messages=summaries,catalog=[{k:c[k] for k in ('id','name','observed_types') if k in c} for c in catalog],local_organization=dict(standing_local_consent=True,tools=sorted(LocalOrganizationAgent.READS|LocalOrganizationAgent.WRITES),planning=ORGANIZATION_PLANNING))
            schema=organization_schema(data,SCHEMA)
            key='group:'+digest([SEMANTIC_VERSION,SYSTEM,schema,data,[row_fingerprint(r) for r in rows]])
            bound=len((SYSTEM+json.dumps(data,ensure_ascii=False)+json.dumps(schema)).encode())+512
            languages=tuple(sorted({lang for s in summaries for lang in message_languages(s['excerpt']+' '+s['history_context'])}))
            candidates=self.inference.options(account,key,languages,bound,self.work.preference(account),'taxonomy')
            excluded=[(m.provider,m.model) for m in candidates if not all(m.task_quality.get(lang,{}).get('taxonomy',0)>=.9 for lang in languages)]
            eligible=[m for m in candidates if (m.provider,m.model) not in excluded]
            if not eligible or provider and eligible[0].provider!=provider:continue
            if not self.work.claim_messages(account,ids):continue
            if not self.work.claim_group(account,key):
                self.work.release_messages(account,ids);continue
            model=eligible[0]
            for id in ids:self.work.task(account,id,'local_processing' if model.provider=='ollama' else 'cloud_processing','Reading a coherent collection',model.provider,model.model)
            try:
                response,model=self.inference.run(account,key,ids,SYSTEM,data,schema,languages,self.work.preference(account),'taxonomy',excluded=excluded,budget_seconds=budget_seconds,provider=provider)
                frames,changes,issues=self.gate.accept(response.text,summaries,mapped,catalog,model)
                result=self.organizer.apply_validated(account,mapped,summaries,frames,changes,model.provider)
                moved=0;verified=0
                for alias,row in mapped.items():
                    after=self.organizer.store.message_row(account,row['message_id']);frame=frames.get(alias) or {}
                    changed=bool(after and after['decision']['category_id']!='other' and after['decision']['source']!='manual');moved+=int(changed)
                    understood=bool(frame.get('understood') and frame.get('quality_verified') and not frame.get('analysis_reason') and not frame.get('workflow_needs_escalation') and after and after['input_fingerprint']==row_fingerprint(after))
                    verified+=int(understood)
                    self.work.resolve_semantic(account,row['message_id'],understood)
                    self.work.task(account,row['message_id'],'classified' if changed and understood else 'analyzed_other' if understood else 'waiting_cloud','Verified collection organization' if changed else 'Collection examined; further evidence or a grouped decision may be needed',model.provider,model.model)
                result.update(status='group_partial' if verified!=len(rows) or issues else 'group_organized' if moved else 'group_analyzed',provider=model.provider,model=model.model,moved=moved,issues=len(issues))
                self.work.record_organization_result(account,key,model,verified==len(rows) and not issues,moved)
                self.work.finish_group(account,key,'done' if verified==len(rows) and not issues else 'retry',result)
                return result
            except Exception as exc:
                from email_agent.application.model_errors import classify_error
                failure=classify_error(exc)
                for id in ids:self.work.task(account,id,'waiting_cloud','Collection retained for bounded retry',due=time.time()+300)
                self.work.finish_group(account,key,'retry',dict(reason=failure.state))
                return dict(status='group_waiting',reason=failure.state)
        return None

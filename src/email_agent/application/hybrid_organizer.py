"""Resumable semantic work uses the existing organizer's verified local tools."""
import json
import time
from email_agent.application.hybrid_ai import HybridInference,ClassificationQualityGate,message_languages,organization_schema
from email_agent.application.semantic_mail import SYSTEM,SCHEMA,SINGLE_MESSAGE_SYSTEM,public_summary,digest,SEMANTIC_VERSION
from email_agent.application.semantic_organizer import row_fingerprint


class HybridOrganizer:
    def __init__(self,store,organizer,providers,legacy_step=None,health_check=lambda:None,qualification_ready=lambda:False):
        self.store,self.organizer=store,organizer
        self.inference=HybridInference(store,providers);self.gate=ClassificationQualityGate();self.legacy_step=legacy_step
        self.health_check=health_check
        self.qualification_ready=qualification_ready

    def refresh_eligibility(self,account,limit=100):
        """Bounded local preflight, not inference; never claims understanding."""
        catalog=self.organizer.store.model_catalog(account)
        models=self.store.models()
        revision=digest(['eligibility-v2-shared',self.store.preference(account),models,[(m['provider'],m['model'],self.store.healthy(m['provider'],m['model'])) for m in models],catalog,SYSTEM,SCHEMA,SINGLE_MESSAGE_SYSTEM])
        allowed=self.store.candidates(account);preference=self.store.preference(account)
        from email_agent.application.hybrid_ai import ModelCapability
        evaluated=[ModelCapability(**m['payload']) for m in models if m['payload']['validated'] and m['payload']['structured']]
        health={(m.provider,m.model):self.store.healthy(m.provider,m.model) for m in allowed}
        processed=0;deadline=time.monotonic()+3
        for id in self.store.routing_items(account,revision,limit):
            if time.monotonic()>deadline:break
            row=self.organizer.store.message_row(account,id)
            if not row or not row['content_complete']:continue
            summary,public,schema,key,bound=self.inputs(account,row,catalog)
            languages=message_languages(summary.get('excerpt','')+' '+summary.get('history_context',''))
            excluded=self.store.excluded(account,id,self.evidence_key(row,catalog))
            def matches(m):return m.generation and all(m.quality.get(lang,0)>=.9 for lang in languages) and bound+2048<=m.context and (m.provider,m.model) not in excluded and (preference!='local' or m.provider=='ollama')
            candidates=[m for m in allowed if matches(m)]
            ready=[m for m in candidates if health[(m.provider,m.model)]]
            reason='Ready for eligible provider' if ready else 'Waiting for rate-limit cooldown' if candidates else 'Awaiting model qualification or context capacity' if not any(matches(m) for m in evaluated) else 'Awaiting account consent' if not any(self.store.account_allows(account,m.provider) for m in evaluated if matches(m)) else 'Awaiting authorized budget or provider capacity'
            self.store.route_task(account,id,revision,'ready_local' if any(m.provider=='ollama' for m in ready) else 'waiting_cloud',reason)
            processed+=1
        return processed

    @staticmethod
    def evidence_key(row,catalog):
        # Volatile observed type counts and automatic assignments are not new
        # message evidence. Structural category changes still permit reassessment.
        return digest(['evidence-v1',SEMANTIC_VERSION,row_fingerprint(row),sorted((c['id'],c['name']) for c in catalog)])

    def inputs(self,account,row,catalog):
        summary=public_summary(row,'m0','full')
        # A single-message schema forbids structural actions; tool planning is
        # supplied only by the existing multi-message taxonomy path.
        public=dict(messages=[summary],catalog=[{k:c[k] for k in ('id','name','observed_types') if k in c} for c in catalog])
        schema=organization_schema(public,SCHEMA)
        # Removing impossible tool instructions is contract-equivalent: retain
        # the prior semantic identity so accepted unchanged results are reused.
        from copy import deepcopy
        from email_agent.application.local_organization_agent import LocalOrganizationAgent,ORGANIZATION_PLANNING
        identity_schema=deepcopy(schema);identity_schema['properties']['changes']['items']=deepcopy(SCHEMA['properties']['changes']['items'])
        identity_data={**public,'local_organization':dict(standing_local_consent=True,tools=list(LocalOrganizationAgent(self.organizer,self.organizer.store).registry()),planning=ORGANIZATION_PLANNING)}
        key=digest([SEMANTIC_VERSION,SYSTEM,identity_schema,row_fingerprint(row),identity_data])
        bound=len((SINGLE_MESSAGE_SYSTEM+json.dumps(public,ensure_ascii=False)+json.dumps(schema)).encode())+512
        return summary,public,schema,key,bound

    def batch(self,account,max_items=3,seconds=85,provider=None):
        """One sequential worker budget; every inference retains its own consent cap."""
        deadline=time.monotonic()+min(85,seconds);results=[]
        from email_agent.application.hybrid_group_organizer import HybridGroupOrganizer
        grouped=HybridGroupOrganizer(self.store,self.organizer,self.inference,self.gate).step(account,budget_seconds=max(0,deadline-time.monotonic()),provider=provider)
        if grouped:return grouped
        for _ in range(min(3,max_items)):
            remaining=deadline-time.monotonic()
            if remaining<5:break
            result=self.step(account,budget_seconds=remaining,preflight=not results and provider is None,provider=provider);results.append(result)
            if result.get('status') not in ('classified','analyzed_other'):break
            model=next((m for m in self.store.models() if m['provider']==result.get('provider') and m['model']==result.get('model')),None)
            if not model or deadline-time.monotonic()<max(5,model['payload']['latency']+3):break
        return dict(results[-1] if results else dict(status='waiting_model'),batch_completed=len(results))

    def _lane_pages(self,account,first,provider,budget_seconds):
        """Bounded, persistent per-provider scanning; unsupported head rows cannot starve other languages."""
        seen=set();ids=first;deadline=time.monotonic()+min(5,max(1,budget_seconds/2))
        for page in range(5 if provider else 1):
            for index,id in enumerate(ids):
                if id not in seen:seen.add(id);yield id,len(ids)-index
            if not provider or page==4 or time.monotonic()>=deadline:return
            ids=self.store.select(account,100,provider=provider)
            if not ids:return

    def step(self,account,budget_seconds=85,preflight=True,provider=None):
        self.organizer.account(account);self.store.reconcile_other(account)
        self.health_check()
        if not self.organizer.store.local_policy(account)['enabled']:return dict(status='local_policy_off')
        if preflight:self.refresh_eligibility(account)
        preference=self.store.preference(account)
        ids=self.store.select(account,100,provider=provider) if provider else self.store.select(account,12)
        if not ids:return dict(status='idle')
        catalog=self.organizer.store.model_catalog(account)
        lane_models=[m for m in self.store.candidates(account) if m.provider==provider] if provider else []
        for id,unvisited in self._lane_pages(account,ids,provider,budget_seconds):
            row=self.organizer.store.message_row(account,id)
            if not row or not row['content_complete']:continue
            summary,public,schema,key,bound=self.inputs(account,row,catalog)
            language=message_languages(summary.get('excerpt','')+' '+summary.get('history_context',''))
            if provider and not any(m.generation and m.structured and m.validated and all(m.quality.get(lang,0)>=.9 for lang in language) and bound+2048<=m.context for m in lane_models):continue
            excluded=self.store.excluded(account,id,self.evidence_key(row,catalog))
            candidates=self.inference.options(account,key,language,bound,preference,excluded=excluded)
            if candidates and (not provider or candidates[0].provider==provider):
                if self.store.claim_messages(account,[id]):
                    if provider:self.store.rewind_lane(account,provider,unvisited)
                    break
            if provider:continue
            self.store.task(account,id,'waiting_cloud','Waiting for an available evaluated model',due=time.time()+120)
        else:return dict(status='waiting_model') if provider else self._legacy(account,preference)
        selected=candidates[0];self.store.task(account,id,'local_processing' if selected.provider=='ollama' else 'cloud_processing','Reading complete available text',selected.provider,selected.model)
        try:
            # A committed valid provider response is reused after a crash. Cache
            # includes actual model digest and prompt/content/catalog versions.
            cache_key=digest(['hybrid',key,selected.provider,selected.model,selected.fingerprint])
            cached=self.organizer.store.cache(cache_key)
            if cached is None:
                result,selected=self.inference.run(account,key,[id],SINGLE_MESSAGE_SYSTEM,public,schema,language,preference,excluded=excluded,budget_seconds=budget_seconds,provider=provider,purpose='reclassification' if (row.get('semantic') or {}).get('source') not in (None,'local') else 'production')
                raw=result.text
            else:raw=cached
            frames,changes,issues=self.gate.accept(raw,[summary],{'m0':row},catalog,selected)
            if not issues and frames:self.organizer.store.cache(digest(['hybrid',key,selected.provider,selected.model,selected.fingerprint]),raw)
            applied=self.organizer.apply_validated(account,{'m0':row},[summary],frames,changes,selected.provider)
            frame=frames.get('m0') or {};after=self.organizer.store.message_row(account,id)
            verified=bool(frame.get('understood') and not frame.get('analysis_reason') and frame.get('quality_verified') and after['input_fingerprint']==row_fingerprint(after))
            moved=after['decision']['category_id']!='other'
            complete=verified and not frame.get('workflow_needs_escalation')
            status='classified' if complete and moved else 'analyzed_other' if complete else 'waiting_cloud'
            if not complete:self.store.escalate(account,id,self.evidence_key(row,catalog),selected,'Insufficient grounded confidence or workflow quality; independent escalation required')
            self.store.task(account,id,status,'Verified local organization' if verified else 'More reliable semantic evidence needed',selected.provider,selected.model,due=time.time()+300 if not verified else 0,attempt=not verified)
            self.store.resolve_semantic(account,id,complete)
            self.store.record_organization_result(account,key,selected,verified,int(applied.get('assigned',0)>0 and after['decision']['category_id']!=row['decision']['category_id']),message_id=id,newly_organized=bool(complete and after['decision']['status']=='classified' and not (row['decision']['status']=='classified' and (row.get('semantic') or {}).get('understood') and not (row.get('semantic') or {}).get('analysis_reason') and not (row.get('semantic') or {}).get('workflow_needs_escalation'))),issues=len(issues))
            return dict(status=status,provider=selected.provider,model=selected.model,issues=len(issues),**applied)
        except Exception as exc:
            from email_agent.application.model_errors import classify_error
            failure=classify_error(exc)
            self.store.record_organization_result(account,key,selected,False,0,message_id=id,issues=1)
            if isinstance(exc,(ValueError,TypeError,KeyError)):self.store.escalate(account,id,self.evidence_key(row,catalog),selected,'Invalid structured semantic evidence')
            self.store.task(account,id,'waiting_cloud' if failure.state=='no_eligible_model' else 'failed','Model unavailable; saved for bounded retry' if failure.state!='harness_error' else 'Structured evidence needs recheck',due=0 if provider and failure.state=='no_eligible_model' else time.time()+300,attempt=failure.state!='no_eligible_model')
            return dict(status='waiting_model') if provider else self._legacy(account,preference)

    def _legacy(self,account,preference):
        if preference!='local' and self.legacy_step:
            return self.legacy_step(account)
        return dict(status='waiting_model',message='AI analysis is paused. Your messages are saved and will resume when a model is available.')

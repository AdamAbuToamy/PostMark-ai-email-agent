"""Account-scoped semantic organization, explicit AI scopes and approved taxonomy."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Protocol
from email_agent.application.model_errors import classify_error
from email_agent.application.semantic_mail import (
    SEMANTIC_VERSION, SYSTEM, SCHEMA, digest, slug, local_frame, public_summary,
    validate_batch, discover_facets, feature, supported_cluster_size,
)


class OrganizerStore(Protocol):
    def snapshot(self,account:str,limit:int=10000)->list[dict]: ...
    def message_row(self,account:str,id:str)->dict|None: ...
    def grouping_snapshot(self,account:str)->list[dict]: ...
    def profile_frames(self,account:str): ...
    def grouping_revision(self,account:str)->str: ...
    def grouping_checkpoint(self,account:str,revision:str|None=None,stage:str='group_complete')->str|None: ...
    def walk(self,account:str,limit:int)->list[dict]: ...
    def planning_groups(self,account:str,limit:int=8)->list[tuple[str,list[dict]]]: ...
    def semantic_counts(self,account:str)->dict: ...
    def seed_content_patterns(self,account:str,limit:int=4)->None: ...
    def put_frame(self,row:dict,frame:dict,version:str)->bool: ...
    def assign_semantic(self,row:dict,frame:dict)->bool: ...
    def catalog(self,account=None)->list[dict]: ...
    def model_catalog(self,account:str)->list[dict]: ...
    def profiles(self,account:str)->dict: ...
    def save_observed_profile(self,account:str,category:str,profile:dict)->None: ...
    def create_campaign(self,scope:dict,model:str,request_cap:int,body_cap:int,batches:list)->str: ...
    def campaign(self,id:str)->dict: ...
    def consent(self,id:str)->None: ...
    def batches(self,id:str)->list[dict]: ...
    def claim_batch(self,id:str,ordinal:int)->bool: ...
    def reserve_request(self,id:str,day:str|None=None,daily_cap:int=0)->bool: ...
    def finish_batch(self,id:str,ordinal:int,status:str,result:dict)->None: ...
    def cache(self,key:str,result:str|None=None)->str|None: ...
    def propose(self,account:str,key:str,payload:dict)->str: ...
    def proposals(self,account:str)->list[dict]: ...
    def defer_proposal(self,account:str,id:str,reason:str)->None: ...
    def decide(self,account:str,id:str,fingerprint:str,approved:bool,authority:str='user')->str: ...
    def publication_prerequisites(self)->None: ...
    def apply(self,account:str,id:str,policy_revision=None)->dict: ...
    def local_policy(self,account:str,enabled=None,approved=False,reference='settings')->dict: ...
    def organization_history(self,account:str)->list[dict]: ...
    def recurring(self,account:str,policy:dict|None=None)->dict: ...
    def recurring_claim(self,account:str,day:str)->bool: ...


class OrganizerModel(Protocol):
    def generate_organization(self,system:str,data:dict,schema:dict)->str: ...


def row_fingerprint(row,*,include_labels=False):
    manual=row.get('decision') or {}
    keys=('account_id','message_id','subject','sender','body','headers')+(('labels',) if include_labels else ())
    return digest({key:row.get(key) for key in keys}|
        {'user_category':manual.get('category_id') if manual.get('source')=='manual' else None})


class SemanticOrganizer:
    def __init__(self,store:OrganizerStore,messages,classifier,settings,reserve,models,usage,body_review=None,on_scope=lambda account,ids,state,model:None):
        self.store,self.messages,self.classifier=store,messages,classifier
        self.settings,self.reserve,self.models,self.usage=settings,reserve,models,usage
        self.body_review=body_review
        self.on_scope=on_scope

    @staticmethod
    def account(account):
        if account not in ('personal','university'):raise ValueError('Unknown account')

    def organize_local(self,account,limit=200):
        self.account(account)
        if type(limit) is not int or not 1<=limit<=200:raise ValueError('Organize at most200 changed messages per step')
        self.store.seed_content_patterns(account)
        rows=self.store.walk(account,limit);processed=0
        for row in rows:
            if processed>=limit:break
            frame=row.get('semantic') or {}
            if row['input_fingerprint']==row_fingerprint(row) and not (frame.get('source')=='local' and frame.get('extraction_version')!=SEMANTIC_VERSION):continue
            frame=local_frame(row)
            self.store.put_frame(row,frame,SEMANTIC_VERSION)
            self.classifier.classify(self.messages.get(account,row['message_id']))
            processed+=1
        revision=self.store.grouping_revision(account)
        changed=revision!=self.store.grouping_checkpoint(account)
        planning_rows=None
        if changed:
            by_category=defaultdict(list)
            for category,frame in self.store.profile_frames(account):by_category[category].append(frame)
            for category,frames in by_category.items():self.store.save_observed_profile(account,category,discover_facets(frames))
        # A meaningful local intent can propose a category; generic lexical
        # recurrence alone is not enough to publish one or fill human review.
        from email_agent.application.taxonomy_fit import fit
        catalog=self.store.catalog(account)
        active_ids={c['id'] for c in catalog}
        for name,members in self.store.planning_groups(account):
            stage='purpose:'+digest(name)
            group_revision=digest(['structural-fit-v2',[(r['message_id'],r['input_fingerprint'],r.get('semantic'),r['decision']) for r in members],sorted(active_ids),self.store.local_policy(account)])
            if self.store.grouping_checkpoint(account,stage=stage)==group_revision:continue
            members=[row for row in members if row['input_fingerprint']==row_fingerprint(row) and ((row.get('semantic') or {}).get('source')!='local' or row['semantic'].get('extraction_version')==SEMANTIC_VERSION) and (row.get('semantic') or {}).get('category_hint') not in active_ids-{'other'}]
            if not supported_cluster_size([m.get('semantic') or {} for m in members]):
                self.store.grouping_checkpoint(account,group_revision,stage)
                continue
            structural=fit([m.get('semantic') or {} for m in members],catalog)
            if not structural:
                self.store.grouping_checkpoint(account,group_revision,stage)
                continue
            if planning_rows is None:planning_rows=self.store.grouping_snapshot(account)
            try:self.propose_change(account,dict(action='create',name=structural['name'],sources=[],message_ids=[m['message_id'] for m in members],dimension=None,reason='Shared purpose supported by current full-message evidence'),source='local',rows=planning_rows)
            except ValueError:pass
            self.store.grouping_checkpoint(account,group_revision,stage)
        from email_agent.application.local_organization_agent import LocalOrganizationAgent
        maintenance_revision=digest(['taxonomy-fit-v2',revision])
        if self.store.grouping_checkpoint(account,stage='taxonomy_fit')!=maintenance_revision:
            self.maintain_taxonomy(account,rows=planning_rows)
            self.store.grouping_checkpoint(account,maintenance_revision,'taxonomy_fit')
        actions=LocalOrganizationAgent(self,self.store).step(account)
        self.store.grouping_checkpoint(account,revision)
        return dict(processed=processed,actions=actions,**self.overview(account))

    def maintain_taxonomy(self,account,rows=None):
        """Reconcile persisted drafts and exact redundant local groups, no AI calls."""
        if not self.store.local_policy(account)['enabled']:return
        from email_agent.application.taxonomy_fit import fit,equivalent_source,concept
        rows=self.store.grouping_snapshot(account) if rows is None else rows
        by_id={r['message_id']:r for r in rows};catalog=self.store.catalog(account)
        for draft in self.store.proposals(account):
            plan=draft['payload']
            if draft['status']!='suggested' or plan['action']!='create' or plan.get('source')!='local':continue
            members=[by_id[id] for id in plan.get('evidence_fingerprints',{}) if id in by_id]
            structural=fit([r.get('semantic') or {} for r in members],catalog)
            if not structural or structural['name'].casefold()!=plan['name'].casefold():
                # Keep semantic subtype/status and the audited draft; never guess an assignment.
                self.store.defer_proposal(account,draft['id'],'Document/intent evidence is not a reusable category scope')
                if structural and structural['target']:
                    try:self.propose_change(account,dict(action='create',name=structural['name'],sources=[],message_ids=[r['message_id'] for r in members],dimension=None,reason='Reuse the existing exact semantic purpose'),rows=rows)
                    except ValueError:pass
        types_by_category=defaultdict(set)
        for row in rows:types_by_category[row['decision']['category_id']].add(concept((row.get('semantic') or {}).get('message_type')))
        for source in catalog:
            for target in catalog:
                if concept(target['name']) not in types_by_category[source['id']]:continue
                if equivalent_source(source['id'],target['id'],catalog,rows):
                    own=[r for r in rows if r['decision']['category_id']==source['id']]
                    try:self.propose_change(account,dict(action='merge',name=target['name'],sources=[source['id']],message_ids=[r['message_id'] for r in own],dimension=None,reason='Every source document meaning already exists under this exact named category'),source='maintenance',rows=rows)
                    except ValueError:pass
                    break

    def overview(self,account):
        self.account(account)
        counts=self.store.semantic_counts(account)
        proposals=self.store.proposals(account)
        visible={c['id'] for c in self.store.catalog(account)}
        def published(p):
            plan=p['payload']
            ids={op['id'] for op in plan['operations'] if op['kind'] in ('create','rename','profile')}|{a['target'] for a in plan.get('assignments',[])}
            return sorted(ids&visible) if p['status']=='applied' else []
        return dict(**counts,
            proposals=[dict(p['payload'],id=p['id'],status=p['status'],fingerprint=p['fingerprint'],category_ids=published(p),requires_attention=p['status']=='needs_review' or (p['status'] in ('suggested','approved') and p['payload']['action'] in ('rename','split','merge','retire') and p['payload'].get('source')!='maintenance')) for p in proposals],
            recurring=self.store.recurring(account),local_policy=self.store.local_policy(account),organization_history=self.store.organization_history(account))

    def preview(self,accounts,request_cap=8,max_messages=32,body_cap=0,content_mode='excerpt',message_ids=None):
        if not isinstance(accounts,list) or not 1<=len(accounts)<=2 or len(set(accounts))!=len(accounts):raise ValueError('Choose explicit account scope')
        for account in accounts:self.account(account)
        if type(request_cap)is not int or not 1<=request_cap<=8 or type(max_messages)is not int or not 1<=max_messages<=64 or body_cap!=0 or content_mode not in ('excerpt','full'):raise ValueError('Bounded scope required')
        if message_ids is not None and (len(accounts)!=1 or not isinstance(message_ids,list) or len(set(message_ids))!=len(message_ids) or len(message_ids)>max_messages):raise ValueError('Explicit IDs require one account and bounded scope')
        selected=[];batches=[];deferred=[]
        per_account=max(1,max_messages//len(accounts))
        for account in accounts:
            catalog=self.store.model_catalog(account)
            rows=([row for id in message_ids if (row:=self.store.message_row(account,id)) is not None] if message_ids is not None else self.store.snapshot(account));groups=defaultdict(list)
            for row in rows:
                if message_ids is not None and row['message_id'] not in message_ids:continue
                if content_mode=='full' and not row.get('content_complete'):
                    deferred.append(dict(account=account,id=row['message_id'],reason='full_body_not_verified'));continue
                groups[row['decision'].get('category_id') or 'other'].append(row)
            for members in groups.values():members.sort(key=lambda row:(not bool(row['body']),not bool((row.get('semantic') or {}).get('understood')),-row['internal_date_ms']))
            sample=[]
            while len(sample)<per_account and any(groups.values()):
                for category in sorted(groups,key=lambda k:(k!='other',k)):
                    if groups[category] and len(sample)<per_account:sample.append(groups[category].pop(0))
            sample.sort(key=lambda row:(row['decision'].get('category_id') or 'other',row['message_id']))
            chunk=[];size=0
            def append_chunk(chunk):
                summaries=[public_summary(row,'m'+str(i),content_mode) for i,row in enumerate(chunk)]
                from email_agent.application.local_organization_agent import LocalOrganizationAgent
                tool_contract=dict(standing_local_consent=self.store.local_policy(account)['enabled'],tools=LocalOrganizationAgent(self,self.store).registry(),planning='Return structured changes; backend compiles them to these tools, checks policy and verifies persistence. Never claim execution.')
                batches.append(dict(account=account,messages=summaries,catalog=catalog,local_organization=tool_contract,private_ids={s['id']:row['message_id'] for s,row in zip(summaries,chunk)},fingerprints={s['id']:row_fingerprint(row) for s,row in zip(summaries,chunk)}))
                selected.extend((account,row['message_id']) for row in chunk)
            for row in sample:
                summary=public_summary(row,'m0',content_mode);length=len(str(summary))
                if content_mode=='full' and length>110000:
                    deferred.append(dict(account=account,id=row['message_id'],reason='full_body_exceeds_single_request_scope_not_clipped'));continue
                if chunk and (len(chunk)>=4 or size+length>110000):
                    if len(batches)>=request_cap:break
                    append_chunk(chunk);chunk=[];size=0
                if len(batches)>=request_cap:break
                chunk.append(row);size+=length
            if chunk and len(batches)<request_cap:append_chunk(chunk)
        scope=dict(accounts=accounts,message_ids=[dict(account=a,id=id) for a,id in selected],schema_version=SEMANTIC_VERSION,content_mode=content_mode,
            max_excerpt_characters=600 if content_mode=='excerpt' else None,max_subject_characters=240 if content_mode=='excerpt' else None,provider='Free-only',taxonomy_apply=False,apply_existing_categories=content_mode=='full',recurring=False)
        model=self.settings(accounts[0])['model'];id=self.store.create_campaign(scope,model,request_cap,body_cap,batches)
        return dict(id=id,accounts=accounts,model=model,request_cap=request_cap,body_cap=0,message_count=len(selected),content_mode=content_mode,deferred=deferred,
            estimated_input_tokens=sum(len(str(b['messages']))+len(SYSTEM)+len(str(SCHEMA)) for b in batches)//3,max_output_tokens_total=len(batches)*4096,
            previews=[dict(account=b['account'],messages=b['messages']) for b in batches],note='Full mode includes complete verified text with redactions and separated historical context. Oversize/missing bodies are deferred, never clipped. Local existing-category assignments may update; new categories require approval. No Gmail changes.')

    def _free_policy(self,account,model):
        settings=self.settings(account);policy=settings.get('usage',{}).get('provider_policy',{})
        if not settings.get('configured') or not settings.get('enabled') or not settings.get('processing_consent') or settings['model']!=model or not policy.get('free_tier_attested') or policy.get('paid_approved') or policy.get('model')!=model or (settings.get('connection') or {}).get('blocking_reasons'):
            raise ValueError('Configured model, current consent and confirmed Free-only shared provider policy are required')

    def run(self,id,approved=False,day=None,daily_cap=0):
        if approved is not True:raise ValueError('Approve this exact bounded scope before any submission')
        campaign=self.store.campaign(id)
        for account in campaign['scope']['accounts']:self._free_policy(account,campaign['model'])
        if not any(batch['status']=='pending' for batch in self.store.batches(id)):return self.campaign_result(id)
        self.store.consent(id)
        for batch in self.store.batches(id):
            if batch['status']!='pending':continue
            data=batch['input'];account=data['account'];self._free_policy(account,campaign['model'])
            mapped={alias:self.store.message_row(account,real) for alias,real in data['private_ids'].items()}
            if any(row is None or row_fingerprint(row)!=data['fingerprints'][alias] for alias,row in mapped.items()):raise ValueError('Message evidence changed; prepare a fresh scope')
            public={key:data[key] for key in ('messages','catalog')}
            if 'local_organization' in data:public['local_organization']=data['local_organization']
            fingerprint=digest([SEMANTIC_VERSION,SYSTEM,SCHEMA,campaign['model'],public])
            cached=self.store.cache(fingerprint)
            if not self.store.claim_batch(id,batch['ordinal']):continue
            self.on_scope(account,list(data['private_ids'].values()),'cloud_processing',campaign['model'])
            raw=None
            try:
                if cached is None:
                    if not self.store.reserve_request(id,day,daily_cap):raise ValueError('Global organizing request cap reached')
                    if not self.reserve(account,'semantic-organizer'):raise ValueError('Shared provider budget/throttle blocked the request')
                    model=self.models()
                    self._free_policy(account,campaign['model'])
                    current=self.settings(account)
                    if not current.get('enabled') or not current.get('processing_consent') or current['model']!=campaign['model'] or current['usage']['provider_policy'].get('paid_approved'):
                        raise ValueError('Consent/model changed before submission')
                    from copy import deepcopy
                    schema=deepcopy(SCHEMA)
                    schema['properties']['frames']['items']['properties']['category_id']={'type':['string','null'],'enum':[c['id'] for c in data['catalog']]+[None]}
                    raw=model.generate_organization(SYSTEM,public,schema)
                    self.usage(account,campaign['model'],getattr(model,'last_usage',{}))
                    frames,changes,issues=validate_batch(raw,data['messages'],{alias:local_frame(row) for alias,row in mapped.items()},[c['id'] for c in data['catalog']])
                    if not issues:self.store.cache(fingerprint,raw)
                else:frames,changes,issues=validate_batch(cached,data['messages'],{alias:local_frame(row) for alias,row in mapped.items()},[c['id'] for c in data['catalog']])
                for alias,frame in frames.items():
                    frame.update(provider='gemini',model=campaign['model'],prompt_version=SEMANTIC_VERSION)
                    frame['related_ids']=[data['private_ids'][other] for other in frame.get('related_ids',[]) if other!=alias]
                    self.store.put_frame(mapped[alias],frame,SEMANTIC_VERSION)
                    if campaign['scope'].get('apply_existing_categories') and frame.get('suggested_category_id') and frame.get('understood') and frame['confidence']>=(.85 if self.store.local_policy(account)['enabled'] else .9) and not frame.get('analysis_reason') and frame['input_coverage']['body_complete']:
                        changed=self.store.assign_semantic(mapped[alias],frame)
                        if not changed and mapped[alias]['decision']['source']!='manual' and frame['suggested_category_id']!=mapped[alias]['decision']['category_id']:
                            frame['analysis_reason']='conflicting_assignment_evidence'
                            self.store.put_frame(mapped[alias],frame,SEMANTIC_VERSION)
                accepted=0
                for change in changes:
                    change['message_ids']=[data['private_ids'][alias] for alias in change['message_ids']]
                    try:
                        self.propose_change(account,change,source='gemini');accepted+=1
                    except ValueError:continue  # Unsupported/stale structure never invalidates grounded frames.
                self.store.finish_batch(id,batch['ordinal'],'partial' if issues and frames else 'failed' if issues else 'done',dict(messages=len(frames),proposals=accepted,rejected_proposals=len(changes)-accepted,validation_issues=issues,cached=cached is not None,cache_key=fingerprint,rejected_output=raw[:60000] if issues and isinstance(raw,str) else None))
            except Exception as exc:
                self.store.finish_batch(id,batch['ordinal'],'failed',dict(error='Analysis stopped; request reservation retained; no automatic retry',error_kind=type(exc).__name__,failure_stage='transport' if classify_error(exc).state=='network_unavailable' else 'provider' if classify_error(exc).http_status else 'response' if isinstance(exc,(ValueError,TypeError,KeyError)) else 'execution',diagnostic=classify_error(exc).diagnostic(),rejected_output=raw[:60000] if isinstance(exc,ValueError) and isinstance(raw,str) else None))
                break
            finally:self.on_scope(account,list(data['private_ids'].values()),'settled',campaign['model'])
        for account in campaign['scope']['accounts']:
            by_category=defaultdict(list)
            for row in self.store.grouping_snapshot(account):
                if row.get('semantic'):by_category[row['decision']['category_id']].append(row['semantic'])
            for category,frames in by_category.items():self.store.save_observed_profile(account,category,discover_facets(frames))
        from email_agent.application.local_organization_agent import LocalOrganizationAgent
        for account in campaign['scope']['accounts']:LocalOrganizationAgent(self,self.store).step(account)
        return self.campaign_result(id)

    def campaign_result(self,id):
        campaign=self.store.campaign(id)
        return dict(id=id,status=campaign['status'],requests_used=campaign['requests_used'],request_cap=campaign['request_cap'],
            message_count=len(campaign['scope']['message_ids']),batches=[dict(ordinal=b['ordinal'],status=b['status']) for b in self.store.batches(id)],
            gmail_changes=0,taxonomy_applied=0)

    def apply_validated(self,account,mapped,summaries,frames,changes,provider):
        """Common local tools/persistence path for quality-gated providers."""
        private={s['id']:mapped[s['id']]['message_id'] for s in summaries}
        applied=0
        for alias,frame in frames.items():
            frame['related_ids']=[private[other] for other in frame.get('related_ids',[]) if other in private and other!=alias]
            self.store.put_frame(mapped[alias],frame,SEMANTIC_VERSION)
            if frame.get('suggested_category_id') and frame.get('understood') and not frame.get('analysis_reason') and frame.get('quality_verified') and self.store.local_policy(account)['enabled']:
                changed=self.store.assign_semantic(mapped[alias],frame);applied+=bool(changed)
                if not changed and mapped[alias]['decision']['source']!='manual' and frame['suggested_category_id']!=mapped[alias]['decision']['category_id']:
                    frame['analysis_reason']='conflicting_assignment_evidence'
                    self.store.put_frame(mapped[alias],frame,SEMANTIC_VERSION)
        proposed=0
        for change in changes:
            change={**change,'message_ids':[private[alias] for alias in change['message_ids']]}
            try:self.propose_change(account,change,source=provider);proposed+=1
            except ValueError:continue
        from email_agent.application.local_organization_agent import LocalOrganizationAgent
        actions=LocalOrganizationAgent(self,self.store).step(account)
        return dict(accepted=len(frames),assigned=applied,proposed=proposed,actions=actions)

    def propose_change(self,account,change,source='local',rows=None):
        self.account(account)
        rows={r['message_id']:r for r in (self.store.grouping_snapshot(account) if rows is None else rows)}
        for id in change['message_ids']:
            if id not in rows:
                row=self.store.message_row(account,id)
                if row:rows[id]=row
        members=[rows[id] for id in change['message_ids'] if id in rows]
        if not members:raise ValueError('Current message evidence is required')
        if any(r['input_fingerprint']!=row_fingerprint(r) for r in members):raise ValueError('Analyze changed message evidence before proposing structure')
        catalog={c['id']:c for c in self.store.catalog(account)}
        action=change['action'];name=change['name'].strip();sources=change.get('sources') or []
        if change.get('icon_key'):
            from email_agent.domain.category_icons import validate_category_icon
            validate_category_icon(change['icon_key'])
        if source=='local':
            name=name.replace('_',' ');name=name[:1].upper()+name[1:]
        if any(id not in catalog for id in sources):raise ValueError('Unknown source category')
        if not name or len(name)>80:raise ValueError('Use a concise category/section name')
        from email_agent.domain.category_icon_resolver import resolve_category_icon
        operations=[];assignments=[];policies=[];candidates=members
        expected=[catalog[id] for id in sources]
        from email_agent.application.taxonomy_fit import concept
        concepts={concept((r.get('semantic') or {}).get('attributes',{}).get('category_name')) for r in members}
        semantic_concept=next(iter(concepts)) if len(concepts)==1 and next(iter(concepts)) else concept(name)
        same=next((id for id,c in catalog.items() if concept(c['name'])==concept(name)),None)
        if not same and action=='create':
            for prior in self.store.proposals(account):
                if prior['status']=='applied' and prior['payload'].get('semantic_concept')==semantic_concept:
                    existing=[op['id'] for op in prior['payload']['operations'] if op['kind']=='create' and op['id'] in catalog]
                    if len(existing)==1:same=existing[0];name=catalog[same]['name'];break
        target=same or ('sem_'+slug(name)[:24]+'_'+digest([account,name.casefold()])[:8])
        if not same and action=='create':
            for draft in self.store.proposals(account):
                plan=draft['payload']
                if draft['status']=='suggested' and plan.get('account_scoped') and (plan.get('semantic_concept') or concept(plan['name']))==semantic_concept:
                    creates=[op for op in plan['operations'] if op['kind']=='create']
                    if len(creates)==1:target=creates[0]['id'];break
        if same and same not in sources:expected.append(catalog[same])
        if action in ('rename','retire') and len(sources)!=1:raise ValueError('Choose one category')
        if action=='rename':
            operations=[dict(kind='rename',id=sources[0],name=name)];target=sources[0]
        elif action in ('refine','rename_section','merge_sections','split_section','retire_section'):
            if len(sources)!=1:raise ValueError('Choose one category for a coherent facet')
            target=sources[0];frames=[r['semantic'] for r in rows.values() if r['decision']['category_id']==target and r.get('semantic')]
            dimension=change.get('dimension')
            if dimension in ('entity','company','sender','domain'):raise ValueError('Entities belong to a secondary filter, not a primary hierarchy mixed with status/type')
            if not dimension or not any(feature(frame,dimension) for frame in frames):raise ValueError('Facet needs observed semantic attributes')
            definition=dict(self.store.profiles(account).get(target) or discover_facets(frames))
            definition['labels']=dict(definition.get('labels',{}))
            if definition.get('dimension')!=dimension:definition=discover_facets(frames)
            definition['dimension']=dimension
            values={feature(r.get('semantic') or {},dimension) for r in members};values.discard(None)
            if action=='rename_section':
                if len(values)!=1:raise ValueError('Rename one observed section')
                definition.setdefault('labels',{}).update({next(iter(values)):name})
            elif action=='merge_sections':
                definition['value_map']={**definition.get('value_map',{}),**{value:name for value in values}};definition.setdefault('labels',{})[name]=name
            elif action=='retire_section':definition['retired_values']=sorted(set(definition.get('retired_values',[]))|values)
            definition['source']='user_approved'
            operations=[dict(kind='profile',id=target,definition=definition)]
        else:
            if action not in ('create','split','merge','retire'):raise ValueError('Unsupported taxonomy change')
            if action=='retire':
                if not same or same==sources[0]:raise ValueError('Retirement requires an existing replacement category')
                target=same
            elif not same:operations.append(dict(kind='create',id=target,name=name,icon_key=change.get('icon_key') or resolve_category_icon(name,change['reason'])))
            frames=[r.get('semantic') or {} for r in members]
            conditions={}
            for key in ('primary_intent','message_type'):
                values={feature(frame,key) for frame in frames}
                if len(values)==1 and None not in values and '' not in values:conditions[key]=next(iter(values))
            candidates=members
            if action in ('merge','retire'):
                candidates=[r for r in rows.values() if r['decision']['category_id'] in sources]
                if len(candidates)>200:raise ValueError('Structural reassignment exceeds200-message preview; split first')
            elif conditions and not same:
                candidates=[r for r in rows.values() if (not self.store.local_policy(account)['enabled'] or r.get('content_complete')) and r.get('semantic') and r['input_fingerprint']==row_fingerprint(r) and r['semantic'].get('confidence',0)>=.85 and not r['semantic'].get('analysis_reason') and all(feature(r['semantic'],key)==value for key,value in conditions.items())][:(50 if self.store.local_policy(account)['enabled'] else 200)]
            for row in candidates:
                if row['decision']['source']=='manual' or row['decision']['category_id']==target:continue
                before=dict(account_id=account,message_id=row['message_id'],**row['decision'])
                assignments.append(dict(message_id=row['message_id'],target=target,before=before))
            if conditions and action not in ('retire',):policies.append(dict(target=target,conditions=conditions))
            if action in ('merge','retire'):
                operations.extend(dict(kind='retire',id=id,target=target) for id in sources if id!=target)
            if not operations and not assignments:raise ValueError('No structural or assignment change to propose')
        payload=dict(action=action,name=name,semantic_concept=semantic_concept,semantic_revision=digest({r['message_id']:r.get('semantic') for r in members}),reason=change['reason'][:240],source=source,operations=operations,
            assignments=assignments,policies=policies,expected_categories=expected,
            preview=[dict(message_id=r['message_id'],subject=r['subject'],category=r['decision']['category_id'],manual=r['decision']['source']=='manual',semantic={key:(r.get('semantic') or {}).get(key) for key in ('primary_intent','message_type','status','entity','source')}) for r in members[:8]],
            affected=len(assignments),shared_category_names=action in ('rename','merge','retire'),account_scoped=True,
            evidence_fingerprints={r['message_id']:row_fingerprint(r) for r in [*members,*candidates]})
        key=digest([action,target,sources,policies,change.get('dimension')])
        return self.store.propose(account,key,payload)

    def decide(self,account,id,fingerprint,approved):
        self.account(account)
        if approved:self.store.publication_prerequisites()
        status=self.store.decide(account,id,fingerprint,approved)
        if status in ('approved','applied'):
            result=self.store.apply(account,id)
            from email_agent.application.local_organization_agent import LocalOrganizationAgent
            proposal=next(p for p in self.store.proposals(account) if p['id']==id)
            verified=LocalOrganizationAgent(self,self.store).verify(account,proposal)
            return dict(result,category_ids=result.get('category_ids') or verified['category_ids'],publication_verified=True)
        return dict(status=status,affected=0)

    def configure_recurring(self,account,enabled,request_cap=2,max_messages=8,approved=False):
        self.account(account)
        if type(enabled) is not bool or type(request_cap) is not int or not 1<=request_cap<=2 or type(max_messages) is not int or not 1<=max_messages<=8:raise ValueError('Recurring scope is at most8 cached summaries and2 requests per day')
        if enabled and approved is not True:raise ValueError('Separate recurring processing opt-in required')
        if enabled:self._free_policy(account,self.settings(account)['model'])
        return self.store.recurring(account,dict(enabled=enabled,request_cap=request_cap,max_messages=max_messages,body_cap=0,
            max_excerpt_characters=600,global_daily_request_cap=2,model=self.settings(account)['model']))

    def recurring_step(self,account):
        policy=self.store.recurring(account)
        if not policy.get('enabled'):return None
        self._free_policy(account,policy['model'])
        day=datetime.now(timezone.utc).date().isoformat()
        if not self.store.recurring_claim(account,day):return None
        self.organize_local(account)
        preview=self.preview([account],policy['request_cap'],policy['max_messages'])
        return self.run(preview['id'],True,day,2)

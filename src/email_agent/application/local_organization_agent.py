"""Closed-loop local tools. Model text never grants permission or executes code."""
from dataclasses import dataclass, field
from typing import Literal, Protocol
from email_agent.application.semantic_organizer import row_fingerprint
from email_agent.application.semantic_mail import supported_cluster_size


ToolName = Literal['search_messages','read_message_content','inspect_thread','inspect_categories',
    'inspect_existing_groups','create_local_category','rename_local_category','create_subcategory',
    'assign_messages_to_category','update_message_status','merge_local_groups',
    'publish_category_to_sidebar','record_classification_evidence','undo_organization_change']


@dataclass(frozen=True)
class OrganizationCommand:
    tool: ToolName
    arguments: dict = field(default_factory=dict)


class LocalOrganizationPort(Protocol):
    def local_policy(self, account, enabled=None, approved=False, reference='settings'): ...
    def proposals(self, account): ...
    def defer_proposal(self,account,id,reason): ...
    def catalog(self, account=None): ...
    def message_row(self, account, id): ...
    def grouping_snapshot(self, account): ...
    def record_tool(self, account, tool, result): ...
    def reserve_local_action(self, account, proposal_id, fingerprint): ...
    def release_local_action(self, account, proposal_id, fingerprint): ...
    def undo_local_change(self, account, proposal_id): ...
    def decide(self, account, id, fingerprint, approved, authority='user'): ...
    def user_approved(self, account, id): ...
    def apply(self, account, id, policy_revision=None): ...


from email_agent.application.organization_contract import ORGANIZATION_PLANNING, PublicationBusy


class LocalOrganizationAgent:
    """Observe persisted grounded frames; plan narrowly; act; read back; learn policies."""
    READS = {'search_messages','read_message_content','inspect_thread','inspect_categories','inspect_existing_groups'}
    WRITES = {'create_local_category','rename_local_category','create_subcategory','assign_messages_to_category',
        'update_message_status','merge_local_groups','publish_category_to_sidebar','record_classification_evidence','undo_organization_change'}

    def __init__(self, organizer, store: LocalOrganizationPort):
        self.organizer, self.store = organizer, store

    def registry(self):
        # A small typed tool surface: only stored, validated proposal/evidence IDs
        # can mutate state. Creation/assignment is one atomic verified transaction.
        return {name: {'arguments': ['proposal_id'] if name in self.WRITES-{'update_message_status','record_classification_evidence'} else ['message_id'] if name in {'read_message_content','inspect_thread','update_message_status','record_classification_evidence'} else ['query'] if name=='search_messages' else [],
                       'scope':'local','external_effects':False} for name in sorted(self.READS|self.WRITES)}

    def _supported(self, account, plan):
        if plan['action']=='merge' and plan.get('source')=='maintenance':
            from email_agent.application.taxonomy_fit import equivalent_source
            retired=[op['id'] for op in plan['operations'] if op['kind']=='retire']
            targets={a['target'] for a in plan.get('assignments',[])}
            return bool(len(retired)==1 and len(targets)==1 and plan.get('affected',0)<=50 and equivalent_source(retired[0],next(iter(targets)),self.store.catalog(account),self.store.grouping_snapshot(account)))
        if plan['action'] not in ('create','refine','rename_section','merge_sections'):return False
        if plan.get('affected',0)>50:return False
        if any(a['before']['category_id']!='other' for a in plan.get('assignments',[])):return False
        members=[self.store.message_row(account,id) for id in plan.get('evidence_fingerprints',{})]
        if not members:return False
        if any(not r or not r.get('content_complete') or r['decision']['source']=='manual' or r['input_fingerprint']!=row_fingerprint(r) for r in members):return False
        frames=[r.get('semantic') or {} for r in members]
        if any(f.get('taxonomy_quality_verified') is False for f in frames):return False
        if not supported_cluster_size(frames):return False
        if any(not f.get('understood') or f.get('analysis_reason') or not f.get('evidence') for f in frames):return False
        intents={f.get('primary_intent') for f in frames}
        if len(intents)!=1 or next(iter(intents)) in (None,'','unknown','Unknown','Message','message'):return False
        if plan['action']=='create' and not plan.get('policies'):return False
        # Never create unbounded sender-level taxonomy.
        if any(plan['name'].casefold()==(f.get('entity') or '').casefold() for f in frames):return False
        if plan['action']=='create' and any(op['kind']=='create' for op in plan['operations']):
            from email_agent.application.taxonomy_fit import fit
            structural=fit(frames,self.store.catalog(account))
            if plan.get('source')=='local' and (not structural or structural['target'] or structural['name'].casefold()!=plan['name'].casefold()):return False
        return True

    def execute(self, account: str, command: OrganizationCommand):
        self.organizer.account(account)
        tool=command.tool;args=command.arguments
        if tool not in self.registry():raise ValueError('Unknown local organization tool')
        expected=set(self.registry()[tool]['arguments'])
        if not isinstance(args,dict) or set(args)!=expected or any(not isinstance(v,str) or not 1<=len(v)<=200 for v in args.values()):raise ValueError('Invalid typed tool arguments')
        if tool=='inspect_categories':return self.store.catalog(account)
        if tool=='inspect_existing_groups':return self.store.proposals(account)
        if tool=='search_messages':
            q=args['query'].casefold()
            return [dict(message_id=r['message_id'],subject=r['subject'],category=r['decision']['category_id']) for r in self.store.grouping_snapshot(account) if q in (r['subject']+' '+r['body']).casefold()][:50]
        if 'message_id' in args:
            row=self.store.message_row(account,args['message_id'])
            if not row:raise ValueError('Message not found in authorized account')
            if tool=='read_message_content':return dict(subject=row['subject'],headers=row['headers'],body=row['body'],complete=row['content_complete'],untrusted=True)
            if tool=='inspect_thread':return [r['message_id'] for r in self.store.grouping_snapshot(account) if (row.get('semantic') or {}).get('thread_key') and (r.get('semantic') or {}).get('thread_key')==row['semantic']['thread_key']][:50]
        policy=self.store.local_policy(account)
        if not policy['enabled'] and tool!='undo_organization_change':raise ValueError('Enable autonomous local organization for this account first')
        try:
            if tool in ('update_message_status','record_classification_evidence'):
                f=row.get('semantic') or {}
                if not row['content_complete'] or row['input_fingerprint']!=row_fingerprint(row) or not f.get('understood') or f.get('analysis_reason') or not f.get('evidence'):raise ValueError('Current complete grounded evidence required')
                if tool=='update_message_status' and not f.get('status'):raise ValueError('No supported workflow outcome')
                # Status/evidence is the canonical persisted frame consumed by mailbox facets.
                result=dict(status='verified',message_id=row['message_id'],workflow_status=f.get('status'),evidence_count=len(f['evidence']))
            elif tool=='undo_organization_change':result=self.store.undo_local_change(account,args['proposal_id'])
            elif tool=='publish_category_to_sidebar':
                p=next((p for p in self.store.proposals(account) if p['id']==args['proposal_id']),None)
                if not p or p['status']!='applied':raise ValueError('Category creation has not committed')
                ids={op['id'] for op in p['payload']['operations'] if op['kind']=='create'}
                visible={c['id'] for c in self.store.catalog(account)}
                if not ids or not ids<=visible:raise ValueError('Sidebar publication verification failed')
                result=dict(status='verified',category_ids=sorted(ids))
            else:
                p=next((p for p in self.store.proposals(account) if p['id']==args['proposal_id']),None)
                if not p:raise ValueError('Unknown account-local plan')
                if p['status']=='applied':return self.verify(account,p)
                actions={'create_local_category':{'create'},'create_subcategory':{'refine','rename_section','merge_sections'},'rename_local_category':{'rename'},'merge_local_groups':{'merge','merge_sections'},'assign_messages_to_category':{'create','split'}}
                if p['payload']['action'] not in actions[tool] or not self._supported(account,p['payload']):return dict(status='needs_approval',proposal_id=p['id'])
                self.store.reserve_local_action(account,p['id'],p['fingerprint'])
                self.store.decide(account,p['id'],p['fingerprint'],True,authority='standing_policy')
                self.store.apply(account,p['id'],policy_revision=policy['revision'])
                result=self.verify(account,p)
            self.store.record_tool(account,tool,result)
            return result
        except Exception as exc:
            if isinstance(exc,PublicationBusy):self.store.release_local_action(account,p['id'],p['fingerprint'])
            self.store.record_tool(account,tool,dict(status='failed',error_kind=type(exc).__name__))
            raise

    def verify(self, account, proposal):
        plan=proposal['payload'];catalog={c['id'] for c in self.store.catalog(account)}
        latest=next(p for p in self.store.proposals(account) if p['id']==proposal['id'])
        ids=latest['payload'].get('category_ids') or sorted({op['id'] for op in plan['operations'] if op['kind']=='create'}|{a['target'] for a in plan['assignments']})
        if latest['status']!='applied' or not set(ids)<=catalog:raise ValueError('Persisted category publication failed')
        for a in plan['assignments']:
            row=self.store.message_row(account,a['message_id'])
            if not row or row['decision']['category_id']!=a['target']:raise ValueError('Persisted message assignment failed')
        return dict(status='verified',proposal_id=proposal['id'],category_ids=ids,assigned=len(plan['assignments']))

    def step(self, account):
        if not self.store.local_policy(account)['enabled']:return dict(status='disabled',applied=0)
        results=[]
        for p in self.store.proposals(account):
            if len(results)>=4:break
            if p['status']=='approved' and self.store.user_approved(account,p['id']):
                try:
                    self.store.reserve_local_action(account,p['id'],p['fingerprint'])
                    self.store.apply(account,p['id'])
                    result=self.verify(account,p);self.store.record_tool(account,'apply_approved_plan',result);results.append(result)
                except ValueError as exc:
                    if isinstance(exc,PublicationBusy):self.store.release_local_action(account,p['id'],p['fingerprint'])
                    self.store.record_tool(account,'apply_approved_plan',dict(status='waiting' if isinstance(exc,PublicationBusy) else 'failed',proposal_id=p['id'],error=str(exc)[:180]))
                continue
            if p['status'] not in ('suggested','approved'):continue
            if not self._supported(account,p['payload']):
                if p['status']=='suggested' and p['payload']['action']=='create' and p['payload'].get('source')=='local':self.store.defer_proposal(account,p['id'],'Awaiting current complete grounded category evidence; message facets retained')
                continue
            tool='merge_local_groups' if p['payload']['action']=='merge' else 'create_local_category' if p['payload']['action']=='create' else 'create_subcategory'
            try:results.append(self.execute(account,OrganizationCommand(tool,{'proposal_id':p['id']})))
            except ValueError:continue  # persisted failure; stale evidence re-planned next bounded cycle
        return dict(status='active',applied=sum(r['status']=='verified' for r in results),results=results)

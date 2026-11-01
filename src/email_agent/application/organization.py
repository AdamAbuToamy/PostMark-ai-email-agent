"""Account-scoped grouped decisions; never authorizes Gmail or AI effects."""
from collections import Counter
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from typing import Protocol
from email_agent.application.classify_messages import decide, VERSION
from email_agent.application.semantic_mail import SEMANTIC_VERSION
from email_agent.application.local_understanding import cluster_identity
from email_agent.application.mail_management import assess
from email_agent.application.smart_sections import attention_state
from email_agent.domain.message import EmailMessage


@dataclass(frozen=True)
class OrganizationItem:
    message: EmailMessage
    decision: dict
    protection: str | None = None
    attention: str | None = None
    body: str | None = None
    semantic: dict | None = None


class OrganizationStore(Protocol):
    def decisions(self, account: str) -> dict[str,str]: ...
    def snapshot(self, account: str, limit: int) -> tuple[list[OrganizationItem], int]: ...
    def item_cache(self, account: str, version: str) -> dict: ...
    def save_item_cache(self, account: str, version: str, items: dict) -> None: ...
    def apply(self, account: str, key: str, action: str, category: str,
              items: list[OrganizationItem]) -> int: ...


class Organization:
    LIMIT = 10000
    CACHE_VERSION = 'v1:'+VERSION+':'+SEMANTIC_VERSION

    def __init__(self, store: OrganizationStore, registry, exclusions):
        self.store, self.registry, self.exclusions = store, registry, exclusions
        self._group_cache={}
        self._item_cache={}

    @staticmethod
    def validate(account):
        if account not in ('personal', 'university'):
            raise ValueError('Unknown account')

    def _groups(self, account):
        self.validate(account)
        rows, total = self.store.snapshot(account, self.LIMIT)
        decisions = self.store.decisions(account)
        exclusions = self.exclusions(account)
        fingerprint=hashlib.sha256(json.dumps([total,decisions,exclusions],sort_keys=True).encode())
        signatures=[]
        for item in rows:
            encoded=json.dumps(dict(message=asdict(item.message),decision=item.decision,protection=item.protection,attention=item.attention,body=item.body,semantic=item.semantic),sort_keys=True).encode();fingerprint.update(encoded);signatures.append(hashlib.sha256(encoded).hexdigest())
        revision=fingerprint.hexdigest()
        cached=self._group_cache.get(account)
        if cached and cached[0]==revision:return cached[1]
        groups = {}
        uncertain = []
        counts = Counter()
        previous=self._item_cache.get(account)
        if previous is None:previous=self.store.item_cache(account,self.CACHE_VERSION)
        next_items={}
        policy_key=hashlib.sha256(json.dumps(exclusions,sort_keys=True).encode()).hexdigest()
        for item,signature in zip(rows,signatures):
            message, current = item.message, item.decision
            memo=previous.get(message.id)
            same=bool(memo and memo[0]==signature and memo[1]==policy_key)
            if same:category,status,reason=memo[2]
            elif current.get('status')=='classified' and current.get('category_id'):
                category,status,reason=current['category_id'],current['status'],current.get('reason','')
            else:category, status, reason = decide(message,item.body or '')
            # Manual feedback partitions groups and never becomes a learned ad rule.
            if current.get('source') == 'manual':
                category, status, reason = current['category_id'], current['status'], 'manual_override'
            elif (current.get('category_id') == 'job_applications' or current.get('reason')=='approved_semantic_policy') and current.get('status') == 'classified':
                category, status, reason = current['category_id'], current['status'], current.get('reason', '')
            if status != 'classified' and current.get('source') != 'manual' and len(uncertain)<10:
                uncertain.append(dict(message_id=message.id,subject=message.subject))
            key, name, icon = cluster_identity(message, category, reason)
            semantic=item.semantic or {}
            semantically_named=semantic.get('understood') and semantic.get('confidence',0)>=.85 and semantic.get('primary_intent') and current.get('source')!='manual'
            if semantically_named:
                key='semantic_'+hashlib.sha256(json.dumps([category,semantic['primary_intent'],semantic.get('message_type')]).encode()).hexdigest()[:20]
                name=semantic['primary_intent'];icon='folder-open'
            if current.get('source') == 'manual':
                key += '_manual_' + category
            proposed = dict(category_id=category, status=status, source='automatic')
            assessment = memo[3] if same else assess(message, current if current.get('source') == 'manual' else proposed,
                                exclusions, item.protection,item.body)
            next_items[message.id]=(signature,policy_key,(category,status,reason),assessment)
            counts['attention_'+('protected' if item.protection else item.attention or attention_state(message,current))] += 1
            counts['organized'] += current.get('status') == 'classified'
            counts['locally_understood'] += status == 'classified'
            counts[assessment['verdict']] += 1
            counts['unknown_labels'] += message.label_ids is None
            group = groups.setdefault(key, dict(key=key, name=name, icon_key=icon,
                category_id=category, status=status, count=0, protected=0,
                eligible=0, examples=[], _items=[], _fingerprints=[], _semantic=bool(semantically_named)))
            group['count'] += 1
            group['protected'] += assessment['verdict'] == 'protected'
            group['eligible'] += assessment['verdict'] == 'eligible'
            group['_items'].append(replace(item,body=None,semantic=None))
            group['_fingerprints'].append(dict(message=asdict(message), decision=current, assessment=assessment, attention=item.attention,semantic=item.semantic))
            if len(group['examples']) < 3:
                group['examples'].append(dict(message_id=message.id, subject=message.subject))
        results = []
        individual = 0
        for group in groups.values():
            if group['count'] < 2:
                individual += group['count']
                continue
            group['fingerprint'] = hashlib.sha256(json.dumps(group.pop('_fingerprints'), sort_keys=True).encode()).hexdigest()
            group['boundaries'] = 'Exact local semantic subtype or list identity + purpose. Manual overrides are preserved. No Gmail action.'
            group['approved'] = all(i.decision.get('reason') == 'approved_recurring_pattern' for i in group['_items'])
            group['state'] = decisions.get(group['key']) or ('applied' if group['approved'] else 'untouched' if all(not i.protection and (i.attention or attention_state(i.message,i.decision))=='untouched' for i in group['_items']) else 'protected' if all(bool(i.protection) or (i.attention or attention_state(i.message,i.decision))=='protected' for i in group['_items']) else 'organized' if all(i.decision.get('status')=='classified' for i in group['_items']) and group['key'] not in ('github_ci_failed','github_ci','github_pr') else 'pending')
            if group['_semantic'] and group['state']=='pending':
                group['state']='protected' if group['protected'] else 'organized'
                group['boundaries']='Meaning supported by semantic evidence. Structural suggestions require their own representative approval.'
            if group['key']=='awaiting_semantic_analysis' and group['state'] not in ('applied','dismissed'):
                group['state']='untouched'
                group['boundaries']='Awaiting bounded content/semantic analysis; retained without Gmail action.'
            group['can_approve'] = group['state']=='pending' and not group['approved'] and group['status'] == 'classified' and not any(i.decision.get('source') == 'manual' for i in group['_items'])
            results.append(group)
        results.sort(key=lambda g: (-g['count'], g['key']))
        result=results, dict(counts, uncertain_examples=uncertain, indexed=total, analyzed=len(rows), truncated=total > len(rows),
                             recurring_groups=len(results), individual_messages=individual,
                             suggested_categories=sum(g['can_approve'] and g['key'] in ('github_ci_failed','github_ci','github_pr') for g in results), analysis_source='local')
        changed={k:v for k,v in next_items.items() if previous.get(k)!=v}
        if changed:self.store.save_item_cache(account,self.CACHE_VERSION,changed)
        self._item_cache[account]=next_items
        self._group_cache[account]=(revision,result)
        return result

    def overview(self, account, offset=0, limit=12, state='all'):
        if offset < 0 or not 1 <= limit <= 50:
            raise ValueError('Invalid group page')
        groups, summary = self._groups(account)
        if state not in ('all','pending','applied','dismissed','organized','protected','untouched'):raise ValueError('Invalid group state')
        summary['pending_decisions']=sum(g['state']=='pending' for g in groups)
        summary['applied_decisions']=sum(g['state']=='applied' for g in groups)
        if state!='all':groups=[g for g in groups if g['state']==state]
        return dict(summary=summary, groups=[{k:v for k,v in g.items() if not k.startswith('_')}
            for g in groups[offset:offset+limit]], total=len(groups),
            next_offset=offset+limit if offset+limit < len(groups) else None)

    def decide_group(self, account, key, fingerprint, action):
        if action not in ('approve', 'keep', 'exclude', 'dismiss'):
            raise ValueError('Unsupported group decision')
        groups, summary = self._groups(account)
        group = next((g for g in groups if g['key'] == key), None)
        if not group or group['fingerprint'] != fingerprint or summary['truncated']:
            raise ValueError('Group changed or preview is incomplete; refresh before approving')
        if action == 'approve' and not group['can_approve']:
            raise ValueError('Uncertain or manually classified messages require individual review')
        category = group['category_id']
        if action == 'approve' and key in ('github_ci_failed', 'github_ci', 'github_pr'):
            category = 'github_ci' if key.startswith('github_ci') else 'github_pr'
            name = 'GitHub / CI' if category == 'github_ci' else 'GitHub / Pull requests'
            self.registry.propose(category, name, 'Explicitly approved recurring GitHub notification structure')
            proposal = next((p for p in self.registry.proposals() if p['category_id'] == category and p['status'] == 'pending'), None)
            if proposal:
                self.registry.approve(proposal['id'])
        # Rejected or inactive category definitions cannot be silently published.
        if action == 'approve' and category not in {c['id'] for c in self.registry.categories(account)}:
            raise ValueError('Approve the proposed category structure first')
        changed = self.store.apply(account, key, action, category, group['_items'])
        return dict(status='saved', affected=changed, category_id=category,
                    gmail_changes=0, ai_requests=0)

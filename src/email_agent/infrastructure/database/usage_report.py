"""Read-only reported usage and effects. Unknown usage is never zero-priced."""
from datetime import datetime, timezone
import json
import time
import sqlite3
from contextlib import closing
from pathlib import Path


def report(store, account='personal', start=None, end=None, provider=None, model=None, task=None, offset=0):
    now=time.time();end=now if end is None else end
    start=datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp() if start is None else start
    if account not in ('personal','university','combined') or not 0<=start<end<=now+60 or end-start>31*86400 or offset<0:raise ValueError('Choose a valid account and a range of at most 31 days')
    where=['a.created_at>=?','a.created_at<?'];args=[start,end]
    for field,value in [('account_id',account if account!='combined' else None),('provider',provider),('model',model)]:
        if value:
            if not isinstance(value,str) or len(value)>180:raise ValueError('Invalid usage filter')
            where.append('a.'+field+'=?');args.append(value)
    # Only explicit recorded purposes or identifiable test/group scopes are attributed.
    purpose="COALESCE(json_extract(a.usage,'$.purpose'),CASE WHEN a.task_key LIKE 'qual:%' OR a.task_key LIKE 'eval:%' THEN 'qualification' WHEN a.task_key LIKE 'probe:%' THEN 'connection_test' WHEN a.task_key LIKE 'group:%' THEN 'taxonomy' ELSE 'mail_legacy' END)"
    if task:
        if task not in ('production','reclassification','taxonomy','qualification','connection_test','mail_legacy'):raise ValueError('Invalid task type')
        where.append(purpose+'=?');args.append(task)
    query="""SELECT a.id,a.account_id,a.provider,a.model,a.status,a.created_at,a.seconds,a.error,a.task_key,a.usage,EXISTS(SELECT 1 FROM hybrid_attempt_messages x WHERE x.attempt_id=+a.id AND x.account_id=a.account_id) mapped,
        COALESCE((SELECT MAX(json_extract(e.detail,'$.verified')) FROM hybrid_events e WHERE e.event='organization_verified' AND json_extract(e.detail,'$.attempt_id')=+a.id),0) verified,
        (SELECT MAX(json_extract(e.detail,'$.category_changes')) FROM hybrid_events e WHERE e.event='organization_verified' AND json_extract(e.detail,'$.attempt_id')=+a.id) saved_changes,
        (SELECT MAX(json_extract(e.detail,'$.newly_organized')) FROM hybrid_events e WHERE e.event='organization_verified' AND json_extract(e.detail,'$.attempt_id')=+a.id) newly_organized,
        EXISTS(SELECT 1 FROM hybrid_events e WHERE e.event='organization_verified' AND json_extract(e.detail,'$.attempt_id')=+a.id) evaluated
        ,(SELECT json_extract(e.detail,'$.cost_usd') FROM hybrid_events e WHERE e.event='inference_finished' AND json_extract(e.detail,'$.attempt_id')=+a.id ORDER BY e.id DESC LIMIT 1) cost_usd
        FROM hybrid_attempts a WHERE """+' AND '.join(where)+' ORDER BY a.id DESC'
    def blank():return dict(requests=0,completed=0,failed=0,interrupted=0,retries=0,input_tokens=0,output_tokens=0,total_tokens=0,cached_tokens=0,reasoning_tokens=0,unknown_input=0,unknown_output=0,unknown_usage=0,unknown_cached=0,unknown_reasoning=0,verified=0,saved_changes=0,unchanged_confirmations=0,evaluated_uncertain=0,newly_organized=0,unknown_newly_organized=0,unmapped_requests=0,known_cost_usd=0,unknown_cost_requests=0)
    def add(target,row,u):
        target['requests']+=1
        target['unmapped_requests']+=not row['mapped']
        target['unknown_cost_requests']+=row['cost_usd'] is None
        target['known_cost_usd']+=row['cost_usd'] or 0
        for state in ('completed','failed','interrupted'):target[state]+=row['status']==('done' if state=='completed' else state)
        target['retries']+='#retry' in row['task_key'];target['verified']+=bool(row['verified']);target['saved_changes']+=row['saved_changes'] or 0
        target['unchanged_confirmations']+=bool(row['verified'] and row['saved_changes']==0)
        target['evaluated_uncertain']+=bool(row['evaluated'] and not row['verified'])
        target['newly_organized']+=int(row['newly_organized'] or 0);target['unknown_newly_organized']+=row['newly_organized'] is None
        for field,key,unknown in [('input_tokens','prompt_tokens','unknown_input'),('output_tokens','output_tokens','unknown_output'),('cached_tokens','cached_tokens','unknown_cached'),('reasoning_tokens','reasoning_tokens','unknown_reasoning')]:
            value=u.get(key,u.get('thinking_tokens') if field=='reasoning_tokens' else None)
            if type(value)is int:target[field]+=value
            else:target[unknown]+=1
        total=u.get('total_tokens')
        if type(total)is not int and all(type(u.get(k))is int for k in ('prompt_tokens','output_tokens')):total=u['prompt_tokens']+u['output_tokens']+u.get('thinking_tokens',0)
        if type(total)is int:target['total_tokens']+=total
        else:target['unknown_usage']+=1
    summary=blank();groups={};records=[];components={};component_samples=0
    with store.connect() as db:
        # Stream usage rows instead of retaining bodies, prompts or response payloads.
        for raw in db.execute(query,args):
            row=dict(raw);u=json.loads(row.pop('usage') or '{}');kind=u.get('purpose') or ('qualification' if row['task_key'].startswith(('qual:','eval:')) else 'connection_test' if row['task_key'].startswith('probe:') else 'taxonomy' if row['task_key'].startswith('group:') else 'mail_legacy')
            add(summary,row,u);key=(row['account_id'],row['provider'],row['model'],kind)
            if key not in groups:groups[key]=dict(account=key[0],provider=key[1],model=key[2],task=kind,**blank())
            add(groups[key],row,u)
            parts=u.get('input_components_bytes')
            if parts:
                component_samples+=1
                for k,v in parts.items():components[k]=components.get(k,0)+v
            if offset<=summary['requests']-1<offset+50:
                ids=[r[0] for r in db.execute('SELECT message_id FROM hybrid_attempt_messages WHERE attempt_id=? AND account_id=?',(row['id'],row['account_id']))]
                records.append(dict(id=row['id'],account=row['account_id'],provider=row['provider'],model=row['model'],task=kind,status=row['status'],started_at=row['created_at'],seconds=row['seconds'],message_ids=ids,message_mapping='recorded' if ids else 'not_recorded',input_tokens=u.get('prompt_tokens'),output_tokens=u.get('output_tokens'),cached_tokens=u.get('cached_tokens'),reasoning_tokens=u.get('reasoning_tokens',u.get('thinking_tokens')),verified=bool(row['verified']),saved_changes=row['saved_changes'],cost_usd=row['cost_usd']))
    legacy=None
    path=Path(store.path).parent/'ai_usage.db'
    if path.is_file() and not provider and not task:
        with closing(sqlite3.connect(path,timeout=.1)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_provider_usage'").fetchone():
                clauses=['created_at>=?','created_at<?'];values=[datetime.fromtimestamp(start,timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),datetime.fromtimestamp(end,timezone.utc).strftime('%Y-%m-%d %H:%M:%S')]
                if account!='combined':clauses.append('account_id=?');values.append(account)
                if model:clauses.append('model=?');values.append(model)
                rows=db.execute("SELECT model,COUNT(*),SUM(json_extract(metrics,'$.prompt_tokens')),SUM(json_extract(metrics,'$.output_tokens')),SUM(json_extract(metrics,'$.total_tokens')) FROM ai_provider_usage WHERE "+' AND '.join(clauses)+' GROUP BY model',values).fetchall()
                legacy=dict(rows=[dict(model=r[0],entries=r[1],input_tokens=r[2],output_tokens=r[3],total_tokens=r[4]) for r in rows],source='Legacy recorded provider metadata; overlap with task ledger cannot be reconstructed, excluded from totals')
    summary['tokens_per_saved_change']=summary['total_tokens']/summary['saved_changes'] if summary['saved_changes'] else None
    summary['tokens_per_verified_result']=summary['total_tokens']/summary['verified'] if summary['verified'] else None
    return dict(legacy=legacy,account=account,start=start,end=end,observed_at=now,summary=summary,groups=list(groups.values()),records=records,offset=offset,next_offset=offset+50 if offset+50<summary['requests'] else None,component_bytes=dict(values=components,samples=component_samples,kind='UTF-8 byte estimates; not provider token attribution'),cost=dict(usd=summary['known_cost_usd'] if not summary['unknown_cost_requests'] else None,known_usd=summary['known_cost_usd'],unknown_requests=summary['unknown_cost_requests'],source='Persisted request cost evidence; missing cost is unknown'),anomalies=dict(repeated_uncertain=summary['evaluated_uncertain'],recovery_retries=summary['retries'],route_protection='Unchanged insufficient evidence is excluded per model; other providers continue'),definitions='Request-start cohort. Cached/reasoning tokens are subsets where reported, not added twice. Legacy first analysis versus reclassification is unattributed. No bodies, prompts, keys or provider response text.')

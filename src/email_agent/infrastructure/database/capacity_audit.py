"""Observed inference accounting; never claims local counters are provider balances."""
from collections import defaultdict
from datetime import datetime,timezone,timedelta
import json
import re
import time
from email_agent.application.hybrid_ai import PROVIDERS,ModelCapability
from email_agent.infrastructure.ai.resource_monitor import resources


def bucket(row):
    recorded=json.loads(row['usage'] or '{}').get('purpose')
    if recorded in ('production','reclassification','taxonomy'):return recorded
    key=row['task_key']
    purpose='qualification' if key.startswith(('qual:','eval:')) else 'connection_test' if key.startswith('probe:') else 'taxonomy' if key.startswith('group:') else 'production'
    return purpose


def usage(rows):
    result=dict(requests=len(rows),input_tokens=0,output_tokens=0,reported_tokens=0,reasoning_tokens=None,unreported_usage_requests=0,retries=0,failed=0)
    for r in rows:
        u=json.loads(r['usage'] or '{}')
        result['unreported_usage_requests']+=not any(k in u for k in ('prompt_tokens','output_tokens'))
        for field,key in [('input_tokens','prompt_tokens'),('output_tokens','output_tokens')]:result[field]+=u.get(key,0) if type(u.get(key)) is int else 0
        reported=u.get('total_tokens')
        if type(reported)is int:result['reported_tokens']+=reported
        else:result['reported_tokens']+=sum(u.get(k,0) for k in ('prompt_tokens','output_tokens','thinking_tokens') if type(u.get(k)) is int)
        thinking=u.get('reasoning_tokens',u.get('thinking_tokens'))
        if type(thinking)is int:result['reasoning_tokens']=(result['reasoning_tokens'] or 0)+thinking
        result['retries']+='#retry' in r['task_key'];result['failed']+=r['status']=='failed'
    return result


def capacity_snapshot(store,account):
    now=time.time();start=datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    with store.connect() as db:
        attempts=[dict(r) for r in db.execute('SELECT * FROM hybrid_attempts WHERE created_at>=? ORDER BY id',(start,))]
        health=[dict(r) for r in db.execute('SELECT * FROM hybrid_health')]
        diagnostics={r['attempt_id']:json.loads(r['payload']) for r in db.execute('SELECT * FROM hybrid_attempt_diagnostics')}
    models=store.models();allowed={(m.provider,m.model) for m in store.candidates(account)};providers=[]
    for name in PROVIDERS:
        config=store.provider(name);global_rows=[r for r in attempts if r['provider']==name];own=[r for r in global_rows if r['account_id']==account]
        breakdown={}
        for label in ('production','reclassification','qualification','connection_test','taxonomy'):
            breakdown[label]=usage([r for r in own if bucket(r)==label])
        # Disjoint UI buckets; underlying purpose remains separately available.
        daily={}
        for label in ('mail','testing','retries_errors','other'):
            selected=[]
            for r in own:
                kind='retries_errors' if r['status']=='failed' or '#retry' in r['task_key'] else 'testing' if bucket(r) in ('qualification','connection_test') else 'mail' if bucket(r) in ('production','reclassification') else 'other'
                if kind==label:selected.append(r)
            daily[label]=usage(selected)
        details=[]
        for model in [m for m in models if m['provider']==name]:
            cap=ModelCapability(**model['payload']);all_model=[r for r in global_rows if r['model']==cap.model];my_model=[r for r in own if r['model']==cap.model]
            headers={};observed=None
            for r in reversed(all_model):
                headers=json.loads(r['usage'] or '{}').get('quota_headers') or diagnostics.get(r['id'],{}).get('context',{}).get('quota_headers') or {}
                if headers:observed=r['created_at'];break
            circuits=[h for h in health if h['provider']==name and h['model'] in ('*',cap.model)]
            until=max((h['until'] for h in circuits),default=0)
            last429=next((r for r in reversed(all_model) if r['error']=='provider_rate_limited'),None)
            diagnostic=diagnostics.get(last429['id'],{}) if last429 else {}
            remaining={k:v for k,v in headers.items() if 'remaining' in k};limits={k:v for k,v in headers.items() if 'limit' in k and 'remaining' not in k and 'reset' not in k};resets={k:v for k,v in headers.items() if 'reset' in k or k=='retry-after'}
            quota_error=diagnostic.get('reason','');known_daily=re.search(r'limit:\s*(\d+).*?requests per day',quota_error,re.I|re.S)
            if known_daily:
                limits['requests_per_day']=int(known_daily[1])
                if until>now and not remaining:
                    remaining={'requests_remaining':'0 (last daily-quota error)'}
                    observed=last429['created_at']
            status='Disabled' if not config['enabled'] else 'Exhausted' if until>now and last429 and ('per day' in quota_error.lower() or diagnostic.get('context',{}).get('limit_window')=='day') else 'Limited' if until>now else 'Waiting' if (name,cap.model) not in allowed or not cap.validated or not store.healthy(name,cap.model) or not store.capacity(name) else 'Ready'
            profile=next((json.loads(r['usage'] or '{}').get('input_components_bytes') for r in reversed(all_model) if json.loads(r['usage'] or '{}').get('input_components_bytes')),None)
            details.append(dict(latest_request_component_bytes=profile,model=cap.model,status=status,account_usage=usage(my_model),project_observed_usage=usage(all_model),remaining=remaining or None,limits=limits or None,resets=resets or None,retry_at=until if until>now else None,last_429=dict(at=last429['created_at'],retry_after=diagnostic.get('retry_after'),kind='provider_rate_limited') if last429 else None,capacity_source='provider_response_headers' if headers else 'provider_quota_error' if known_daily else 'not_reported',capacity_observed_at=observed,scope='Response limit scope is provider-defined; project counters include only requests observed by this app'))
        active=next((r['model'] for r in reversed(global_rows) if r['status']=='running' and r.get('lease_until',0)>now),None)
        last=global_rows[-1]['model'] if global_rows else None
        provider_status='Disabled' if not config['enabled'] else 'Ready' if any(m['status']=='Ready' for m in details) else 'Exhausted' if any(m['status']=='Exhausted' for m in details) else 'Limited' if any(m['status']=='Limited' for m in details) else 'Waiting'
        provider=dict(provider=name,active_model=active,last_model=last,status=provider_status,usage=usage(own),project_observed_usage=usage(global_rows),breakdown=breakdown,daily_breakdown=daily,models=details,remaining_source='Local usage is observed, not provider/project balance')
        if name=='cloudflare':provider['allocation']=dict(value=10000,unit='neurons/day',source='official_public_free_plan',remaining=None,scope='Entire Cloudflare account',reset_at=(datetime.fromtimestamp(start,timezone.utc)+timedelta(days=1)).timestamp(),billing_condition='Workers Free plan only; paid-required models excluded')
        if name=='sambanova':provider['eligibility_note']='Current official plan requires payment method and purchased credits; no verified free inference capacity'
        providers.append(provider)
    return dict(account=account,window_start=start,window_end=now,window='UTC day; older production records do not separate first analysis from reclassification',usage_source='Persisted provider-reported tokens; missing usage remains unknown. Reasoning may be included in output; do not add it again.',providers=providers,resources=resources.snapshot())

"""Read-only routing explanations and measured worker throughput; no inference."""
import time
from email_agent.application.hybrid_ai import PROVIDERS


def routing_diagnostics(store,account,configured):
    now=time.time();partition={};candidates=store.candidates(account);models=store.models();providers=[]
    with store.connect() as db:
        states={r['status']:r['n'] for r in db.execute('SELECT status,COUNT(*) n FROM hybrid_tasks WHERE account_id=? GROUP BY status',(account,))}
        unassigned=db.execute("SELECT COUNT(*) FROM hybrid_tasks WHERE account_id=? AND provider IS NULL AND status IN ('ready_local','waiting_cloud','failed','waiting_body','blocked')",(account,)).fetchone()[0]
        pending=db.execute("SELECT status,reason,attempts,due,lease,COUNT(*) n FROM hybrid_tasks WHERE account_id=? AND status IN ('ready_local','waiting_cloud','failed','waiting_body','blocked','local_processing','cloud_processing') GROUP BY status,reason,attempts,due,lease",(account,)).fetchall()
        for task in pending:
            state,reason=task['status'],task['reason'] or ''
            cause='waiting_body' if state=='waiting_body' else 'processing' if state.endswith('_processing') and task['lease']>now else 'policy_or_unreadable' if state=='blocked' else 'recoverable_failure' if state=='failed' or state.endswith('_processing') or task['attempts']>=3 else 'rate_limit_cooldown' if 'rate-limit' in reason else 'retry_cooldown' if task['due']>now else 'ready_not_dispatched' if reason in ('Ready for eligible provider','Queued for evaluated local AI') else 'model_qualification_or_context' if reason=='Awaiting model qualification or context capacity' else 'account_consent' if reason=='Awaiting account consent' else 'budget_or_provider_capacity' if reason=='Awaiting authorized budget or provider capacity' else 'awaiting_eligibility_check'
            partition[cause]=partition.get(cause,0)+task['n']

        for name in PROVIDERS:
            p=store.provider(name);ms=[m for m in models if m['provider']==name];reasons=[]
            grants=db.execute('SELECT * FROM hybrid_grants WHERE account_id=? AND provider=? AND enabled=1',(account,name)).fetchall()
            if not configured(name):reasons.append('missing_key')
            if not p['enabled']:reasons.append('provider_disabled')
            if name!='ollama' and (not store.account_allows(account,name) or not grants and not store.standing(account,name)):reasons.append('account_consent_or_budget_missing')
            qualified=[m for m in ms if m['payload']['validated'] and m['payload']['structured']]
            if not qualified:reasons.append('model_quality_not_evaluated')
            if ms and not any(store.payment_allowed(account,__cap(m)) for m in ms):reasons.append('cost_not_authorized_or_free_eligibility_unknown')
            if qualified and name!='ollama' and not any(store.model_checked(account,name,m['model']) for m in qualified):reasons.append('current_credential_generation_not_verified')
            if p['last_state']=='offline':reasons.append('connection_unavailable')
            if p['enabled'] and ms and not any(store.healthy(name,m['model']) for m in ms):reasons.append('provider_disabled_or_circuit_open')
            if name!='ollama' and store.shared_quota and not store.shared_quota.free_automation(name) and store.shared_quota.status()['remaining']<1:reasons.append('shared_request_ceiling_reached')
            for g in ([] if store.standing(account,name) else grants):
                used,inp,out=db.execute('SELECT COUNT(*),COALESCE(SUM(input_bound),0),COALESCE(SUM(output_bound),0) FROM hybrid_attempts WHERE grant_id=?',(g['id'],)).fetchone()
                if used>=g['request_cap'] or inp+5000>g['input_cap'] or out+2048>g['output_cap']:reasons.append('approved_budget_insufficient')
            attempts={r['status']:r['n'] for r in db.execute("SELECT status,COUNT(*) n FROM hybrid_attempts WHERE account_id=? AND provider=? AND created_at>=? AND task_key NOT LIKE 'qual:%' AND task_key NOT LIKE 'probe:%' AND task_key NOT LIKE 'eval:%' GROUP BY status",(account,name,now-3600))}
            waiting=db.execute("SELECT COUNT(*) FROM hybrid_tasks WHERE account_id=? AND provider=? AND status IN ('ready_local','waiting_cloud','failed','blocked')",(account,name)).fetchone()[0]
            providers.append(dict(provider=name,reasons=list(dict.fromkeys(reasons)),discovered=len(ms),qualified=len(qualified),qualified_languages=sorted({lang for m in qualified for lang,score in m['payload']['quality'].items() if score>=.9}),routable=sum(m.provider==name for m in candidates),last_provider_backlog=waiting,attempts_started_1h=attempts))
        moved=db.execute("SELECT COUNT(DISTINCT message_id) FROM classification_audit WHERE account_id=? AND created_at>=datetime('now','-1 hour') AND json_extract(before_json,'$.category_id')='other' AND json_extract(after_json,'$.category_id')!='other'",(account,)).fetchone()[0]
        last=db.execute('SELECT MAX(created_at) FROM hybrid_attempts WHERE account_id=?',(account,)).fetchone()[0]
        activity=db.execute('SELECT MAX(created_at) FROM automation_events WHERE account_id=?',(account,)).fetchone()[0]
        legacy=[]
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='automation_ai_attempts'").fetchone():
            legacy=[dict(r) for r in db.execute("SELECT c.model,COUNT(*) successful_requests FROM organizer_campaigns c JOIN automation_ai_attempts a ON a.campaign_id=c.id JOIN automation_ai_grants g ON g.id=a.grant_id WHERE g.account_id=? AND a.kind='generate' AND a.status='done' GROUP BY c.model",(account,))]
    return dict(providers=providers,states=states,unassigned_waiting=unassigned,pending_partition=partition,pending_total=sum(partition.values()),moved_1h=moved,last_inference_started=last,last_worker_activity=activity,completed_1h=sum(p['attempts_started_1h'].get('done',0) for p in providers),legacy_generation_evidence=legacy,measurement='Attempts started in the last60 minutes, by actual stored status; Backlog by last attempted provider is not an inference reservation; unassigned work is shared')


def __cap(row):
    from email_agent.application.hybrid_ai import ModelCapability
    return ModelCapability(**row['payload'])

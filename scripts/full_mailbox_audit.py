"""Read-only, privacy-preserving production delivery metrics; no network access."""
import argparse
from collections import Counter
from contextlib import closing,contextmanager
import json
from pathlib import Path
import sqlite3
from datetime import datetime,timezone
from email_agent.infrastructure.database.continuous_store import ContinuousStore
from email_agent.application.mail_management import retention_reason_kind


def connect(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    return db


class ReadOnlyProgress(ContinuousStore):
    @contextmanager
    def connect(self):
        with closing(connect(self.path)) as db:yield db


def report(root,baseline):
    root,baseline=Path(root),Path(baseline)
    progress=ReadOnlyProgress(root/'data/email_agent.db',None)
    result=dict(measured_at_utc=datetime.now(timezone.utc).isoformat(),accounts={})
    with closing(connect(root/'data/email_agent.db')) as db,closing(connect(baseline/'email_agent.db')) as before:
        db.execute('BEGIN');before.execute('BEGIN')
        for account in ('personal','university'):
            state=progress.progress(account);reasons=Counter();other=0
            for row in db.execute("""SELECT a.source,c.status body_status,f.payload,l.status local_status,b.status body_work FROM message_category_assignments a
                LEFT JOIN message_content_state c USING(account_id,message_id) LEFT JOIN semantic_frames f USING(account_id,message_id)
                LEFT JOIN mail_work l ON l.account_id=a.account_id AND l.message_id=a.message_id AND l.stage='local'
                LEFT JOIN mail_work b ON b.account_id=a.account_id AND b.message_id=a.message_id AND b.stage='body'
                WHERE a.account_id=? AND a.category_id='other'""",(account,)):
                frame=json.loads(row['payload'] or '{}');other+=1
                cause='manual_other' if row['source']=='manual' else 'no_readable_body' if row['body_work'] in ('empty','attachment_only') else 'gmail_read_failed' if row['body_work']=='failed' else 'waiting_body' if row['body_status']!='complete' else 'waiting_local' if row['local_status'] in ('pending','retry','running') else 'semantic_conflict' if str(frame.get('analysis_reason') or '').startswith(('conflicting','negated')) else 'waiting_semantic_understanding' if not frame.get('understood') or frame.get('analysis_reason') else 'understood_other_without_coherent_taxonomy'
                reasons[cause]+=1
            protections=Counter()
            for row in db.execute('SELECT verdict,reason,COUNT(*) n FROM cleanup_assessments WHERE account_id=? GROUP BY verdict,reason',(account,)):
                protections[retention_reason_kind(row['verdict'],row['reason']) if row['verdict']=='protected' else row['verdict']]+=row['n']
            initial_other=before.execute("SELECT COUNT(*) FROM message_category_assignments WHERE account_id=? AND category_id='other'",(account,)).fetchone()[0]
            initial_ids={r[0] for r in before.execute("SELECT message_id FROM message_category_assignments WHERE account_id=? AND category_id='other'",(account,))}
            existing_other=sum(1 for r in db.execute("SELECT message_id FROM message_category_assignments WHERE account_id=? AND category_id='other'",(account,)) if r[0] in initial_ids)
            initial_indexed=before.execute('SELECT COUNT(*) FROM messages WHERE account_id=?',(account,)).fetchone()[0]
            result['accounts'][account]=dict(coverage=state['coverage'],inventory_complete=state['inventory_complete'],incremental_pending=state['incremental_pending'],indexed=state['indexed'],newly_indexed=state['indexed']-initial_indexed,other_before=initial_other,other_after=other,initial_other_cohort_remaining=existing_other,other_causes=dict(reasons),cleanup=dict(protections),queue_states=state['queues'])
            initial_complete={r[0] for r in before.execute("SELECT message_id FROM message_content_state WHERE account_id=? AND status='complete'",(account,))}
            current_complete={r[0] for r in db.execute("SELECT message_id FROM message_content_state WHERE account_id=? AND status='complete'",(account,))}
            old_failures={r[0] for r in before.execute("SELECT message_id FROM mail_work WHERE account_id=? AND stage='body' AND status='failed'",(account,))}
            initial_assignments={r['message_id']:r['category_id'] for r in before.execute('SELECT message_id,category_id FROM message_category_assignments WHERE account_id=?',(account,))}
            corrections=sum(r['message_id'] in initial_assignments and r['category_id']!=initial_assignments[r['message_id']] for r in db.execute("SELECT message_id,category_id FROM message_category_assignments WHERE account_id=? AND source!='manual'",(account,)))
            result['accounts'][account].update(new_complete_bodies=len(current_complete-initial_complete),old_body_failures_recovered=len(current_complete&old_failures),persistent_category_corrections=corrections,unassessed_local=state['indexed']-sum(protections.values()))
            result['accounts'][account]['organized_by_source']=[dict(r) for r in db.execute("""SELECT a.source,COUNT(*) n FROM message_category_assignments a JOIN message_content_state c USING(account_id,message_id) JOIN semantic_frames f USING(account_id,message_id)
                WHERE a.account_id=? AND c.status='complete' AND a.status='classified' AND (a.source='manual' OR (json_extract(f.payload,'$.understood')=1 AND json_extract(f.payload,'$.analysis_reason') IS NULL))
                AND NOT EXISTS(SELECT 1 FROM mail_work w WHERE w.account_id=a.account_id AND w.message_id=a.message_id AND w.stage IN ('semantic','local') AND w.status NOT IN ('done','local_resolved')) GROUP BY a.source""",(account,))]
        reservations=dict(db.execute('SELECT kind,COUNT(*) FROM automation_ai_attempts GROUP BY kind'))
        result['gemini']=dict(attempts=reservations,totals=dict(db.execute('SELECT SUM(input_reserved) input_reserved,SUM(output_reserved) output_reserved FROM automation_ai_attempts').fetchone()),grants=[dict(r) for r in db.execute('SELECT account_id,request_cap,input_cap,output_cap,reason FROM automation_ai_grants WHERE enabled=1')])
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='automation_ai_settlements'").fetchone():
            from email_agent.infrastructure.database.automation_ai_store import AutomationAIStore
            result['gemini']['charged']={r['id']:dict(zip(('requests','input','output'),AutomationAIStore._charged(db,r['id']))) for r in db.execute('SELECT id FROM automation_ai_grants WHERE enabled=1')}
        status=Counter();issues=Counter();submitted=0
        for row in db.execute("SELECT b.result FROM organizer_batches b WHERE b.campaign_id IN (SELECT DISTINCT campaign_id FROM automation_ai_attempts)"):
            data=json.loads(row[0] or '{}');submitted+=data.get('messages',0)
            for issue in data.get('validation_issues',[]):issues[issue['code']]+=1
        result['gemini']['accepted_frames']=submitted;result['gemini']['validation_issues']=dict(issues)
        provider_errors=Counter()
        for row in db.execute("SELECT DISTINCT b.campaign_id,b.ordinal,b.result FROM organizer_batches b JOIN automation_ai_attempts a ON a.campaign_id=b.campaign_id WHERE b.status='failed'"):
            data=json.loads(row['result'] or '{}');diagnostic=data.get('diagnostic') or {}
            provider_errors[str(diagnostic.get('http_status') or data.get('error_kind') or data.get('failure_stage') or 'unknown')]+=1
        result['gemini']['failed_batch_causes']=dict(provider_errors)
        result['gemini']['attempt_states']=[dict(r) for r in db.execute('SELECT kind,status,COUNT(*) n FROM automation_ai_attempts GROUP BY kind,status')]
        old_categories=set()
        with closing(connect(baseline/'categories.db')) as b:old_categories={r[0] for r in b.execute('SELECT id FROM categories')}
        with closing(connect(root/'data/categories.db')) as cats:
            result['new_categories']=[dict(r) for r in cats.execute("SELECT c.id,c.name,c.icon_key,o.account_id FROM categories c LEFT JOIN local_category_accounts o ON o.category_id=c.id WHERE c.source!='retired'") if r['id'] not in old_categories]
        result['patterns']=db.execute('SELECT COUNT(*) FROM semantic_content_patterns WHERE enabled=1').fetchone()[0]
        manual=lambda connection:[tuple(r) for r in connection.execute("SELECT * FROM message_category_assignments WHERE source='manual' ORDER BY account_id,message_id")]
        result['manual_unchanged']=manual(db)==manual(before)
        policies=lambda c:[tuple(r) for r in c.execute('SELECT account_id,enabled,mode,exclusions,revision,enabled_at_ms,approved_at,paused_reason FROM cleanup_policy ORDER BY account_id')]
        result['cleanup_policy_unchanged']=policies(db)==policies(before)
        result['ai_preferences_unchanged']=[tuple(r) for r in db.execute('SELECT * FROM chat_preferences ORDER BY account_id')]==[tuple(r) for r in before.execute('SELECT * FROM chat_preferences ORDER BY account_id')]
        old_max=before.execute('SELECT COALESCE(MAX(id),0) FROM cleanup_audit').fetchone()[0]
        result['cleanup_events_since_baseline']=[dict(r) for r in db.execute('SELECT event,COUNT(*) n FROM cleanup_audit WHERE id>? GROUP BY event',(old_max,))]
        prior_ids={r[0] for r in before.execute('SELECT id FROM action_previews')}
        changes=Counter((r['kind'],r['status']) for r in db.execute('SELECT id,kind,status FROM action_previews') if r['id'] not in prior_ids)
        result['action_states_since_baseline']=[dict(kind=k,status=s,n=n) for (k,s),n in changes.items()]
        labels=root/'backups/autonomous-20261028T215341Z/actual-evaluation-labels.json'
        quality=[]
        if labels.exists():
            for label in json.loads(labels.read_text()):
                row=db.execute('SELECT a.category_id,c.digest FROM message_category_assignments a JOIN message_content_state c USING(account_id,message_id) WHERE a.account_id=? AND a.message_id=?',(label['account_id'],label['message_id'])).fetchone()
                if row:quality.append(dict(alias=label['alias'],expected=label['expected_category'],actual=row['category_id'],body_digest_unchanged=row['digest']==label['digest']))
        result['real_evaluation']=dict(reviewed=len(quality),correct=sum(r['expected']==r['actual'] for r in quality),unchanged_bodies=sum(r['body_digest_unchanged'] for r in quality),scope='Previously independently annotated stratified sample; not population accuracy',cases=quality)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',default=Path(__file__).resolve().parents[1]);p.add_argument('--baseline',required=True);args=p.parse_args()
    print(json.dumps(report(args.root,args.baseline),ensure_ascii=False,indent=2))

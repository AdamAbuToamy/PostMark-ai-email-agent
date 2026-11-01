"""Read-only aggregate delivery audit; never prints IDs, subjects or bodies."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3


def connect(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;return db


def report(baseline=None):
    root=Path(__file__).resolve().parents[1];result=dict(measured_at_utc=datetime.now(timezone.utc).isoformat(),accounts={})
    with closing(connect(root/'data/email_agent.db')) as db:
        db.execute('BEGIN')
        for account in ('personal','university'):
            count=lambda query:db.execute(query,(account,)).fetchone()[0]
            state=db.execute('SELECT pages_completed,completed FROM history_progress WHERE account_id=?',(account,)).fetchone()
            legacy=db.execute('SELECT legacy_pages FROM history_observation_runs WHERE account_id=?',(account,)).fetchone()
            observed={r[0] for r in db.execute('SELECT message_id FROM history_observations WHERE account_id=?',(account,))}
            archives=db.execute('SELECT state,observations FROM automation_history_archive WHERE account_id=?',(account,)).fetchall()
            for archive in archives:observed.update(r['message_id'] for r in json.loads(archive['observations']))
            result['accounts'][account]=dict(observed_ids=count('SELECT COUNT(*) FROM history_observations WHERE account_id=?'),metadata_fetched_in_inventory=count('SELECT COUNT(*) FROM history_observations WHERE account_id=? AND fetched=1'),indexed=count('SELECT COUNT(*) FROM messages WHERE account_id=?'),bodies_complete=count("SELECT COUNT(*) FROM message_content_state WHERE account_id=? AND status='complete'"),inventory_pages=state['pages_completed'],inventory_complete=bool(state['completed'] and not (legacy and legacy[0])),legacy_metrics_partial=bool(legacy and legacy[0]),queues=[dict(r) for r in db.execute('SELECT stage,status,COUNT(*) count FROM mail_work WHERE account_id=? GROUP BY stage,status',(account,))],failures=[dict(r) for r in db.execute("SELECT stage,error_kind,COUNT(*) count FROM mail_work WHERE account_id=? AND status IN ('failed','retry','attachment_only','empty','uncertain') GROUP BY stage,error_kind",(account,))],fully_organized=count("SELECT COUNT(*) FROM message_category_assignments a JOIN message_content_state c USING(account_id,message_id) JOIN semantic_frames f USING(account_id,message_id) WHERE a.account_id=? AND c.status='complete' AND a.status='classified' AND (a.source='manual' OR (json_extract(f.payload,'$.understood')=1 AND json_extract(f.payload,'$.analysis_reason') IS NULL)) AND NOT EXISTS(SELECT 1 FROM mail_work w WHERE w.account_id=a.account_id AND w.message_id=a.message_id AND w.stage IN ('semantic','local') AND w.status NOT IN ('done','local_resolved'))"))
            result['accounts'][account]['observed_unique_across_traversals']=len(observed)
        result['ai_grants']=[dict(r) for r in db.execute('SELECT account_id,model,max_messages,request_cap,input_cap,output_cap,reason FROM automation_ai_grants')]
        result['ai_reservations']=dict(db.execute('SELECT kind,COUNT(*) FROM automation_ai_attempts GROUP BY kind'))
        result['ai_reserved_tokens']=dict(db.execute('SELECT SUM(input_reserved) input_reserved,SUM(output_reserved) output_reserved FROM automation_ai_attempts').fetchone())
        result['ai_attempts']=[dict(r) for r in db.execute('SELECT kind,status,COUNT(*) count FROM automation_ai_attempts GROUP BY kind,status')]
        accepted=submitted=0;reasons=Counter();unique=set()
        for row in db.execute("SELECT b.input_json,b.result FROM organizer_batches b JOIN automation_ai_attempts a ON a.campaign_id=b.campaign_id WHERE a.kind='generate'"):
            data=json.loads(row['input_json']);out=json.loads(row['result'] or '{}');submitted+=len(data['messages']);accepted+=out.get('messages',0);unique.update((data['account'],id) for id in data['private_ids'].values());reasons.update(i['code'] for i in out.get('validation_issues',[]) if 'id' in i)
        result['ai_messages']=dict(submitted=submitted,unique=len(unique),accepted=accepted,rejected_reasons=dict(reasons))
        result['proposals']=[dict(account=r['account_id'],status=r['status'],name=json.loads(r['payload'])['name'],affected=json.loads(r['payload'])['affected']) for r in db.execute('SELECT account_id,status,payload FROM semantic_taxonomy_proposals')]
        if baseline:
            with closing(connect(Path(baseline)/'email_agent.db')) as old:
                keys=lambda connection,table:[tuple(r) for r in connection.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                result['unchanged']={t:keys(old,t)==keys(db,t) for t in ['chat_preferences','cleanup_policy','action_audit','cleanup_audit','cleanup_runs','subscription_manual_audit']}
                manual=lambda connection:[tuple(r) for r in connection.execute("SELECT * FROM message_category_assignments WHERE source='manual' ORDER BY account_id,message_id")]
                result['unchanged']['manual_decisions']=manual(old)==manual(db)
                previous={(r['account_id'],r['message_id']):r['category_id'] for r in old.execute('SELECT * FROM message_category_assignments')};changes=Counter()
                for row in db.execute('SELECT * FROM message_category_assignments'):
                    before=previous.get((row['account_id'],row['message_id']))
                    if before and before!=row['category_id']:changes[before+' -> '+row['category_id']]+=1
                result['primary_category_corrections']=dict(changes);result['primary_category_corrections_total']=sum(changes.values())
    with closing(connect(root/'data/categories.db')) as categories:result['published_categories']=[dict(r) for r in categories.execute("SELECT id,name,source FROM categories WHERE source!='retired'")]
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--baseline');args=p.parse_args();print(json.dumps(report(args.baseline),ensure_ascii=False,indent=2))

"""Anonymized read-only local evidence. Does not initialize stores or call providers."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parseaddr
import json
from pathlib import Path
import sqlite3
from email_agent.application.classify_messages import decide, VERSION
from email_agent.application.local_understanding import cluster_identity
from email_agent.application.mail_management import assess
from email_agent.application.subscriptions import subscription_identity
from email_agent.domain.message import EmailMessage


def inspect(path):
    report = dict(captured_at=datetime.now(timezone.utc).isoformat(),source='read-only local SQLite snapshot',
                  rule_version=VERSION,gmail_requests=0,gemini_requests=0,accounts={})
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN')
        for account in ('personal','university'):
            rows=db.execute('''SELECT m.*,h.headers,a.category_id,a.source,a.status,a.reason,a.rule_version
                FROM messages m LEFT JOIN message_list_headers h USING(account_id,message_id)
                LEFT JOIN message_category_assignments a USING(account_id,message_id)
                WHERE m.account_id=? ORDER BY m.message_id''',(account,)).fetchall()
            protections={(r['scope'],r['identity']):r['reason'] for r in db.execute('SELECT * FROM cleanup_protections WHERE account_id=?',(account,))}
            policy=db.execute('SELECT exclusions FROM cleanup_policy WHERE account_id=?',(account,)).fetchone()
            exclusions=json.loads(policy[0]) if policy else []
            baseline=Counter();projected=Counter();verdicts=Counter();reasons=Counter();clusters=Counter();github=Counter();labels=Counter();versions=Counter()
            projected_assessments=[]
            for row in rows:
                message=EmailMessage(row['message_id'],account,row['sender'],row['subject'],row['internal_date_ms'],
                    tuple(json.loads(row['label_ids'])) if row['labels_known'] else None,
                    tuple(json.loads(row['headers']).items()) if row['headers'] else None)
                baseline[str(row['category_id'])+':'+str(row['status'])]+=1
                versions[str(row['rule_version'])]+=1
                category,status,reason=decide(message)
                decision=dict(category_id=category,status=status,source='automatic')
                if row['source']=='manual':decision={k:row[k] for k in ('category_id','status','source')}
                projected[decision['category_id']+':'+decision['status']]+=1
                identity,kind=subscription_identity(dict(message.list_headers or ()),message.sender)
                protection=next((protections[scope] for scope in [('message',message.id),('sender',parseaddr(message.sender)[1].casefold()),('list',identity)] if scope in protections),None)
                evaluation=assess(message,decision,exclusions,protection)
                verdicts[evaluation['verdict']]+=1;reasons[evaluation['reason']]+=1
                key,name,icon=cluster_identity(message,category,reason)
                clusters[key]+=1
                if key.startswith('github_'):github[key]+=1
                labels['known' if message.label_ids is not None else 'unknown']+=1
                if 'TRASH' in (message.label_ids or ()):labels['cached_trash']+=1
                if message.list_headers:labels['list_metadata_present']+=1
            stored=db.execute('SELECT verdict,reason,COUNT(*) n FROM cleanup_assessments WHERE account_id=? GROUP BY verdict,reason',(account,)).fetchall()
            history=db.execute('SELECT pages_completed,messages_processed,completed FROM history_progress WHERE account_id=?',(account,)).fetchone()
            report['accounts'][account]=dict(indexed=len(rows),metadata=dict(labels),stored_classification=dict(baseline),stored_rule_versions=dict(versions),
                stored_assessments=[dict(r) for r in stored],projected_local_classification=dict(projected),projected_cleanup=dict(verdicts),projected_protection_reasons=dict(reasons),
                recurring_groups=sum(n>=2 for n in clusters.values()),messages_in_recurring_groups=sum(n for n in clusters.values() if n>=2),
                github_subtypes=dict(github),gmail_history=dict(history) if history else None)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=Path(__file__).resolve().parents[1]/'data'/'email_agent.db')
    print(json.dumps(inspect(parser.parse_args().db),indent=2,ensure_ascii=False))

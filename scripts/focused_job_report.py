"""Read-only employment evidence audit. Never initializes stores or calls providers."""
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
from email_agent.application.classify_messages import decide
from email_agent.application.job_evidence import ADVERTISEMENT, employment_evidence
from email_agent.domain.message import EmailMessage


def analyze(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        db.execute('BEGIN')
        rows=db.execute('''SELECT m.*,a.category_id,a.source,a.reason,h.headers,
            substr(COALESCE(b.body_text,''),1,1200) body
            FROM messages m LEFT JOIN message_category_assignments a USING(account_id,message_id)
            LEFT JOIN message_list_headers h USING(account_id,message_id)
            LEFT JOIN message_search_content b USING(account_id,message_id)
            ORDER BY m.account_id,m.message_id''').fetchall()
    verified_threads=set()
    messages=[]
    for row in rows:
        headers=json.loads(row['headers']) if row['headers'] else {}
        message=EmailMessage(row['message_id'],row['account_id'],row['sender'],row['subject'],row['internal_date_ms'],
            tuple(json.loads(row['label_ids'])) if row['labels_known'] else None,tuple(headers.items()))
        evidence=employment_evidence(message.subject,message.sender,row['body'])
        if evidence or (row['source']=='manual' and row['category_id']=='job_applications' and 'Submitted application:' in (row['reason'] or '')):
            thread=headers.get('thread-id')
            if thread:verified_threads.add((message.account_id,thread))
        messages.append((message,row,evidence))
    direct=[];inherited=[];counts=Counter();manual=0
    for message,row,evidence in messages:
        if row['source']=='manual':
            manual+=1
            continue
        category,_,_=decide(message,row['body'])
        thread=dict(message.list_headers or ()).get('thread-id')
        verified=(message.account_id,thread) in verified_threads
        # Reuse classification's exact ad/course gate; same-account thread only.
        inherited_match=category=='other' and verified and not re.search(ADVERTISEMENT+r'|\b(course|training|newsletter|discount|sale)\b',message.subject.casefold())
        if row['category_id']!='job_applications' and (category=='job_applications' or inherited_match):
            (direct if evidence else inherited).append(message)
            state=(evidence or employment_evidence(message.subject,message.sender,row['body'],True)).state
            counts[message.account_id+'/'+state]+=1
    return dict(source='SQLite mode=ro; no provider calls',indexed=len(rows),candidate_count=len(direct)+len(inherited),
        evidence_counts=dict(counts),manual_decisions_preserved=manual),direct+inherited


if __name__=='__main__':
    report,_=analyze(Path(__file__).resolve().parents[1]/'data'/'email_agent.db')
    print(json.dumps(report,indent=2))

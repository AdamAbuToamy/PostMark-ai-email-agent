"""Bounded local purpose audit. No writes, Gmail, model calls or personal output."""
from collections import Counter
from contextlib import closing
from pathlib import Path
import json
import sqlite3
from email_agent.application.semantic_mail import local_frame,digest
from email_agent.application.classify_messages import decide
from email_agent.domain.message import EmailMessage

root=Path(__file__).resolve().parents[1]
report={};examples=[]
with closing(sqlite3.connect(f'file:{root}/data/email_agent.db?mode=ro',uri=True)) as db:
    db.row_factory=sqlite3.Row
    for account in ('personal','university'):
        rows=[]
        for category in ('bills','job_applications','orders','university','other'):
            rows.extend(db.execute('''SELECT m.*,a.category_id,a.source,a.reason,h.headers,b.body_text FROM messages m
                JOIN message_category_assignments a USING(account_id,message_id)
                LEFT JOIN message_list_headers h USING(account_id,message_id)
                LEFT JOIN message_search_content b USING(account_id,message_id)
                WHERE m.account_id=? AND a.category_id=? ORDER BY m.internal_date_ms DESC,m.message_id LIMIT 12''',(account,category)))
        transitions=Counter();causes=Counter();manual=0;body=0;jobs=0
        for row in rows:
            message=EmailMessage(row['message_id'],account,row['sender'],row['subject'],row['internal_date_ms'],tuple(json.loads(row['label_ids'])) if row['labels_known'] else None,tuple(json.loads(row['headers']).items()) if row['headers'] else None)
            text=row['body_text'] or '';body+=bool(text)
            category,status,reason=decide(message,text)
            if row['source']=='manual':manual+=1;continue
            if category!=row['category_id'] and category!='other' and status=='classified':
                transitions[row['category_id']+' -> '+category]+=1;jobs+=category=='job_applications'
                if len(examples)<8:examples.append(dict(example=digest([account,row['message_id']])[:8],before=row['category_id'],proposed=category,evidence='cached body' if decide(message,'')[0]!=category else 'local subject/headers',reason=reason))
            frame=local_frame(dict(account_id=account,message_id=message.id,sender=message.sender,subject=message.subject,internal_date_ms=message.internal_date_ms,labels=list(message.label_ids) if message.label_ids is not None else None,headers=dict(message.list_headers or ()),body=text,decision=dict(category_id=row['category_id'],source=row['source'],reason=row['reason'])))
            if not frame['understood']:causes[frame['analysis_reason']]+=1
        report[account]=dict(sampled=len(rows),cached_bodies=body,manual_preserved=manual,confident_purpose_corrections_available=dict(transitions),job_corrections_available=jobs,uncertainty=dict(causes))
print(json.dumps(dict(accounts=report,anonymized_examples=examples,applied=0,gmail_reads=0,model_calls=0),ensure_ascii=False))

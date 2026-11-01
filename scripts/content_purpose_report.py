"""Anonymized readonly comparison of cached message purpose; no initialization."""
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from email_agent.application.classify_messages import decide
from email_agent.application.content_understanding import content_purpose
from email_agent.domain.message import EmailMessage


def inspect(path):
    totals=Counter();examples=[]
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        rows=db.execute('''SELECT m.*,a.category_id,a.source,a.status,h.headers,
            substr(COALESCE(b.body_text,''),1,8000) body
            FROM messages m LEFT JOIN message_category_assignments a USING(account_id,message_id)
            LEFT JOIN message_search_content b USING(account_id,message_id)
            LEFT JOIN message_list_headers h USING(account_id,message_id)''').fetchall()
        for row in rows:
            if row['source']=='manual':continue
            message=EmailMessage(row['message_id'],row['account_id'],row['sender'],row['subject'],row['internal_date_ms'],
                tuple(json.loads(row['label_ids'])) if row['labels_known'] else None,
                tuple(json.loads(row['headers']).items()) if row['headers'] else None)
            category,status,reason=decide(message,row['body'])
            if category!=row['category_id'] and reason.startswith('content_'):
                basis='cached_body' if content_purpose(message.subject) is None and row['body'] else 'metadata_meaning'
                totals[row['account_id']+'/'+str(row['category_id'])+' -> '+category+'/'+basis]+=1
                if len(examples)<6:
                    examples.append(dict(before=row['category_id'],after=category,basis=basis,meaning=reason))
    return dict(source='SQLite mode=ro; no provider calls',indexed=len(rows),corrections=sum(totals.values()),counts=dict(totals),anonymous_examples=examples)


if __name__=='__main__':
    print(json.dumps(inspect(Path(__file__).resolve().parents[1]/'data'/'email_agent.db'),indent=2))

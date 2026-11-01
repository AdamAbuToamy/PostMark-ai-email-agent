"""Durable content audit queue. Bodies remain in the existing local search cache."""
from contextlib import closing
import json
import sqlite3


class SQLiteContentReviewStore:
    HOLD='Content purpose not verified; retained during content review'

    def __init__(self,path,messages):self.path,self.messages=path,messages

    def initialize(self):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('''CREATE TABLE IF NOT EXISTS content_review_queue(
                account_id TEXT NOT NULL,message_id TEXT NOT NULL,rule_version TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',basis TEXT,changed INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(account_id,message_id))''')
            db.execute('INSERT OR IGNORE INTO classification_schema_versions VALUES (7)')

    def prepare(self,account,version,retry_failed=False):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('''INSERT INTO content_review_queue(account_id,message_id,rule_version)
                SELECT a.account_id,a.message_id,? FROM message_category_assignments a
                LEFT JOIN message_search_content b USING(account_id,message_id)
                WHERE a.account_id=? AND a.source!='manual'
                ON CONFLICT(account_id,message_id) DO UPDATE SET rule_version=excluded.rule_version,
                state='pending',basis=NULL,changed=0 WHERE content_review_queue.rule_version!=excluded.rule_version''',(version,account))
            if retry_failed:db.execute("UPDATE content_review_queue SET state='pending' WHERE account_id=? AND state='failed'",(account,))
            # Preserve user protections; only this exact owned hold is released.
            db.execute('''INSERT OR IGNORE INTO cleanup_protections(account_id,scope,identity,reason)
                SELECT q.account_id,'message',q.message_id,? FROM content_review_queue q
                JOIN message_category_assignments a USING(account_id,message_id)
                WHERE q.account_id=? AND q.state IN ('pending','awaiting_body','failed') AND a.category_id='promotions' ''',(self.HOLD,account))

    def page(self,account,limit):
        with closing(sqlite3.connect(self.path)) as db:
            return [r[0] for r in db.execute('''SELECT q.message_id FROM content_review_queue q
                LEFT JOIN message_search_content b USING(account_id,message_id)
                WHERE q.account_id=? AND q.state IN ('pending','awaiting_body')
                ORDER BY (b.body_text IS NOT NULL) DESC,(q.state='pending') DESC,q.message_id LIMIT ?''',(account,limit))]

    def cached(self,account,id):
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT body_text FROM message_search_content WHERE account_id=? AND message_id=?',(account,id)).fetchone()
            return row[0] if row else ''

    def save(self,account,id,evidence):
        self.messages.record_search_content(account,id,evidence['body'])
        # Merge rather than discard provider thread/list identity already stored.
        message=self.messages.get(account,id)
        headers=dict(message.list_headers or ())
        headers.update({k:v for k,v in evidence.get('headers',{}).items() if k not in headers})
        if headers:self.messages.record_list_headers(account,id,headers)

    def finish(self,account,id,state,basis,changed):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('''UPDATE content_review_queue SET state=?,basis=?,changed=?,updated_at=CURRENT_TIMESTAMP
                WHERE account_id=? AND message_id=?''',(state,basis,int(changed),account,id))
            if state=='done' and (self.cached(account,id) or basis=='metadata'):
                db.execute("DELETE FROM cleanup_protections WHERE account_id=? AND scope='message' AND identity=? AND reason=?",(account,id,self.HOLD))

    def progress(self,account):
        with closing(sqlite3.connect(self.path)) as db:
            rows=db.execute('SELECT state,COUNT(*),SUM(changed) FROM content_review_queue WHERE account_id=? GROUP BY state',(account,)).fetchall()
            reasons=db.execute('SELECT basis,COUNT(*),SUM(changed) FROM content_review_queue WHERE account_id=? AND state=\'done\' GROUP BY basis',(account,)).fetchall()
        counts={r[0]:r[1] for r in rows}
        return dict(states=counts,reclassified=sum(r[2] or 0 for r in rows),complete=counts.get('pending',0)+counts.get('awaiting_body',0)==0,
            evidence=[dict(basis=r[0],processed=r[1],changed=r[2]) for r in reasons])

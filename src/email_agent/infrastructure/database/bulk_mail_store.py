"""Frozen account-local selections and durable per-message mutation receipts."""
from contextlib import closing,contextmanager
import json
import sqlite3
import time
from email_agent.domain.search_text import normalize_search_text

class SQLiteBulkMailStore:
    def __init__(self,path,actions):
        self.path,self.actions=path,actions
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS bulk_mail_runs(action_id TEXT PRIMARY KEY,approved INTEGER NOT NULL DEFAULT 0,lease REAL NOT NULL DEFAULT 0,due REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS bulk_mail_items(action_id TEXT,message_id TEXT,snapshot TEXT NOT NULL,state TEXT NOT NULL,result TEXT,PRIMARY KEY(action_id,message_id));
            CREATE INDEX IF NOT EXISTS bulk_mail_pending ON bulk_mail_items(action_id,state,message_id);
            CREATE TABLE IF NOT EXISTS permanent_mail_tombstones(account_id TEXT,message_id TEXT,operation_id TEXT,deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(account_id,message_id));
            ''')
    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path,timeout=20)) as db:
            with db:yield db
    def rows(self,account,scope):
        with self.connect() as db:
            db.row_factory=sqlite3.Row;db.create_function('fold',1,normalize_search_text,deterministic=True)
            query='''SELECT m.message_id FROM messages m LEFT JOIN message_category_assignments a USING(account_id,message_id)
            LEFT JOIN local_message_state s USING(account_id,message_id) LEFT JOIN message_search_content b USING(account_id,message_id)
            WHERE m.account_id=? AND NOT EXISTS(SELECT 1 FROM permanent_mail_tombstones t WHERE t.account_id=m.account_id AND t.message_id=m.message_id)
            AND (?='' OR instr(fold(m.subject),fold(?)) OR instr(fold(m.sender),fold(?)) OR instr(fold(COALESCE(b.body_text,'')),fold(?)))
            AND (? IS NULL OR a.category_id=?) AND (?='all' OR COALESCE(s.state,'new')=?)'''
            search=scope.get('search','');category=scope.get('category');state=scope.get('state','all')
            params=[account,search,search,search,search,category,category,state,state]
            if scope['view']=='trash':query+=" AND m.labels_known=1 AND EXISTS(SELECT 1 FROM json_each(m.label_ids) WHERE value='TRASH')"
            elif scope['view']=='spam':query+=" AND m.labels_known=1 AND EXISTS(SELECT 1 FROM json_each(m.label_ids) WHERE value='SPAM')"
            else:query+=" AND NOT EXISTS(SELECT 1 FROM json_each(m.label_ids) WHERE value IN ('TRASH','SPAM'))"
            if scope['view']=='eligible':query+=" AND EXISTS(SELECT 1 FROM cleanup_assessments e WHERE e.account_id=m.account_id AND e.message_id=m.message_id AND e.verdict='eligible')"
            # One SQLite read snapshot; pagination cannot drift as workers update mail.
            cursor=db.execute(query+' ORDER BY m.message_id',params)
            while rows:=cursor.fetchmany(200):
                yield from (dict(r) for r in rows)
    def frame(self,account,id):
        with self.connect() as db:
            r=db.execute('SELECT payload FROM semantic_frames WHERE account_id=? AND message_id=?',(account,id)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_frames'").fetchone() else None
        return json.loads(r[0]) if r else {}
    def body_digest(self,account,id):
        with self.connect() as db:r=db.execute('SELECT digest FROM message_content_state WHERE account_id=? AND message_id=?',(account,id)).fetchone()
        return r[0] if r else None
    def create(self,account,operation,criterion,items,excluded):
        preview=self.actions.preview(account,'gmail:bulk',dict(operation=operation,criterion=criterion,count=len(items),matched=len(items)+len(excluded),excluded=len(excluded),items=[]))
        with self.connect() as db:
            db.execute('INSERT INTO bulk_mail_runs(action_id) VALUES(?)',(preview['id'],))
            db.executemany('INSERT INTO bulk_mail_items VALUES(?,?,?,?,NULL)',[(preview['id'],i['message_id'],json.dumps(i),'pending') for i in items]+[(preview['id'],i['message_id'],json.dumps(i),'excluded') for i in excluded])
        return self.get(account,preview['id'])
    def get(self,account,id,offset=0):
        p=self.actions.get(account,id)
        if p['kind']!='gmail:bulk':raise ValueError('Not a bulk operation')
        with self.connect() as db:
            run=db.execute('SELECT approved,lease,due FROM bulk_mail_runs WHERE action_id=?',(id,)).fetchone()
            if run is None:raise ValueError('Selection was not committed; create a new preview')
            counts=dict(db.execute('SELECT state,count(*) FROM bulk_mail_items WHERE action_id=? GROUP BY state',(id,)))
            page=[{**json.loads(r[0]),'state':r[1],'result':json.loads(r[2]) if r[2] else None} for r in db.execute('SELECT snapshot,state,result FROM bulk_mail_items WHERE action_id=? ORDER BY message_id LIMIT 50 OFFSET ?',(id,offset))]
        return dict(id=id,account=account,status=p['status'],expires=p['expires'],**{k:v for k,v in p['payload'].items() if k!='items'},approved=bool(run[0]),lease=run[1],due=run[2],counts=counts,items=page,offset=offset,next_offset=offset+len(page) if offset+len(page)<p['payload']['matched'] else None)
    def approve(self,account,id,approved,confirmation):
        p=self.get(account,id)
        if not approved:raise ValueError('Explicit confirmation required')
        if p['operation']=='delete' and confirmation!=f"DELETE {p['count']}":raise ValueError('Confirm permanent deletion by typing DELETE and the exact count')
        if p['approved']:return p
        if not p['count']:raise ValueError('No eligible messages selected')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            count=db.execute("SELECT count(*) FROM bulk_mail_items WHERE action_id=? AND state='pending'",(id,)).fetchone()[0]
            if count!=p['count']:raise ValueError('Selection incomplete; create a new preview')
            if not db.execute("UPDATE action_previews SET status='running' WHERE id=? AND account_id=? AND status='pending' AND expires>?",(id,account,time.time())).rowcount:raise ValueError('Preview expired or already confirmed')
            db.execute('UPDATE bulk_mail_runs SET approved=1 WHERE action_id=?',(id,))
            db.execute("INSERT INTO action_audit(action_id,event,detail) VALUES(?,'bulk_approved',?)",(id,json.dumps(dict(account=account,operation=p['operation'],count=count,irreversible=p['operation']=='delete'))))
        return self.get(account,id)
    def lease(self,account,id):
        self.get(account,id)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');now=time.time()
            if not db.execute('UPDATE bulk_mail_runs SET lease=? WHERE action_id=? AND approved=1 AND lease<? AND due<=?',(now+600,id,now,now)).rowcount:return False
            # Never replay a write interrupted by a crash or lost HTTP response.
            db.execute("UPDATE bulk_mail_items SET state='uncertain',result=? WHERE action_id=? AND state='executing'",(json.dumps({'reason':'Interrupted write; inspect Gmail before any new action'}),id))
        return True
    def pending(self,id):
        with self.connect() as db:return [{**json.loads(r[0]),'_read_retries':json.loads(r[1] or '{}').get('read_retries',0)} for r in db.execute("SELECT snapshot,result FROM bulk_mail_items WHERE action_id=? AND state='pending' ORDER BY message_id LIMIT 3",(id,))]
    def uncertain(self,id):
        with self.connect() as db:return [json.loads(r[0]) for r in db.execute("SELECT snapshot FROM bulk_mail_items WHERE action_id=? AND state='uncertain' LIMIT 3",(id,))]
    def mark(self,id,message,state,result):
        with self.connect() as db:
            db.execute('UPDATE bulk_mail_items SET state=?,result=? WHERE action_id=? AND message_id=?',(state,json.dumps(result),id,message))
            db.execute('INSERT INTO action_audit(action_id,event,detail) VALUES(?,?,?)',(id,'bulk_'+state,json.dumps(dict(message_id=message,**result))))
    def mark_deleted(self,account,message,operation):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO permanent_mail_tombstones(account_id,message_id,operation_id) VALUES(?,?,?)',(account,message,operation))
            for table in ('messages','message_search_content','message_list_headers','message_content_state'):
                db.execute('DELETE FROM '+table+' WHERE account_id=? AND message_id=?',(account,message))
    def release(self,account,id,due=0):
        with self.connect() as db:db.execute('UPDATE bulk_mail_runs SET lease=0,due=? WHERE action_id=?',(due,id))
        p=self.get(account,id);counts=p['counts']
        if not counts.get('pending') and not counts.get('executing'):
            state='succeeded' if counts.get('succeeded',0)==p['count'] else 'partial' if counts.get('succeeded') else 'failed'
            self.actions.finish(account,id,state,dict(counts=counts,succeeded=counts.get('succeeded',0)))

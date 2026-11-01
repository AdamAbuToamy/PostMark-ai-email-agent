"""Atomic campaign checkpoints and installation-wide attempt reservation."""
from contextlib import closing
import json
import sqlite3
from email_agent.infrastructure.database.content_review_store import SQLiteContentReviewStore


class SQLiteContentFillStore(SQLiteContentReviewStore):
    def initialize(self):
        super().initialize()
        with closing(sqlite3.connect(self.path)) as db,db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS content_fill_campaigns(id TEXT PRIMARY KEY,attempt_cap INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS content_fill_items(campaign TEXT,ordinal INTEGER,account_id TEXT,message_id TEXT,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,result TEXT,PRIMARY KEY(campaign,account_id,message_id));
                CREATE TABLE IF NOT EXISTS content_fill_attempts(id INTEGER PRIMARY KEY,campaign TEXT,account_id TEXT,message_id TEXT,kind TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS message_content_state(account_id TEXT,message_id TEXT,status TEXT NOT NULL,characters INTEGER NOT NULL,digest TEXT NOT NULL,source TEXT NOT NULL,PRIMARY KEY(account_id,message_id));
                INSERT OR IGNORE INTO classification_schema_versions VALUES(8);
            ''')

    def create_fill(self,id,targets,attempt_cap):
        if not targets or len(targets)>64 or type(attempt_cap)is not int or not 1<=attempt_cap<=128:raise ValueError('Scope<=64 messages/128 attempts')
        if len(set(targets))!=len(targets) or any(a not in ('personal','university') for a,_ in targets):raise ValueError('Invalid content scope')
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT attempt_cap FROM content_fill_campaigns WHERE id=?',(id,)).fetchone()
            if previous:
                rows=db.execute('SELECT account_id,message_id FROM content_fill_items WHERE campaign=? ORDER BY ordinal',(id,)).fetchall()
                if rows!=targets or previous[0]!=attempt_cap:raise ValueError('Cannot expand an existing content campaign')
                return id
            db.execute('INSERT INTO content_fill_campaigns(id,attempt_cap) VALUES(?,?)',(id,attempt_cap))
            db.executemany('INSERT INTO content_fill_items(campaign,ordinal,account_id,message_id) VALUES(?,?,?,?)',[(id,i,a,m) for i,(a,m) in enumerate(targets)])
            return id

    def claim(self,campaign):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
            cap=db.execute('SELECT attempts,attempt_cap FROM content_fill_campaigns WHERE id=?',(campaign,)).fetchone()
            if not cap:raise ValueError('Unknown content campaign')
            if cap['attempts']>=cap['attempt_cap']:return None
            row=db.execute("SELECT * FROM content_fill_items WHERE campaign=? AND status='pending' ORDER BY ordinal LIMIT 1",(campaign,)).fetchone()
            if row:db.execute("UPDATE content_fill_items SET status='running' WHERE campaign=? AND ordinal=?",(campaign,row['ordinal']))
            return dict(row) if row else None

    def reserve_attempt(self,campaign,item,kind):
        if kind not in ('gmail_raw','oauth'):raise ValueError('Only readonly content/auth operations are budgeted')
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT attempts,status FROM content_fill_items WHERE campaign=? AND ordinal=?',(campaign,item['ordinal'])).fetchone()
            if not current or current[1]!='running':raise ValueError('No active content checkpoint')
            # Each message gets <=2 raw attempts; auth retries share global cap.
            if kind=='gmail_raw' and db.execute("SELECT COUNT(*) FROM content_fill_attempts WHERE campaign=? AND account_id=? AND message_id=? AND kind='gmail_raw'",(campaign,item['account_id'],item['message_id'])).fetchone()[0]>=2:raise ValueError('Message retry cap exhausted')
            if not db.execute('UPDATE content_fill_campaigns SET attempts=attempts+1 WHERE id=? AND attempts<attempt_cap',(campaign,)).rowcount:raise ValueError('Campaign attempt budget exhausted')
            db.execute('UPDATE content_fill_items SET attempts=attempts+1 WHERE campaign=? AND ordinal=?',(campaign,item['ordinal']))
            db.execute('INSERT INTO content_fill_attempts(campaign,account_id,message_id,kind) VALUES(?,?,?,?)',(campaign,item['account_id'],item['message_id'],kind))

    def cached_complete(self,account,id):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT 1 FROM message_content_state WHERE account_id=? AND message_id=? AND status='complete'",(account,id)).fetchone() is not None

    def save(self,account,id,evidence):
        import hashlib
        body=evidence['body'];self.messages.record_search_content(account,id,body,complete=True)
        message=self.messages.get(account,id);headers=dict(message.list_headers or ())
        headers.update({k:v for k,v in evidence.get('headers',{}).items() if k not in headers})
        if headers:self.messages.record_list_headers(account,id,headers)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("INSERT INTO message_content_state VALUES(?,?,'complete',?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET status=excluded.status,characters=excluded.characters,digest=excluded.digest,source=excluded.source",(account,id,len(body),hashlib.sha256(body.encode()).hexdigest(),evidence.get('source','gmail_raw')))
            db.execute('CREATE TABLE IF NOT EXISTS message_content_diagnostics(account_id TEXT,message_id TEXT,parser_version TEXT NOT NULL,decodings TEXT NOT NULL,PRIMARY KEY(account_id,message_id))')
            db.execute('INSERT INTO message_content_diagnostics VALUES(?,?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET parser_version=excluded.parser_version,decodings=excluded.decodings',(account,id,'mime-full-2',json.dumps(evidence.get('decodings',[]))))

    def finish_item(self,campaign,item,status,result):
        with closing(sqlite3.connect(self.path)) as db,db:
            previous=db.execute('SELECT result FROM content_fill_items WHERE campaign=? AND ordinal=?',(campaign,item['ordinal'])).fetchone()
            old=json.loads(previous[0] or '{}') if previous else {}
            if old.get('previous_failure'):result['prior_failures']=[*old.get('prior_failures',[]),old['previous_failure']]
            db.execute('UPDATE content_fill_items SET status=?,result=? WHERE campaign=? AND ordinal=?',(status,json.dumps(result),campaign,item['ordinal']))
            if status=='done':
                db.execute("UPDATE content_review_queue SET state='done',basis='gmail_body',changed=?,updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND message_id=?",(int(result.get('category_changed',False)),item['account_id'],item['message_id']))

    def reopen_parser_failure(self,campaign,ordinal):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT account_id,message_id,status,result FROM content_fill_items WHERE campaign=? AND ordinal=?",(campaign,ordinal)).fetchone()
            if not row or row[2]!='failed':raise ValueError('Only an explicitly reviewed failed checkpoint can reopen')
            count=db.execute("SELECT COUNT(*) FROM content_fill_attempts WHERE campaign=? AND account_id=? AND message_id=? AND kind='gmail_raw'",(campaign,row[0],row[1])).fetchone()[0]
            if count>=2:raise ValueError('Cannot reset the two-attempt message cap')
            old=json.loads(row[3] or '{}')
            db.execute("UPDATE content_fill_items SET status='pending',result=? WHERE campaign=? AND ordinal=?",(json.dumps(dict(previous_failure=old,reopened_for='explicit parser fix verification')),campaign,ordinal))

    def progress_fill(self,campaign):
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT attempts,attempt_cap FROM content_fill_campaigns WHERE id=?',(campaign,)).fetchone()
            if not row:raise ValueError('Unknown content campaign')
            states=dict(db.execute('SELECT status,COUNT(*) FROM content_fill_items WHERE campaign=? GROUP BY status',(campaign,)))
            changes=sum(json.loads(r[0] or '{}').get('category_changed',False) for r in db.execute('SELECT result FROM content_fill_items WHERE campaign=?',(campaign,)))
            kinds=dict(db.execute('SELECT kind,COUNT(*) FROM content_fill_attempts WHERE campaign=? GROUP BY kind',(campaign,)))
        return dict(campaign=campaign,states=states,attempts=row[0],attempt_cap=row[1],attempt_kinds=kinds,category_changes=changes,gmail_mutations=0,gemini_requests=0)

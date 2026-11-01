"""Additive mail management persistence: local scans, decisions and Gmail label pages."""
from contextlib import closing
import json
import sqlite3
import time
import uuid
from pathlib import Path
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository


class SQLiteMailStore:
    def __init__(self,path,events_path=None):
        self.path=path
        self.events_path=Path(events_path) if events_path is not None else None
        with closing(self.connect()) as db,db:
            db.execute("CREATE TABLE IF NOT EXISTS cleanup_assessments(account_id TEXT NOT NULL,message_id TEXT NOT NULL,verdict TEXT NOT NULL,reason TEXT NOT NULL,confidence TEXT NOT NULL,source TEXT NOT NULL,category_id TEXT,scan_id TEXT,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(account_id,message_id))")
            columns={row[1] for row in db.execute('PRAGMA table_info(cleanup_assessments)')}
            for column in ('sender_address','list_identity'):
                if column not in columns:db.execute('ALTER TABLE cleanup_assessments ADD COLUMN '+column+' TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS cleanup_assessment_sender ON cleanup_assessments(account_id,sender_address)')
            db.execute('CREATE INDEX IF NOT EXISTS cleanup_assessment_list ON cleanup_assessments(account_id,list_identity)')
            db.execute("CREATE INDEX IF NOT EXISTS cleanup_assessment_page ON cleanup_assessments(account_id,verdict,message_id)")
            db.execute("CREATE TABLE IF NOT EXISTS cleanup_protections(account_id TEXT NOT NULL,scope TEXT NOT NULL,identity TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(account_id,scope,identity))")
            db.execute("CREATE TABLE IF NOT EXISTS cleanup_local_scans(account_id TEXT PRIMARY KEY,id TEXT NOT NULL,cursor INTEGER NOT NULL,upper_rowid INTEGER NOT NULL,total INTEGER NOT NULL,scanned INTEGER NOT NULL,eligible INTEGER NOT NULL,protected INTEGER NOT NULL,needs_review INTEGER NOT NULL,errors INTEGER NOT NULL,status TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS cleanup_assessment_events(account_id TEXT PRIMARY KEY,watermark INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS mail_label_sync(account_id TEXT NOT NULL,label TEXT NOT NULL,id TEXT NOT NULL,next_token TEXT,listing_done INTEGER NOT NULL DEFAULT 0,recheck_cursor INTEGER NOT NULL DEFAULT 0,upper_rowid INTEGER NOT NULL,processed INTEGER NOT NULL DEFAULT 0,completed INTEGER NOT NULL DEFAULT 0,errors INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(account_id,label))")
            db.execute("CREATE TABLE IF NOT EXISTS mail_read_metrics(account_id TEXT NOT NULL,kind TEXT NOT NULL,unavailable INTEGER NOT NULL DEFAULT 0,read_errors INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(account_id,kind))")
            db.execute("CREATE INDEX IF NOT EXISTS cleanup_audit_message ON cleanup_audit(account_id,message_id,event,created_at)")
            db.execute("INSERT OR IGNORE INTO cleanup_schema_versions VALUES (2)")

    def connect(self):return sqlite3.connect(self.path,timeout=30)

    def protection_snapshot(self,account,messages):
        """Same precedence as protection(), with one account snapshot instead of N connections."""
        from email_agent.application.subscriptions import subscription_identity
        from email.utils import parseaddr
        with closing(self.connect()) as db:
            reasons={(s,i):r for s,i,r in db.execute('SELECT scope,identity,reason FROM cleanup_protections WHERE account_id=?',(account,))}
            unresolved=set();bulk_unresolved=set()
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='bulk_mail_items'").fetchone():
                bulk_unresolved={r[0] for r in db.execute("SELECT DISTINCT i.message_id FROM bulk_mail_items i JOIN action_previews p ON p.id=i.action_id WHERE p.account_id=? AND i.state IN ('executing','uncertain')",(account,))}
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='action_previews'").fetchone():
                unresolved={r[0] for r in db.execute("SELECT json_extract(i.value,'$.message_id') FROM action_previews p,json_each(p.payload,'$.items') i WHERE p.account_id=? AND p.kind IN ('gmail:trash','gmail:restore') AND (p.status='running' OR EXISTS(SELECT 1 FROM json_each(p.result,'$.items') r WHERE json_extract(r.value,'$.status')='uncertain'))",(account,))}
        result={}
        for message in messages:
            identity,kind=subscription_identity(dict(message.list_headers or ()),message.sender)
            scopes=[('message',message.id),('sender',parseaddr(message.sender)[1].casefold())]
            if kind!='promotional_sender':scopes.append(('list',identity))
            result[message.id]=next((reasons[s] for s in scopes if s in reasons),'Unresolved bulk Gmail operation; verify before another action' if message.id in bulk_unresolved else 'Unresolved Gmail operation; reconcile before another cleanup' if message.id in unresolved else None)
        return result

    def protection(self,account,message,ignore_action=None,ignore_system=False):
        from email_agent.application.subscriptions import subscription_identity
        from email.utils import parseaddr
        identity,kind=subscription_identity(dict(message.list_headers or ()),message.sender)
        scopes=[('message',message.id),('sender',parseaddr(message.sender)[1].casefold())]
        if kind!='promotional_sender':scopes.append(('list',identity))
        with closing(self.connect()) as db:
            for scope,value in scopes:
                row=db.execute("SELECT reason FROM cleanup_protections WHERE account_id=? AND scope=? AND identity=?",(account,scope,value)).fetchone()
                if row and (not ignore_system or row[0] not in {'Semantic sensitivity or unresolved meaning; retain for organization','Historical discovery; no historical cleanup approval','Content purpose not verified; retained during content review'}):return row[0]
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='action_previews'").fetchone():
                uncertain=db.execute("SELECT 1 FROM action_previews p WHERE p.account_id=? AND p.kind IN ('gmail:trash','gmail:restore') AND p.id!=? AND (p.status='running' OR EXISTS(SELECT 1 FROM json_each(p.result,'$.items') r WHERE json_extract(r.value,'$.status')='uncertain')) AND EXISTS(SELECT 1 FROM json_each(p.payload,'$.items') i WHERE json_extract(i.value,'$.message_id')=?) LIMIT 1",(account,ignore_action or '',message.id)).fetchone()
                if uncertain:return 'Unresolved Gmail operation; reconcile before another cleanup'
        return None

    def protect(self,account,scope,identity,reason):
        column={'message':'message_id','sender':'sender_address','list':'list_identity'}.get(scope)
        if column is None:raise ValueError('Unknown protection scope')
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT INTO cleanup_protections(account_id,scope,identity,reason) VALUES (?,?,?,?) ON CONFLICT(account_id,scope,identity) DO UPDATE SET reason=excluded.reason",(account,scope,identity,reason))
            db.execute("UPDATE cleanup_assessments SET verdict='protected',reason=?,confidence='Protected',updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND "+column+'=?',(reason,account,identity))
            db.execute("INSERT INTO cleanup_audit(account_id,message_id,event,detail) VALUES (?,?, 'protected',?)",(account,identity if scope=='message' else None,json.dumps({'scope':scope,'identity':identity,'reason':reason})))

    def keep_message(self,account,id):
        self.protect(account,'message',id,'Kept by user; never automatically clean this message')
        with closing(self.connect()) as db,db:db.execute("UPDATE cleanup_assessments SET verdict='protected',reason='Kept by user; never automatically clean this message',confidence='Protected',updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND message_id=?",(account,id))

    def _assessment(self,db,account,row,source,scan_id=None):
        db.execute("INSERT INTO cleanup_assessments(account_id,message_id,verdict,reason,confidence,source,category_id,scan_id) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET verdict=excluded.verdict,reason=excluded.reason,confidence=excluded.confidence,source=excluded.source,category_id=excluded.category_id,scan_id=excluded.scan_id,updated_at=CURRENT_TIMESTAMP",(account,row['message_id'],row['verdict'],row['reason'],row['confidence'],source,row.get('category_id'),scan_id))
        db.execute('UPDATE cleanup_assessments SET sender_address=?,list_identity=? WHERE account_id=? AND message_id=?',(row.get('sender_address'),row.get('list_identity'),account,row['message_id']))

    def record(self,account,rows,source='incremental'):
        with closing(self.connect()) as db,db:
            for row in rows:self._assessment(db,account,row,source)

    def scan_state(self,account):
        with closing(self.connect()) as db:
            db.row_factory=sqlite3.Row
            row=db.execute("SELECT * FROM cleanup_local_scans WHERE account_id=?",(account,)).fetchone()
        return dict(row) if row else None

    def start_scan(self,account):
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            total,upper=db.execute("SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM messages WHERE account_id=?",(account,)).fetchone()
            db.execute("INSERT OR REPLACE INTO cleanup_local_scans VALUES (?,?,0,?,?,0,0,0,0,0,?)",(account,uuid.uuid4().hex,upper,total,'paused' if total else 'completed'))
        return self.scan_state(account)

    def scan_page(self,account,state,limit):
        with closing(self.connect()) as db:
            return db.execute("SELECT rowid,message_id FROM messages WHERE account_id=? AND rowid>? AND rowid<=? ORDER BY rowid LIMIT ?",(account,state['cursor'],state['upper_rowid'],limit)).fetchall()

    def commit_scan(self,account,state,page,rows):
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM cleanup_local_scans WHERE account_id=? AND id=? AND cursor=?",(account,state['id'],state['cursor'])).fetchone():raise ValueError('Scan checkpoint changed; refresh progress')
            for row in rows:self._assessment(db,account,row,'historical',state['id'])
            cursor=page[-1][0] if page else state['upper_rowid']
            remains=db.execute("SELECT 1 FROM messages WHERE account_id=? AND rowid>? AND rowid<=? LIMIT 1",(account,cursor,state['upper_rowid'])).fetchone()
            counts=[sum(row['verdict']==v for row in rows) for v in ('eligible','protected','needs_review','error')]
            db.execute("UPDATE cleanup_local_scans SET cursor=?,scanned=scanned+?,eligible=eligible+?,protected=protected+?,needs_review=needs_review+?,errors=errors+?,status=? WHERE account_id=? AND id=?",(cursor,len(rows),*counts,'paused' if remains else 'completed',account,state['id']))
        return self.scan_state(account)

    def event_watermark(self,account):
        with closing(self.connect()) as db:
            row=db.execute('SELECT watermark FROM cleanup_assessment_events WHERE account_id=?',(account,)).fetchone()
        return row[0] if row else 0

    def commit_events(self,account,expected,events,rows,next_cursor=None):
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT watermark FROM cleanup_assessment_events WHERE account_id=?',(account,)).fetchone()
            if (row[0] if row else 0)!=expected:raise ValueError('Incremental assessment checkpoint changed')
            for row in rows:self._assessment(db,account,row,'incremental')
            cursor=next_cursor if next_cursor is not None else events[-1][0] if events else expected
            db.execute('INSERT INTO cleanup_assessment_events VALUES (?,?) ON CONFLICT(account_id) DO UPDATE SET watermark=excluded.watermark',(account,cursor))

    def summary(self,account):
        with closing(self.connect()) as db:
            total=db.execute('SELECT COUNT(*) FROM messages WHERE account_id=?',(account,)).fetchone()[0]
            counts=dict(db.execute('SELECT verdict,COUNT(*) FROM cleanup_assessments WHERE account_id=? GROUP BY verdict',(account,)))
        return {'indexed':total,'assessed':sum(counts.values()),'remaining_local':max(0,total-sum(counts.values())),**{v:counts.get(v,0) for v in ('eligible','protected','needs_review','error')}}

    def assessments(self,account,verdict,search,offset,limit):
        clause="a.account_id=? AND a.verdict IN ('protected','needs_review','error')" if verdict=='protected' else 'a.account_id=? AND a.verdict=?';params=[account] if verdict=='protected' else [account,verdict]
        if search:clause+=" AND (instr(lower(m.sender),lower(?))>0 OR instr(lower(m.subject),lower(?))>0)";params += [search,search]
        # Labels remain real Gmail labels; no local category can turn a message into Trash.
        with closing(self.connect()) as db:
            db.row_factory=sqlite3.Row
            total=db.execute('SELECT COUNT(*) FROM cleanup_assessments a JOIN messages m USING(account_id,message_id) WHERE '+clause,params).fetchone()[0]
            rows=db.execute("SELECT a.*,m.sender,m.subject,m.internal_date_ms,m.label_ids,c.category_id AS current_category,c.source AS classification_source FROM cleanup_assessments a JOIN messages m USING(account_id,message_id) LEFT JOIN message_category_assignments c USING(account_id,message_id) WHERE "+clause+" ORDER BY m.internal_date_ms DESC,m.message_id LIMIT ? OFFSET ?",(*params,limit,offset)).fetchall()
        return {'items':[dict(row) for row in rows],'total':total,'offset':offset,'next_offset':offset+limit if offset+limit<total else None}

    def history(self,account,search,offset,limit,message_id=None):
        query="""WITH operations AS (
          SELECT account_id,message_id,'automatic:'||message_id AS action_id,'trash' AS operation,status,detail,updated_at AS performed_at,'automatic' AS source
          FROM cleanup_items WHERE json_extract(detail,'$.operation')='trash' AND status IN ('succeeded','uncertain','failed','running')
          UNION ALL
          SELECT p.account_id,json_extract(i.value,'$.message_id'),p.id,substr(p.kind,7),json_extract(i.value,'$.status'),i.value,
            COALESCE((SELECT MAX(created_at) FROM action_audit WHERE action_id=p.id),'1970-01-01'),'reviewed'
          FROM action_previews p,json_each(p.result,'$.items') i WHERE p.kind IN ('gmail:trash','gmail:restore') AND p.result IS NOT NULL
          UNION ALL
          SELECT p.account_id,json_extract(i.value,'$.message_id'),p.id,substr(p.kind,7),'uncertain',json_object('reason','Interrupted operation; verify current Gmail labels'),
             COALESCE((SELECT MAX(created_at) FROM action_audit WHERE action_id=p.id),'1970-01-01'),'reviewed'
          FROM action_previews p,json_each(p.payload,'$.items') i WHERE p.kind IN ('gmail:trash','gmail:restore') AND p.status='running' AND p.result IS NULL
        ) """
        clause='o.account_id=?' ;params=[account]
        if message_id is not None:clause+=' AND o.message_id=?';params.append(message_id)
        if search:clause+=" AND (instr(lower(m.sender),lower(?))>0 OR instr(lower(m.subject),lower(?))>0)";params += [search,search]
        with closing(self.connect()) as db:
            db.row_factory=sqlite3.Row
            total=db.execute(query+'SELECT COUNT(*) FROM operations o LEFT JOIN messages m USING(account_id,message_id) WHERE '+clause,params).fetchone()[0]
            rows=db.execute(query+"SELECT o.*,m.sender,m.subject,m.internal_date_ms,m.label_ids,c.category_id,CASE WHEN o.source='automatic' THEN COALESCE(json_extract(o.detail,'$.reason'),'Authorized future advertising cleanup') WHEN o.operation='restore' THEN 'User-approved Restore' ELSE 'User-approved Gmail Trash' END AS reason FROM operations o LEFT JOIN messages m USING(account_id,message_id) LEFT JOIN message_category_assignments c USING(account_id,message_id) WHERE "+clause+" ORDER BY performed_at DESC,action_id DESC LIMIT ? OFFSET ?",(*params,limit,offset)).fetchall()
        result=[]
        for row in rows:
            value=dict(row);value['detail']=json.loads(value['detail']);value['label_ids']=json.loads(value['label_ids']) if value['label_ids'] else None;result.append(value)
        return {'items':result,'total':total,'next_offset':offset+limit if offset+limit<total else None,'offset':offset}

    def trash(self,account,search,offset,limit,label="TRASH"):
        clause="m.account_id=? AND m.labels_known=1 AND EXISTS(SELECT 1 FROM json_each(m.label_ids) WHERE value=?)";params=[account,label]
        if search:clause+=" AND (instr(lower(m.sender),lower(?))>0 OR instr(lower(m.subject),lower(?))>0)";params += [search,search]
        with closing(self.connect()) as db:
            db.row_factory=sqlite3.Row
            total=db.execute('SELECT COUNT(*) FROM messages m WHERE '+clause,params).fetchone()[0]
            rows=db.execute("SELECT m.*,a.category_id FROM messages m LEFT JOIN message_category_assignments a USING(account_id,message_id) WHERE "+clause+" ORDER BY internal_date_ms DESC,message_id LIMIT ? OFFSET ?",(*params,limit,offset)).fetchall()
        return {'items':[dict(row) for row in rows],'total':total,'offset':offset,'next_offset':offset+limit if offset+limit<total else None}

    def label_state(self,account,label="TRASH"):
        with closing(self.connect()) as db:
            db.row_factory=sqlite3.Row;row=db.execute("SELECT * FROM mail_label_sync WHERE account_id=? AND label=?",(account,label)).fetchone()
        return dict(row) if row else None

    def start_label_sync(self,account,label="TRASH"):
        with closing(self.connect()) as db,db:
            upper=db.execute('SELECT COALESCE(MAX(rowid),0) FROM messages WHERE account_id=?',(account,)).fetchone()[0]
            db.execute("INSERT OR REPLACE INTO mail_label_sync(account_id,label,id,upper_rowid) VALUES (?,?,?,?)",(account,label,uuid.uuid4().hex,upper))
        return self.label_state(account,label)

    def label_rechecks(self,account,state,limit=10):
        with closing(self.connect()) as db:
            return db.execute("SELECT rowid,message_id FROM messages m WHERE account_id=? AND rowid>? AND rowid<=? AND labels_known=1 AND EXISTS(SELECT 1 FROM json_each(m.label_ids) WHERE value=?) ORDER BY rowid LIMIT ?",(account,state['recheck_cursor'],state['upper_rowid'],state['label'],limit)).fetchall()

    def commit_label_page(self,account,state,messages,next_token,rechecks,errors):
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM mail_label_sync WHERE account_id=? AND label=? AND id=? AND next_token IS ? AND recheck_cursor=?",(account,state['label'],state['id'],state['next_token'],state['recheck_cursor'])).fetchone():raise ValueError('Trash checkpoint changed')
            for message in messages:
                if message.account_id!=account:raise ValueError('Account mismatch')
                SQLiteMessageRepository.save_in_transaction(db,message)
            cursor=rechecks[-1][0] if rechecks else state['upper_rowid']
            remaining=db.execute("SELECT 1 FROM messages m WHERE account_id=? AND rowid>? AND rowid<=? AND EXISTS(SELECT 1 FROM json_each(m.label_ids) WHERE value=?) LIMIT 1",(account,cursor,state['upper_rowid'],state['label'])).fetchone()
            listing_done=int(next_token is None)
            db.execute("UPDATE mail_label_sync SET next_token=?,listing_done=?,recheck_cursor=?,processed=processed+?,completed=?,errors=errors+?,updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND label=?",(next_token,listing_done,cursor,len(messages),int(listing_done and not remaining),errors,account,state['label']))
        return self.label_state(account,state['label'])

    def label_error(self,account,label="TRASH"):
        with closing(self.connect()) as db,db:db.execute("UPDATE mail_label_sync SET errors=errors+1,updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND label=?",(account,label))

    def read_metrics(self,account,kind):
        with closing(self.connect()) as db:
            row=db.execute('SELECT unavailable,read_errors FROM mail_read_metrics WHERE account_id=? AND kind=?',(account,kind)).fetchone()
        return {'unavailable':row[0] if row else 0,'read_errors':row[1] if row else 0}
    def record_read(self,account,kind,unavailable=0,read_errors=0):
        with closing(self.connect()) as db,db:db.execute('INSERT INTO mail_read_metrics VALUES (?,?,?,?) ON CONFLICT(account_id,kind) DO UPDATE SET unavailable=unavailable+excluded.unavailable,read_errors=read_errors+excluded.read_errors',(account,kind,unavailable,read_errors))

    def event_page(self,account,cursor,limit=100):
        if self.events_path is None or not self.events_path.exists():return [],cursor
        with closing(sqlite3.connect(self.events_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_events'").fetchone():return [],cursor
            db.execute('ATTACH DATABASE ? AS indexed_mail',(Path(self.path).resolve().as_uri()+'?mode=ro',))
            db.execute('BEGIN')
            upper=db.execute('SELECT COALESCE(MAX(rowid),0) FROM inbox_events WHERE account_id=?',(account,)).fetchone()[0]
            rows=db.execute("SELECT e.rowid,e.message_id FROM inbox_events e WHERE e.account_id=? AND e.rowid>? AND e.rowid<=? AND NOT EXISTS(SELECT 1 FROM indexed_mail.cleanup_assessments a WHERE a.account_id=e.account_id AND a.message_id=e.message_id) ORDER BY e.rowid LIMIT ?",(account,cursor,upper,limit)).fetchall()
        return rows,rows[-1][0] if len(rows)==limit else upper

    def changed_page(self,account,limit=50):
        with closing(self.connect()) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS cleanup_reassessment_cursor(account_id TEXT PRIMARY KEY,classification_id INTEGER NOT NULL,frame_id INTEGER NOT NULL)')
            db.execute('INSERT OR IGNORE INTO cleanup_schema_versions VALUES(3)')
            old=db.execute('SELECT classification_id,frame_id FROM cleanup_reassessment_cursor WHERE account_id=?',(account,)).fetchone() or (0,0)
            assignments=db.execute('SELECT id,message_id FROM classification_audit WHERE account_id=? AND id>? ORDER BY id LIMIT ?',(account,old[0],limit)).fetchall()
            frames=db.execute('SELECT id,message_id FROM semantic_frame_history WHERE account_id=? AND id>? ORDER BY id LIMIT ?',(account,old[1],limit)).fetchall() if db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_frame_history'").fetchone() else []
            after=(assignments[-1][0] if assignments else old[0],frames[-1][0] if frames else old[1])
            return tuple(old),after,list(dict.fromkeys(r[1] for r in [*assignments,*frames]))

    def commit_changed(self,account,before,after,rows):
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT classification_id,frame_id FROM cleanup_reassessment_cursor WHERE account_id=?',(account,)).fetchone() or (0,0)
            if tuple(current)!=before:raise ValueError('Reassessment checkpoint changed')
            for row in rows:self._assessment(db,account,row,'classification_refresh')
            db.execute('INSERT INTO cleanup_reassessment_cursor VALUES(?,?,?) ON CONFLICT(account_id) DO UPDATE SET classification_id=excluded.classification_id,frame_id=excluded.frame_id',(account,*after))

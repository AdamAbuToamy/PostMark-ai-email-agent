"""Read snapshots and atomic local group decisions, without external effects."""
from contextlib import closing
import json
import sqlite3
from email_agent.application.organization import OrganizationItem
from email_agent.domain.message import EmailMessage
from email_agent.domain.message_category import MessageCategoryAssignment
from email_agent.application.classify_messages import VERSION
from email_agent.infrastructure.database.sqlite_message_category_repository import SQLiteMessageCategoryRepository


class SQLiteOrganizationStore:
    def __init__(self, path, management):
        self.path, self.management = path, management

    def snapshot(self, account, limit):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            total = db.execute('SELECT COUNT(*) FROM messages WHERE account_id=?', (account,)).fetchone()[0]
            has_frames=db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_frames'").fetchone() is not None
            frame_select='f.payload semantic_json,' if has_frames else 'NULL semantic_json,'
            frame_join='LEFT JOIN semantic_frames f USING(account_id,message_id)' if has_frames else ''
            rows = db.execute('''SELECT '''+frame_select+''' m.*,h.headers,a.category_id,a.source,a.status,a.reason,a.rule_version,n.state attention,substr(COALESCE(b.body_text,''),1,8000) body
                FROM messages m LEFT JOIN message_list_headers h USING(account_id,message_id)
                LEFT JOIN message_category_assignments a USING(account_id,message_id)
                LEFT JOIN message_attention n USING(account_id,message_id)
                LEFT JOIN message_search_content b USING(account_id,message_id)
                '''+frame_join+''' WHERE m.account_id=? ORDER BY m.message_id LIMIT ?''', (account, limit)).fetchall()
        result = []
        for row in rows:
            message = EmailMessage(row['message_id'], account, row['sender'], row['subject'],
                row['internal_date_ms'], tuple(json.loads(row['label_ids'])) if row['labels_known'] else None,
                tuple(json.loads(row['headers']).items()) if row['headers'] else None)
            decision = {k: row[k] for k in ('category_id','source','status','reason','rule_version')}
            result.append(OrganizationItem(message, decision, None,row['attention'],row['body'],json.loads(row['semantic_json']) if row['semantic_json'] else None))
        protections=self.management.protection_snapshot(account,[item.message for item in result])
        from dataclasses import replace
        result=[replace(item,protection=protections[item.message.id]) for item in result]
        return result, total

    def item_cache(self, account, version):
        prefix='organization-item:'+version+':'
        try:
            with closing(sqlite3.connect(self.path,timeout=.1)) as db:
                rows=db.execute('SELECT substr(fingerprint,?+1),result FROM organization_semantic_cache WHERE account_id=? AND substr(fingerprint,1,?)=? LIMIT 10000',(len(prefix),account,len(prefix),prefix)).fetchall()
            result={}
            for id,raw in rows:
                try:
                    v=json.loads(raw)
                    if len(v)==4 and isinstance(v[0],str) and isinstance(v[1],str) and len(v[2])==3 and isinstance(v[3],dict):result[id]=(v[0],v[1],tuple(v[2]),v[3])
                except (ValueError,TypeError,KeyError):continue
            return result
        except sqlite3.OperationalError:return {}  # Optional memoization never blocks progress.

    def save_item_cache(self, account, version, items):
        prefix='organization-item:'+version+':'
        try:
            with closing(sqlite3.connect(self.path,timeout=.1)) as db,db:
                db.executemany('INSERT INTO organization_semantic_cache(account_id,fingerprint,result) VALUES(?,?,?) ON CONFLICT(account_id,fingerprint) DO UPDATE SET result=excluded.result',[(account,prefix+id,json.dumps(v)) for id,v in list(items.items())[:10000]])
                # Bound retained entries; model/semantic proposal caches are separate keys.
                db.execute("DELETE FROM organization_semantic_cache WHERE account_id=? AND substr(fingerprint,1,18)='organization-item:' AND (substr(fingerprint,1,?)!=? OR substr(fingerprint,?+1) NOT IN (SELECT message_id FROM messages WHERE account_id=? ORDER BY message_id LIMIT 10000))",(account,len(prefix),prefix,len(prefix),account))
        except sqlite3.OperationalError:pass

    def decisions(self, account):
        with closing(sqlite3.connect(self.path)) as db:
            return dict(db.execute('SELECT cluster_key,status FROM organization_decisions WHERE account_id=?',(account,)))

    def section_category(self, account, message_id):
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT category_id FROM message_category_assignments WHERE account_id=? AND message_id=?',(account,message_id)).fetchone()
            return row[0] if row else None

    def cached_excerpt(self, account, message_id):
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT substr(body_text,1,600) FROM message_search_content WHERE account_id=? AND message_id=?',(account,message_id)).fetchone()
            return row[0] if row else ''

    def apply(self, account, key, action, category, items):
        affected = 0
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            # Optimistic validation covers concurrent manual corrections.
            for item in items:
                row = db.execute('SELECT category_id,source,status,reason,rule_version FROM message_category_assignments WHERE account_id=? AND message_id=?', (account,item.message.id)).fetchone()
                current = dict(row) if row else {k:None for k in item.decision}
                if current != item.decision:
                    raise ValueError('Classification changed; refresh the group preview')
            if action == 'approve':
                db.execute('''INSERT INTO organization_patterns(account_id,cluster_key,category_id)
                    VALUES (?,?,?) ON CONFLICT(account_id,cluster_key) DO UPDATE SET
                    category_id=excluded.category_id''', (account,key,category))
            for item in items:
                if action == 'approve':
                    assignment = MessageCategoryAssignment(account,item.message.id,category,'automatic',
                        'classified','approved_recurring_pattern',VERSION)
                    affected += SQLiteMessageCategoryRepository._put_in_transaction(db,assignment)
                elif action!='dismiss':
                    db.execute('''INSERT INTO cleanup_protections(account_id,scope,identity,reason)
                        VALUES (?,'message',?,?) ON CONFLICT(account_id,scope,identity) DO NOTHING''',
                        (account,item.message.id,'User group decision: '+action))
                    db.execute("UPDATE cleanup_assessments SET verdict='protected',confidence='Protected',reason=? WHERE account_id=? AND message_id=?", ('User group decision: '+action,account,item.message.id))
                    db.execute("INSERT INTO message_attention VALUES (?,?,'protected') ON CONFLICT(account_id,message_id) DO UPDATE SET state='protected'",(account,item.message.id))
                    affected += 1
            db.execute("INSERT INTO organization_decisions(account_id,cluster_key,status) VALUES (?,?,?) ON CONFLICT(account_id,cluster_key) DO UPDATE SET status=excluded.status,updated_at=CURRENT_TIMESTAMP",(account,key,'dismissed' if action=='dismiss' else 'applied'))
            db.execute('INSERT INTO organization_audit(account_id,cluster_key,action,affected) VALUES (?,?,?,?)', (account,key,action,affected))
        return affected

    def create_semantic_plan(self, account, plan):
        import uuid
        id=uuid.uuid4().hex
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO organization_semantic_plans(id,account_id,payload,status) VALUES (?,?,?,'pending')",(id,account,json.dumps(plan)))
        return id

    def claim_semantic_plan(self, account, id):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT payload FROM organization_semantic_plans WHERE id=? AND account_id=? AND status='pending' AND created_at>=datetime('now','-15 minutes')",(id,account)).fetchone()
            if not row:raise ValueError('Semantic plan expired or already attempted; prepare a fresh preview')
            db.execute("UPDATE organization_semantic_plans SET status='running' WHERE id=?",(id,))
        return json.loads(row[0])

    def finish_semantic_plan(self, account, id, result):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE organization_semantic_plans SET status='finished',result=? WHERE id=? AND account_id=?",(json.dumps(result),id,account))

    def semantic_cache(self, account, fingerprint):
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT result FROM organization_semantic_cache WHERE account_id=? AND fingerprint=?',(account,fingerprint)).fetchone()
        return row[0] if row else None

    def save_semantic_cache(self, account, fingerprint, result):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR IGNORE INTO organization_semantic_cache VALUES (?,?,?)',(account,fingerprint,result))

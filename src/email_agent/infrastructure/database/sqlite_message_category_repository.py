"""SQLite persistence for local message category assignments."""

import sqlite3
import json
from dataclasses import asdict
from contextlib import closing
from pathlib import Path

from email_agent.domain.message_category import MessageCategoryAssignment


class SQLiteMessageCategoryRepository:
    def __init__(self, db_path: Path, categories_path: Path | None = None):
        self._db_path = db_path
        self._categories_path=categories_path or db_path.parent/'categories.db'

    def record_semantic_frame(self,message):
        with closing(sqlite3.connect(self._db_path)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_frames'").fetchone():return
        from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore
        from email_agent.application.semantic_mail import local_frame,SEMANTIC_VERSION
        from email_agent.application.semantic_organizer import row_fingerprint
        store=SQLiteSemanticOrganizerStore(self._db_path,self._categories_path)
        row=store.message_row(message.account_id,message.id)
        frame=(row or {}).get('semantic') or {}
        excerpt_only=row and row['content_complete'] and frame.get('source')=='gemini' and not frame.get('input_coverage',{}).get('body_complete')
        pattern=self.content_pattern(message.account_id,message.id,row['body']) if row and (not frame.get('understood') or frame.get('pattern_source')) else None
        pattern_changed=bool(pattern and not frame.get('understood') and pattern['target']==row['decision']['category_id'] or frame.get('pattern_source') and (not pattern or pattern['target']!=row['decision']['category_id']))
        if row and (row['input_fingerprint']!=row_fingerprint(row) or excerpt_only or pattern_changed or (frame.get('source')=='local' and frame.get('extraction_version')!=SEMANTIC_VERSION)):
            fresh=local_frame(row)
            if not fresh['understood'] and pattern and pattern['target']==row['decision']['category_id']:
                from email_agent.application.message_text import current_text
                fresh.update(understood=True,analysis_reason=None,primary_intent=pattern['primary_intent'],message_type=pattern['message_type'],confidence=pattern['confidence'],category_hint=pattern['target'],sensitivities=sorted(set(fresh['sensitivities'])|set(pattern['sensitivities'])),pattern_source=pattern['pattern'],factuality='Exact current-body template matched approved independent examples; workflow outcome not inherited',evidence=[dict(field='excerpt',quote=current_text(row['body'])[:180])])
            store.put_frame(row,fresh,SEMANTIC_VERSION)

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        with closing(sqlite3.connect(self._db_path)) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""
                    CREATE TABLE IF NOT EXISTS message_category_assignments (
                        account_id TEXT NOT NULL,
                        message_id TEXT NOT NULL,
                        category_id TEXT NOT NULL,
                        source TEXT NOT NULL,
                        PRIMARY KEY (account_id, message_id)
                    )
                """)

                self._migrate(db)

                db.execute("""
                    CREATE INDEX IF NOT EXISTS
                        idx_message_category_lookup
                    ON message_category_assignments (
                        account_id, category_id
                    )
                """)

    def _migrate(self, db):
        columns = {r[1] for r in db.execute("PRAGMA table_info(message_category_assignments)")}
        for name, default in (("status", "classified"), ("reason", "legacy assignment"), ("rule_version", "legacy")):
            if name not in columns:
                db.execute(f"ALTER TABLE message_category_assignments ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
        db.execute("CREATE TABLE IF NOT EXISTS classification_audit (id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, message_id TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        db.execute("CREATE TABLE IF NOT EXISTS placement_reviews (id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, message_id TEXT NOT NULL, labels_json TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(account_id,message_id,labels_json))")
        db.execute("CREATE TABLE IF NOT EXISTS placement_audit (id INTEGER PRIMARY KEY, review_id INTEGER NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        db.execute("CREATE TABLE IF NOT EXISTS classification_schema_versions (version INTEGER PRIMARY KEY)")
        db.execute("INSERT OR IGNORE INTO classification_schema_versions VALUES (1)")
        # v2 is additive: existing mail is New until a user records otherwise.
        db.execute("""CREATE TABLE IF NOT EXISTS local_message_state (
            account_id TEXT NOT NULL, message_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('new','done')),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(account_id,message_id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS local_message_state_audit (
            id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, message_id TEXT NOT NULL,
            previous_state TEXT NOT NULL, state TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        db.execute("INSERT OR IGNORE INTO classification_schema_versions VALUES (2)")
        db.execute("""CREATE TABLE IF NOT EXISTS classification_review_decisions (
            id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, message_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(account_id,message_id,fingerprint))""")
        db.execute("INSERT OR IGNORE INTO classification_schema_versions VALUES (3)")
        db.execute("CREATE TABLE IF NOT EXISTS organization_patterns(account_id TEXT NOT NULL,cluster_key TEXT NOT NULL,category_id TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(account_id,cluster_key))")
        db.execute("CREATE TABLE IF NOT EXISTS organization_audit(id INTEGER PRIMARY KEY,account_id TEXT NOT NULL,cluster_key TEXT NOT NULL,action TEXT NOT NULL,affected INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        db.execute("INSERT OR IGNORE INTO classification_schema_versions VALUES (4)")
        db.execute("CREATE TABLE IF NOT EXISTS organization_semantic_plans(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,result TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        db.execute("CREATE TABLE IF NOT EXISTS organization_semantic_cache(account_id TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(account_id,fingerprint))")
        db.execute("INSERT OR IGNORE INTO classification_schema_versions VALUES (5)")
        db.execute("CREATE TABLE IF NOT EXISTS message_attention(account_id TEXT NOT NULL,message_id TEXT NOT NULL,state TEXT NOT NULL,PRIMARY KEY(account_id,message_id))")
        db.execute("CREATE TABLE IF NOT EXISTS organization_decisions(account_id TEXT NOT NULL,cluster_key TEXT NOT NULL,status TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(account_id,cluster_key))")
        db.execute("INSERT OR IGNORE INTO organization_decisions(account_id,cluster_key,status) SELECT a.account_id,a.cluster_key,CASE WHEN a.action='dismiss' THEN 'dismissed' ELSE 'applied' END FROM organization_audit a WHERE a.id=(SELECT MAX(b.id) FROM organization_audit b WHERE b.account_id=a.account_id AND b.cluster_key=a.cluster_key)")
        db.execute("INSERT OR IGNORE INTO classification_schema_versions VALUES (6)")

    def cached_evidence(self, account, message_id):
        with closing(sqlite3.connect(self._db_path)) as db:
            row = db.execute("SELECT body_text FROM message_search_content WHERE account_id=? AND message_id=?", (account,message_id)).fetchone()
            return row[0] if row else ''

    def cleanup_evidence(self,account,message_id):
        with closing(sqlite3.connect(self._db_path)) as db:
            complete=db.execute("SELECT 1 FROM message_content_state WHERE account_id=? AND message_id=? AND status='complete'",(account,message_id)).fetchone()
        return self.cached_evidence(account,message_id) if complete else ''

    def has_complete_body(self,account,message_id):
        with closing(sqlite3.connect(self._db_path)) as db:return bool(db.execute("SELECT 1 FROM message_content_state WHERE account_id=? AND message_id=? AND status='complete'",(account,message_id)).fetchone())

    def current_semantic_evidence(self,message):
        with closing(sqlite3.connect(self._db_path)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_frames'").fetchone():return None
        from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore,input_fingerprint
        row=SQLiteSemanticOrganizerStore(self._db_path,self._categories_path).message_row(message.account_id,message.id)
        if not row or row['decision']['source']=='manual' or row['input_fingerprint']!=input_fingerprint(row):return None
        return row.get('semantic')

    def content_pattern(self,account,id,body):
        from email_agent.application.message_text import content_template
        signature=content_template(body)
        if not signature:return None
        with closing(sqlite3.connect(self._db_path)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_content_patterns'").fetchone():return None
            if not db.execute('SELECT 1 FROM local_organization_policy WHERE account_id=? AND enabled=1',(account,)).fetchone():return None
            if not db.execute("SELECT 1 FROM message_content_state WHERE account_id=? AND message_id=? AND status='complete'",(account,id)).fetchone():return None
            rows=db.execute('SELECT target_category,definition FROM semantic_content_patterns WHERE account_id=? AND signature=? AND enabled=1',(account,signature)).fetchall()
            if len(rows)!=1:return None
            return dict(target=rows[0][0],**json.loads(rows[0][1]),pattern=signature)

    def routine_semantic_threshold(self,account):
        with closing(sqlite3.connect(self._db_path)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='local_organization_policy'").fetchone():return .9
            return .85 if db.execute('SELECT 1 FROM local_organization_policy WHERE account_id=? AND enabled=1',(account,)).fetchone() else .9

    def automatic_category_allowed(self,account,category):
        with closing(sqlite3.connect(self._db_path)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='local_autonomous_changes'").fetchone():return True
            if db.execute('SELECT 1 FROM local_organization_policy WHERE account_id=? AND enabled=1',(account,)).fetchone():return True
            return not db.execute("SELECT 1 FROM local_autonomous_changes c JOIN semantic_taxonomy_proposals p ON p.id=c.proposal_id,json_each(p.payload,'$.operations') o WHERE c.account_id=? AND json_extract(o.value,'$.kind')='create' AND json_extract(o.value,'$.id')=?",(account,category)).fetchone()

    def approved_semantic_policy(self,message,body):
        from email_agent.application.semantic_mail import local_frame,feature
        from email_agent.application.semantic_organizer import row_fingerprint
        with closing(sqlite3.connect(self._db_path)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_policies'").fetchone():return None
            db.row_factory=sqlite3.Row
            current=db.execute('SELECT * FROM message_category_assignments WHERE account_id=? AND message_id=?',(message.account_id,message.id)).fetchone()
            if current and current['source']=='manual':return None
            row=dict(account_id=message.account_id,message_id=message.id,subject=message.subject,sender=message.sender,
                internal_date_ms=message.internal_date_ms,body=body,headers=dict(message.list_headers or ()),labels=list(message.label_ids) if message.label_ids is not None else None,decision=dict(current) if current else {})
            cached=db.execute('SELECT payload,input_fingerprint FROM semantic_frames WHERE account_id=? AND message_id=?',(message.account_id,message.id)).fetchone()
            frame=json.loads(cached['payload']) if cached and cached['input_fingerprint']==row_fingerprint(row) else local_frame(row)
            if not frame.get('understood'):
                pattern=self.content_pattern(message.account_id,message.id,body)
                if pattern:return pattern['target']
            if not frame.get('understood') or frame.get('analysis_reason') or frame.get('confidence',0)<.85:return None
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='local_organization_feedback'").fetchone() and db.execute("SELECT 1 FROM local_organization_policy WHERE account_id=? AND enabled=1",(message.account_id,)).fetchone():
                learned={}
                for feedback in db.execute('SELECT signature,target,features,COUNT(*) n FROM local_organization_feedback WHERE account_id=? GROUP BY signature,target',(message.account_id,)):
                    conditions=json.loads(feedback['features'])
                    if all(feature(frame,key)==value for key,value in conditions.items()):learned.setdefault(feedback['signature'],[]).append(feedback)
                learned_targets={items[0]['target'] for items in learned.values() if len(items)==1 and items[0]['n']>=2}
                if len(learned_targets)==1:return next(iter(learned_targets))
            autonomous=set()
            enabled=False
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='local_autonomous_changes'").fetchone():
                autonomous={r[0] for r in db.execute('SELECT proposal_id FROM local_autonomous_changes WHERE account_id=?',(message.account_id,))}
                enabled=bool(db.execute('SELECT 1 FROM local_organization_policy WHERE account_id=? AND enabled=1',(message.account_id,)).fetchone())
            targets={r['target_category'] for r in db.execute('SELECT * FROM semantic_policies WHERE account_id=? AND enabled=1',(message.account_id,)) if (r['proposal_id'] not in autonomous or enabled) and all(feature(frame,key)==value for key,value in json.loads(r['conditions']).items())}
            return next(iter(targets)) if len(targets)==1 else None

    def verified_application_thread(self, message):
        thread=dict(message.list_headers or ()).get('thread-id')
        if not thread:return False
        with closing(sqlite3.connect(self._db_path)) as db:
            return db.execute("""SELECT 1 FROM message_category_assignments a
                JOIN message_list_headers h USING(account_id,message_id)
                WHERE a.account_id=? AND a.message_id<>?
                AND a.category_id='job_applications' AND a.status='classified'
                AND ((a.source='manual' AND instr(a.reason,'Submitted application:')>0)
                  OR (a.source='automatic' AND a.reason LIKE 'explicit_submitted_application_%'))
                AND json_extract(h.headers,'$."thread-id"')=? LIMIT 1""",
                (message.account_id,message.id,thread)).fetchone() is not None

    def approved_pattern(self, message, category, status, reason):
        if status != 'classified':
            return None
        from email_agent.application.local_understanding import cluster_identity
        key, _, _ = cluster_identity(message, category, reason)
        with closing(sqlite3.connect(self._db_path)) as db:
            row = db.execute('SELECT category_id FROM organization_patterns WHERE account_id=? AND cluster_key=?', (message.account_id,key)).fetchone()
        return row[0] if row else None

    def update_attention(self, message):
        from email_agent.application.smart_sections import attention_state
        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT * FROM message_category_assignments WHERE account_id=? AND message_id=?',(message.account_id,message.id)).fetchone()
            if row:
                state=attention_state(message,dict(row))
                if db.execute("SELECT 1 FROM classification_review_decisions d JOIN message_category_assignments a USING(account_id,message_id) WHERE a.account_id=? AND a.message_id=? AND (d.fingerprint=json_array(a.category_id,a.status,a.source,a.reason,a.rule_version) OR (json_extract(d.fingerprint,'$[0]') IS a.category_id AND json_extract(d.fingerprint,'$[1]') IS a.status AND json_extract(d.fingerprint,'$[2]') IS a.source AND json_extract(d.fingerprint,'$[3]') IS a.reason))",(message.account_id,message.id)).fetchone():state='protected'
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='cleanup_protections'").fetchone() and db.execute("SELECT 1 FROM cleanup_protections WHERE account_id=? AND scope='message' AND identity=?",(message.account_id,message.id)).fetchone():state='protected'
                db.execute('INSERT INTO message_attention VALUES (?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET state=excluded.state',(message.account_id,message.id,state))

    def assign(self, assignment: MessageCategoryAssignment) -> bool:
        """Persist assignment without overwriting a previous decision."""
        with closing(sqlite3.connect(self._db_path)) as db:
            with db:
                cursor = db.execute("""
                    INSERT OR IGNORE INTO message_category_assignments
                        (account_id, message_id, category_id, source)
                    VALUES (?, ?, ?, ?)
                """, (
                    assignment.account_id,
                    assignment.message_id,
                    assignment.category_id,
                    assignment.source,
                ))

                return cursor.rowcount == 1

    def list_for_account(self, account_id: str) -> dict[str, str]:
        with closing(sqlite3.connect(self._db_path)) as db:
            rows = db.execute("""
                SELECT message_id, category_id
                FROM message_category_assignments
                WHERE account_id = ?
            """, (account_id,)).fetchall()

        return {message_id: category_id for message_id, category_id in rows}

    def put(self, assignment: MessageCategoryAssignment) -> bool:
        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            return self._put_in_transaction(db, assignment)

    @staticmethod
    def _put_in_transaction(db, assignment):
        values = asdict(assignment)
        old = db.execute("SELECT * FROM message_category_assignments WHERE account_id=? AND message_id=?", (assignment.account_id, assignment.message_id)).fetchone()
        before = dict(old) if old else None
        if before and ((before["source"] == "manual" and assignment.source != "manual") or before == values):
            return False
        db.execute("""INSERT INTO message_category_assignments
            (account_id,message_id,category_id,source,status,reason,rule_version)
            VALUES (:account_id,:message_id,:category_id,:source,:status,:reason,:rule_version)
            ON CONFLICT(account_id,message_id) DO UPDATE SET
            category_id=excluded.category_id,source=excluded.source,status=excluded.status,
            reason=excluded.reason,rule_version=excluded.rule_version""", values)
        db.execute("INSERT INTO classification_audit(account_id,message_id,before_json,after_json) VALUES (?,?,?,?)", (assignment.account_id,assignment.message_id,json.dumps(before),json.dumps(values)))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='message_attention'").fetchone():
            state='protected' if assignment.source=='manual' else 'organized' if assignment.status=='classified' else 'review'
            db.execute('INSERT INTO message_attention VALUES (?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET state=excluded.state',(assignment.account_id,assignment.message_id,state))
        if assignment.source=='manual' and db.execute("SELECT 1 FROM sqlite_master WHERE name='local_organization_feedback'").fetchone():
            from email_agent.application.semantic_mail import feature,digest
            cached=db.execute('SELECT payload,input_fingerprint FROM semantic_frames WHERE account_id=? AND message_id=?',(assignment.account_id,assignment.message_id)).fetchone()
            from email_agent.application.semantic_organizer import row_fingerprint
            observed=db.execute('SELECT m.subject,m.sender,h.headers,b.body_text FROM messages m LEFT JOIN message_list_headers h USING(account_id,message_id) LEFT JOIN message_search_content b USING(account_id,message_id) WHERE m.account_id=? AND m.message_id=?',(assignment.account_id,assignment.message_id)).fetchone()
            current=dict(account_id=assignment.account_id,message_id=assignment.message_id,subject=observed['subject'],sender=observed['sender'],headers=json.loads(observed['headers']) if observed['headers'] else {},body=observed['body_text'] or '',decision=before or {}) if observed else None
            valid=bool(cached and current and cached['input_fingerprint']==row_fingerprint(current))
            db.execute('DELETE FROM local_organization_feedback WHERE account_id=? AND message_id=?',(assignment.account_id,assignment.message_id))
            complete=db.execute("SELECT 1 FROM message_content_state WHERE account_id=? AND message_id=? AND status='complete'",(assignment.account_id,assignment.message_id)).fetchone()
            frame=json.loads(cached[0]) if cached else {}
            conditions={key:feature(frame,key) for key in ('primary_intent','message_type','entity') if feature(frame,key)}
            if valid and db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_content_patterns'").fetchone():
                from email_agent.application.message_text import content_template
                db.execute('UPDATE semantic_content_patterns SET enabled=0 WHERE account_id=? AND signature=? AND target_category!=?',(assignment.account_id,content_template(current['body']),assignment.category_id))
            if valid and complete and frame.get('understood') and not frame.get('analysis_reason') and len(conditions)>=2:
                db.execute('INSERT INTO local_organization_feedback VALUES(?,?,?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET signature=excluded.signature,target=excluded.target,features=excluded.features',(assignment.account_id,assignment.message_id,digest(conditions),assignment.category_id,json.dumps(conditions)))
                for policy in db.execute('SELECT p.* FROM semantic_policies p JOIN local_autonomous_changes c ON c.proposal_id=p.proposal_id WHERE p.account_id=? AND p.enabled=1',(assignment.account_id,)).fetchall():
                    if policy['target_category']!=assignment.category_id and all(feature(frame,k)==v for k,v in json.loads(policy['conditions']).items()):db.execute('UPDATE semantic_policies SET enabled=0 WHERE id=?',(policy['id'],))
                db.execute("INSERT INTO local_organization_audit(account_id,kind,payload) VALUES(?,'manual_feedback',?)",(assignment.account_id,json.dumps(dict(target=assignment.category_id,pattern=digest(conditions)))))
        if assignment.source=='manual' and db.execute("SELECT 1 FROM sqlite_master WHERE name='cleanup_assessments'").fetchone():
            db.execute("UPDATE cleanup_assessments SET verdict='protected',reason='Manual category override',confidence='Protected',category_id=?,updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND message_id=?",(assignment.category_id,assignment.account_id,assignment.message_id))
        return True

    def propose_review(self, message, reason):
        if message.label_ids is None:
            return  # Unknown labels cannot invalidate earlier evidence.
        labels = json.dumps(sorted(message.label_ids))
        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            stale = db.execute("SELECT id FROM placement_reviews WHERE account_id=? AND message_id=? AND status='proposed' AND (labels_json != ? OR ? IS NULL)", (message.account_id,message.id,labels,reason)).fetchall()
            for (review_id,) in stale:
                db.execute("UPDATE placement_reviews SET status='superseded' WHERE id=?", (review_id,))
                db.execute("INSERT INTO placement_audit(review_id,decision,reason) VALUES (?,'superseded','Gmail label evidence changed')", (review_id,))
            if reason:
                cursor = db.execute("INSERT OR IGNORE INTO placement_reviews(account_id,message_id,labels_json,reason) VALUES (?,?,?,?)", (message.account_id,message.id,labels,reason))
                if not cursor.rowcount:
                    prior = db.execute("SELECT id FROM placement_reviews WHERE account_id=? AND message_id=? AND labels_json=? AND status='superseded'", (message.account_id,message.id,labels)).fetchone()
                    if prior:
                        db.execute("UPDATE placement_reviews SET status='proposed',reason=? WHERE id=?", (reason,prior[0]))
                        db.execute("INSERT INTO placement_audit(review_id,decision,reason) VALUES (?,'proposed',?)", (prior[0],reason))
                if cursor.rowcount:
                    db.execute("INSERT INTO placement_audit(review_id,decision,reason) VALUES (?,'proposed',?)", (cursor.lastrowid,reason))

    def review(self, account_id, review_id, decision, reason):
        if decision not in ("dismissed", "confirmed_for_manual_action") or not 1 <= len(reason.strip()) <= 500:
            raise ValueError("Invalid review decision or reason")
        with closing(sqlite3.connect(self._db_path)) as db, db:
            cursor = db.execute("UPDATE placement_reviews SET status=? WHERE id=? AND account_id=? AND status='proposed'", (decision,review_id,account_id))
            if not cursor.rowcount:
                raise ValueError("Review is not pending in this account")
            db.execute("INSERT INTO placement_audit(review_id,decision,reason) VALUES (?,?,?)", (review_id,decision,reason))

    def reviews(self, account_id, after=0, limit=100, queue="all"):
        if queue not in ("all","spam","trash","history"):
            raise ValueError("Unsupported placement queue")
        where, params = "r.account_id=? AND r.id>?", [account_id,after]
        if queue == "history":
            where += " AND r.status!='proposed'"
        elif queue in ("spam","trash"):
            where += """ AND r.status='proposed' AND EXISTS (
                SELECT 1 FROM messages m WHERE m.account_id=r.account_id AND m.message_id=r.message_id
                AND m.labels_known=1 AND EXISTS(SELECT 1 FROM json_each(m.label_ids) WHERE value=?))"""
            params.append(queue.upper())
        params.append(limit)
        with closing(sqlite3.connect(self._db_path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("SELECT r.* FROM placement_reviews r WHERE " + where + " ORDER BY r.id LIMIT ?", params)]

    def progress(self, account_id):
        with closing(sqlite3.connect(self._db_path)) as db:
            total, classified, review = db.execute("""SELECT COUNT(*),
                COALESCE(SUM(a.status='classified'),0), COALESCE(SUM(a.status='needs_review'),0)
                FROM messages m LEFT JOIN message_category_assignments a
                ON a.account_id=m.account_id AND a.message_id=m.message_id
                WHERE m.account_id=?""", (account_id,)).fetchone()
            proposals = db.execute("SELECT COUNT(*) FROM placement_reviews WHERE account_id=? AND status='proposed'", (account_id,)).fetchone()[0]
            protected_uncertain=db.execute("SELECT COUNT(*) FROM message_attention n JOIN message_category_assignments a USING(account_id,message_id) WHERE n.account_id=? AND n.state='protected' AND a.status='needs_review'",(account_id,)).fetchone()[0]
            untouched=db.execute("SELECT COUNT(*) FROM message_attention n JOIN message_category_assignments a USING(account_id,message_id) WHERE n.account_id=? AND n.state='untouched' AND a.status='needs_review'",(account_id,)).fetchone()[0]
        return dict(total_local=total,classified=classified,needs_review=review,untouched=untouched,review_required=self.review_counts(account_id)["category"],
                    protected_uncertain=protected_uncertain,unclassified=total-classified-review,placement_review=proposals)

    def items(self, account_id, category=None, after="", limit=100):
        with closing(sqlite3.connect(self._db_path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("""SELECT a.*,m.sender,m.subject,m.internal_date_ms,m.label_ids,m.labels_known
                FROM message_category_assignments a JOIN messages m
                ON a.account_id=m.account_id AND a.message_id=m.message_id
                WHERE a.account_id=? AND a.message_id>? AND (? IS NULL OR a.category_id=?)
                ORDER BY a.message_id LIMIT ?""", (account_id,after,category,category,limit))]

    def set_local_state(self, account_id: str, message_id: str, state: str) -> None:
        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM messages WHERE account_id=? AND message_id=?", (account_id, message_id)).fetchone():
                raise LookupError("Message not found in this account")
            row = db.execute("SELECT state FROM local_message_state WHERE account_id=? AND message_id=?", (account_id, message_id)).fetchone()
            previous = row[0] if row else "new"
            if previous == state:
                return
            db.execute("""INSERT INTO local_message_state(account_id,message_id,state)
                VALUES (?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET
                state=excluded.state,updated_at=CURRENT_TIMESTAMP""", (account_id, message_id, state))
            db.execute("""INSERT INTO local_message_state_audit
                (account_id,message_id,previous_state,state) VALUES (?,?,?,?)""",
                (account_id, message_id, previous, state))

    def category_counts(self, account_id: str) -> dict[str, dict]:
        with closing(sqlite3.connect(self._db_path)) as db:
            rows = db.execute("""SELECT a.category_id,
                SUM(COALESCE(s.state,'new')='new'), SUM(COALESCE(s.state,'new')='done'), COUNT(*)
                FROM message_category_assignments a JOIN messages m
                ON m.account_id=a.account_id AND m.message_id=a.message_id
                LEFT JOIN local_message_state s
                ON s.account_id=a.account_id AND s.message_id=a.message_id
                WHERE a.account_id=? GROUP BY a.category_id""", (account_id,)).fetchall()
        return {r[0]: {"new": r[1], "done": r[2], "all": r[3]} for r in rows}

    def mailbox(self, account_id, category, state, search, sort, offset, limit, section='all'):
        from email_agent.application.dynamic_sections import discover_sections
        from email_agent.application.job_evidence import job_status
        joins = """FROM message_category_assignments a JOIN messages m
            ON m.account_id=a.account_id AND m.message_id=a.message_id
            LEFT JOIN local_message_state s USING(account_id,message_id)
            LEFT JOIN message_search_content b USING(account_id,message_id)
            LEFT JOIN message_attention n USING(account_id,message_id)
            WHERE a.account_id=? AND a.category_id=?"""
        filters = """ AND (?='all' OR COALESCE(s.state,'new')=?)
            AND (?='' OR instr(lower(m.sender),lower(?))>0 OR instr(lower(m.subject),lower(?))>0)"""
        params=(account_id,category,state,state,search,search,search)
        expression="discovered_section(m.message_id)"
        direction='DESC' if sort=='newest' else 'ASC'
        with closing(sqlite3.connect(self._db_path)) as db:
            db.row_factory=sqlite3.Row
            db.execute('BEGIN')
            has_frames=db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_frames'").fetchone() is not None
            frame_select='f.payload semantic_json,f.input_fingerprint semantic_fingerprint,' if has_frames else 'NULL semantic_json,NULL semantic_fingerprint,'
            frame_join=' LEFT JOIN semantic_frames f USING(account_id,message_id) ' if has_frames else ''
            corpus = [dict(row) for row in db.execute('''SELECT m.message_id,m.subject,m.sender,
                COALESCE(b.body_text,'') body,a.reason,a.source,h.headers,
                json_extract(h.headers,'$."thread-id"') thread_id,
                '''+frame_select+''' a.category_id FROM message_category_assignments a JOIN messages m USING(account_id,message_id)
                LEFT JOIN message_search_content b USING(account_id,message_id)
                LEFT JOIN message_list_headers h USING(account_id,message_id)
                '''+frame_join+''' WHERE a.account_id=? AND a.category_id=? ORDER BY m.message_id''',
                (account_id,category))]
            from email_agent.application.semantic_organizer import row_fingerprint
            for row in corpus:
                row['semantic']=json.loads(row.pop('semantic_json')) if row['semantic_json'] else None
                current=dict(account_id=account_id,message_id=row['message_id'],subject=row['subject'],sender=row['sender'],body=row['body'],headers=json.loads(row['headers'] or '{}'),decision={'category_id':row['category_id'],'source':row['source']})
                if row['semantic_fingerprint']!=row_fingerprint(current):row['semantic']=None
            profile=None
            if has_frames:
                saved=db.execute('SELECT definition FROM semantic_profiles WHERE account_id=? AND category_id=?',(account_id,category)).fetchone()
                if saved:profile=json.loads(saved[0])
            truncated = False
            discovered = discover_sections(category, corpus,profile)
            # Legacy sections can remain selected with zero matches; an unknown
            # arbitrary section is rejected instead of changing the user's filter.
            from email_agent.application.smart_sections import SECTIONS
            allowed = set(discovered.names) | {key for key,_ in SECTIONS.get(category,())}
            if section != 'all' and section not in allowed:
                raise ValueError('Invalid category section')
            db.create_function('discovered_section',1,lambda id: discovered.assignments.get(id,'other'),deterministic=True)
            counts=db.execute("SELECT SUM(COALESCE(s.state,'new')='new') new,SUM(COALESCE(s.state,'new')='done') done,COUNT(*) total "+joins,(account_id,category)).fetchone()
            sections=db.execute('SELECT '+expression+' section_id,COUNT(*) count '+joins+filters+' GROUP BY section_id',params).fetchall()
            selected=" AND (?='all' OR "+expression+'=?)'
            total=db.execute('SELECT COUNT(*) '+joins+filters+selected,(*params,section,section)).fetchone()[0]
            rows=db.execute("""SELECT a.account_id,a.message_id,a.category_id,a.status classification_status,
                m.sender,m.subject,m.internal_date_ms,m.label_ids,m.labels_known,COALESCE(s.state,'new') local_state,
                COALESCE(n.state,CASE WHEN a.status='classified' THEN 'organized' ELSE 'review' END) attention_state,a.reason _reason,COALESCE(b.body_text,'') _body,"""+expression+' section_id '+joins+filters+selected+
                f' ORDER BY m.internal_date_ms {direction},m.message_id {direction} LIMIT ? OFFSET ?',(*params,section,section,limit,offset)).fetchall()
        items=[dict(row) for row in rows]
        for item in items:
            known,raw=item.pop('labels_known'),item.pop('label_ids')
            item['_labels']=tuple(json.loads(raw)) if known else None
            body=item.pop('_body')
            reason=item.pop('_reason')
            verified=reason.startswith(('explicit_submitted_application_', 'verified_submitted_application_thread')) or 'Submitted application:' in reason
            item['job_status']=job_status(item['subject'],item['sender'],body,verified) if category=='job_applications' else None
            if has_frames:
                frame=next((row['semantic'] for row in corpus if row['message_id']==item['message_id']),None)
                if frame and item['job_status'] and item['job_status']['state']=='unknown':
                    from email_agent.application.semantic_mail import supported_application_frame,status_key,JOB_LABELS
                    if supported_application_frame(frame):
                        key=status_key(frame['status']);item['job_status']=dict(state=key,label=JOB_LABELS[key],source=frame.get('source'))
                if frame and (not frame.get('understood') or frame.get('confidence',0)<.85):frame=dict(frame,status=None)
                if frame and str(frame.get('status','')).casefold() in {'completed','done','resolved','successful'}:frame=dict(frame,status=None,status_note='Process outcome unverified')
                if frame and item['job_status'] and item['job_status']['state']!='unknown':frame=dict(frame,status=item['job_status']['label'],status_source=item['job_status'].get('source','local'))
                item['semantic']={key:frame.get(key) for key in ('status','entity','source','provider','model','message_type','primary_intent','sensitivities','factuality','input_coverage','status_source','status_note','understood','analysis_reason','dimensions','workflow_quality_score')} if frame else None
        section_counts={row['section_id']:row['count'] for row in sections}
        names=dict(discovered.names)
        if section_counts.get('other'):names['other']='Other'
        return dict(items=items,sections=[dict(id=key,name=name,count=section_counts.get(key,0),reason=discovered.reasons.get(key,'')) for key,name in names.items()],discovery=dict(source=discovered.source,truncated=truncated,analyzed=len(corpus)),counts=dict(new=counts['new'] or 0,done=counts['done'] or 0,all=counts['total']),
            total=total,next_offset=offset+len(items) if offset+len(items)<total else None,
            section_counts={row['section_id']:row['count'] for row in sections})

    def message_details(self, account_id, message_id):
        with closing(sqlite3.connect(self._db_path)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("""SELECT m.account_id,m.message_id,a.category_id,a.source,a.status,a.reason,a.rule_version,COALESCE(s.state,'new') AS local_state
                FROM messages m LEFT JOIN message_category_assignments a
                ON a.account_id=m.account_id AND a.message_id=m.message_id
                LEFT JOIN local_message_state s
                ON s.account_id=m.account_id AND s.message_id=m.message_id
                WHERE m.account_id=? AND m.message_id=?""", (account_id, message_id)).fetchone()
        return dict(row) if row else None

    def resolve_placement(self, account_id, message_id):
        with closing(sqlite3.connect(self._db_path)) as db, db:
            rows = db.execute("SELECT id FROM placement_reviews WHERE account_id=? AND message_id=? AND status='proposed'", (account_id,message_id)).fetchall()
            for (id,) in rows:
                db.execute("UPDATE placement_reviews SET status='restored' WHERE id=?", (id,))
                db.execute("INSERT INTO placement_audit(review_id,decision,reason) VALUES (?,'restored','Gmail restoration succeeded after explicit human confirmation')", (id,))

    _REVIEW_SELECT = """SELECT m.message_id,m.sender,m.subject,m.internal_date_ms,m.labels_known,m.label_ids,
        a.category_id,a.status,a.reason,a.source,
        json_array(a.category_id,a.status,a.source,a.reason,a.rule_version) AS fingerprint
        FROM messages m LEFT JOIN message_category_assignments a
        ON a.account_id=m.account_id AND a.message_id=m.message_id
        WHERE m.account_id=? AND (a.message_id IS NULL OR a.status='needs_review')"""

    def classification_queue(self, account, limit=100, after="", attention="all"):
        with closing(sqlite3.connect(self._db_path)) as db:
            db.row_factory = sqlite3.Row
            extra=" AND NOT EXISTS(SELECT 1 FROM message_attention n WHERE n.account_id=m.account_id AND n.message_id=m.message_id AND n.state IN ('protected','untouched'))" if attention=='required' else " AND EXISTS(SELECT 1 FROM message_attention n WHERE n.account_id=m.account_id AND n.message_id=m.message_id AND n.state='untouched')" if attention=='untouched' else ''
            rows = db.execute(self._REVIEW_SELECT + extra + """ AND m.message_id>?
                AND NOT EXISTS (SELECT 1 FROM classification_review_decisions d
                WHERE d.account_id=m.account_id AND d.message_id=m.message_id
                AND (d.fingerprint=json_array(a.category_id,a.status,a.source,a.reason,a.rule_version) OR (json_extract(d.fingerprint,'$[0]') IS a.category_id AND json_extract(d.fingerprint,'$[1]') IS a.status AND json_extract(d.fingerprint,'$[2]') IS a.source AND json_extract(d.fingerprint,'$[3]') IS a.reason)))
                ORDER BY m.message_id LIMIT ?""", (account,after,limit)).fetchall()
            return [self._review_row(row) for row in rows]

    def classification_history(self, account):
        with closing(sqlite3.connect(self._db_path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM classification_review_decisions WHERE account_id=? ORDER BY id DESC LIMIT 100", (account,))]

    def label_coverage(self, account):
        with closing(sqlite3.connect(self._db_path)) as db:
            total, known = db.execute("SELECT COUNT(*),COALESCE(SUM(labels_known),0) FROM messages WHERE account_id=?", (account,)).fetchone()
            return {"local_messages": total, "known_labels": known, "unknown_labels": total-known}

    def classification_candidate(self, account, message_id):
        with closing(sqlite3.connect(self._db_path)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(self._REVIEW_SELECT + """ AND m.message_id=? AND NOT EXISTS (
                SELECT 1 FROM classification_review_decisions d WHERE d.account_id=m.account_id
                AND d.message_id=m.message_id AND (d.fingerprint=json_array(a.category_id,a.status,a.source,a.reason,a.rule_version) OR (json_extract(d.fingerprint,'$[0]') IS a.category_id AND json_extract(d.fingerprint,'$[1]') IS a.status AND json_extract(d.fingerprint,'$[2]') IS a.source AND json_extract(d.fingerprint,'$[3]') IS a.reason)))""", (account,message_id)).fetchone()
            return self._review_row(row) if row else None

    def resolve_classification_review(self, account, message_id, fingerprint, decision, reason, assignment=None):
        # Compare candidate, apply manual override and audit the decision atomically.
        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(self._REVIEW_SELECT + " AND m.message_id=?", (account,message_id)).fetchone()
            prior = db.execute("SELECT 1 FROM classification_review_decisions WHERE account_id=? AND message_id=? AND fingerprint=?", (account,message_id,fingerprint)).fetchone()
            if row is None or row["fingerprint"]!=fingerprint or prior:
                raise ValueError("Review changed or already resolved; refresh before deciding")
            if assignment is not None:
                self._put_in_transaction(db, assignment)
            db.execute("INSERT INTO classification_review_decisions(account_id,message_id,fingerprint,decision,reason) VALUES (?,?,?,?,?)", (account,message_id,fingerprint,decision,reason))
            db.execute("INSERT INTO message_attention VALUES (?,?,'protected') ON CONFLICT(account_id,message_id) DO UPDATE SET state='protected'",(account,message_id))

    def review_counts(self, account):
        with closing(sqlite3.connect(self._db_path)) as db:
            category = db.execute("SELECT COUNT(*) FROM (" + self._REVIEW_SELECT + " AND NOT EXISTS(SELECT 1 FROM message_attention n WHERE n.account_id=m.account_id AND n.message_id=m.message_id AND n.state IN ('protected','untouched'))" + """ AND NOT EXISTS (
                SELECT 1 FROM classification_review_decisions d WHERE d.account_id=m.account_id AND d.message_id=m.message_id
                AND (d.fingerprint=json_array(a.category_id,a.status,a.source,a.reason,a.rule_version) OR (json_extract(d.fingerprint,'$[0]') IS a.category_id AND json_extract(d.fingerprint,'$[1]') IS a.status AND json_extract(d.fingerprint,'$[2]') IS a.source AND json_extract(d.fingerprint,'$[3]') IS a.reason))))""", (account,)).fetchone()[0]
            untouched=db.execute("SELECT COUNT(*) FROM message_attention n JOIN message_category_assignments a USING(account_id,message_id) WHERE n.account_id=? AND n.state='untouched' AND a.status='needs_review'",(account,)).fetchone()[0]
            protected=db.execute("SELECT COUNT(*) FROM message_attention n JOIN message_category_assignments a USING(account_id,message_id) WHERE n.account_id=? AND n.state='protected' AND a.status='needs_review'",(account,)).fetchone()[0]
            counts = {"category":category,"untouched":untouched,"protected":protected}
            for name,label in (("spam","SPAM"),("trash","TRASH")):
                counts[name] = db.execute("""SELECT COUNT(*) FROM placement_reviews r JOIN messages m
                    ON m.account_id=r.account_id AND m.message_id=r.message_id
                    WHERE r.account_id=? AND r.status='proposed' AND m.labels_known=1
                    AND EXISTS(SELECT 1 FROM json_each(m.label_ids) WHERE value=?)""", (account,label)).fetchone()[0]
            return counts

    @staticmethod
    def _review_row(row):
        result = dict(row)
        labels = result.pop("label_ids")
        result["_labels"] = tuple(json.loads(labels)) if result["labels_known"] else None
        return result

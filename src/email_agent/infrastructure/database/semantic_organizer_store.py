"""Durable frames, bounded campaigns and transactional taxonomy proposals."""
from contextlib import closing, contextmanager
from pathlib import Path
import json
import sqlite3
import uuid
from email_agent.application.semantic_mail import digest, feature
from email_agent.infrastructure.database.sqlite_message_category_repository import SQLiteMessageCategoryRepository
from email_agent.domain.message_category import MessageCategoryAssignment


def input_fingerprint(row):
    from email_agent.application.semantic_organizer import row_fingerprint
    return row_fingerprint(row)


from email_agent.infrastructure.database.local_organization_store import LocalOrganizationStore


class SQLiteSemanticOrganizerStore(LocalOrganizationStore):
    HOLD='Semantic sensitivity or unresolved meaning; retain for organization'

    def __init__(self,path,categories):self.path,self.categories=Path(path),Path(categories)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path,timeout=30)) as db:
            db.row_factory=sqlite3.Row
            db.execute('ATTACH DATABASE ? AS taxonomy',(str(self.categories),))
            yield db

    def repair_generated_icons(self,choices):
        """Repair only the unchanged fallback saved by an applied generated creation."""
        from email_agent.domain.category_icons import validate_category_icon
        changed=[]
        with self.connect() as db,db:
            db.execute('BEGIN IMMEDIATE')
            for category,icon in choices.items():
                validate_category_icon(icon)
                if icon=='folder-open':continue
                current=db.execute("SELECT icon_key FROM taxonomy.categories WHERE id=? AND source='approved'",(category,)).fetchone()
                if not current or current['icon_key']!='folder-open':continue
                creator=None
                for proposal in db.execute("SELECT id,payload FROM semantic_taxonomy_proposals WHERE status='applied'"):
                    plan=json.loads(proposal['payload'])
                    if plan.get('source') not in ('local','gemini','groq','mistral','openrouter','ollama'):continue
                    if any(op.get('kind')=='create' and op.get('id')==category and op.get('icon_key','folder-open')=='folder-open' for op in plan.get('operations',[])):creator=proposal['id'];break
                if not creator:continue
                before=dict(category_id=category,icon_key=current['icon_key']);after=dict(category_id=category,icon_key=icon)
                db.execute('UPDATE taxonomy.categories SET icon_key=? WHERE id=? AND icon_key=?',(icon,category,'folder-open'))
                db.execute("INSERT INTO semantic_taxonomy_audit(proposal_id,action,before_json,after_json) VALUES(?,'semantic_icon_repaired',?,?)",(creator,json.dumps(before),json.dumps(after)))
                changed.append(after)
        return changed

    def initialize(self):
        with self.connect() as db,db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS organizer_schema_versions(version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO organizer_schema_versions VALUES(1);
                CREATE TABLE IF NOT EXISTS semantic_frames(account_id TEXT,message_id TEXT,input_fingerprint TEXT NOT NULL,payload TEXT NOT NULL,source TEXT NOT NULL,version TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(account_id,message_id));
                CREATE INDEX IF NOT EXISTS semantic_frame_thread ON semantic_frames(account_id,json_extract(payload,'$.thread_key'));
                CREATE INDEX IF NOT EXISTS semantic_frame_entity ON semantic_frames(account_id,json_extract(payload,'$.entity'));
                CREATE TABLE IF NOT EXISTS semantic_frame_history(id INTEGER PRIMARY KEY,account_id TEXT,message_id TEXT,before_json TEXT,after_json TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS organizer_campaigns(id TEXT PRIMARY KEY,scope TEXT NOT NULL,model TEXT NOT NULL,request_cap INTEGER NOT NULL,body_cap INTEGER NOT NULL,requests_used INTEGER NOT NULL DEFAULT 0,bodies_used INTEGER NOT NULL DEFAULT 0,consented INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'preview',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS organizer_batches(campaign_id TEXT,ordinal INTEGER,input_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',result TEXT,PRIMARY KEY(campaign_id,ordinal));
                CREATE TABLE IF NOT EXISTS organizer_cache(fingerprint TEXT PRIMARY KEY,result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS semantic_taxonomy_proposals(id TEXT PRIMARY KEY,account_id TEXT,proposal_key TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'suggested',fingerprint TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(account_id,proposal_key));
                CREATE TABLE IF NOT EXISTS semantic_taxonomy_audit(id INTEGER PRIMARY KEY,proposal_id TEXT,action TEXT,before_json TEXT,after_json TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS semantic_profiles(account_id TEXT,category_id TEXT,definition TEXT NOT NULL,approved INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(account_id,category_id));
                CREATE TABLE IF NOT EXISTS semantic_policies(id TEXT PRIMARY KEY,account_id TEXT,target_category TEXT,conditions TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,proposal_id TEXT);
                CREATE TABLE IF NOT EXISTS semantic_relationships(account_id TEXT,message_id TEXT,other_id TEXT,kind TEXT,basis TEXT,confidence REAL,PRIMARY KEY(account_id,message_id,other_id,kind));
                CREATE TABLE IF NOT EXISTS organizer_recurring(account_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,policy TEXT NOT NULL,consented_at TEXT,last_campaign TEXT);
                CREATE TABLE IF NOT EXISTS organizer_daily_budget(day TEXT PRIMARY KEY,used INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS taxonomy.category_aliases(alias TEXT PRIMARY KEY,target TEXT NOT NULL);
                UPDATE organizer_campaigns SET status='needs_attention' WHERE status IN ('running','ready')
                    AND EXISTS(SELECT 1 FROM organizer_batches b WHERE b.campaign_id=organizer_campaigns.id AND b.status='failed')
                    AND NOT EXISTS(SELECT 1 FROM organizer_batches b WHERE b.campaign_id=organizer_campaigns.id AND b.status IN ('pending','running'));
                INSERT OR IGNORE INTO organizer_schema_versions VALUES(2);
                CREATE TABLE IF NOT EXISTS organizer_walk(account_id TEXT,stage TEXT,cursor TEXT NOT NULL,PRIMARY KEY(account_id,stage));
                INSERT OR IGNORE INTO organizer_schema_versions VALUES(5);
                CREATE TABLE IF NOT EXISTS semantic_pattern_seeded(proposal_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS semantic_content_patterns(account_id TEXT,signature TEXT,target_category TEXT,proposal_id TEXT,definition TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(account_id,signature,target_category));
                INSERT OR IGNORE INTO organizer_schema_versions VALUES(6);
            ''')
            needs_rebase=not db.execute('SELECT 1 FROM organizer_schema_versions WHERE version=3').fetchone()
            old_rows=db.execute('SELECT account_id,message_id,input_fingerprint FROM semantic_frames').fetchall() if needs_rebase else []
        self.initialize_local_policy()
        with self.connect() as db,db:
            if not db.execute('SELECT 1 FROM organizer_schema_versions WHERE version=7').fetchone():
                # Legacy approved drafts predate account-scoped publication.
                # Repair only where creation history and real membership agree.
                for category in db.execute("SELECT id FROM taxonomy.categories c WHERE c.id LIKE 'sem_%' AND NOT EXISTS(SELECT 1 FROM taxonomy.local_category_accounts o WHERE o.category_id=c.id)").fetchall():
                    creators=[]
                    for proposal in db.execute("SELECT id,account_id,payload FROM semantic_taxonomy_proposals WHERE status='applied'"):
                        if any(op['kind']=='create' and op['id']==category[0] for op in json.loads(proposal['payload'])['operations']):creators.append(proposal)
                    owners={r['account_id'] for r in creators}|{r[0] for r in db.execute('SELECT DISTINCT account_id FROM message_category_assignments WHERE category_id=?',(category[0],))}
                    if creators and len(owners)==1:
                        owner=next(iter(owners))
                        db.execute('INSERT OR IGNORE INTO taxonomy.local_category_accounts VALUES(?,?)',(category[0],owner))
                        db.execute("INSERT INTO semantic_taxonomy_audit(proposal_id,action,after_json) VALUES(?,'account_scope_repaired',?)",(creators[0]['id'],json.dumps(dict(category_id=category[0],account_id=owner))))
                db.execute('INSERT OR IGNORE INTO organizer_schema_versions VALUES(7)')
            if not db.execute('SELECT 1 FROM organizer_schema_versions WHERE version=8').fetchone():
                # Only retries consumed by the obsolete WAL guard are restored.
                blocked=db.execute("""SELECT t.* FROM local_tool_attempts t JOIN semantic_taxonomy_proposals p ON p.id=t.proposal_id
                    WHERE p.status='approved' AND t.fingerprint=p.fingerprint AND EXISTS(SELECT 1 FROM local_organization_audit a
                    WHERE a.account_id=t.account_id AND json_extract(a.payload,'$.proposal_id')=t.proposal_id
                    AND json_extract(a.payload,'$.error')='Atomic taxonomy publication requires rollback journals; no change applied')""").fetchall()
                for row in blocked:
                    db.execute('UPDATE local_tool_attempts SET attempts=0 WHERE account_id=? AND proposal_id=? AND fingerprint=?',(row['account_id'],row['proposal_id'],row['fingerprint']))
                    db.execute("INSERT INTO semantic_taxonomy_audit(proposal_id,action,after_json) VALUES(?,'publication_retry_enabled',?)",(row['proposal_id'],json.dumps({'previous_attempts':row['attempts'],'reason':'Native rollback journal publication repaired'})))
                db.execute('INSERT OR IGNORE INTO organizer_schema_versions VALUES(8)')
        if needs_rebase:
            from email_agent.application.semantic_organizer import row_fingerprint
            for old in old_rows:
                row=self.message_row(old['account_id'],old['message_id'])
                if row and old['input_fingerprint']==row_fingerprint(row,include_labels=True):
                    with self.connect() as db,db:db.execute('UPDATE semantic_frames SET input_fingerprint=? WHERE account_id=? AND message_id=? AND input_fingerprint=?',(input_fingerprint(row),old['account_id'],old['message_id'],old['input_fingerprint']))
            with self.connect() as db,db:db.execute('INSERT OR IGNORE INTO organizer_schema_versions VALUES(3)')

    def snapshot(self,account,limit=10000,balanced=False):
        with self.connect() as db:
            order="ROW_NUMBER() OVER (PARTITION BY a.category_id,json_extract(f.payload,'$.primary_intent') ORDER BY m.internal_date_ms DESC),m.internal_date_ms DESC" if balanced else 'm.internal_date_ms DESC,m.message_id'
            rows=db.execute('''SELECT m.*,h.headers,b.body_text,c.status content_status,a.category_id,a.source assignment_source,a.status,a.reason,a.rule_version,
                f.payload semantic_json,f.input_fingerprint
                FROM messages m LEFT JOIN message_list_headers h USING(account_id,message_id)
                LEFT JOIN message_search_content b USING(account_id,message_id)
                LEFT JOIN message_content_state c USING(account_id,message_id)
                LEFT JOIN message_category_assignments a USING(account_id,message_id)
                LEFT JOIN semantic_frames f USING(account_id,message_id)
                WHERE m.account_id=? ORDER BY '''+order+' LIMIT ?',(account,limit)).fetchall()
        return [self.row(row) for row in rows]

    def grouping_snapshot(self,account):return self.snapshot(account,balanced=True)

    def profile_frames(self,account):
        """Same bounded order as grouping_snapshot, without bodies/header copies."""
        with self.connect() as db:
            rows=db.execute("""SELECT a.category_id,f.payload FROM messages m
                LEFT JOIN message_category_assignments a USING(account_id,message_id)
                LEFT JOIN semantic_frames f USING(account_id,message_id) WHERE m.account_id=?
                ORDER BY ROW_NUMBER() OVER(PARTITION BY a.category_id,json_extract(f.payload,'$.primary_intent') ORDER BY m.internal_date_ms DESC),m.internal_date_ms DESC LIMIT 10000""",(account,))
            for category,payload in rows:
                if payload:yield category or 'other',json.loads(payload)


    def grouping_revision(self,account):
        from email_agent.application.semantic_mail import digest
        with self.connect() as db:
            rows=[tuple(r) for r in db.execute('SELECT f.message_id,f.input_fingerprint,f.payload,a.category_id,a.source,a.status FROM semantic_frames f LEFT JOIN message_category_assignments a USING(account_id,message_id) WHERE f.account_id=? ORDER BY f.message_id',(account,))]
        return digest([rows,self.catalog(account),self.local_policy(account),[(p['id'],p['status']) for p in self.proposals(account)]])

    def grouping_checkpoint(self,account,revision=None,stage='group_complete'):
        with self.connect() as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS organizer_walk(account_id TEXT,stage TEXT,cursor TEXT NOT NULL,PRIMARY KEY(account_id,stage))')
            if revision is not None:db.execute("INSERT INTO organizer_walk VALUES(?,?,?) ON CONFLICT(account_id,stage) DO UPDATE SET cursor=excluded.cursor",(account,stage,revision))
            row=db.execute('SELECT cursor FROM organizer_walk WHERE account_id=? AND stage=?',(account,stage)).fetchone()
            return row[0] if row else None

    def walk(self,account,limit):
        """Persisted full-mailbox local walk; a recent preview is not coverage."""
        with self.connect() as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS organizer_walk(account_id TEXT,stage TEXT,cursor TEXT NOT NULL,PRIMARY KEY(account_id,stage))')
            row=db.execute("SELECT cursor FROM organizer_walk WHERE account_id=? AND stage='local'",(account,)).fetchone()
            cursor=row[0] if row else ''
            ids=[r[0] for r in db.execute('SELECT message_id FROM messages WHERE account_id=? AND message_id>? ORDER BY message_id LIMIT ?',(account,cursor,limit))]
            if not ids:ids=[r[0] for r in db.execute('SELECT message_id FROM messages WHERE account_id=? ORDER BY message_id LIMIT ?',(account,limit))]
            db.execute("INSERT INTO organizer_walk VALUES(?,'local',?) ON CONFLICT(account_id,stage) DO UPDATE SET cursor=excluded.cursor",(account,ids[-1] if ids else ''))
        return [r for id in ids if (r:=self.message_row(account,id)) is not None]

    def planning_groups(self,account,limit=8):
        """Rotate semantic purposes across all ages, with bounded representative IDs."""
        with self.connect() as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS organizer_walk(account_id TEXT,stage TEXT,cursor TEXT NOT NULL,PRIMARY KEY(account_id,stage))')
            previous=db.execute("SELECT cursor FROM organizer_walk WHERE account_id=? AND stage='taxonomy'",(account,)).fetchone()
            cursor=previous[0] if previous else ''
            query="""SELECT json_extract(f.payload,'$.primary_intent') purpose,COUNT(*) n FROM semantic_frames f
                JOIN message_category_assignments a USING(account_id,message_id) JOIN message_content_state c USING(account_id,message_id)
                WHERE f.account_id=? AND a.category_id='other' AND a.source!='manual' AND c.status='complete'
                AND json_extract(f.payload,'$.understood')=1 AND json_extract(f.payload,'$.analysis_reason') IS NULL
                AND json_extract(f.payload,'$.confidence')>=.85 AND json_extract(f.payload,'$.primary_intent')>?
                GROUP BY purpose ORDER BY purpose LIMIT ?"""
            groups=db.execute(query,(account,cursor,limit)).fetchall()
            if not groups:groups=db.execute(query,(account,'',limit)).fetchall()
            selected=[]
            for group in groups:
                ids=[r[0] for r in db.execute("""SELECT f.message_id FROM semantic_frames f JOIN message_category_assignments a USING(account_id,message_id)
                    JOIN message_content_state c USING(account_id,message_id) JOIN messages m USING(account_id,message_id)
                    WHERE f.account_id=? AND a.category_id='other' AND a.source!='manual' AND c.status='complete'
                    AND json_extract(f.payload,'$.primary_intent')=? AND json_extract(f.payload,'$.understood')=1
                    AND json_extract(f.payload,'$.analysis_reason') IS NULL AND json_extract(f.payload,'$.confidence')>=.85
                    ORDER BY ROW_NUMBER() OVER(PARTITION BY COALESCE(json_extract(f.payload,'$.thread_key'),m.sender) ORDER BY f.message_id),f.message_id LIMIT 8""",(account,group['purpose']))]
                selected.append((group['purpose'],ids))
            db.execute("INSERT INTO organizer_walk VALUES(?,'taxonomy',?) ON CONFLICT(account_id,stage) DO UPDATE SET cursor=excluded.cursor",(account,groups[-1]['purpose'] if groups else ''))
        return [(name,[r for id in ids if (r:=self.message_row(account,id))]) for name,ids in selected]

    def semantic_counts(self,account):
        from collections import Counter
        states=Counter();causes=Counter();sources=Counter();count=0
        with self.connect() as db:
            rows=db.execute('''SELECT a.source,f.source frame_source,json_extract(f.payload,'$.understood') understood,
                json_extract(f.payload,'$.analysis_reason') reason,json_extract(f.payload,'$.sensitivities') sensitivities,c.status content_status
                FROM messages m LEFT JOIN message_category_assignments a USING(account_id,message_id)
                LEFT JOIN semantic_frames f USING(account_id,message_id) LEFT JOIN message_content_state c USING(account_id,message_id) WHERE m.account_id=?''',(account,))
            for r in rows:
                count+=1
                if r['source']=='manual':states['protected']+=1
                elif r['content_status']!='complete' or not r['understood'] or r['reason']:
                    states['awaiting_analysis']+=1;causes['body_missing' if r['content_status']!='complete' else r['reason'] or 'not_analyzed']+=1
                elif set(json.loads(r['sensitivities'] or '[]'))&{'financial','transactional','security','personal'}:states['protected']+=1
                else:states['organized']+=1
                if r['frame_source']:sources[r['frame_source']]+=1
        return dict(indexed=count,states=dict(states),awaiting_causes=dict(causes),sources=dict(sources))

    def message_row(self,account,id):
        with self.connect() as db:
            row=db.execute('''SELECT m.*,h.headers,b.body_text,c.status content_status,a.category_id,a.source assignment_source,a.status,a.reason,a.rule_version,
                f.payload semantic_json,f.input_fingerprint FROM messages m
                LEFT JOIN message_list_headers h USING(account_id,message_id)
                LEFT JOIN message_search_content b USING(account_id,message_id)
                LEFT JOIN message_content_state c USING(account_id,message_id)
                LEFT JOIN message_category_assignments a USING(account_id,message_id)
                LEFT JOIN semantic_frames f USING(account_id,message_id)
                WHERE m.account_id=? AND m.message_id=?''',(account,id)).fetchone()
            return self.row(row) if row else None

    @staticmethod
    def row(row):
        result=dict(account_id=row['account_id'],message_id=row['message_id'],sender=row['sender'],subject=row['subject'],
            internal_date_ms=row['internal_date_ms'],labels=json.loads(row['label_ids']) if row['labels_known'] else None,
            headers=json.loads(row['headers']) if row['headers'] else {},body=(row['body_text'] or ''),
            decision=dict(category_id=row['category_id'],source=row['assignment_source'],status=row['status'],reason=row['reason'] or '',rule_version=row['rule_version']),
            content_complete=row['content_status']=='complete' if 'content_status' in row.keys() else False,semantic=json.loads(row['semantic_json']) if row['semantic_json'] else None,input_fingerprint=row['input_fingerprint'])
        return result

    def put_frame(self,row,frame,version):
        payload=json.dumps(frame,sort_keys=True);fingerprint=input_fingerprint(row)
        with self.connect() as db,db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('''SELECT m.*,h.headers,b.body_text,c.status content_status,a.category_id,a.source assignment_source,a.status,a.reason,a.rule_version,NULL semantic_json,NULL input_fingerprint
                FROM messages m LEFT JOIN message_list_headers h USING(account_id,message_id)
                LEFT JOIN message_search_content b USING(account_id,message_id) LEFT JOIN message_content_state c USING(account_id,message_id) LEFT JOIN message_category_assignments a USING(account_id,message_id)
                WHERE m.account_id=? AND m.message_id=?''',(row['account_id'],row['message_id'])).fetchone()
            if not current or input_fingerprint(self.row(current))!=fingerprint:raise ValueError('Message evidence changed during analysis; retain current user decision')
            old=db.execute('SELECT payload,input_fingerprint FROM semantic_frames WHERE account_id=? AND message_id=?',(row['account_id'],row['message_id'])).fetchone()
            if old and old['payload']==payload and old['input_fingerprint']==fingerprint:return False
            db.execute('INSERT INTO semantic_frame_history(account_id,message_id,before_json,after_json) VALUES(?,?,?,?)',(row['account_id'],row['message_id'],old['payload'] if old else None,payload))
            db.execute('''INSERT INTO semantic_frames(account_id,message_id,input_fingerprint,payload,source,version) VALUES(?,?,?,?,?,?)
                ON CONFLICT(account_id,message_id) DO UPDATE SET input_fingerprint=excluded.input_fingerprint,payload=excluded.payload,source=excluded.source,version=excluded.version,updated_at=CURRENT_TIMESTAMP''',(row['account_id'],row['message_id'],fingerprint,payload,frame['source'],version))
            if set(frame['sensitivities'])&{'financial','transactional','security','personal'}:
                db.execute("INSERT OR IGNORE INTO cleanup_protections(account_id,scope,identity,reason) VALUES(?,'message',?,?)",(row['account_id'],row['message_id'],self.HOLD))
            # Exact provider threads or same-domain/reference evidence, never a
            # company name alone. Existing links retain their earlier evidence.
            own_refs={r['key'] for r in frame.get('references',[])}
            others=db.execute("SELECT message_id,payload FROM semantic_frames WHERE account_id=? AND message_id<>? AND ((? IS NOT NULL AND json_extract(payload,'$.thread_key')=?) OR (?=1 AND json_extract(payload,'$.entity')=?))",(row['account_id'],row['message_id'],frame.get('thread_key'),frame.get('thread_key'),int(bool(own_refs)),frame.get('entity'))).fetchall()
            for other in others:
                payload_other=json.loads(other['payload']);kind=None
                if frame.get('thread_key') and frame['thread_key']==payload_other.get('thread_key'):kind='provider_thread'
                elif frame.get('entity')==payload_other.get('entity') and own_refs&{r['key'] for r in payload_other.get('references',[])}:kind='shared_reference'
                if kind:db.execute('INSERT OR IGNORE INTO semantic_relationships VALUES(?,?,?,?,?,?)',(row['account_id'],row['message_id'],other['message_id'],kind,'observed',1.0))
            for other_id in frame.get('related_ids',[]):
                if other_id!=row['message_id'] and db.execute('SELECT 1 FROM messages WHERE account_id=? AND message_id=?',(row['account_id'],other_id)).fetchone():
                    db.execute('INSERT OR REPLACE INTO semantic_relationships VALUES(?,?,?,?,?,?)',(row['account_id'],row['message_id'],other_id,'semantic_relation','AI interpretation',frame['confidence']))
        return True

    def assign_semantic(self,row,frame):
        from email_agent.application.classify_messages import decide
        from email_agent.domain.message import EmailMessage
        target=frame['suggested_category_id'];account,id=row['account_id'],row['message_id']
        if row['decision']['source']=='manual' or (target==row['decision']['category_id'] and row['decision']['status']=='classified'):return False
        local,status,reason=decide(EmailMessage(id,account,row['sender'],row['subject']),row['body'])
        from email_agent.application.semantic_mail import supported_semantic_purpose
        understood_other=target=='other' and row['decision']['category_id']=='other' and supported_semantic_purpose(frame)
        if (target=='other' and not understood_other) or (status=='classified' and reason.startswith(('content_','explicit_submitted_application_')) and local!=target):return False
        if target=='job_applications':
            from email_agent.application.job_evidence import employment_evidence
            from email_agent.application.semantic_mail import supported_application_frame
            semantic_application=supported_application_frame(frame)
            if reason=='recruitment_advertisement' or not (employment_evidence(row['subject'],row['sender'],row['body']) or semantic_application):return False
        with self.connect() as db,db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT * FROM message_category_assignments WHERE account_id=? AND message_id=?',(account,id)).fetchone()
            if not current or current['source']=='manual' or any(current[key]!=value for key,value in row['decision'].items()):raise ValueError('Classification changed during semantic analysis')
            if not db.execute("SELECT 1 FROM taxonomy.categories WHERE id=? AND source!='retired'",(target,)).fetchone():raise ValueError('Category is no longer active')
            changed=SQLiteMessageCategoryRepository._put_in_transaction(db,MessageCategoryAssignment(account,id,target,'automatic','classified','semantic_full_message_evidence','semantic-full-1'))
            db.execute("INSERT INTO message_attention VALUES(?,?,'protected') ON CONFLICT(account_id,message_id) DO UPDATE SET state='protected'",(account,id))
            db.execute("INSERT OR IGNORE INTO cleanup_protections(account_id,scope,identity,reason) VALUES(?,'message',?,'Full semantic organization; cleanup requires independent verification')",(account,id))
            return bool(changed)

    def create_campaign(self,scope,model,request_cap,body_cap,batches):
        id=uuid.uuid4().hex
        with self.connect() as db,db:
            db.execute('INSERT INTO organizer_campaigns(id,scope,model,request_cap,body_cap) VALUES(?,?,?,?,?)',(id,json.dumps(scope),model,request_cap,body_cap))
            for ordinal,batch in enumerate(batches):db.execute('INSERT INTO organizer_batches(campaign_id,ordinal,input_json) VALUES(?,?,?)',(id,ordinal,json.dumps(batch)))
        return id

    def campaign(self,id):
        with self.connect() as db:
            row=db.execute('SELECT * FROM organizer_campaigns WHERE id=?',(id,)).fetchone()
            if not row:raise ValueError('Unknown organizing scope')
            return dict(row)|{'scope':json.loads(row['scope'])}

    def consent(self,id):
        with self.connect() as db,db:
            db.execute("UPDATE organizer_campaigns SET consented=1,status='ready' WHERE id=? AND status IN ('preview','needs_attention')",(id,))

    def batches(self,id):
        with self.connect() as db:
            return [dict(row)|{'input':json.loads(row['input_json'])} for row in db.execute('SELECT * FROM organizer_batches WHERE campaign_id=? ORDER BY ordinal',(id,))]

    def claim_batch(self,id,ordinal):
        with self.connect() as db,db:
            db.execute('BEGIN IMMEDIATE')
            campaign=db.execute('SELECT * FROM organizer_campaigns WHERE id=?',(id,)).fetchone()
            row=db.execute('SELECT status FROM organizer_batches WHERE campaign_id=? AND ordinal=?',(id,ordinal)).fetchone()
            if not campaign or not campaign['consented'] or campaign['status'] not in ('ready','running') or not row or row['status']!='pending':return False
            db.execute("UPDATE organizer_batches SET status='running' WHERE campaign_id=? AND ordinal=?",(id,ordinal))
            db.execute("UPDATE organizer_campaigns SET status='running' WHERE id=?",(id,))
            return True

    def reserve_request(self,id,day=None,daily_cap=0):
        with self.connect() as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT request_cap,requests_used,consented FROM organizer_campaigns WHERE id=?',(id,)).fetchone()
            if not row or not row['consented'] or row['requests_used']>=row['request_cap']:return False
            if day:
                db.execute('INSERT OR IGNORE INTO organizer_daily_budget(day) VALUES(?)',(day,))
                if db.execute('SELECT used FROM organizer_daily_budget WHERE day=?',(day,)).fetchone()[0]>=daily_cap:return False
                db.execute('UPDATE organizer_daily_budget SET used=used+1 WHERE day=?',(day,))
            db.execute('UPDATE organizer_campaigns SET requests_used=requests_used+1 WHERE id=?',(id,))
            return True

    def reserve_body(self,id):
        with self.connect() as db,db:
            cursor=db.execute('UPDATE organizer_campaigns SET bodies_used=bodies_used+1 WHERE id=? AND consented=1 AND bodies_used<body_cap',(id,))
            return cursor.rowcount==1

    def finish_batch(self,id,ordinal,status,result):
        with self.connect() as db,db:
            db.execute('UPDATE organizer_batches SET status=?,result=? WHERE campaign_id=? AND ordinal=?',(status,json.dumps(result),id,ordinal))
            remaining=db.execute("SELECT COUNT(*) FROM organizer_batches WHERE campaign_id=? AND status!='done'",(id,)).fetchone()[0]
            if not remaining:db.execute("UPDATE organizer_campaigns SET status='completed' WHERE id=?",(id,))
            elif status=='failed' or not db.execute("SELECT 1 FROM organizer_batches WHERE campaign_id=? AND status IN ('pending','running')",(id,)).fetchone():db.execute("UPDATE organizer_campaigns SET status='needs_attention' WHERE id=?",(id,))

    def cache(self,key,result=None):
        with self.connect() as db,db:
            if result is not None:db.execute('INSERT OR REPLACE INTO organizer_cache VALUES(?,?)',(key,result));return result
            row=db.execute('SELECT result FROM organizer_cache WHERE fingerprint=?',(key,)).fetchone()
            return row[0] if row else None

    def catalog(self,account=None):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,name,source,icon_key FROM taxonomy.categories WHERE source!='retired' AND (? IS NULL OR NOT EXISTS(SELECT 1 FROM taxonomy.local_category_accounts v WHERE v.category_id=taxonomy.categories.id) OR EXISTS(SELECT 1 FROM taxonomy.local_category_accounts v WHERE v.category_id=taxonomy.categories.id AND v.account_id=?)) ORDER BY name",(account,account))]

    def model_catalog(self,account):
        """Account-scoped observed document meanings; not category CAS state."""
        catalog=self.catalog(account);types={}
        with self.connect() as db:
            work_guard="AND NOT EXISTS(SELECT 1 FROM mail_work w WHERE w.account_id=a.account_id AND w.message_id=a.message_id AND w.stage='local' AND w.status!='done')" if db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_work'").fetchone() else ''
            for row in db.execute("""SELECT a.category_id,MIN(json_extract(f.payload,'$.message_type')) message_type,COUNT(*) n FROM semantic_frames f
                JOIN message_category_assignments a USING(account_id,message_id) JOIN message_content_state c USING(account_id,message_id)
                WHERE a.account_id=? AND c.status='complete' AND json_extract(f.payload,'$.understood')=1 AND json_extract(f.payload,'$.analysis_reason') IS NULL
                AND json_extract(f.payload,'$.confidence')>=.85 AND length(json_extract(f.payload,'$.message_type')) BETWEEN 1 AND 60
                """+work_guard+" GROUP BY a.category_id,lower(json_extract(f.payload,'$.message_type')) ORDER BY n DESC",(account,)):
                members=types.setdefault(row['category_id'],[])
                if len(members)<8:members.append(row['message_type'])
        return [dict(c,observed_types=sorted(types.get(c['id'],[]),key=str.casefold)) for c in catalog]

    def supersede_redundant_local_proposals(self,account):
        active={c['id'] for c in self.catalog()}-{'other'}
        for proposal in self.proposals(account):
            payload=proposal['payload']
            if proposal['status']!='suggested' or payload.get('source')!='local' or payload.get('action')!='create':continue
            frames=[self.message_row(account,r['message_id']) for r in payload.get('preview',[])]
            hints={(row.get('semantic') or {}).get('category_hint') for row in frames if row}
            if not frames or len(hints)!=1 or not hints<=active:continue
            with self.connect() as db,db:
                db.execute("UPDATE semantic_taxonomy_proposals SET status='superseded' WHERE id=? AND status='suggested'",(proposal['id'],))
                db.execute("INSERT INTO semantic_taxonomy_audit(proposal_id,action,after_json) VALUES(?,'superseded',?)",(proposal['id'],json.dumps({'reason':'Observed purpose already matches an active category; use existing category/facets','existing_category':next(iter(hints))})))

    def profiles(self,account):
        with self.connect() as db:return {r['category_id']:json.loads(r['definition']) for r in db.execute('SELECT * FROM semantic_profiles WHERE account_id=?',(account,))}

    def save_observed_profile(self,account,category,profile):
        with self.connect() as db,db:
            db.execute('''INSERT INTO semantic_profiles VALUES(?,?,?,0) ON CONFLICT(account_id,category_id)
                DO UPDATE SET definition=excluded.definition WHERE semantic_profiles.approved=0''',(account,category,json.dumps(profile)))

    def propose(self,account,proposal_key,payload):
        id=uuid.uuid4().hex;fingerprint=digest(payload)
        with self.connect() as db,db:
            own=set(payload.get('evidence_fingerprints',{}))|{p['message_id'] for p in payload.get('assignments',[])}
            for previous in db.execute('SELECT * FROM semantic_taxonomy_proposals WHERE account_id=?',(account,)):
                plan=json.loads(previous['payload']);other=set(plan.get('evidence_fingerprints',{}))|{p['message_id'] for p in plan.get('assignments',[])}
                same_evidence=all(plan.get('evidence_fingerprints',{}).get(id)==payload.get('evidence_fingerprints',{}).get(id) for id in own&other)
                if previous['status']=='suggested' and payload.get('account_scoped') and not plan.get('account_scoped') and plan['action']=='create' and plan['name'].replace('_',' ').casefold()==payload['name'].casefold():
                    db.execute("UPDATE semantic_taxonomy_proposals SET status='superseded' WHERE id=?",(previous['id'],))
                    db.execute("INSERT INTO semantic_taxonomy_audit(proposal_id,action,after_json) VALUES(?,'scope_upgrade',?)",(previous['id'],json.dumps({'account_scoped':True})))
                    continue
                if previous['status']=='suggested' and plan['action']=='create' and payload['action']=='create' and own&other and same_evidence and (plan.get('semantic_concept') or plan['name'].casefold())!=(payload.get('semantic_concept') or payload['name'].casefold()):
                    db.execute("UPDATE semantic_taxonomy_proposals SET status='needs_review' WHERE id=?",(previous['id'],))
                    db.execute("INSERT INTO semantic_taxonomy_audit(proposal_id,action,after_json) VALUES(?,'conflicting_purposes',?)",(previous['id'],json.dumps(dict(competing_name=payload['name'],reason='Overlapping unchanged evidence has competing primary purposes; preserve current categories'))))
                    return previous['id']
                label_refresh=previous['status']=='suggested' and plan.get('source')=='local' and '_' in plan['name'] and plan['name'].replace('_',' ').casefold()==payload['name'].casefold()
                if plan['action'] in ('create','split') and payload['action'] in ('create','split') and (plan.get('semantic_concept') or plan['name'].replace('_',' ').casefold())==(payload.get('semantic_concept') or payload['name'].replace('_',' ').casefold()) and own and other and own<=other and same_evidence and (previous['status']!='deferred' or plan.get('semantic_revision')==payload.get('semantic_revision')) and not label_refresh:
                    return previous['id']
            existing=db.execute('SELECT * FROM semantic_taxonomy_proposals WHERE account_id=? AND proposal_key=?',(account,proposal_key)).fetchone()
            if existing and existing['status'] in ('suggested','deferred') and existing['fingerprint']!=fingerprint:
                db.execute("UPDATE semantic_taxonomy_proposals SET payload=?,fingerprint=?,status='suggested' WHERE id=? AND status IN ('suggested','deferred')",(json.dumps(payload),fingerprint,existing['id']))
                db.execute("INSERT INTO semantic_taxonomy_audit(proposal_id,action,before_json,after_json) VALUES(?,'refreshed',?,?)",(existing['id'],existing['payload'],json.dumps(payload)))
                return existing['id']
            db.execute('INSERT OR IGNORE INTO semantic_taxonomy_proposals(id,account_id,proposal_key,payload,fingerprint) VALUES(?,?,?,?,?)',(id,account,proposal_key,json.dumps(payload),fingerprint))
            row=db.execute('SELECT id FROM semantic_taxonomy_proposals WHERE account_id=? AND proposal_key=?',(account,proposal_key)).fetchone()
            return row[0]

    def defer_proposal(self,account,id,reason):
        with self.connect() as db,db:
            changed=db.execute("UPDATE semantic_taxonomy_proposals SET status='deferred' WHERE account_id=? AND id=? AND status='suggested'",(account,id)).rowcount
            if changed:db.execute("INSERT INTO semantic_taxonomy_audit(proposal_id,action,after_json) VALUES(?,'deferred',?)",(id,json.dumps(dict(reason=reason))))

    def proposals(self,account):
        with self.connect() as db:
            return [dict(row)|{'payload':json.loads(row['payload'])} for row in db.execute('SELECT * FROM semantic_taxonomy_proposals WHERE account_id=? ORDER BY created_at DESC,id',(account,))]

    def decide(self,account,id,fingerprint,approved,authority='user'):
        with self.connect() as db,db:
            row=db.execute('SELECT * FROM semantic_taxonomy_proposals WHERE account_id=? AND id=?',(account,id)).fetchone()
            if not row or row['fingerprint']!=fingerprint:raise ValueError('Proposal changed; refresh its preview')
            if row['status'] in ('applied','dismissed'):return row['status']
            status='approved' if approved else 'dismissed'
            db.execute('UPDATE semantic_taxonomy_proposals SET status=? WHERE id=?',(status,id))
            db.execute('INSERT INTO semantic_taxonomy_audit(proposal_id,action,after_json) VALUES(?,?,?)',(id,status,json.dumps({'fingerprint':fingerprint,'authority':authority})))
            return status

    def user_approved(self,account,id):
        with self.connect() as db:
            row=db.execute("SELECT a.after_json FROM semantic_taxonomy_audit a JOIN semantic_taxonomy_proposals p ON p.id=a.proposal_id WHERE p.account_id=? AND p.id=? AND p.status='approved' AND a.action='approved' ORDER BY a.id DESC LIMIT 1",(account,id)).fetchone()
            if not row:return False
            authority=json.loads(row[0]).get('authority')
            if authority:return authority=='user'
            # Legacy human approvals had no authority field; automatic attempts
            # are durably identifiable and must not inherit human authority.
            return not db.execute('SELECT 1 FROM local_tool_attempts WHERE account_id=? AND proposal_id=?',(account,id)).fetchone()

    def publication_prerequisites(self):
        from email_agent.infrastructure.database.taxonomy_transaction import publication_prerequisites
        publication_prerequisites(self.path,self.categories)

    def apply(self,account,id,policy_revision=None):
        from email_agent.infrastructure.database.taxonomy_transaction import taxonomy_transaction
        with taxonomy_transaction(self.path,self.categories) as db:
            if policy_revision is not None:
                policy=db.execute('SELECT enabled,revision FROM local_organization_policy WHERE account_id=?',(account,)).fetchone()
                if not policy or not policy['enabled'] or policy['revision']!=policy_revision:raise ValueError('Autonomous organization revoked or changed')
            proposal=db.execute('SELECT * FROM semantic_taxonomy_proposals WHERE account_id=? AND id=?',(account,id)).fetchone()
            if not proposal:raise ValueError('Unknown proposal')
            if proposal['status']=='applied':return dict(status='applied',affected=0,category_ids=json.loads(proposal['payload']).get('category_ids',[]))
            if proposal['status']!='approved':raise ValueError('Approve the exact representative preview first')
            plan=json.loads(proposal['payload'])
            for expected in plan.get('expected_categories',[]):
                row=db.execute('SELECT id,name,source,icon_key FROM taxonomy.categories WHERE id=?',(expected['id'],)).fetchone()
                if not row or dict(row)!=expected:raise ValueError('Category structure changed; request a new proposal')
            # Validate all assignment snapshots before publishing any structure.
            for message_id,expected in plan.get('evidence_fingerprints',{}).items():
                current=db.execute('''SELECT m.*,h.headers,b.body_text,c.status content_status,a.category_id,a.source assignment_source,a.status,a.reason,a.rule_version,NULL semantic_json,NULL input_fingerprint
                    FROM messages m LEFT JOIN message_list_headers h USING(account_id,message_id)
                    LEFT JOIN message_search_content b USING(account_id,message_id)
                LEFT JOIN message_content_state c USING(account_id,message_id)
                    LEFT JOIN message_category_assignments a USING(account_id,message_id)
                    WHERE m.account_id=? AND m.message_id=?''',(account,message_id)).fetchone()
                if not current or input_fingerprint(self.row(current))!=expected:raise ValueError('Message evidence changed; refresh proposal')
            original_plan=json.dumps(plan);rebased=False
            for assignment in plan.get('assignments',[]):
                row=db.execute('SELECT * FROM message_category_assignments WHERE account_id=? AND message_id=?',(account,assignment['message_id'])).fetchone()
                if not row:raise ValueError('Message classification changed; refresh proposal')
                if dict(row)!=assignment['before']:
                    frame_row=db.execute('SELECT payload FROM semantic_frames WHERE account_id=? AND message_id=?',(account,assignment['message_id'])).fetchone()
                    frame=json.loads(frame_row[0]) if frame_row else {}
                    stable=all(row[k]==assignment['before'][k] for k in ('account_id','message_id','category_id','source'))
                    matching=any(policy['target']==assignment['target'] and all(feature(frame,k)==v for k,v in policy['conditions'].items()) for policy in plan.get('policies',[]))
                    if not (plan['action']=='create' and stable and row['source']=='automatic' and frame.get('understood') and not frame.get('analysis_reason') and frame.get('confidence',0)>=.85 and matching):raise ValueError('Message classification changed; refresh proposal')
                    assignment['before']=dict(row);rebased=True
                if row['source']=='manual':raise ValueError('User category corrections remain authoritative')
            if rebased:
                db.execute('INSERT INTO semantic_taxonomy_audit(proposal_id,action,before_json,after_json) VALUES(?,?,?,?)',(id,'automatic_snapshot_refresh',original_plan,json.dumps(plan)))
                db.execute('UPDATE semantic_taxonomy_proposals SET payload=?,fingerprint=? WHERE id=?',(json.dumps(plan),digest(plan),id))
            before_categories=[dict(r) for r in db.execute('SELECT * FROM taxonomy.categories')]
            retirement_journal=[]
            for op in plan['operations']:
                if op['kind']!='retire':continue
                alias=db.execute('SELECT * FROM taxonomy.category_aliases WHERE alias=?',(op['id'],)).fetchone()
                policies=[dict(r) for r in db.execute('SELECT * FROM semantic_policies WHERE account_id=? AND target_category=?',(account,op['id']))]
                patterns=[dict(r) for r in db.execute('SELECT * FROM semantic_content_patterns WHERE account_id=? AND target_category=?',(account,op['id']))] if db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_content_patterns'").fetchone() else []
                retirement_journal.append(dict(id=op['id'],target=op['target'],alias=dict(alias) if alias else None,policies=policies,patterns=patterns))
            before_profiles={r['category_id']:dict(r) for r in db.execute('SELECT * FROM semantic_profiles WHERE account_id=?',(account,))}
            for operation in plan['operations']:
                kind=operation['kind'];category=operation['id']
                if kind=='create':
                    existing=db.execute('SELECT id FROM taxonomy.categories WHERE id=?',(category,)).fetchone()
                    if existing:raise ValueError('Target category already exists')
                    db.execute("INSERT INTO taxonomy.categories(id,name,source,icon_key) VALUES(?,?,'approved',?)",(category,operation['name'],operation.get('icon_key','folder-open')))
                    if plan.get('account_scoped'):db.execute('INSERT INTO taxonomy.local_category_accounts VALUES(?,?)',(category,account))
                elif kind=='rename':db.execute('UPDATE taxonomy.categories SET name=? WHERE id=?',(operation['name'],category))
                elif kind=='retire':
                    moving={a['message_id'] for a in plan.get('assignments',[]) if a['before']['category_id']==category}
                    members=db.execute('SELECT account_id,message_id,source FROM message_category_assignments WHERE category_id=?',(category,)).fetchall()
                    if any(r['source']=='manual' or r['account_id']!=account or r['message_id'] not in moving for r in members):raise ValueError('Retirement would hide mail outside this preview; split or rename instead')
                    db.execute("UPDATE taxonomy.categories SET source='retired' WHERE id=?",(category,))
                    db.execute('INSERT OR REPLACE INTO taxonomy.category_aliases VALUES(?,?)',(category,operation['target']))
                    db.execute('UPDATE semantic_policies SET enabled=0 WHERE account_id=? AND target_category=?',(account,category))
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_content_patterns'").fetchone():db.execute('UPDATE semantic_content_patterns SET enabled=0 WHERE account_id=? AND target_category=?',(account,category))
                elif kind=='profile':db.execute('INSERT INTO semantic_profiles VALUES(?,?,?,1) ON CONFLICT(account_id,category_id) DO UPDATE SET definition=excluded.definition,approved=1',(account,category,json.dumps(operation['definition'])))
                else:raise ValueError('Unsupported structural operation')
            affected=0
            for assignment in plan.get('assignments',[]):
                if not db.execute("SELECT 1 FROM taxonomy.categories WHERE id=? AND source!='retired'",(assignment['target'],)).fetchone():raise ValueError('Target category is not active')
                value=MessageCategoryAssignment(account,assignment['message_id'],assignment['target'],'automatic','classified','approved_semantic_policy','semantic-policy-1')
                affected+=SQLiteMessageCategoryRepository._put_in_transaction(db,value)
            for policy in plan.get('policies',[]):
                db.execute('INSERT OR IGNORE INTO semantic_policies VALUES(?,?,?,?,1,?)',(digest([id,policy]),account,policy['target'],json.dumps(policy['conditions']),id))
            self._learn_content_patterns(db,account,id,plan)
            if policy_revision is not None:db.execute('INSERT OR IGNORE INTO local_autonomous_changes VALUES(?,?)',(id,account))
            ids=sorted({op['id'] for op in plan['operations'] if op['kind'] in ('create','rename','profile')}|{a['target'] for a in plan.get('assignments',[])})
            for category in ids:
                if not db.execute("SELECT 1 FROM taxonomy.categories WHERE id=? AND source!='retired'",(category,)).fetchone():raise ValueError('Category publication verification failed')
            plan['category_ids']=ids
            db.execute('UPDATE semantic_taxonomy_proposals SET payload=? WHERE id=?',(json.dumps(plan),id))
            db.execute("UPDATE semantic_taxonomy_proposals SET status='applied' WHERE id=?",(id,))
            db.execute('INSERT INTO semantic_taxonomy_audit(proposal_id,action,before_json,after_json) VALUES(?,?,?,?)',(id,'applied',json.dumps(before_categories),json.dumps({'plan':plan,'affected':affected,'before_profiles':before_profiles,'retirement_journal':retirement_journal,'after_assignments':[dict(db.execute('SELECT * FROM message_category_assignments WHERE account_id=? AND message_id=?',(account,a['message_id'])).fetchone()) for a in plan.get('assignments',[])]})))
            return dict(status='applied',affected=affected,category_ids=ids)

    def seed_content_patterns(self,account,limit=4):
        with self.connect() as db,db:
            plans=db.execute("SELECT p.* FROM semantic_taxonomy_proposals p WHERE account_id=? AND status='applied' AND NOT EXISTS(SELECT 1 FROM semantic_pattern_seeded s WHERE s.proposal_id=p.id) ORDER BY created_at,id LIMIT ?",(account,limit)).fetchall()
            for p in plans:
                self._learn_content_patterns(db,account,p['id'],json.loads(p['payload']))
                db.execute('INSERT OR IGNORE INTO semantic_pattern_seeded VALUES(?)',(p['id'],))

    @staticmethod
    def _learn_content_patterns(db,account,proposal_id,plan):
        from collections import defaultdict
        from email_agent.application.message_text import content_template
        groups=defaultdict(list)
        for assignment in plan.get('assignments',[]):
            row=db.execute('''SELECT b.body_text,m.sender,m.subject,f.payload,f.input_fingerprint,h.headers,a.source,a.category_id FROM message_search_content b JOIN message_content_state c USING(account_id,message_id)
                JOIN semantic_frames f USING(account_id,message_id) JOIN messages m USING(account_id,message_id)
                JOIN message_category_assignments a USING(account_id,message_id) LEFT JOIN message_list_headers h USING(account_id,message_id)
                WHERE b.account_id=? AND b.message_id=? AND c.status='complete' ''',(account,assignment['message_id'])).fetchone()
            if not row or row['source']=='manual' or row['category_id']!=assignment['target']:continue
            current=dict(account_id=account,message_id=assignment['message_id'],subject=row['subject'],sender=row['sender'],body=row['body_text'],headers=json.loads(row['headers'] or '{}'),decision={'source':'automatic'})
            if row['input_fingerprint']!=input_fingerprint(current):continue
            frame=json.loads(row['payload']);signature=content_template(row['body_text'])
            if signature and frame.get('understood') and not frame.get('analysis_reason') and frame.get('confidence',0)>=.85:groups[(signature,assignment['target'])].append((frame,row['sender']))
        for (signature,target),members in groups.items():
            if len(members)<2 or len({f.get('thread_key') or sender for f,sender in members})<2:continue
            intents={f['primary_intent'] for f,_ in members};types={f['message_type'] for f,_ in members}
            if len(intents)!=1 or len(types)!=1:continue
            definition=dict(primary_intent=next(iter(intents)),message_type=next(iter(types)),confidence=min(f['confidence'] for f,_ in members),sensitivities=sorted({s for f,_ in members for s in f.get('sensitivities',[])}),examples=len(members))
            db.execute('INSERT OR IGNORE INTO semantic_content_patterns VALUES(?,?,?,?,?,1)',(account,signature,target,proposal_id,json.dumps(definition)))

    def recurring(self,account,policy=None):
        with self.connect() as db,db:
            if policy is not None:
                db.execute('INSERT INTO organizer_recurring(account_id,enabled,policy,consented_at) VALUES(?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(account_id) DO UPDATE SET enabled=excluded.enabled,policy=excluded.policy,consented_at=CURRENT_TIMESTAMP',(account,int(policy['enabled']),json.dumps(policy)))
            row=db.execute('SELECT * FROM organizer_recurring WHERE account_id=?',(account,)).fetchone()
            return json.loads(row['policy']) if row else dict(enabled=False)

    def recurring_claim(self,account,day):
        with self.connect() as db,db:
            cursor=db.execute('UPDATE organizer_recurring SET last_campaign=? WHERE account_id=? AND enabled=1 AND (last_campaign IS NULL OR last_campaign!=?)',(day,account,day))
            return cursor.rowcount==1

    def timeline(self,account,id):
        with self.connect() as db:
            links=[dict(r) for r in db.execute('SELECT other_id peer,basis,kind FROM semantic_relationships WHERE account_id=? AND message_id=? UNION SELECT message_id peer,basis,kind FROM semantic_relationships WHERE account_id=? AND other_id=?',(account,id,account,id))]
            linked={}
            for link in links:
                old=linked.get(link['peer'])
                if not old or link['basis']=='observed':linked[link['peer']]=link
            ids=[id,*linked][:100];marks=','.join('?' for _ in ids)
            return [dict(r)|{'semantic':json.loads(r['payload']),'relationship_basis':linked.get(r['message_id'],{}).get('basis','current_message'),'relationship_kind':linked.get(r['message_id'],{}).get('kind')} for r in db.execute(f'SELECT m.message_id,m.internal_date_ms,f.payload FROM semantic_frames f JOIN messages m USING(account_id,message_id) WHERE f.account_id=? AND f.message_id IN ({marks}) ORDER BY m.internal_date_ms,m.message_id',(account,*ids))]

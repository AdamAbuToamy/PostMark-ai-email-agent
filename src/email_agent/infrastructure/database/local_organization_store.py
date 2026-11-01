"""Durable, account-local organization consent and conflict-checked reversals."""
import json
from email_agent.domain.message_category import MessageCategoryAssignment
from email_agent.infrastructure.database.sqlite_message_category_repository import SQLiteMessageCategoryRepository


class LocalOrganizationStore:
    def initialize_local_policy(self):
        with self.connect() as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS local_organization_policy(account_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 0,approval_reference TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS local_organization_feedback(account_id TEXT,message_id TEXT,signature TEXT,target TEXT,features TEXT NOT NULL,PRIMARY KEY(account_id,message_id));
                CREATE TABLE IF NOT EXISTS local_tool_attempts(account_id TEXT,proposal_id TEXT,fingerprint TEXT,attempts INTEGER NOT NULL,PRIMARY KEY(account_id,proposal_id,fingerprint));
                CREATE TABLE IF NOT EXISTS local_autonomous_changes(proposal_id TEXT PRIMARY KEY,account_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS local_organization_audit(id INTEGER PRIMARY KEY,account_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS taxonomy.local_category_accounts(category_id TEXT PRIMARY KEY,account_id TEXT NOT NULL);
                INSERT OR IGNORE INTO organizer_schema_versions VALUES(4);
            ''')

    def local_policy(self, account, enabled=None, approved=False, reference='settings'):
        if account not in ('personal', 'university'):raise ValueError('Unknown account')
        with self.connect() as db, db:
            if enabled is not None:
                if type(enabled) is not bool or (enabled and approved is not True):raise ValueError('Explicit account-local organization approval required')
                db.execute('INSERT INTO local_organization_policy(account_id,enabled,revision,approval_reference) VALUES(?,?,1,?) ON CONFLICT(account_id) DO UPDATE SET enabled=excluded.enabled,revision=revision+1,approval_reference=excluded.approval_reference,updated_at=CURRENT_TIMESTAMP',(account,int(enabled),reference))
                db.execute('INSERT INTO local_organization_audit(account_id,kind,payload) VALUES(?,?,?)',(account,'consent',json.dumps(dict(enabled=enabled,reference=reference))))
            row=db.execute('SELECT * FROM local_organization_policy WHERE account_id=?',(account,)).fetchone()
            return dict(row)|dict(enabled=bool(row['enabled'])) if row else dict(account_id=account,enabled=False,revision=0)

    def reserve_local_action(self, account, proposal_id, fingerprint):
        with self.connect() as db, db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT attempts FROM local_tool_attempts WHERE account_id=? AND proposal_id=? AND fingerprint=?',(account,proposal_id,fingerprint)).fetchone()
            if row and row[0]>=3:raise ValueError('Unchanged local plan exhausted three attempts; needs attention')
            db.execute('INSERT INTO local_tool_attempts VALUES(?,?,?,1) ON CONFLICT(account_id,proposal_id,fingerprint) DO UPDATE SET attempts=attempts+1',(account,proposal_id,fingerprint))

    def release_local_action(self, account, proposal_id, fingerprint):
        with self.connect() as db, db:
            db.execute('UPDATE local_tool_attempts SET attempts=MAX(0,attempts-1) WHERE account_id=? AND proposal_id=? AND fingerprint=?',(account,proposal_id,fingerprint))

    def record_tool(self, account, tool, result):
        with self.connect() as db, db:
            db.execute('INSERT INTO local_organization_audit(account_id,kind,payload) VALUES(?,?,?)',(account,tool,json.dumps(result)))

    def organization_history(self, account):
        with self.connect() as db:
            return [dict(id=r['id'],kind=r['kind'],created_at=r['created_at'],result=json.loads(r['payload'])) for r in db.execute('SELECT * FROM local_organization_audit WHERE account_id=? ORDER BY id DESC LIMIT 30',(account,))]

    def undo_local_change(self, account, proposal_id):
        from email_agent.infrastructure.database.taxonomy_transaction import taxonomy_transaction
        with taxonomy_transaction(self.path,self.categories) as db:
            proposal=db.execute('SELECT * FROM semantic_taxonomy_proposals WHERE account_id=? AND id=?',(account,proposal_id)).fetchone()
            if not proposal:raise ValueError('Unknown account-local change')
            if proposal['status']=='undone':return dict(status='undone',affected=0)
            audit=db.execute("SELECT after_json,before_json FROM semantic_taxonomy_audit WHERE proposal_id=? AND action='applied' ORDER BY id DESC LIMIT 1",(proposal_id,)).fetchone()
            if proposal['status']!='applied' or not audit:raise ValueError('Change is not applied')
            saved=json.loads(audit[0]);plan=saved['plan'];after=saved.get('after_assignments')
            if after is None:raise ValueError('Legacy change requires a fresh grouped reversal preview')
            for expected in after:
                row=db.execute('SELECT * FROM message_category_assignments WHERE account_id=? AND message_id=?',(account,expected['message_id'])).fetchone()
                if not row or dict(row)!=expected:raise ValueError('A later or manual correction must be preserved; grouped reversal required')
            created=[op['id'] for op in plan['operations'] if op['kind']=='create']
            ids={r['message_id'] for r in after}
            for category in created:
                rows=db.execute('SELECT account_id,message_id FROM message_category_assignments WHERE category_id=?',(category,)).fetchall()
                if any(r['account_id']!=account or r['message_id'] not in ids for r in rows):raise ValueError('Newer category members need a grouped reversal preview')
            retirements=saved.get('retirement_journal',[])
            if any(op['kind'] not in ('create','profile','retire') for op in plan['operations']) or (any(op['kind']=='retire' for op in plan['operations']) and not retirements):raise ValueError('Broad taxonomy changes need a grouped reversal preview')
            before_categories={c['id']:c for c in json.loads(audit['before_json'])}
            for retired in retirements:
                category=db.execute('SELECT * FROM taxonomy.categories WHERE id=?',(retired['id'],)).fetchone()
                alias=db.execute('SELECT target FROM taxonomy.category_aliases WHERE alias=?',(retired['id'],)).fetchone()
                if not category or dict(category)!={**before_categories[retired['id']],'source':'retired'} or not alias or alias[0]!=retired['target']:raise ValueError('Retired category changed; grouped reversal required')
                if db.execute('SELECT 1 FROM message_category_assignments WHERE category_id=?',(retired['id'],)).fetchone():raise ValueError('Retired category has later members; grouped reversal required')

            for assignment in plan['assignments']:
                value=MessageCategoryAssignment(**assignment['before'])
                SQLiteMessageCategoryRepository._put_in_transaction(db,value)
            for op in plan['operations']:
                if op['kind']=='create':db.execute("UPDATE taxonomy.categories SET source='retired' WHERE id=?",(op['id'],))
                elif op['kind']=='profile':
                    before=saved['before_profiles'].get(op['id'])
                    if before:db.execute('INSERT OR REPLACE INTO semantic_profiles VALUES(?,?,?,?)',(account,op['id'],before['definition'],before['approved']))
                    else:db.execute('DELETE FROM semantic_profiles WHERE account_id=? AND category_id=?',(account,op['id']))
            for retired in retirements:
                before=before_categories[retired['id']]
                db.execute('UPDATE taxonomy.categories SET name=?,source=?,icon_key=? WHERE id=?',(before['name'],before['source'],before['icon_key'],retired['id']))
                if retired['alias']:db.execute('INSERT OR REPLACE INTO taxonomy.category_aliases VALUES(?,?)',(retired['alias']['alias'],retired['alias']['target']))
                else:db.execute('DELETE FROM taxonomy.category_aliases WHERE alias=?',(retired['id'],))
                for old in retired['policies']:db.execute('UPDATE semantic_policies SET enabled=? WHERE id=? AND account_id=? AND enabled=0',(old['enabled'],old['id'],account))
                for old in retired['patterns']:db.execute('UPDATE semantic_content_patterns SET enabled=? WHERE account_id=? AND signature=? AND target_category=? AND enabled=0',(old['enabled'],account,old['signature'],old['target_category']))
            db.execute('UPDATE semantic_policies SET enabled=0 WHERE account_id=? AND proposal_id=?',(account,proposal_id))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_content_patterns'").fetchone():db.execute('UPDATE semantic_content_patterns SET enabled=0 WHERE account_id=? AND proposal_id=?',(account,proposal_id))
            db.execute("UPDATE semantic_taxonomy_proposals SET status='undone' WHERE id=?",(proposal_id,))
            db.execute("INSERT INTO semantic_taxonomy_audit(proposal_id,action,after_json) VALUES(?,'undone',?)",(proposal_id,json.dumps(dict(affected=len(after)))))
            db.execute('INSERT INTO local_organization_audit(account_id,kind,payload) VALUES(?,?,?)',(account,'undo_organization_change',json.dumps(dict(proposal_id=proposal_id,affected=len(after)))))
            return dict(status='undone',affected=len(after))

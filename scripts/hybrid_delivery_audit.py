"""Read-only hybrid delivery snapshot; no model, Gmail or secret access."""
import argparse
from contextlib import contextmanager, closing
import json
from pathlib import Path
from full_mailbox_audit import report, connect
from email_agent.infrastructure.database.hybrid_store import SQLiteHybridStore


class ReadOnlyHybridStore(SQLiteHybridStore):
    def reconcile_other(self, account):
        # Audit existing persisted state; queue maintenance belongs to workers.
        pass

    @contextmanager
    def connect(self):
        with closing(connect(self.path)) as db:
            yield db


def snapshot(root, baseline):
    result = report(root, baseline)
    store = ReadOnlyHybridStore(root / 'data/email_agent.db')
    with closing(connect(store.path)) as db, closing(connect(baseline / 'email_agent.db')) as before:
        db.execute('BEGIN')
        result['hybrid'] = {}
        for account in ('personal', 'university'):
            progress = store.other_progress(account)
            missing = db.execute("""SELECT COUNT(*) FROM message_category_assignments a
                LEFT JOIN hybrid_tasks t USING(account_id,message_id)
                WHERE a.account_id=? AND a.category_id='other' AND t.message_id IS NULL""", (account,)).fetchone()[0]
            assert sum(progress['counts'].values()) == progress['total'] and not missing
            frames = [dict(row) for row in db.execute("""SELECT source,
                COUNT(*) analyzed, SUM(json_extract(payload,'$.understood')=1 AND json_extract(payload,'$.analysis_reason') IS NULL) accepted,
                SUM(COALESCE(json_extract(payload,'$.workflow_needs_escalation'),0)) workflow_pending
                FROM semantic_frames WHERE account_id=? GROUP BY source""", (account,))]
            result['hybrid'][account] = dict(other=progress, missing_processing_state=missing, frames=frames,
                tasks=[dict(r) for r in db.execute('SELECT status,COUNT(*) n FROM hybrid_tasks WHERE account_id=? GROUP BY status', (account,))],
                actual_mail_attempts=[dict(r) for r in db.execute('SELECT provider,model,status,error,COUNT(*) n,AVG(seconds) mean_seconds FROM hybrid_attempts WHERE account_id=? GROUP BY provider,model,status,error', (account,))])
        result['hybrid_models'] = store.models()
        result['hybrid_health'] = [dict(r) for r in db.execute('SELECT * FROM hybrid_health')]
        old = before.execute('SELECT COUNT(*) FROM automation_ai_attempts').fetchone()[0]
        result['gemini']['new_attempts_since_baseline'] = db.execute('SELECT COUNT(*) FROM automation_ai_attempts').fetchone()[0] - old
        result['hybrid']['changes_reversible_history'] = db.execute('SELECT COUNT(*) FROM semantic_frame_history').fetchone()[0]
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--baseline', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.root, args.baseline), ensure_ascii=False, indent=2))

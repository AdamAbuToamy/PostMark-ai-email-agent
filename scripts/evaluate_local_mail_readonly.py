"""Bounded12 independently annotated real cases, local inference only, no writes."""
from contextlib import contextmanager,closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from email_agent.application.hybrid_ai import ModelCapability
from email_agent.application.hybrid_evaluation import SemanticEvaluation
from email_agent.infrastructure.ai.hybrid_providers import LocalModelProvider
from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore

ROOT=Path(__file__).resolve().parents[1]


class ReadOnlySemanticStore(SQLiteSemanticOrganizerStore):
    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            db.execute('ATTACH DATABASE ? AS taxonomy',((ROOT/'data/categories.db').resolve().as_uri()+'?mode=ro',))
            yield db


def main():
    labels=json.loads((ROOT/'backups/autonomous-20261028T215341Z/actual-evaluation-labels.json').read_text())
    store=ReadOnlySemanticStore(ROOT/'data/email_agent.db',ROOT/'data/categories.db');cases=[];stale=0
    with closing(sqlite3.connect((ROOT/'data/email_agent.db').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        for label in labels[:12]:
            manifest=db.execute('SELECT digest FROM message_content_state WHERE account_id=? AND message_id=?',(label['account_id'],label['message_id'])).fetchone()
            if not manifest or manifest[0]!=label['digest']:stale+=1;continue
            row=store.message_row(label['account_id'],label['message_id'])
            if row and row['content_complete']:cases.append(dict(id=label['alias'],subject=row['subject'],body=row['body'],category=label['expected_category'],catalog=store.model_catalog(label['account_id'])))
    provider=LocalModelProvider();installed=next(m for m in provider.discover() if m['model']=='qwen3.5:4b')
    capability=ModelCapability('ollama','qwen3.5:4b',16384,free=True,fingerprint=installed['fingerprint'])
    provider.http.request('/api/chat',dict(model=capability.model,messages=[dict(role='user',content='Return {"ok":true}')],format={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok']},stream=False,think=False,options=dict(num_ctx=16384,num_predict=16)))
    result=SemanticEvaluation(provider,capability,cases).run()
    result.update(stale_labels_skipped=stale,scope='Read-only prior independent real12 labels; matching verified body digests. Local model only, no classification/policy changes; selected sample, not mailbox population accuracy.')
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':
    from email_agent.workers.fill_content import fill_lock
    with fill_lock(ROOT/'data'):
        main()

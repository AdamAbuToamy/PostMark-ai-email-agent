"""Freeze64 targets: all32Jobs, original semantic sample, then missing Bills.

Does not make remote calls. Reusing the fixed pointer never resets the budget.
"""
from pathlib import Path
from contextlib import closing
import sqlite3,json
from email_agent.workers.fill_content import build_fill

root=Path(__file__).resolve().parents[1];release=Path(Path('/tmp/content-fill-release.txt').read_text().strip());pointer=release/'campaign.json'
if pointer.exists():
    print(json.dumps(json.loads(pointer.read_text())));raise SystemExit
service=build_fill(root);targets=[]
with closing(sqlite3.connect('file:data/email_agent.db?mode=ro',uri=True)) as db:
    jobs=db.execute("SELECT account_id,message_id FROM message_category_assignments WHERE category_id='job_applications' ORDER BY account_id,message_id").fetchall()
    candidates=jobs
    for raw, in db.execute('SELECT scope FROM organizer_campaigns WHERE request_cap=8 ORDER BY created_at'):
        scope=json.loads(raw)
        if len(scope.get('message_ids',[]))==32:candidates += [(m['account'],m['id']) for m in scope['message_ids']]
    candidates+=db.execute("SELECT a.account_id,a.message_id FROM message_category_assignments a LEFT JOIN message_search_content b USING(account_id,message_id) WHERE a.category_id='bills' ORDER BY (b.body_text IS NOT NULL),a.account_id,a.message_id").fetchall()
    for target in candidates:
        if target not in targets and len(targets)<64:targets.append(target)
    nonblank=sum(bool((db.execute('SELECT body_text FROM message_search_content WHERE account_id=? AND message_id=?',target).fetchone() or [''])[0].strip()) for target in targets)
    before={a+':'+id:dict(zip(('category_id','source','status','reason'),db.execute('SELECT category_id,source,status,reason FROM message_category_assignments WHERE account_id=? AND message_id=?',(a,id)).fetchone())) for a,id in targets}
id='content-completion-20261028-64'
service.store.create_fill(id,targets,128)
result=dict(campaign=id,messages=len(targets),attempt_cap=128,concurrency=1,previous_nonblank_bodies=nonblank)
pointer.write_text(json.dumps(result));pointer.chmod(0o600)
(release/'before-decisions.json').write_text(json.dumps(before));(release/'before-decisions.json').chmod(0o600)
print(json.dumps(result))

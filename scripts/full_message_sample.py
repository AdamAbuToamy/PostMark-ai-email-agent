"""Exactly the separately consented8 full personal messages, <=8 requests.

No Gmail calls/settings/structural approval. Existing-category local decisions
may update under the full-evidence contract. Pointer persists across resumes.
"""
import argparse,json,sqlite3
from pathlib import Path
from contextlib import closing
from urllib.request import Request,urlopen
from email_agent.application.job_evidence import employment_evidence

ROOT=Path(__file__).resolve().parents[1]

def api(path,payload=None):
    request=Request('http://127.0.0.1:8000'+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json','X-Email-Agent-Action':'message-management'})
    with urlopen(request,timeout=300) as response:return json.load(response)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--approved',action='store_true');args=parser.parse_args()
    release=Path(Path('/tmp/content-fill-release.txt').read_text().strip());pointer=release/'full-model-campaign.json'
    if pointer.exists():scope=json.loads(pointer.read_text())
    else:
        with closing(sqlite3.connect('file:data/email_agent.db?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            rows=[dict(r) for r in db.execute("SELECT m.*,a.category_id,a.reason,b.body_text FROM messages m JOIN message_category_assignments a USING(account_id,message_id) JOIN message_search_content b USING(account_id,message_id) JOIN message_content_state c USING(account_id,message_id) WHERE m.account_id='personal' AND c.status='complete' ORDER BY m.message_id")]
        jobs=[r for r in rows if r['category_id']=='job_applications'];unknown=[];rejected=[];other_jobs=[]
        for row in jobs:
            state=employment_evidence(row['subject'],row['sender'],row['body_text'],True).state
            (unknown if state=='unknown' else rejected if state=='rejected' else other_jobs).append(row)
        bills=[r for r in rows if r['category_id']=='bills'];bills.sort(key=lambda r:('receipt' not in r['subject'].casefold(),r['message_id']))
        others=[r for r in rows if r['category_id']=='other' and r['body_text'].strip()]
        # New unknown outcomes + a known rejection + receipts + two recurring
        # unknown-purpose messages, without a company-specific classification.
        chosen=unknown[:3]+rejected[:1]
        for row in other_jobs:
            if len(chosen)>=4:break
            chosen.append(row)
        chosen+=bills[:2]+others[:2]
        ids=list(dict.fromkeys(r['message_id'] for r in chosen))[:8]
        if len(ids)!=8:raise ValueError('Require8 verified full messages; do not substitute metadata')
        scope=api('/api/organizer/preview',dict(accounts=['personal'],request_cap=8,max_messages=8,content_mode='full',message_ids=ids))
        if scope['message_count']!=8 or scope['content_mode']!='full' or scope['deferred']:raise ValueError('Exact full-text scope unavailable; no model call')
        pointer.write_text(json.dumps(scope,ensure_ascii=False));pointer.chmod(0o600)
    print(json.dumps(dict(id=scope['id'],messages=scope['message_count'],request_cap=scope['request_cap'],content_mode=scope['content_mode'],input_lengths=[dict(current=len(m['excerpt']),history=len(m.get('history_context','')),complete=m.get('body_complete')) for batch in scope['previews'] for m in batch['messages']]),ensure_ascii=False),flush=True)
    if args.approved:
        result=api(f"/api/organizer/campaigns/{scope['id']}/run",dict(approved=True))
        (release/'full-model-result.json').write_text(json.dumps(result))
        print(json.dumps(result),flush=True)

if __name__=='__main__':main()

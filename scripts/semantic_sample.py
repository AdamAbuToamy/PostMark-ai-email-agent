"""Restart-safe operator sample: existing local API, <=64 messages/8 requests.

Use --approved only for the exact scope consented on 2026-10-28. Reuses a
private campaign pointer. Never reads credentials, Gmail or applies taxonomy.
"""
import argparse
from pathlib import Path
import json
from urllib.request import Request,urlopen


def request(path,payload=None):
    data=json.dumps(payload).encode() if payload is not None else None
    req=Request('http://127.0.0.1:8000'+path,data=data,headers={'Content-Type':'application/json','X-Email-Agent-Action':'message-management'})
    with urlopen(req,timeout=300) as response:return json.load(response)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--approved',action='store_true');parser.add_argument('--temporary-university-opt-in',action='store_true');args=parser.parse_args()
    release=Path(Path('/tmp/semantic-engine-release.txt').read_text().strip());checkpoint=release/'real-sample-campaign.json'
    if checkpoint.exists():scope=json.loads(checkpoint.read_text())
    else:
        scope=request('/api/organizer/preview',dict(accounts=['personal','university'],request_cap=8,max_messages=32))
        checkpoint.write_text(json.dumps(scope,ensure_ascii=False));checkpoint.chmod(0o600)
    print(json.dumps({key:scope[key] for key in ('id','message_count','request_cap','model')},ensure_ascii=False),flush=True)
    if not args.approved:return
    preferences=request('/api/chat/university/settings')
    original={key:preferences[key] for key in ('enabled','developer_local','processing_consent')}
    policy=preferences['usage']['provider_policy']
    if args.temporary_university_opt_in:
        path=release/'temporary-university-preferences.json'
        if path.exists():original=json.loads(path.read_text())
        else:path.write_text(json.dumps(original));path.chmod(0o600)
    try:
        if args.temporary_university_opt_in:
            request('/api/chat/university/settings',dict(enabled=True,developer_local=original['developer_local'],processing_consent=True,cost_approved=True,paid_approved=False))
        result=request(f"/api/organizer/campaigns/{scope['id']}/run",dict(approved=True))
    finally:
        if args.temporary_university_opt_in:
            restored=request('/api/chat/university/settings',dict(**original,cost_approved=False,paid_approved=False))
            assert all(restored[key]==value for key,value in original.items()),'Account preferences were not restored'
            assert restored['usage']['provider_policy']==policy,'Provider policy changed'
            print(json.dumps(dict(university_preferences_restored=True,paid_approved=False)),flush=True)
    path=release/'sample-result.json';path.write_text(json.dumps(result,ensure_ascii=False));path.chmod(0o600)
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()

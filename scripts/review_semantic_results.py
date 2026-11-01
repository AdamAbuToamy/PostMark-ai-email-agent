"""Revalidate only previously accepted stored results. No model/network imports.

Default readonly. --apply updates <=12 existing frames and their audit history;
never reclassifies mail, creates categories, changes settings or reopens calls.
"""
import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from email_agent.application.semantic_mail import validate_model,local_frame,SEMANTIC_VERSION
from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];store=SQLiteSemanticOrganizerStore(root/'data/email_agent.db',root/'data/categories.db')
    with closing(sqlite3.connect(f'file:{store.path}?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        campaigns=[dict(r) for r in db.execute('SELECT * FROM organizer_campaigns WHERE request_cap=8 ORDER BY created_at DESC') if len(json.loads(r['scope']).get('message_ids',[]))==32]
        if len(campaigns)!=1:raise ValueError('Expected the one existing32-message audited campaign')
        campaign=campaigns[0]
        batches=[dict(r) for r in db.execute('SELECT * FROM organizer_batches WHERE campaign_id=? ORDER BY ordinal',(campaign['id'],))]
        cached=[r[0] for r in db.execute('SELECT result FROM organizer_cache')]
        coverage=dict(db.execute("SELECT a.category_id,COUNT(b.body_text) FROM message_category_assignments a LEFT JOIN message_search_content b USING(account_id,message_id) WHERE a.category_id IN ('bills','other','orders','job_applications','university') GROUP BY a.category_id"))
    repairs=[];failures=Counter();sent_bodies=0;accepted_body_inputs=0
    for batch in batches:
        data=json.loads(batch['input_json']);sent_bodies+=sum(bool(m['excerpt'].strip()) for m in data['messages'])
        if batch['status']!='done':
            diagnostic=json.loads(batch['result'] or '{}').get('diagnostic',{})
            failures['provider_request' if json.loads(batch['result'] or '{}').get('error_kind') in ('ClientError','ServerError') or diagnostic.get('http_status') else diagnostic.get('reason','unrecorded')]+=len(data['messages']);continue
        rows={alias:store.message_row(data['account'],id) for alias,id in data['private_ids'].items()}
        local={alias:local_frame(row) for alias,row in rows.items()}
        matches=[]
        for raw in cached:
            try:frames,_=validate_model(raw,data['messages'],local,[c['id'] for c in data['catalog']])
            except (ValueError,TypeError,KeyError):continue
            matches.append(frames)
        if len(matches)!=1:raise ValueError('Stored response could not be uniquely matched; no inferred recovery')
        for alias,frame in matches[0].items():
            row=rows[alias]
            if not row or not row.get('semantic') or row['semantic']['source']!='gemini':raise ValueError('Previously accepted evidence changed; stop')
            frame['related_ids']=[data['private_ids'][id] for id in frame['related_ids'] if id!=alias]
            frame['review_version']='stored-review-1'
            repairs.append((row,frame));accepted_body_inputs+=frame['input_coverage']['body_supplied']
    if len(repairs)!=12:raise ValueError('Expected exactly12 previously accepted frames')
    changed=0
    if args.apply:
        for row,frame in repairs:changed+=store.put_frame(row,frame,SEMANTIC_VERSION)
    report=dict(scope_messages=32,body_inputs=sent_bodies,accepted=12,accepted_body_inputs=accepted_body_inputs,failures=dict(failures),locally_cached_bodies_by_category=coverage,frames_updated=changed,category_assignments_changed=0,gemini_requests=0,gmail_reads=0,gmail_mutations=0)
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()

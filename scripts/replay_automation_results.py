"""Revalidate this task's saved full-text responses; never calls a provider."""
import argparse,json,sqlite3
from contextlib import closing
from pathlib import Path
from email_agent.application.semantic_mail import public_summary,local_frame,validate_model,validate_batch,SEMANTIC_VERSION,SYSTEM,SCHEMA,digest
from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore


def main():
    p=argparse.ArgumentParser();p.add_argument('--grant');p.add_argument('--campaign');p.add_argument('--apply',action='store_true');args=p.parse_args()
    if bool(args.grant)==bool(args.campaign):p.error('Choose one saved grant or campaign; no provider calls')
    root=Path(__file__).resolve().parents[1];store=SQLiteSemanticOrganizerStore(root/'data/email_agent.db',root/'data/categories.db')
    with closing(sqlite3.connect(f'file:{store.path}?mode=ro',uri=True)) as db:
        if args.grant:batches=db.execute("SELECT b.input_json,c.scope,c.model,b.result FROM organizer_batches b JOIN organizer_campaigns c ON c.id=b.campaign_id JOIN automation_ai_attempts a ON a.campaign_id=b.campaign_id WHERE a.grant_id=? AND a.kind='generate' AND a.status='done'",(args.grant,)).fetchall()
        else:batches=db.execute("SELECT b.input_json,c.scope,c.model,b.result FROM organizer_batches b JOIN organizer_campaigns c ON c.id=b.campaign_id WHERE c.id=? AND b.status IN ('done','partial')",(args.campaign,)).fetchall()
        cache=dict(db.execute('SELECT fingerprint,result FROM organizer_cache'))
    reviewed=restored=unmatched=changed=rejected=0
    for raw_batch,campaign_scope,model,result in batches:
        data=json.loads(raw_batch);rows={alias:store.message_row(data['account'],id) for alias,id in data['private_ids'].items()}
        valid_aliases=set()
        for original in data['messages']:
            row=rows[original['id']]
            if not row or not row['content_complete']:changed+=1;continue
            now=public_summary(row,original['id'],'full')
            if any(now.get(k)!=v for k,v in original.items() if k!='category'):changed+=1;continue
            valid_aliases.add(original['id'])
        if not valid_aliases or any(row is None for row in rows.values()):continue
        # Exact known prompt revisions of this task; never guess a response by
        # coincidentally matching quotes in two similar message batches.
        sentence='For job applications use attribute application_context=submitted only for a direct response to an actually submitted employment application, with two separate current-body evidence quotes supporting application context and outcome; never for account codes, advertisements, conditional submission or academic admissions. '
        version=json.loads(campaign_scope)['schema_version'];public={k:data[k] for k in ('messages','catalog')}
        from copy import deepcopy
        legacy_schema=deepcopy(SCHEMA);legacy_schema['properties']['frames']['items']['properties']['evidence']['items']['properties']['field']['enum']=['subject','excerpt']
        old_system=SYSTEM.replace('supplied subject/excerpt/history_context text. Historical evidence can explain purpose/type, but never establishes a new current outcome.','supplied subject/excerpt text.')
        keys={digest([version,prompt,schema,model,public]) for prompt in [SYSTEM,SYSTEM.replace(sentence,''),old_system,old_system.replace(sentence,'')] for schema in [SCHEMA,legacy_schema]}
        if json.loads(result or '{}').get('cache_key'):keys={json.loads(result)['cache_key']}
        matches=[]
        recorded=json.loads(result or '{}').get('rejected_output')
        if isinstance(recorded,str):
            frames,_,issues=validate_batch(recorded,data['messages'],{a:local_frame(r) for a,r in rows.items()},[c['id'] for c in data['catalog']]);matches.append(frames);rejected+=sum('id' in issue for issue in issues)
        else:
            for raw in {cache[key] for key in keys if key in cache}:
                try:frames,_=validate_model(raw,data['messages'],{a:local_frame(r) for a,r in rows.items()},[c['id'] for c in data['catalog']])
                except (ValueError,TypeError,KeyError):continue
                matches.append(frames)
        if len(matches)!=1:unmatched+=len(rows);continue
        for alias,frame in matches[0].items():
            if alias not in valid_aliases:continue
            reviewed+=1;row=rows[alias];frame['related_ids']=[data['private_ids'][id] for id in frame['related_ids'] if id!=alias and id in valid_aliases];frame['review_version']='automation-cache-review-1'
            if not args.apply:continue
            restored+=store.put_frame(row,frame,SEMANTIC_VERSION)
            if frame.get('suggested_category_id') and frame.get('understood') and frame['confidence']>=.9 and not frame.get('analysis_reason'):
                assigned=store.assign_semantic(row,frame)
                if not assigned and row['decision']['source']!='manual' and frame['suggested_category_id']!=row['decision']['category_id']:
                    frame['analysis_reason']='conflicting_assignment_evidence';store.put_frame(row,frame,SEMANTIC_VERSION)
            with closing(sqlite3.connect(store.path)) as db,db:db.execute("UPDATE mail_work SET status=? WHERE account_id=? AND message_id=? AND stage='semantic' AND status IN ('pending','retry','local_resolved')",('done' if frame.get('understood') and not frame.get('analysis_reason') else 'uncertain',data['account'],row['message_id']))
    print(json.dumps(dict(reviewed=reviewed,frames_updated=restored,validation_rejected=rejected,changed_inputs_skipped=changed,unmatched=unmatched,gemini_requests=0,gmail_calls=0)))

if __name__=='__main__':main()

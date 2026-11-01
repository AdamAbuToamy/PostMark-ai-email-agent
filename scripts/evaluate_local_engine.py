"""Bounded installed-model benchmark; no downloads, cloud calls or Gmail writes."""
import argparse
from dataclasses import replace
from importlib.resources import files
import json
from pathlib import Path
from email_agent.application.hybrid_ai import ModelCapability
from email_agent.application.hybrid_evaluation import SemanticEvaluation
from email_agent.infrastructure.ai.hybrid_providers import LocalModelProvider
from email_agent.infrastructure.database.hybrid_store import SQLiteHybridStore


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--model',default='qwen3.5:4b');parser.add_argument('--persist',action='store_true');args=parser.parse_args()
    provider=LocalModelProvider();installed=next((m for m in provider.discover() if m['model']==args.model),None)
    if not installed:raise SystemExit('Install the explicitly approved model first; this script never downloads')
    cap=ModelCapability('ollama',args.model,16384,free=True,fingerprint=installed['fingerprint'])
    # Separate cold-start availability from the classification quality sample.
    warm=provider.http.request('/api/chat',dict(model=args.model,messages=[dict(role='user',content='Return {"ok":true}')],format={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok']},stream=False,think=False,options=dict(num_ctx=16384,num_predict=32)))
    print(json.dumps(dict(warmup_seconds=warm.get('total_duration',0)/1e9,prompt_tokens=warm.get('prompt_eval_count'),output_tokens=warm.get('eval_count'))),flush=True)
    cases=json.loads(files('email_agent.resources').joinpath('semantic_benchmark.json').read_text())
    from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore
    root=Path(__file__).resolve().parents[1]
    catalog=SQLiteSemanticOrganizerStore(root/'data/email_agent.db',root/'data/categories.db').model_catalog('personal')
    catalog=[{k:c[k] for k in ('id','name','observed_types') if k in c} for c in catalog if c['id'] in {case['category'] for case in cases}]
    result=SemanticEvaluation(provider,cap,cases,catalog).run()
    if args.persist:
        store=SQLiteHybridStore(Path(__file__).resolve().parents[1]/'data/email_agent.db');store.initialize()
        verified=bool(result['completed'] and result['valid_schema'] and max(result['category_quality'].values(),default=0)>=.9 and not result['unsafe_promotional'])
        store.register(replace(cap,structured=bool(result['valid_schema']),validated=verified,quality=result['category_quality'],workflow_quality=result['workflow_quality'],latency=result['mean_seconds'] or 90))
        with store.connect() as db:db.execute('INSERT INTO hybrid_evaluations(provider,model,fingerprint,result) VALUES(?,?,?,?)',('ollama',args.model,cap.fingerprint,json.dumps(result)))
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':
    from email_agent.workers.fill_content import fill_lock
    with fill_lock(Path(__file__).resolve().parents[1]/'data'):
        main()

"""Dated official pricing evidence; never equates discovery with qualification."""
from datetime import datetime
from importlib.resources import files
import json
import time


def policy():return json.loads(files('email_agent.resources').joinpath('free_model_policy.json').read_text())
def verified_free(model):
    p=policy();return model in p['models'] and datetime.fromisoformat(p['expires_at']).timestamp()>time.time()
def priority(model,provider='gemini'):
    p=policy()
    if provider=='siliconflow':return (siliconflow_free(model) or {}).get('priority',0)
    if provider=='gemini':return p['models'].get(model,{}).get('task_priority',0)
    pool=p.get('evaluation_candidates',{}).get(provider,{})
    if not pool or datetime.fromisoformat(pool['expires_at']).timestamp()<=time.time():return 0
    return pool['models'].get(model,{}).get('task_priority',0)


def siliconflow_free(model,region="china"):
    """Exact zero-priced standard variants only; not credits, Pro aliases or size guesses."""
    if region!='china':return None  # CN zero prices do not establish international free eligibility.
    p=json.loads(files('email_agent.resources').joinpath('siliconflow_free_models.json').read_text())
    if datetime.fromisoformat(p['expires_at']).timestamp()<=time.time():return None
    return p['models'].get(model)

"""Structural fit is separate from a message's intent, document and lifecycle."""
import re
import unicodedata


def concept(value):
    return ' '.join(re.findall(r'[^\W_]+',unicodedata.normalize('NFKC',value or '').casefold()))


def fit(frames,catalog):
    """Prefer explicit existing purposes; never infer navigation from intent alone.

    Unknown purposes need an independently described reusable category scope.
    Cardinality is not evidence of structural usefulness.
    """
    if not frames:return None
    names={concept(c['name']):c for c in catalog}
    ids={c['id']:c for c in catalog if c['id']!='other'}
    explicit={f.get('suggested_category_id') for f in frames}
    if len(explicit)==1 and next(iter(explicit)) in ids:
        c=ids[next(iter(explicit))];return dict(name=c['name'],target=c['id'],scope='existing')
    labels={concept((f.get('attributes') or {}).get('category_name')) for f in frames}
    if len(labels)==1 and next(iter(labels)) in names and names[next(iter(labels))]['id']!='other':
        c=names[next(iter(labels))];return dict(name=c['name'],target=c['id'],scope='existing')
    # Exact observed concepts can reuse an existing group without a new model call.
    matches=[]
    for f in frames:
        found={names[v]['id'] for v in [concept(f.get('primary_intent')),concept(f.get('message_type'))] if v in names and names[v]['id']!='other'}
        if len(found)!=1:break
        matches.append(next(iter(found)))
    if len(matches)==len(frames) and len(set(matches))==1:
        c=ids[matches[0]];return dict(name=c['name'],target=c['id'],scope='existing')
    attrs=[f.get('attributes') or {} for f in frames]
    if len(labels)!=1 or not next(iter(labels)):return None
    if any(not isinstance(a.get('category_scope'),str) or not a['category_scope'].strip() for a in attrs):return None
    name=attrs[0]['category_name'].strip()
    if any(concept(name) in {concept(f.get(k)) for k in ('message_type','status','entity')} for f in frames):return None
    return dict(name=name,target=None,scope=attrs[0]['category_scope'].strip())


def equivalent_source(source, target, catalog, rows):
    """Narrow reversible consolidation, anchored by an existing named meaning.

    All source types must already occur in the target; generic shared labels
    alone cannot justify a merge. Account and manual protections are additional.
    """
    names={c['id']:concept(c['name']) for c in catalog}
    own=[r for r in rows if r['decision']['category_id']==source]
    other=[r for r in rows if r['decision']['category_id']==target]
    if not own or not other or source==target or not source.startswith('sem_'):return False
    from email_agent.application.semantic_mail import supported_cluster_size
    from email_agent.application.semantic_organizer import row_fingerprint
    if not supported_cluster_size([r.get('semantic') or {} for r in own]):return False
    if any(r.get('input_fingerprint')!=row_fingerprint(r) for r in own):return False
    if any(r['decision']['source']=='manual' or not r.get('content_complete') for r in own):return False
    types={concept((r.get('semantic') or {}).get('message_type')) for r in own}
    observed={concept((r.get('semantic') or {}).get('message_type')) for r in other}
    return bool(types and '' not in types and 'unknown' not in types and types<=observed and names.get(target) in types)

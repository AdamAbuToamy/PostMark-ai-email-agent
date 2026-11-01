"""Versioned gold rubric. Predictions never generate their own expected labels."""
from collections import defaultdict
from email_agent.application.semantic_dimensions import dimensions

RUBRIC_VERSION='dimensions-rubric-3-semantic-family-approved'
CATALOG=[
 dict(id='other',name='Other',description='Purpose genuinely unclear; never a shortcut for an unfamiliar sender.'),
 dict(id='bills',name='Bills and payments',description='Invoices, payment notices and financial documents. An unpaid invoice is not a receipt.'),
 dict(id='orders',name='Orders and purchases',description='Purchases and fulfillment. A paid purchase receipt may have this primary category ONLY with receipt document type and paid financial status; not an unpaid invoice.'),
 dict(id='security',name='Security',description='Authentication and account security regardless of sender institution.'),
 dict(id='university',name='Academic notices',description='Teaching/course administration; account-security notices belong in Security.'),
 dict(id='promotions',name='Marketing',description='Advertisements and commercial newsletters; not transactional receipts or applications.'),
 dict(id='job_applications',name='Submitted job applications',description='Responses to applications actually submitted, not advertising or academic admission.'),
 dict(id='correspondence',name='Conversations',description='Ordinary personal correspondence.'),
]


def score_frame(case,raw,valid,issues,source_texts=(),new_category_plan=False):
    d=dimensions(raw);gold=case['gold'];status=d.workflow_stage
    category=d.primary_category in gold['categories']
    if gold.get('requires_new_category'):
        category=new_category_plan and d.primary_category in (None,'other')
    doc=gold.get('document_type');document=not doc or d.document_type==doc
    if isinstance(doc,dict):
        label=d.document_type or ''
        quotes=' '.join(e.get('quote','') for e in raw.get('evidence',[]) if e.get('field')=='excerpt').casefold()
        terms=doc['terms']
        document=any(t in label for t in terms) and any(t in quotes for t in terms) and not any(t in label for t in doc.get('incompatible',[]))
    payment=gold.get('payment');financial=not payment or d.payment_status==payment
    workflow=gold.get('workflow');lifecycle=not workflow or status==workflow
    forbidden=gold.get('forbidden_statuses',[])
    uncertainty=not (status in forbidden or d.payment_status in forbidden)
    if len(gold['categories'])>1:category=category and document and financial
    evidence=valid and bool(raw.get('evidence')) and any(e.get('field')=='excerpt' for e in raw.get('evidence',[]))
    sensitivities=set(raw.get('sensitivities') or [])
    important=gold['sensitivity']!='promotional'
    protection=not important or gold['sensitivity'] in sensitivities
    unsafe=important and (d.primary_category=='promotions' or sensitivities=={'promotional'})
    entity=raw.get('entity');text=(' '.join([case['subject'],case['body'],*source_texts])).casefold()
    grounded_entity=not entity or entity.casefold() in text
    return dict(case=case['id'],language=case['language'],schema_valid=valid,category_correct=bool(category and valid),document_correct=bool(document and valid),payment_correct=bool(financial and valid),workflow_correct=bool(lifecycle and uncertainty and valid),evidence_grounded=bool(evidence),protection_recalled=bool(protection),unsafe_cleanup=bool(unsafe),entity_grounded=bool(grounded_entity),has_document=bool(doc),has_payment=bool(payment),has_workflow=bool(workflow or forbidden),important=important,issues=issues,dimensions=d.__dict__,correct=bool(valid and category and document and financial and lifecycle and uncertainty and protection and grounded_entity))


def summarize(cases,outcomes,request_metrics,complete):
    by=defaultdict(list)
    for o in outcomes:by[o['language']].append(o)
    languages={};quality={};workflow={};tasks={}
    for lang,rows in by.items():
        ratio=lambda key,subset=None:sum(bool(r[key]) for r in (subset if subset is not None else rows))/len(subset if subset is not None else rows) if (subset if subset is not None else rows) else 0
        docs=[r for r in rows if r['has_document']];payments=[r for r in rows if r['has_payment']];flows=[r for r in rows if r['has_workflow']];protected=[r for r in rows if r['important']]
        values=dict(cases=len(rows),schema=ratio('schema_valid'),category=ratio('category_correct'),document=ratio('document_correct',docs),payment=ratio('payment_correct',payments),workflow=ratio('workflow_correct',flows),evidence=ratio('evidence_grounded'),protection=ratio('protection_recalled',protected),hallucinations=sum(not r['entity_grounded'] for r in rows),unsafe_cleanup=sum(r['unsafe_cleanup'] for r in rows))
        expected=sum(c['language']==lang for c in cases)
        ready=len({r['case'] for r in rows})==expected and len(rows)==expected and expected>=15 and values['schema']==1 and values['category']>=.9 and values['document']>=.9 and values['evidence']>=.95 and values['protection']==1 and values['hallucinations']==0 and values['unsafe_cleanup']==0
        languages[lang]=dict(values,qualified=ready)
        # Novel taxonomy discovery is a different task from assigning an existing category.
        routine_ids={c['id'] for c in cases if c['language']==lang and not c['gold'].get('requires_new_category')}
        routine=[r for r in rows if r['case'] in routine_ids]
        complete_routine=len(routine_ids)>=15 and len(routine)==len(routine_ids) and {r['case'] for r in routine}==routine_ids
        safe=complete_routine and ratio('schema_valid',routine)==1 and ratio('evidence_grounded',routine)>=.95 and ratio('protection_recalled',[r for r in routine if r['important']])==1 and all(r['entity_grounded'] and not r['unsafe_cleanup'] for r in routine)
        category_score=ratio('category_correct',routine)
        routine_docs=[r for r in routine if r['has_document']]
        document_score=ratio('document_correct',routine_docs)
        quality[lang]=category_score if safe and category_score>=.9 else 0
        workflow[lang]=min(values['payment'],values['workflow']) if safe else 0
        tasks[lang]=dict(category=quality[lang],document=document_score if safe and document_score>=.9 else 0,workflow=workflow[lang],entity=ratio('entity_grounded',routine) if safe else 0)
        languages[lang]['capabilities']={key:score>=.9 for key,score in tasks[lang].items()}
    usage={}
    for metric in request_metrics:
        for key,value in (metric.get('usage') or {}).items():
            if type(value)is int:usage[key]=usage.get(key,0)+value
    return dict(rubric_version=RUBRIC_VERSION,completed=complete,outcomes=outcomes,languages=languages,category_quality=quality,workflow_quality=workflow,task_quality=tasks,quality_qualified=any(v>=.9 for v in quality.values()),requests=len(request_metrics),usage=usage,mean_seconds=sum(m['seconds'] for m in request_metrics)/len(request_metrics) if request_metrics else None)

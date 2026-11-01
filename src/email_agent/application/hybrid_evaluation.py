"""Independent multilingual quality measurement, never production label rules."""
from collections import defaultdict
from dataclasses import replace
import json
from email_agent.application.hybrid_ai import message_language
from email_agent.application.semantic_dimensions import dimensions as semantic_dimensions
from email_agent.application.model_errors import ModelFailure
from email_agent.application.semantic_mail import SYSTEM,SCHEMA,status_key,validate_batch,public_summary,local_frame


def category_matches(case,category,document=None,payment=None):
    return (category in case.get('acceptable_categories',[case['category']])
            and (not case.get('expected_document_type') or document==case['expected_document_type'])
            and (not case.get('expected_payment_status') or payment==case['expected_payment_status']))


def evaluated_capability(capability,result,qualified):
    """Quality failure cannot change independently established pricing or transport."""
    return replace(capability,generation=capability.generation or bool(result.get('attempted')),
                   structured=capability.structured or bool(result.get('valid_schema')),
                   validated=qualified,quality=result['category_quality'],workflow_quality=result['workflow_quality'],
                   latency=result.get('mean_seconds') or capability.latency)


def dimension_scores(cases,outcomes):
    labels={c['id']:c for c in cases};category=defaultdict(lambda:[0,0]);workflow=defaultdict(lambda:[0,0])
    for outcome in outcomes:
        case=labels[outcome['case']];language=outcome['language'];valid=outcome['schema_valid']
        category[language][1]+=1;category[language][0]+=int(valid and category_matches(case,outcome.get('predicted_category'),outcome.get('predicted_document_type'),outcome.get('predicted_payment_status')))
        expected=case.get('job')
        if expected is None:expected={'pickup':'pickup','delivered':'delivered','processing':'processing','due':'payment_due','receipts':'paid','refunds':'refunded'}.get(case.get('section'))
        if expected is None:continue
        actual=outcome.get('predicted_status') or ''
        correct=actual==expected or expected=='unknown' and actual in ('','unknown')
        workflow[language][1]+=1;workflow[language][0]+=int(valid and correct)
    ratios=lambda data:{lang:n/d for lang,(n,d) in data.items() if d>=3}
    return dict(category_quality=ratios(category),workflow_quality=ratios(workflow),category_counts=dict(category),workflow_counts=dict(workflow))


class SemanticEvaluation:
    def __init__(self,provider,capability,cases,catalog=None):self.provider,self.capability,self.cases,self.catalog=provider,capability,cases,catalog
    def run(self):
        scores=defaultdict(lambda:[0,0]);outcomes=[];unsafe=0;latencies=[];valid=0;fatal=None;malformed=0;diagnostic=None
        catalog=[dict(id=id,name=name,source='system') for id,name in [('other','Other'),('bills','Bills'),('security','Security'),('university','University'),('orders','Orders'),('promotions','Promotions'),('job_applications','Jobs')]]
        if self.catalog:catalog=self.catalog
        default_catalog=catalog
        for case in self.cases:
            catalog=case.get('catalog') or default_catalog
            row=dict(account_id='evaluation',message_id=case['id'],subject=case['subject'],sender='unrelated@evaluation.invalid',body=case['body'],headers={},labels=[],internal_date_ms=0,content_complete=True,decision={})
            summary=public_summary(row,'m0','full');language=message_language(case['body']);correct=False;schema_ok=False;raw_status=None;predicted=None;document=None;payment=None;issue_codes=[]
            try:
                response=self.provider.analyze(self.capability,SYSTEM,dict(messages=[summary],catalog=catalog),SCHEMA)
                value=json.loads(response.text);raw=value['frames'][0]
                predicted=raw.get('category_id')
                # Score raw model predictions BEFORE deterministic semantic
                # guards can correct/reject an unsafe outcome.
                raw_status=status_key(raw.get('status'));expected_status=case.get('job')
                status_ok=expected_status is None or raw_status==expected_status or expected_status=='unknown' and raw_status in ('','unknown')
                facets=semantic_dimensions(raw);document=facets.document_type;payment=facets.payment_status
                category_ok=category_matches(case,raw.get('category_id'),document,payment)
                frames,_,issues=validate_batch(response.text,[summary],{'m0':local_frame(row)},[c['id'] for c in catalog])
                issue_codes=[issue['code'] for issue in issues]
                schema_ok=bool(frames) and not issues
                correct=category_ok and status_ok and schema_ok
                unsafe+=int(case['category'] in ('security','bills','orders','job_applications') and raw.get('category_id')=='promotions')
                latencies.append(response.seconds);valid+=schema_ok
            except ModelFailure as exc:fatal=exc.state;diagnostic=exc.diagnostic()
            except Exception as exc:
                issue_codes=[getattr(exc,'code',type(exc).__name__)]
                malformed+=1
                if malformed>=2:fatal='repeated_schema_failure'
            scores[language][0]+=int(correct);scores[language][1]+=1
            outcomes.append(dict(case=case['id'],language=language,correct=correct,schema_valid=schema_ok,predicted_category=predicted,predicted_status=raw_status,predicted_document_type=document,predicted_payment_status=payment,issues=issue_codes))
            if fatal:break
        quality={lang:n/d for lang,(n,d) in scores.items() if d>=3}
        # A dangerous financial/security/transactional-to-promotion error bars
        # autonomous use even if averaged headline accuracy would look good.
        completed=len(outcomes)==len(self.cases)
        if unsafe or not completed:quality={lang:0 for lang in quality}
        dimensions=dimension_scores(self.cases,outcomes)
        if unsafe or not completed:
            dimensions['category_quality']={lang:0 for lang in dimensions['category_quality']};dimensions['workflow_quality']={lang:0 for lang in dimensions['workflow_quality']}
        return dict(cases=len(self.cases),attempted=len(outcomes),completed=completed,blocked_reason=fatal,diagnostic=diagnostic,correct=sum(x['correct'] for x in outcomes),valid_schema=valid,quality=quality,**dimensions,unsafe_promotional=unsafe,mean_seconds=sum(latencies)/len(latencies) if latencies else None,outcomes=outcomes,scope='Independent synthetic multilingual labels; raw model scored before deterministic corrections. Not mailbox population accuracy.')

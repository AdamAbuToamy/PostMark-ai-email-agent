"""Privacy-preserving fixed evaluation: aggregate metrics, no live mail/network."""
import json
from pathlib import Path
from email_agent.application.classify_messages import decide
from email_agent.application.smart_sections import section_for
from email_agent.application.job_evidence import job_status
from email_agent.application.semantic_mail import local_frame
from email_agent.application.promotional_cleanup import eligible_ad
from email_agent.domain.message import EmailMessage


def evaluate():
    cases=json.loads((Path(__file__).resolve().parents[1]/'tests/fixtures/mail_quality.json').read_text())
    scores={k:[0,0] for k in ['category','subcategory','job_lifecycle','protection_recall']};errors=[];unsafe=abstentions=0
    for case in cases:
        # Same meaning under unrelated sender identities/account domains.
        for sender in [case.get('sender','mail@service.example'),'office@campus.ac.il','news@shop.example']:
            message=EmailMessage(case['id'],'personal',sender,case['subject'],9999999999999,('INBOX','CATEGORY_PROMOTIONS'))
            category,status,reason=decide(message,case['body']);scores['category'][1]+=1;scores['category'][0]+=category==case['category']
            if category!=case['category']:errors.append(dict(id=case['id'],dimension='category',expected=case['category'],actual=category))
            if case.get('requires_semantic') and category=='other':abstentions+=1
            if 'section' in case:
                section=section_for(category,case['subject'],sender,case['body']);scores['subcategory'][1]+=1;scores['subcategory'][0]+=section==case['section']
                if section!=case['section']:errors.append(dict(id=case['id'],dimension='subcategory',expected=case['section'],actual=section))
            if 'job' in case:
                state=job_status(case['subject'],sender,case['body'])['state'];scores['job_lifecycle'][1]+=1;scores['job_lifecycle'][0]+=state==case['job']
                if state!=case['job']:errors.append(dict(id=case['id'],dimension='job',expected=case['job'],actual=state))
            decision=dict(category_id=category,status=status,reason=reason,source='automatic');allowed,_=eligible_ad(message,decision,[],-1,case['body'])
            if case['category'] in {'security','bills','orders','job_applications','university'}:
                scores['protection_recall'][1]+=1;scores['protection_recall'][0]+=not allowed;unsafe+=allowed
    return dict(cases=len(cases),sender_variants=3,metrics={k:dict(correct=v[0],total=v[1],accuracy=v[0]/v[1] if v[1] else None) for k,v in scores.items()},incorrect_cleanup_eligibility=unsafe,semantic_abstentions=abstentions,errors=errors,scope='Synthetic anonymized behavioral cases, not population accuracy or provider evaluation')

if __name__=='__main__':print(json.dumps(evaluate(),ensure_ascii=False))

"""Visible local assessment, bounded historical scans and verified mailbox management."""
from datetime import datetime,timezone
import time
import json
import re
from email.utils import parseaddr
from typing import Protocol
from email_agent.application.promotional_cleanup import eligible_ad,advertising_purpose
from email_agent.application.local_understanding import understand
from email_agent.application.smart_sections import attention_state
from email_agent.application.subscriptions import subscription_identity


class MailStore(Protocol):
    def scan_state(self,account): ...
    def start_scan(self,account): ...
    def scan_page(self,account,state,limit): ...
    def commit_scan(self,account,state,page,rows): ...
    def record(self,account,rows,source='incremental'): ...
    def summary(self,account): ...
    def assessments(self,account,verdict,search,offset,limit): ...
    def trash(self,account,search,offset,limit): ...
    def history(self,account,search,offset,limit): ...
    def protect(self,account,scope,identity,reason): ...
    def changed_page(self,account,limit=50): ...
    def commit_changed(self,account,before,after,rows): ...


def retention_reason_kind(verdict,reason):
    text=reason.casefold()
    if verdict in ('needs_review','error'):
        return 'missing_content' if any(word in text for word in ('awaiting content','not inspected','missing body','body missing')) else 'uncertain'
    if text in ('personal correspondence','already in gmail trash','in spam; manual placement review required'):return 'policy'
    if any(word in text for word in ('kept by user','user protection','manual category','manual classification','starred')):return 'user'
    if any(word in text for word in ('historical','protected sender','policy','excluded','operation')):return 'policy'
    return 'important'


def assess(message,decision,exclusions,protection=None,body=None):
    list_id,kind=subscription_identity(dict(message.list_headers or ()),message.sender)
    row={'sender_address':parseaddr(message.sender)[1].casefold(),'list_identity':list_id if kind!='promotional_sender' else None,'message_id':message.id,'category_id':decision.get('category_id'),'confidence':'Protected','verdict':'protected'}
    def result(verdict,reason,confidence='Protected'):
        kind=retention_reason_kind(verdict,reason)
        return {**row,'verdict':verdict,'reason':reason,'confidence':confidence,'protection_kind':kind}
    legacy_semantic_hold=protection in {'Semantic sensitivity or unresolved meaning; retain for organization','Content purpose not verified; retained during content review'}
    historical_hold=protection=='Historical discovery; no historical cleanup approval'
    if protection and not (legacy_semantic_hold or historical_hold):return result('protected',protection)
    evidence=understand(message,body or '')
    if evidence and evidence.protected:return result('protected',evidence.name)
    if evidence and evidence.kind.startswith('github_'):return result('protected','Development notification; keep by default')
    promotional=advertising_purpose(message,body)
    category=decision.get('category_id')
    if category=='orders' or not promotional and re.search(r'\b(order|shipping|delivery|refund|return|purchase)\b|طلب|شحن|مرتجع|تتبع',message.subject.casefold()):return result('protected','Order / shipping / returns')
    if category=='bills' or not promotional and re.search(r'\b(invoice|receipt|bill|payment)\b|فاتور|فواتير|ايصال|دفع',message.subject.casefold()):return result('protected','Bill / invoice / receipt')
    if category=='security' or not promotional and re.search(r'\b(security|password|authentication|verification|sign.in|otp)\b|رمز|تحقق|امان',message.subject.casefold()):return result('protected','Security / authentication')
    if category=='job_applications' or not promotional and re.search(r'\b(job|application|interview|employer|career)\b|وظيف|توظيف|مقابلة',message.subject.casefold()):return result('protected','Job-related mail · manual review')
    if category=='university' or not promotional and re.search(r'\b(university|course|tuition|semester|lecture|exam)\b|جامع|دراس|محاضر|امتحان',message.subject.casefold()):return result('protected','University / course correspondence')
    if decision.get('source')=='manual':return result('protected','Manual category override')
    labels=set(message.label_ids or ())
    if labels & {'IMPORTANT','STARRED'}:return result('protected','Important / starred')
    if 'TRASH' in labels:return result('protected','Already in Gmail Trash')
    if 'SPAM' in labels:return result('protected','In Spam; manual placement review required')
    if labels & {'CATEGORY_PERSONAL','SENT','DRAFT'} or re.match(r'^(re|fw|fwd)\s*:',message.subject.casefold()):return result('protected','Personal correspondence')
    if attention_state(message,decision)=='untouched':return result('needs_review','Awaiting content' if not body else 'Purpose uncertain','Needs review')
    if message.label_ids is None:return result('needs_review','Gmail labels unknown; verify before cleanup','Needs review')
    if decision.get('status')!='classified':return result('needs_review','Uncertain classification; manual review required','Needs review')
    eligible,reason=eligible_ad(message,decision,exclusions,-1,body)
    if eligible:return result('eligible','Historical advertisement · exact review approval required' if historical_hold else 'Verified advertising signals','High · local signals')
    if legacy_semantic_hold:return result('needs_review','Awaiting content' if not body else 'Semantic evidence needs review','Needs review')
    if reason=='Sensitive content evidence; retained until its purpose is verified' and evidence and evidence.category=='promotions':return result('needs_review','Mixed advertising content; evidence review pending','Needs review')
    if reason in ('Message content not inspected; retained for content review','Classification needs review','Not confidently classified advertising','Insufficient independent advertising signals'):return result('needs_review','Awaiting content' if not body else 'Insufficient advertising evidence','Needs review')
    if historical_hold:return result('protected',protection)
    return result('protected',reason)


class MailManagement:
    def __init__(self,messages,classifier,cleanup,actions,discovery):
        self.messages,self.classifier,self.cleanup,self.actions,self.discovery=messages,classifier,cleanup,actions,discovery
        self.store=cleanup.store.management

    def evaluate(self,account,id):
        self.cleanup.validate(account)
        message=self.messages.get(account,id)
        if message is None:raise LookupError('Message not found in this account')
        evidence=getattr(self.classifier.assignments,'cleanup_evidence',self.classifier.assignments.cached_evidence)
        protection=self.cleanup.store.protection(account,message,ignore_system=True) or self.cleanup.store.protection(account,message)
        return assess(message,self.classifier.assignments.message_details(account,id) or {},self.cleanup.store.policy(account)['exclusions'],protection,evidence(account,id))

    def review_eligible(self,account,id):return self.evaluate(account,id)['verdict']=='eligible'

    def progress(self,account):
        self.cleanup.validate(account)
        return {'counts':self.store.summary(account),'scan':self.store.scan_state(account),'gmail_history':{**self.discovery.progress(account),**self.store.read_metrics(account,'backfill')},'incremental_labels':self.store.read_metrics(account,'incremental_labels'),'spam_sync':self.store.label_state(account,'SPAM'),'trash_sync':self.store.label_state(account)}

    def scan(self,account,restart=False,limit=100):
        self.cleanup.validate(account)
        if not 1<=limit<=200:raise ValueError('Scan page must contain 1–200 messages')
        owner=self.cleanup.store.acquire(account)
        if owner is None:raise ValueError('Another account scan is running; refresh progress')
        try:
            state=self.store.scan_state(account)
            if restart or state is None:state=self.store.start_scan(account)
            if state['status']=='completed':return self.progress(account)
            page=self.store.scan_page(account,state,limit);rows=[]
            for _,id in page:
                try:
                    message=self.messages.get(account,id)
                    if message is None:raise LookupError('Unavailable')
                    self.classifier.classify(message)
                    rows.append(self.evaluate(account,id))
                except Exception:rows.append({'message_id':id,'category_id':None,'verdict':'error','reason':'Local assessment failed; recheck this message','confidence':'Needs review'})
            self.store.commit_scan(account,state,page,rows)
            return self.progress(account)
        finally:self.cleanup.store.release(account,owner)

    def incremental(self,account):
        owner=self.cleanup.store.acquire(account)
        if owner is None:return
        try:
            cursor=self.store.event_watermark(account);events,next_cursor=self.store.event_page(account,cursor);rows=[]
            for _,id in events:
                try:rows.append(self.evaluate(account,id))
                except Exception:rows.append({'message_id':id,'verdict':'error','reason':'Message assessment unavailable','confidence':'Needs review'})
            self.store.commit_events(account,cursor,events,rows,next_cursor)
        finally:self.cleanup.store.release(account,owner)

    def refresh_changed(self,account):
        """Reassess bounded local classification/content changes; no Gmail calls."""
        self.cleanup.validate(account)
        owner=self.cleanup.store.acquire(account)
        if owner is None:return dict(status='busy',processed=0)
        try:
            before,after,ids=self.store.changed_page(account)
            rows=[]
            for id in ids:
                try:rows.append(self.evaluate(account,id))
                except LookupError:continue
            self.store.commit_changed(account,before,after,rows)
            for row in rows:
                if row['verdict']=='eligible':self.cleanup.reconsider(account,row['message_id'])
            return dict(status='updated',processed=len(rows))
        finally:self.cleanup.store.release(account,owner)

    def listing(self,account,view='eligible',search='',offset=0,limit=50):
        self.cleanup.validate(account)
        if view not in ('eligible','protected','history','trash','spam') or len(search)>200 or offset<0 or not 1<=limit<=100:raise ValueError('Invalid mailbox page')
        if view=='history':result=self.store.history(account,search,offset,limit)
        elif view=='spam':result=self.store.trash(account,search,offset,limit,'SPAM')
        elif view=='trash':
            result=self.store.trash(account,search,offset,limit)
            for item in result['items']:
                latest=self.store.history(account,'',0,2,message_id=item['message_id'])['items']
                item['trash_origin']='Postmark Trash record' if latest and latest[0]['operation']=='trash' and latest[0]['status']=='succeeded' and latest[0]['detail'].get('changed') is not False and not any(row['operation']=='restore' and row['performed_at']>=latest[0]['performed_at'] for row in latest) else 'Unknown / another client'
        else:
            # Refresh only a bounded displayed page, never scan the entire mailbox on GET.
            for _ in range(2):
                result=self.store.assessments(account,view,search,offset,limit)
                rows=[self.evaluate(account,item['message_id']) for item in result['items']]
                if not any(row['verdict']!=item['verdict'] or row['reason']!=item['reason'] for row,item in zip(rows,result['items'])):break
                self.store.record(account,rows,'recheck')
            result=self.store.assessments(account,view,search,offset,limit)
        names={category['id']:category['name'] for category in self.classifier.registry.categories(account)}
        for item in result['items']:
            item['protection_kind']=retention_reason_kind(item.get('verdict'),item.get('reason') or '')
            item['recommendation']='Move to Trash after review' if item.get('verdict')=='eligible' else 'Kept · no cleanup action' if item.get('verdict')=='protected' else 'Verify before any cleanup'
            item['category_name']=names.get(item.get('current_category') or item.get('category_id'),'Unclassified')
            if isinstance(item.get('label_ids'),str):item['label_ids']=json.loads(item['label_ids'])
        return {**result,**self.progress(account)}

    def keep(self,account,id):
        self.evaluate(account,id)
        self.store.keep_message(account,id)
        self.store.record(account,[self.evaluate(account,id)],'manual')
        return {'status':'kept','message_id':id}

    def protection_choices(self,account,id):
        self.evaluate(account,id);message=self.messages.get(account,id)
        identity,kind=subscription_identity(dict(message.list_headers or ()),message.sender)
        options=[{'scope':'message','label':'Only this message'}]
        if kind!='promotional_sender':options.append({'scope':'list','label':'This exact mailing list','identity':identity})
        address=parseaddr(message.sender)[1].casefold()
        if re.fullmatch(r'[^\s@]{1,100}@[^\s@]{1,150}\.[^\s@]{2,50}',address):options.append({'scope':'sender','label':'This exact sender address','identity':address})
        return {'message_id':id,'options':options}

    def protect(self,account,id,scope):
        choices=self.protection_choices(account,id)
        option=next((item for item in choices['options'] if item['scope']==scope),None)
        if option is None:raise ValueError('Choose an available exact exclusion; domain exclusions are not supported')
        self.store.protect(account,scope,option.get('identity',id),'User protection: '+option['label'])
        self.store.record(account,[self.evaluate(account,id)],'manual')
        return {'status':'protected','scope':scope,'identity':option.get('identity',id)}

    def batch(self,account,ids):
        if not 1<=len(ids)<=20 or len(set(ids))!=len(ids) or any(not isinstance(id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,256}',id) for id in ids):raise ValueError('Select 1–20 distinct reviewed advertisements')
        if not all(self.review_eligible(account,id) for id in ids):raise ValueError('Some messages are protected or changed; refresh the review')
        return self.actions.preview(account,'trash',ids,'promotions',cleanup_review=True)

    def restore(self,account,id):return self.actions.preview(account,'restore',[id],keep_after_restore=True)

    def backfill(self,account,restart=False):
        self.cleanup.validate(account)
        owner=self.cleanup.store.acquire(account)
        if owner is None:raise ValueError('Another account task is running')
        try:
            try:messages=self.discovery.backfill(account,restart)
            except Exception as exc:
                self.store.record_read(account,'backfill',read_errors=1)
                raise ValueError('Gmail history read failed; saved checkpoint retained. Retry or explicitly restart metadata traversal.') from exc
            unavailable=getattr(self.discovery,'last_unavailable',0)
            self.store.record_read(account,'backfill',unavailable=unavailable if isinstance(unavailable,int) else 0)
            rows=[]
            for message in messages:
                try:self.classifier.classify(message);rows.append(self.evaluate(account,message.id))
                except Exception:rows.append({'message_id':message.id,'verdict':'error','reason':'Local classification needs recheck','confidence':'Needs review'})
            self.store.record(account,rows,'backfill')
            return self.progress(account)
        finally:self.cleanup.store.release(account,owner)

    def refresh_trash(self,account,label="TRASH"):
        if label not in ("TRASH","SPAM"):raise ValueError("Unsupported mailbox label")
        self.cleanup.validate(account)
        owner=self.cleanup.store.acquire(account)
        if owner is None:raise ValueError('Another account task is running; refresh progress')
        try:
            state=self.store.label_state(account,label)
            if state is None or state['completed']:state=self.store.start_label_sync(account,label)
            rechecks=self.store.label_rechecks(account,state)
            messages,token=(self.discovery.trash_page(account,state['next_token']) if label=='TRASH' else self.discovery.label_page(account,state['next_token'],label)) if not state['listing_done'] else ([],None)
            unavailable=getattr(self.discovery,'last_unavailable',0) if not state['listing_done'] else 0
            errors=unavailable if isinstance(unavailable,int) else 0
            for _,id in rechecks:
                try:messages.append(self.discovery.message(account,id))
                except LookupError:errors+=1  # Missing is not proof of deletion: retain prior labels.
            self.store.commit_label_page(account,state,messages,token,rechecks,errors)
            rows=[]
            for message in messages:
                try:self.classifier.classify(message);rows.append(self.evaluate(account,message.id))
                except Exception:rows.append({'message_id':message.id,'verdict':'error','reason':'Local classification needs recheck','confidence':'Needs review'})
            self.store.record(account,rows,'trash_sync')
            return self.progress(account)
        except (ValueError,LookupError):raise
        except Exception as exc:
            self.store.label_error(account,label)
            raise ValueError('Gmail read failed; checkpoint retained. Retry this bounded page.') from exc
        finally:self.cleanup.store.release(account,owner)

    def reconcile_action(self,account,id):
        self.cleanup.validate(account)
        value=self.actions.actions.get(account,id)
        if value['kind'] not in ('gmail:trash','gmail:restore'):raise ValueError('Not a Gmail action')
        if value['status']=='running':
            audit=self.actions.actions.audit(account,id)
            latest=max((datetime.fromisoformat(row['created_at']).replace(tzinfo=timezone.utc).timestamp() for row in audit),default=time.time())
            if time.time()-latest<900:raise ValueError('This operation may still be running; wait before reconciliation')
        owner=self.cleanup.store.acquire(account)
        if owner is None:raise ValueError('Account task is running')
        try:
            results=[];operation=value['kind'].split(':')[1]
            for item in value['payload']['items']:
                try:
                    message=self.discovery.message(account,item['message_id']);self.messages.save(message)
                    labels=set(message.label_ids or ())
                    before=set(item.get('label_ids') or ())
                    verified=message.label_ids is not None and ('TRASH' in labels if operation=='trash' else 'TRASH' not in labels and ('TRASH' in before or 'SPAM' not in before or ('SPAM' not in labels and 'INBOX' in labels)))
                    if verified and value['payload'].get('keep_after_restore'):self.store.keep_message(account,message.id)
                    results.append({'message_id':message.id,'status':'succeeded' if verified else 'uncertain','changed':False,'labels_after':list(labels),'verification':'authoritative_label_readback','message':'Current labels verified; no mutation was repeated'})
                except Exception:results.append({'message_id':item['message_id'],'status':'uncertain','message':'Readback unavailable; no mutation repeated'})
            status='succeeded' if all(item['status']=='succeeded' for item in results) else 'partial'
            self.actions.actions.finish(account,id,status,{'items':results,'message':'Read-only reconciliation'})
            return self.actions.actions.get(account,id)
        finally:self.cleanup.store.release(account,owner)

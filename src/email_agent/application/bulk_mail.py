"""Explicit frozen local selections; remote effects only after operation-specific approval."""
from email.utils import parseaddr
import hashlib
import json
import time
from typing import Protocol
from email_agent.application.subscriptions import subscription_identity
from email_agent.application.semantic_dimensions import document_type

class BulkMailStore(Protocol):
    def rows(self,account,scope): ...
    def frame(self,account,id): ...
    def create(self,account,operation,criterion,items,excluded): ...
    def body_digest(self,account,id): ...
    def uncertain(self,id): ...
    def mark_deleted(self,account,id,operation): ...
    def get(self,account,id,offset=0): ...
    def approve(self,account,id,approved,confirmation): ...
    def lease(self,account,id): ...
    def pending(self,id): ...
    def mark(self,id,message,state,result): ...
    def release(self,account,id,due=0): ...

class BulkMail:
    def __init__(self,messages,assignments,store:BulkMailStore,provider,allowed,protect,operation_lock):
        self.messages,self.assignments,self.store=messages,assignments,store
        self.provider,self.allowed,self.protect,self.operation_lock=provider,allowed,protect,operation_lock
    def context(self,account,id):
        message=self.messages.get(account,id)
        if message is None:raise LookupError('Message does not belong to this account')
        current=self.assignments.message_details(account,id) or {}
        decision={key:current.get(key) for key in ('category_id','status','source','reason','rule_version')}
        frame=self.store.frame(account,id)
        fingerprint=hashlib.sha256(json.dumps([message.sender,message.subject,message.label_ids,decision,self.store.body_digest(account,id),frame],sort_keys=True).encode()).hexdigest()
        return message,decision,frame,fingerprint
    @staticmethod
    def identity(criterion,message,decision,frame):
        if criterion=='sender':
            address=parseaddr(message.sender)[1].casefold()
            return address if '@' in address else None
        if criterion=='subscription':
            identity,kind=subscription_identity(dict(message.list_headers or ()),message.sender)
            return identity if kind!='promotional_sender' else None
        if criterion=='category':return decision.get('category_id')
        if not frame.get('understood') or not frame.get('quality_verified') or not frame.get('input_coverage',{}).get('body_complete'):return None
        if criterion=='semantic_type':return document_type(frame.get('message_type'))
        if criterion=='group' and frame.get('primary_intent'):
            # Same grouping dimensions as the existing semantic organization view.
            return [decision.get('category_id'),frame['primary_intent'],frame.get('message_type')]
        return None
    def preview(self,account,operation,scope,ids=None,criterion=None,anchor=None):
        if account not in ('personal','university') or operation not in ('trash','restore','delete'):raise ValueError('Invalid account or operation')
        if operation in ('restore','delete') and scope['view']!='trash':raise ValueError('Restore and permanent deletion require a Trash selection')
        if operation=='trash' and scope['view']=='trash':raise ValueError('Messages are already in Trash')
        if ids is not None and (not ids or len(ids)!=len(set(ids))):raise ValueError('Select distinct messages')
        if criterion not in (None,'sender','subscription','category','semantic_type','group'):raise ValueError('Unsupported grouping criterion')
        target=None
        if criterion:
            if not anchor:raise ValueError('Select an anchor message')
            m,d,f,_=self.context(account,anchor);target=self.identity(criterion,m,d,f)
            if not target:raise ValueError('This message has no verified membership for that grouping criterion')
        requested=set(ids) if ids is not None else None;seen=set();items=[];excluded=[]
        for row in self.store.rows(account,scope):
            id=row['message_id']
            if requested is not None and id not in requested:continue
            m,d,f,fingerprint=self.context(account,id)
            if criterion and self.identity(criterion,m,d,f)!=target:continue
            seen.add(id);labels=set(m.label_ids or ())
            item=dict(message_id=id,sender=m.sender,subject=m.subject,fingerprint=fingerprint,label_ids=list(m.label_ids or ()))
            reason=None
            if operation in ('restore','delete') and 'TRASH' not in labels:reason='Not currently in Trash'
            if operation=='trash' and 'TRASH' in labels:reason='Already in Trash'
            if operation!='restore':reason=reason or self.protect(account,m,d,operation,criterion is not None)
            if reason:excluded.append({**item,'reason':reason})
            else:items.append(item)
        if requested is not None and seen!=requested:raise ValueError('Selection no longer matches this account and current filters; select again')
        return self.store.create(account,operation,dict(scope=scope,kind=criterion or ('selected' if ids is not None else 'all_matching'),anchor=anchor),items,excluded)
    def reconcile(self,account,id):
        state=self.store.get(account,id)
        if not state['approved']:raise ValueError('No approved operation to reconcile')
        with self.operation_lock(account):
            if not self.store.lease(account,id):return self.store.get(account,id)
            try:
                provider=self.provider(account,state['operation'])
                for item in self.store.uncertain(id):
                    try:
                        message_id=item['message_id']
                        if state['operation']=='delete':
                            if provider.exists(message_id):continue
                            self.store.mark_deleted(account,message_id,id)
                        else:
                            labels=provider.labels(message_id)
                            if (state['operation']=='trash')!=('TRASH' in labels):continue
                            self.messages.update_labels(account,message_id,labels)
                        self.store.mark(id,message_id,'succeeded',{'verification':'read_only_reconciliation'})
                    except Exception:pass
            finally:self.store.release(account,id)
        return self.store.get(account,id)

    def step(self,account,id):
        state=self.store.get(account,id)
        if not state['approved']:raise ValueError('Confirm this exact operation first')
        if state['status']!='running' or state['due']>time.time():return state
        if not self.allowed(account,state['operation']):raise PermissionError('Separate Gmail authorization is missing for this action')
        with self.operation_lock(account):
            if not self.store.lease(account,id):return self.store.get(account,id)
            due=0
            try:
                provider=self.provider(account,state['operation'])
                for item in self.store.pending(id):
                    message_id=item['message_id'];started=False
                    try:
                        m,d,f,fingerprint=self.context(account,message_id)
                        if fingerprint!=item['fingerprint']:raise ValueError('Message or classification changed since preview')
                        if state['operation']!='restore':
                            reason=self.protect(account,m,d,state['operation'],state['criterion']['kind'] not in ('selected','all_matching'))
                            if reason:raise ValueError(reason)
                        labels=provider.labels(message_id)
                        if set(labels)!=set(item['label_ids']):raise ValueError('Gmail location or labels changed since preview')
                        self.store.mark(id,message_id,'executing',{'write_attempted':True});started=True
                        if state['operation']=='delete':
                            provider.delete(message_id)
                            if provider.exists(message_id):raise ValueError('Permanent deletion not verified')
                            self.messages.update_labels(account,message_id,())
                            self.store.mark_deleted(account,message_id,id)
                        else:
                            updated=provider.apply(state['operation'],message_id,labels)
                            if updated is None or state['operation']=='trash' and 'TRASH' not in updated or state['operation']=='restore' and 'TRASH' in updated:raise ValueError('Requested labels were not verified')
                            self.messages.update_labels(account,message_id,updated)
                            if state['operation']=='restore':self.assignments.resolve_placement(account,message_id)
                        self.store.mark(id,message_id,'succeeded',{'verification':'authoritative_gmail_readback'})
                    except Exception as exc:
                        status=getattr(getattr(exc,'resp',None),'status',None)
                        if (status in (429,503) or isinstance(exc,(TimeoutError,ConnectionError,OSError))) and not started and item.get('_read_retries',0)<3:
                            retry=(getattr(exc,'resp',None) or {}).get('retry-after','60')
                            try:delay=max(1,min(86400,float(retry)))
                            except (ValueError,TypeError):delay=60
                            due=time.time()+delay;self.store.mark(id,message_id,'pending',{'reason':'Gmail capacity unavailable; waiting to resume','read_retries':item.get('_read_retries',0)+1});break
                        self.store.mark(id,message_id,'uncertain' if started else 'skipped',{'reason':'Result unknown; no automatic write retry' if started else str(exc) if isinstance(exc,(ValueError,LookupError)) else 'Gmail read failed; no write issued','write_attempted':started})
                        if status in (429,503):
                            try:delay=max(1,float((getattr(exc,'resp',None) or {}).get('retry-after','60')))
                            except (ValueError,TypeError):delay=60
                            due=time.time()+delay;break
            finally:self.store.release(account,id,due)
        return self.store.get(account,id)

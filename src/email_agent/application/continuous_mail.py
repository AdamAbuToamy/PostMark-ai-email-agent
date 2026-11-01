"""Bounded mail processing policy, independent of storage and providers."""
from typing import Protocol
import time


class MailWork(Protocol):
    def enabled(self,account:str)->bool: ...
    def seed(self,account:str)->None: ...
    def claim(self,account:str,stage:str)->dict|None: ...
    def finish(self,item:dict,status:str,reason:str|None=None)->None: ...
    def failed(self,item:dict,error:Exception)->None: ...
    def has_body(self,account:str,id:str)->bool: ...
    def save_body(self,account:str,id:str,evidence:dict)->None: ...
    def empty_body(self,item:dict,evidence:dict)->None: ...
    def metadata_saved(self,account:str,id:str)->None: ...
    def queue_semantic(self,account:str,id:str)->None: ...
    def progress(self,account:str)->dict: ...


class ContinuousMail:
    def __init__(self,store:MailWork,messages,classifier,readers):
        self.store,self.messages,self.classifier,self.readers=store,messages,classifier,readers

    def step(self,account:str,metadata_limit:int=12,body_limit:int=24,local_limit:int=100,time_budget:float=70):
        if account not in ('personal','university'):raise ValueError('Unknown account')
        if not 0<=metadata_limit<=24 or not 0<=body_limit<=32 or not 0<=local_limit<=200:raise ValueError('Invalid worker bounds')
        sync_enabled=getattr(self.store,'sync_enabled',self.store.enabled)
        if not self.store.enabled(account) and not sync_enabled(account):return self.store.progress(account)
        deadline=time.monotonic()+time_budget
        self.store.seed(account)
        # A slow provider must not starve already cached mail of local work.
        first_local=min(20,local_limit)
        local_used=0
        for stage,limit in [('local',first_local),('metadata',metadata_limit),('body',body_limit),('local',local_limit)]:
            if time.monotonic()>=deadline:break
            if not (self.store.enabled(account) if stage=='local' else sync_enabled(account)):continue
            if stage!='local' and not getattr(self.store,'read_ready',lambda _:True)(account):continue
            for _ in range(limit):
                if stage=='local' and local_used>=local_limit:break
                if time.monotonic()>=deadline or not (self.store.enabled(account) if stage=='local' else sync_enabled(account)):break
                item=self.store.claim(account,stage)
                if item is None:break
                if stage=='local':local_used+=1
                try:
                    id=item['message_id']
                    if stage=='metadata':
                        message=self.readers(account,item).get_metadata(account,id)
                        if message.account_id!=account or message.id!=id:raise ValueError('Message identity mismatch')
                        self.messages.save(message);self.store.metadata_saved(account,id)
                    elif stage=='body':
                        if not self.store.has_body(account,id):
                            evidence=self.readers(account,item).read(id)
                            if evidence.get('complete') is not True or not isinstance(evidence.get('body'),str):raise ValueError('Incomplete MIME evidence')
                            if not evidence['body'].strip():
                                self.store.empty_body(item,evidence);continue
                            self.store.save_body(account,id,evidence)
                    else:
                        message=self.messages.get(account,id)
                        if not message:raise LookupError('Message unavailable')
                        self.classifier.classify(message)
                        self.store.queue_semantic(account,id)
                    self.store.finish(item,'done')
                except Exception as exc:
                    self.store.failed(item,exc)
                    if getattr(exc,'pause_work',False):break
            self.store.seed(account)
        return self.store.progress(account)

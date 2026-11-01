"""Dispatch a small queue slice under an explicit durable task grant."""
from typing import Protocol


class SemanticQueue(Protocol):
    def current_grant(self,account:str)->dict|None: ...
    def select_semantic(self,account:str,limit:int,priority:tuple[str,...]=())->list[str]: ...
    def ai_wait(self,account:str,reason:str)->None: ...
    def settle_semantic(self,account:str,ids:list[str],campaign:str)->None: ...
    def dispatch_ready(self,grant:dict,scope:dict)->bool: ...
    def minimum_scope_fits(self,grant:dict)->bool: ...


class SemanticDispatch:
    def __init__(self,store:SemanticQueue,organizer_factory):self.store,self.organizer_factory=store,organizer_factory

    def step(self,account):
        grant=self.store.current_grant(account)
        if not grant:return dict(status='waiting_consent')
        if not self.store.minimum_scope_fits(grant):
            self.store.ai_wait(account,'Task budget cannot fit another complete request; local processing continues')
            return dict(status='waiting_task_budget')
        organizer,scope_holder=self.organizer_factory(grant)
        if organizer.settings(account).get('usage',{}).get('remaining',0)<2:
            self.store.ai_wait(account,'Shared application daily budget exhausted; local processing continues')
            return dict(status='waiting_application_budget')
        try:organizer._free_policy(account,grant['model'])
        except ValueError:
            self.store.ai_wait(account,'Current account/Free-only/provider limits block dispatch')
            return dict(status='waiting_provider_policy')
        self.store.ai_wait(account,'Authorized bounded queue; complete bodies, Free-only and original task limits')
        ids=self.store.select_semantic(account,4,('other','job_applications','bills','orders'))
        if not ids:return dict(status='idle')
        scope=organizer.preview([account],1,len(ids),0,'full',ids)
        scope_holder['id']=scope['id'];scope_holder['ids']=ids
        if not scope['message_count']:
            self.store.settle_semantic(account,ids,scope['id']);return dict(status='deferred_content')
        if not self.store.dispatch_ready(grant,scope):
            # Fit fewer complete messages into the remaining approved budget;
            # never shorten bodies or create a fresh grant to evade its cap.
            for size in (2,1):
                if size>=len(ids):continue
                smaller=organizer.preview([account],1,size,0,'full',ids[:size])
                if smaller['message_count'] and self.store.dispatch_ready(grant,smaller):
                    ids=ids[:size];scope=smaller;scope_holder['id']=scope['id'];scope_holder['ids']=ids;break
            else:
                self.store.ai_wait(account,'Remaining task budget cannot fit the next complete-message scope; local processing continues')
                return dict(status='waiting_task_budget')
        result=organizer.run(scope['id'],True)
        self.store.settle_semantic(account,ids,scope['id'])
        return result

"""Resume bounded full-content reads without AI, Gmail writes or taxonomy approval."""
from typing import Protocol


class FillStore(Protocol):
    def claim(self,campaign:str)->dict|None: ...
    def cached_complete(self,account:str,id:str)->bool: ...
    def save(self,account:str,id:str,evidence:dict)->None: ...
    def finish_item(self,campaign:str,item:dict,status:str,result:dict)->None: ...
    def progress_fill(self,campaign:str)->dict: ...
    def reserve_attempt(self,campaign:str,item:dict,kind:str)->None: ...


class FillContent:
    def __init__(self,store:FillStore,messages,classifier,readers):
        self.store,self.messages,self.classifier,self.readers=store,messages,classifier,readers

    def run(self,campaign,limit=8):
        if type(limit) is not int or not 1<=limit<=10:raise ValueError('Read at most10 selected messages per checkpoint')
        for _ in range(limit):
            item=self.store.claim(campaign)
            if item is None:break
            account,id=item['account_id'],item['message_id']
            before=self.classifier.assignments.message_details(account,id) or {}
            try:
                if not self.store.cached_complete(account,id):
                    reader=self.readers(account,lambda kind:self.store.reserve_attempt(campaign,item,kind))
                    evidence=reader.read(id)
                    if not isinstance(evidence,dict) or evidence.get('complete') is not True or not isinstance(evidence.get('body'),str):raise ValueError('incomplete_mime_content')
                    if not evidence['body'].strip():
                        self.store.finish_item(campaign,item,'failed',dict(error_kind='EmptyBody',reason='empty_message_body',mime_types=evidence.get('mime_types',{})))
                        continue
                    self.store.save(account,id,evidence)
                # The classifier's persistence guard preserves manual decisions.
                message=self.messages.get(account,id)
                self.classifier.classify(message)
                after=self.classifier.assignments.message_details(account,id) or {}
                self.store.finish_item(campaign,item,'done',dict(category_before=before.get('category_id'),category_after=after.get('category_id'),manual_preserved=before.get('source')=='manual',category_changed=before.get('category_id')!=after.get('category_id')))
            except Exception as exc:
                import traceback
                location=[dict(file=frame.filename.rsplit('/',1)[-1],line=frame.lineno,function=frame.name) for frame in traceback.extract_tb(exc.__traceback__)[-3:]]
                code=getattr(exc,'code',None) or getattr(getattr(exc,'resp',None),'status',None)
                self.store.finish_item(campaign,item,'failed',dict(location=location,error_kind=type(exc).__name__,http_status=code if type(code)is int else None,reason=str(exc) if type(exc)is ValueError else 'Content read stopped; inspect safe failure kind'))
                break  # No hidden replay; a failed/unknown operation stays reserved.
        return self.store.progress_fill(campaign)

"""Global consent/request/token guard around the existing model adapter."""
import json


class BoundedOrganizerModel:
    def __init__(self,model,store,grant,campaign,ids,reserve_count,verify=lambda:None):
        self.model,self.store,self.grant,self.campaign,self.ids,self.reserve_count=model,store,grant,campaign,ids,reserve_count
        self.last_usage={}
        self.verify=verify

    def generate_organization(self,system,data,schema):
        self.verify()
        # Preflight counts are also provider calls. Charge a conservative UTF-8
        # bound plus envelope allowance even when counting/generation fails.
        upper=len((system+json.dumps(data,ensure_ascii=False)+json.dumps(schema)).encode())+8192
        self.store.reserve_ai(self.grant,self.campaign,'count',upper,0,self.ids)
        if not self.reserve_count():
            self.store.record_ai(self.campaign,'count','blocked');raise ValueError('Shared provider budget blocks token count')
        try:
            count=self.model.count_organization(system,data,schema)
            if type(count)is not int or not 0<count<=upper:raise ValueError('Unverifiable token bound; no generation')
            self.store.record_ai(self.campaign,'count','done',count)
        except Exception:
            self.store.record_ai(self.campaign,'count','failed');raise
        # Include a conservative schema/framing allowance rather than claiming
        # countTokens measures hidden schema overhead in generation.
        generation_bound=count+len(json.dumps(schema).encode())+8192
        self.verify()
        self.store.reserve_ai(self.grant,self.campaign,'generate',generation_bound,4096,self.ids)
        try:
            raw=self.model.generate_organization(system,data,schema);self.last_usage=self.model.last_usage
            self.store.record_ai(self.campaign,'generate','done',self.last_usage.get('total_tokens'))
            return raw
        except Exception:
            self.store.record_ai(self.campaign,'generate','failed');raise

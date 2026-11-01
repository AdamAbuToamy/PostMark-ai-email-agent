"""Human-operated fallback, never an unsubscribe POST or an email sender."""
from urllib.parse import urlsplit, unquote, parse_qs
from email.utils import parseaddr
from email_agent.application.subscriptions import observed_subscriptions


class ManualSubscriptions:
    def __init__(self,messages,management,store,validate_https):
        self.messages,self.management,self.store,self.validate_https=messages,management,store,validate_https

    def group(self,account,id):
        self.management.validate_account(account)
        value=next((g for g in observed_subscriptions(self.messages,account) if g["id"]==id),None)
        if value is None:value=self.store.manual_snapshots(account).get(id)
        if value is None: raise LookupError("Subscription not observed in this account")
        return value

    def method(self,account,id,index,approved):
        group=self.group(account,id)
        if approved is not True: raise ValueError("Explicit approval to review this exact manual method is required")
        options=group["manual_destinations"]
        if type(index) is not int or not 0<=index<len(options): raise ValueError("Manual method not found")
        value=options[index]
        if len(value)>2000 or any(ord(c)<32 for c in unquote(value)): raise ValueError("Unsafe manual method")
        parsed=urlsplit(value)
        if parsed.scheme=="https":
            try:
                self.validate_https(value) # public DNS safety only; never visits or submits the URL
            except ValueError: raise
            except Exception as exc:
                raise ValueError("Public DNS verification unavailable; no link was visited or submitted. Inspect the original manually.") from exc
            result={"kind":"https","url":value,"instructions":"Review the displayed host and sender. Open this link yourself only if trusted; the website may track your visit or redirect. No cancellation has been submitted by Postmark."}
        elif parsed.scheme=="mailto":
            address=unquote(parsed.path)
            if parseaddr(address)[1]!=address or address.count("@")!=1 or any(c in address for c in "<> ,;\\"):
                raise ValueError("Unsafe manual email address")
            query=parse_qs(parsed.query,keep_blank_values=True)
            if set(query)-{"subject","body"} or any(len(v)!=1 for v in query.values()): raise ValueError("Unsupported manual email fields")
            result={"kind":"mailto","address":address,"subject":query.get("subject",[""])[0],"body":query.get("body",[""])[0],"instructions":"Copy these fields to your email client and review the recipient and content yourself. Postmark does not compose or send email automatically."}
        else: raise ValueError("Unsupported manual method; inspect the original sender's process")
        self.store.manual_event(account,id,"manual_method_reviewed","User reviewed method "+str(index)+"; no request submitted")
        return {**result,"subscription_id":id,"display_name":group["display_name"]}

    def handled(self,account,id,approved,note):
        self.group(account,id)
        if approved is not True or not isinstance(note,str) or not 1<=len(note.strip())<=500: raise ValueError("Confirm a local handled note (1–500 characters)")
        return self.store.mark_manual(account,id,note.strip())

    def complete(self,account,id,approved,note):
        self.management.validate_account(account)
        group=next((g for g in observed_subscriptions(self.messages,account) if g['id']==id),None)
        if group is None:group=self.store.manual_snapshots(account).get(id)
        if group is None:raise LookupError('Subscription not found in this account')
        if approved is not True or not isinstance(note,str) or not 1<=len(note.strip())<=500:
            raise ValueError('Confirm you completed this exact unsubscribe manually')
        return self.store.complete_manual(account,group,note.strip())

    def audit(self,account,id):
        self.group(account,id)
        return self.store.manual_audit(account,id)

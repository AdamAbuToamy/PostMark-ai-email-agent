"""One-list cancellation use case, separate from cryptographically verified RFC8058 POST."""
from typing import Protocol, Callable
from urllib.parse import urlsplit
from email_agent.application.subscriptions import observed_subscriptions
from email_agent.application.model_errors import safe_reason


class UnsubscribeOriginal(Protocol):
    def read(self,account:str,message_id:str)->dict: ...

class WebsiteBrowser(Protocol):
    def run(self,scope:dict,emit:Callable[[str,dict],None])->dict: ...
    def resume(self,scope:dict,emit:Callable[[str,dict],None])->dict: ...
    def state(self,id:str,account:str)->dict: ...
    def discard(self,id:str,account:str)->None: ...


class BrowserUnsubscribe:
    def __init__(self,messages,management,store,one_click,reader:UnsubscribeOriginal,browser:WebsiteBrowser,validate_url,recipient:Callable[[str],str]|None=None,validate_manual=None):
        self.messages,self.management,self.store,self.one_click=messages,management,store,one_click
        self.reader,self.browser,self.validate_url=reader,browser,validate_url
        self.recipient=recipient
        self.validate_manual=validate_manual or validate_url

    def group(self,account,id):
        self.management.validate_account(account)
        group=next((g for g in observed_subscriptions(self.messages,account) if g['id']==id),None)
        if group is None:raise LookupError('Subscription not observed in this account')
        return group

    def describe(self,account,id,parent_action=None):
        group=self.group(account,id)
        saved=self.store.subscription_states(account).get(id)
        if saved and parent_action is None:
            existing=self.store.get(account,saved['action_id']);outcome=existing.get('result') or {}
            if outcome.get('session_closed') and outcome.get('reason') in ('information_or_preferences_required','recipient_consent_required','preference_choice_required'):
                return self.recover_before_submission(account,existing['id'])
            return existing
        diagnostics=[];url=None
        message_id=(group['candidate_message_ids'] or group['message_ids'])[0]
        parent=None
        if parent_action:
            parent=self.store.get(account,parent_action)
            result=parent.get('result') or {}
            if parent['kind']!='unsubscribe:browser' or parent['payload']['subscription_id']!=id or result.get('reason')!='destination_changed' or not result.get('proposed_destination'):
                raise ValueError('This action does not offer a changed destination to approve')
            if not saved or saved['action_id']!=parent_action or saved['status']!='needs_action':raise ValueError('The previous cancellation is not safely resumable')
            url=result['proposed_destination']
            message_id=parent['payload']['message_id']
        else:
            if group['one_click_candidate']:
                checked=self.one_click.preview(account,[id])
                if any(item['eligible'] for item in checked['payload']['items']):return checked
                diagnostics=[item.get('reason','') for item in checked['payload']['items']]
            try:
                original=self.reader.read(account,message_id)
                headers=original.get('headers') or {}
                if headers:
                    self.messages.record_list_headers(account,message_id,headers)
                    group=next((g for g in observed_subscriptions(self.messages,account) if message_id in g['message_ids']),group)
                    id=group['id']
                body=original.get('body_links') or []
            except Exception:
                body=[];diagnostics.append('Original body unavailable; using observed header offers only')
            candidates=list(dict.fromkeys(body+group['manual_destinations']))
            for candidate in candidates[:10]:
                if not isinstance(candidate,str) or not candidate.startswith('https://'):continue
                try:self.validate_url(candidate);url=candidate;break
                except Exception:diagnostics.append('An unsafe or unavailable destination was excluded')
        message=self.messages.get(account,message_id)
        if message is None:raise LookupError('Message no longer belongs to this account')
        if url:
            host=self.validate_url(url)
            payload={'subscription_id':id,'identity':group['identity'],'display_name':group['display_name'],'sender':message.sender,'message_id':message_id,'url':url,'destination_domain':host,'method':'website_assisted','parent_action_id':parent_action,'diagnostics':diagnostics,
                'scope':'Cancel only this mailing list, including its ordinary confirmation steps. No login, personal-data entry, payment, other subscriptions or account deletion.',
                'items':[{'message_id':message_id,'subscription_id':id,'sender':message.sender,'subject':message.subject}]}
            return self.store.preview(account,'unsubscribe:browser',payload)
        email_index=next((index for index,value in enumerate(group['manual_destinations']) if value.startswith('mailto:')),None)
        payload={'method':'manual_email' if email_index is not None else 'unavailable','subscription_id':id,'message_id':message_id,'display_name':group['display_name'],'sender':message.sender,'method_index':email_index,'diagnostics':diagnostics,'items':[]}
        preview=self.store.preview(account,'unsubscribe:unavailable',payload)
        self.store.finish(account,preview['id'],'needs_action',{'state':'needs_action','reason':'manual_email' if email_index is not None else 'no_safe_method'})
        return self.store.get(account,preview['id'])

    def confirm(self,account,id,approved):
        self.management.validate_account(account)
        value=self.store.get(account,id)
        if value['kind']!='unsubscribe:browser':raise ValueError('Not a browser cancellation approval')
        if approved is not True:raise ValueError('Approve this exact mailing-list cancellation first')
        if value['status'] not in ('pending','running'):return value
        group=self.group(account,value['payload']['subscription_id'])
        if group['identity']!=value['payload']['identity'] or value['payload']['message_id'] not in group['message_ids']:raise ValueError('Mailing-list identity changed; start again')
        self.validate_url(value['payload']['url'])
        if not self.store.claim(account,id):raise ValueError('Approval expired or already used; no replay')
        parent=value['payload'].get('parent_action_id')
        if parent:
            started=self.store.resume_browser_subscription(account,id,group['id'],parent)
        else:started=self.store.start_subscription(account,id,group['id'])
        if not started:
            self.store.finish(account,id,'failed',{'state':'failed','reason':'another_request_started'})
            return self.store.get(account,id)
        self.store.event(id,'browser_scope_approved',{'subscription_id':group['id'],'message_id':value['payload']['message_id'],'destination_domain':value['payload']['destination_domain'],'scope':value['payload']['scope']})
        scope={**value['payload'],'account_id':account}
        try:result=(self.browser.resume if scope.get('session_id') else self.browser.run)(scope,lambda event,detail:self.store.event(id,event,detail))
        except Exception as exc:result={'state':'needs_action','reason':'result_uncertain','failure':{'kind':type(exc).__name__,'reason':safe_reason(exc) or 'Execution stopped without reliable confirmation; no replay'}}
        if result.get('state') not in ('cancelled','requested','needs_action','failed'):result={'state':'needs_action','reason':'result_uncertain'}
        self.store.finish_browser(account,id,group['id'],result)
        return self.store.get(account,id)

    def prepare_resume(self,account,id,submit_recipient=False,allow_all=False,topic_ids=None,inspect_only=False):
        parent=self.store.get(account,id);payload=parent['payload'];result=parent.get('result') or {}
        if parent['kind']!='unsubscribe:browser' or parent['status']!='needs_action':raise ValueError('Only a paused browser outcome can be resumed')
        self.group(account,payload['subscription_id']);self.validate_url(payload['url'])
        session=result.get('session_id')
        if not session:raise ValueError('This historical session is closed. No safe automatic replay; review the saved diagnostic')
        live=self.browser.state(session,account);page=live.get('page_state') or {}
        if not page.get('snapshot_id') or page.get('origin')!=result.get('page_state',{}).get('origin'):raise ValueError('Page state changed; inspect the current session first')
        topics=topic_ids or []
        fields={f['id']:f for f in page.get('fields',[]) if f['type']=='checkbox' and f.get('purpose')=='topic' and not f.get('disabled')}
        if len(topics)>30 or len(set(topics))!=len(topics) or any(id not in fields for id in topics):raise ValueError('Choose only observed preference topics in this exact page')
        readonly=bool(live.get('inspect_only') or inspect_only)
        if readonly and (submit_recipient or allow_all or topics):raise ValueError('An uncertain submission can only be inspected; no resubmission')
        address=None
        if submit_recipient:
            if not self.recipient:raise ValueError('Authorized account identity is unavailable')
            address=self.recipient(account)
            if not isinstance(address,str) or not __import__('re').fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',address):raise ValueError('No independently associated recipient address is available')
        resumed={**payload,'parent_action_id':id,'session_id':session,'snapshot_id':page['snapshot_id'],'allow_all':allow_all,'recipient_email':address,'topic_ids':topics,'approved_topic_labels':[fields[id]['label'] for id in topics],'inspect_only':readonly,
          'scope':('Inspect the current isolated page only; never resubmit the uncertain cancellation.' if readonly else 'Continue this exact isolated page. '+('Unsubscribe all marketing topics in this sender preference center. ' if allow_all else 'Cancel only the explicitly selected topics/current mailing list. ')+('Submit the displayed authorized Gmail recipient address only to this destination. ' if address else 'No new recipient address submission. '))}
        return self.store.preview(account,'unsubscribe:browser',resumed)

    def recover_before_submission(self,account,id):
        parent=self.store.get(account,id);result=parent.get('result') or {}
        if parent['kind']!='unsubscribe:browser' or parent['status']!='needs_action' or result.get('reason') not in ('information_or_preferences_required','recipient_consent_required','preference_choice_required'):
            raise ValueError('Cannot reopen an uncertain or live cancellation; no automatic replay')
        if result.get('session_id'):
            try:self.browser.state(result['session_id'],account)
            except ValueError:pass
            else:raise ValueError('Use the retained page instead of reopening its URL')
        audit=self.store.audit(account,id)
        if result.get('trace') or any(item['event']=='website_final_request_started' for item in audit):raise ValueError('Prior submission cannot safely be replayed')
        self.group(account,parent['payload']['subscription_id']);self.validate_url(parent['payload']['url'])
        base={key:value for key,value in parent['payload'].items() if key not in ('session_id','snapshot_id','recipient_email','allow_all','topic_ids','approved_topic_labels','inspect_only')}
        return self.store.preview(account,'unsubscribe:browser',{**base,'parent_action_id':id,'scope':'Explicitly reopen the original safe form that stopped before any control/submission. Any email or broader preferences still need separate approval.'})

    def close_session(self,account,id):
        value=self.store.get(account,id);result=value.get('result') or {};payload=value['payload']
        if value['kind']!='unsubscribe:browser' or value['status']!='needs_action':raise ValueError('No paused cancellation to close')
        current=self.store.subscription_states(account).get(payload['subscription_id'])
        if not current or current['action_id']!=id:raise ValueError('Inspect the current cancellation outcome first')
        self.browser.discard(result.get('session_id'),account)
        result={**result,'resumable':False,'session_expires_in':0,'session_closed':True}
        self.store.event(id,'browser_session_closed',{'local_only':True})
        self.store.finish_browser(account,id,payload['subscription_id'],result)
        return self.store.get(account,id)

    def flow_view(self,value):
        result=value.get('result') or {};reason=result.get('reason');state=result.get('state');payload=value['payload']
        if value['status']=='pending':kind='approve'
        elif state=='cancelled':kind='unsubscribed'
        elif state=='requested' or value['kind']=='unsubscribe:one_click' and value['status']=='succeeded':kind='pending'
        elif reason=='login_captcha_or_mfa':kind='challenge'
        elif result.get('inspect_only') and result.get('submitted'):kind='pending'
        elif reason=='destination_changed' and not result.get('inspect_only'):kind='destination'
        elif reason in ('all_scope_requires_approval','preference_choice_required') and not result.get('inspect_only'):kind='scope'
        elif reason=='recipient_consent_required' and not result.get('inspect_only'):kind='email'
        elif result.get('submitted'):kind='pending'
        else:kind='manual'
        return {**value,'flow':{'kind':kind,'title':{'approve':'Unsubscribe','unsubscribed':'Unsubscribed','pending':'Request sent — confirmation pending','destination':'Needs your help — Open browser','scope':'Needs your help — Open browser','email':'Needs your help — Open browser','challenge':'Needs your help — Open browser','manual':'Unable to unsubscribe — Open page manually'}[kind],'manual_transfer':{'same_session':False,'can_resume':False}}}

    def authorize_flow(self,account,id,approved):
        # Ordinary controls share the original authorization; personal data does not.
        return self.flow_view(self.confirm(account,id,approved))

    def continue_flow(self,account,id,approved,allow_all=False,topic_ids=None,change_destination=False):
        if approved is not True:raise ValueError('Approve the displayed scope or destination first')
        if change_destination:
            parent=self.store.get(account,id)
            preview=self.describe(account,parent['payload']['subscription_id'],id)
        else:preview=self.prepare_resume(account,id,allow_all=allow_all,topic_ids=topic_ids)
        return self.authorize_flow(account,preview['id'],True)

    def manual_page(self,account,subscription_id,action_id=None):
        self.group(account,subscription_id)
        if action_id:value=self.store.get(account,action_id)
        else:
            # A manual lookup must not reclassify/re-key the selected list. Exact ID
            # matching below remains mandatory, including for existing action IDs.
            group=self.group(account,subscription_id)
            saved=self.store.subscription_states(account).get(subscription_id)
            if saved:value=self.store.get(account,saved['action_id'])
            else:
                message_id=(group['candidate_message_ids'] or group['message_ids'])[0]
                original=self.reader.read(account,message_id)
                candidates=list(dict.fromkeys((original.get('body_links') or [])+group['manual_destinations']))
                chosen=None
                for candidate in candidates[:10]:
                    if not isinstance(candidate,str):continue
                    try:self.validate_url(candidate);chosen=candidate;break
                    except Exception:continue
                if chosen is None:raise ValueError('No safe website unsubscribe page was observed for this list')
                value=self.store.preview(account,'unsubscribe:manual_page',{'subscription_id':subscription_id,'identity':group['identity'],'message_id':message_id,'url':chosen,'items':[]})
        payload=value['payload'];result=value.get('result') or {}
        actual=payload.get('subscription_id') or next((item.get('subscription_id') for item in payload.get('items',[]) if item.get('subscription_id')),None)
        if actual!=subscription_id:raise ValueError('Manual page belongs to another mailing list')
        url=payload.get('url') or next((item.get('proof',{}).get('url') for item in payload.get('items',[]) if item.get('proof',{}).get('url')),None)
        if not url:raise ValueError('No safe website unsubscribe page was observed for this list')
        if result.get('reason') in ('unsafe_destination','unsafe_navigation','scope_change_or_unsupported'):raise ValueError('The website led to an unsafe or unrelated destination. No manual page will be opened')
        original=urlsplit(url);candidate=result.get('page_url') or url;location=urlsplit(candidate)
        if (location.scheme,location.hostname,location.port or 443)!=(original.scheme,original.hostname,original.port or 443):raise ValueError('Manual destination was not authorized')
        self.validate_manual(candidate)
        session=result.get('session_id')
        if session:
            try:self.browser.discard(session,account)
            except ValueError:pass
        self.store.event(value['id'],'manual_external_handoff',{'host':location.hostname,'same_session':False,'can_resume':False})
        if value['kind']=='unsubscribe:browser' and value['status']=='needs_action':
            self.store.finish_browser(account,value['id'],actual,{**result,'resumable':False,'manual_handoff':True})
        return {'url':candidate,'host':location.hostname,'same_session':False,'can_resume':False,'message':'Complete this in your browser. This is a separate session; automation cannot resume it. No browser cookies or Gmail credentials were transferred.'}

    def dismiss_flow(self,account,id):
        value=self.store.get(account,id)
        if not value['kind'].startswith('unsubscribe:'):raise ValueError('Not an unsubscribe dialog')
        if value['status']=='pending':self.store.reject(account,id)
        owner=value['payload'].get('parent_action_id') if value['payload'].get('session_id') else id
        try:self.close_session(account,owner)
        except (ValueError,LookupError):pass
        return self.store.get(account,id)

"""Temporary Batch 3 browser fixture. All Gmail/AI operations are mocked."""
import base64
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=')
def encode(data): return base64.urlsafe_b64encode(data).decode()


def main():
    import uvicorn
    with tempfile.TemporaryDirectory(prefix="postmark-batch3-") as directory:
        root = Path(directory)
        os.environ['EMAIL_AGENT_PROJECT_ROOT'] = str(root)
        os.environ.pop('GEMINI_API_KEY', None)
        (root/'frontend').symlink_to(ROOT/'frontend', target_is_directory=True)
        from fastapi import FastAPI, HTTPException
        from email_agent.api.app import create_app
        from email_agent.domain.message import EmailMessage
        from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
        from email_agent.intelligence import build_classifier
        messages=SQLiteMessageRepository(root/'data'/'email_agent.db')
        messages.initialize()
        classifier=build_classifier(messages)
        classifier.registry.propose('security','Security','Account notifications')
        classifier.registry.approve(classifier.registry.proposals()[0]['id'])
        data=[('order','AliExpress','Your order has shipped',('INBOX',)),('sale1','Vendor Mail','Sale one',('INBOX',)),('sale2','Vendor Mail','Sale two',('INBOX',)),('sale3','Vendor Mail','Sale three',('INBOX',)),('error','Vendor Mail','Sale provider error',('INBOX',)),('secure','Account Team','New sign-in alert',('INBOX',)),('misplaced','Store Receipts','Order confirmation',('SPAM',))]
        extra = os.environ.get('BATCH3_1_FIXTURE') == '1'
        batch32=os.environ.get('BATCH3_2_FIXTURE')=='1'
        batch33=os.environ.get('BATCH3_3_FIXTURE')=='1'
        batch34=os.environ.get('BATCH3_4_FIXTURE')=='1'
        batch35=os.environ.get('BATCH3_5_FIXTURE')=='1'
        cleanup_fixture=os.environ.get('CLEANUP_FIXTURE')=='1'
        mail_fixture=os.environ.get('MAIL_MANAGEMENT_FIXTURE')=='1'
        core=os.environ.get('CORE_FEATURE_FIXTURE')=='1'
        completion=os.environ.get('COMPLETION_FIXTURE')=='1'
        site_transport=None;website_browser=None;original_reader=None
        probe_mode={'value':'success'}
        probe_calls=[]
        if extra:
            data += [('ambiguous','Store','Your order has shipped - discount sale',('INBOX',)),('unknown','Someone','A plain note',None),('trashed','Store','Order confirmation',('TRASH',))]
        if batch32:
            data += [('ali-promo','AliExpress','AliExpress sale',('INBOX',)),('reolink-a','Reolink <offers@reolink.example>','Reolink sale one',('INBOX',)),('reolink-b','Reolink <offers@reolink.example>','Reolink discount sale',('INBOX',)),('reolink-order','Reolink <offers@reolink.example>','Your order has shipped',('INBOX',)),('uni-new','Campus','Course timetable',('INBOX',)),('metadata-error','Other <mail@other.example>','Discount sale',('INBOX',))]
        if batch33:
            data += [('esp','OpenRouter <postmark-synthetic-3@openrouter.ai>','OpenRouter special sale',('INBOX',)),('unsafe-offer','Unsafe <sales@unsafe.example>','Unsafe sale',('INBOX',)),('index-only','Another Sender','Content-only fixture',('INBOX',))]
            data += [(f'pager-{i:03}','Pagination sender',f'Pagination marker {i:03}',('INBOX',)) for i in range(57)]
        if batch34:
            data += [('reolink-unknown','postmark-synthetic-1@mail.reolinksupport.com','Reolink discount sale',None),('manual-list','Manual News <news@manual.example>','Newsletter sale',('INBOX',))]
        if batch35:
            data += [('expired-list','OpenRouter <postmark-synthetic-3@openrouter.ai>','OpenRouter discount sale',('INBOX',)),('weak-list','postmark-synthetic-1@mail.reolinksupport.com','Reolink discount sale',None)]
            data += [(f'persisted-ali-{i:03}','AliExpress <news@ae.aliexpress.com>','عرض '+('Realistic long subject '*24)+str(i),None) for i in range(12)]
        if core:
            data += [('web-'+case,'Newsletter <news@'+case+'.example.test>','Newsletter discount sale',('INBOX',)) for case in ('simple','multi','login','captcha','information','scope','bad-action','loop','open-only','submitted','redirect','unsafe','no-method','private-redirect','or-direct','or-stage','or-completed','or-explainer','or-uncertain','customerio','customerio-topic','constantcontact','cc-multi','ambiguous','visible-error')]
        if mail_fixture:
            data += [('mm-ad-old','AliExpress <promo@store.example>','Batch AliExpress discount sale',('INBOX','CATEGORY_PROMOTIONS')),('mm-ad-second','Store <deals@store.example>','Batch Store discount sale',('INBOX','CATEGORY_PROMOTIONS')),('mm-order','AliExpress <promo@store.example>','AliExpress order confirmation',('INBOX','CATEGORY_PROMOTIONS')),('mm-bill','Billing','Invoice payment receipt',('INBOX',)),('mm-security','Account','Security verification alert',('INBOX',)),('mm-jobs','Employer','Your application interview',('INBOX',)),('mm-university','Campus','University course timetable',('INBOX',)),('mm-keep','Store <keep@store.example>','Keep this discount sale',('INBOX','CATEGORY_PROMOTIONS')),('mm-scope-a','Scope <scope@store.example>','Scope A discount sale',('INBOX','CATEGORY_PROMOTIONS')),('mm-scope-b','Scope <scope@store.example>','Scope B discount sale',('INBOX','CATEGORY_PROMOTIONS')),('mm-manual','Store','Manually protected discount sale',('INBOX','CATEGORY_PROMOTIONS')),('mm-external','Elsewhere','Externally trashed message',('TRASH',)),('mm-uncertain','Store','Uncertain discount sale',('INBOX','CATEGORY_PROMOTIONS'))]
            data += [('mm-page-'+str(i),'Archive Store','Historical discount sale '+str(i),('INBOX','CATEGORY_PROMOTIONS')) for i in range(61)]
        if os.environ.get('ORGANIZATION_FIXTURE') == '1':
            data += [(f'org-{kind}-{i}','GitHub <postmark-synthetic-2@github.com>',subject,('INBOX',))
                for kind,subject in [('failed','GitHub Actions workflow failed'),('success','Successful CI workflow'),('pr','New pull request (#42)'),('security','GitHub security alert')]
                for i in range(3)]
        if os.environ.get('UX_PRODUCT_FIXTURE')=='1':
            data += [('ux-pickup','Store <orders@shop.example>','Your package is ready for pickup',('INBOX',)),
                ('ux-shipped','Store <orders@shop.example>','Your order has shipped',('INBOX',)),
                ('ux-delivered','Store <orders@shop.example>','Your order was delivered',('INBOX',)),
                ('ux-refund','Store <orders@shop.example>','Refund processed',('INBOX',)),
                ('ux-spam','Campus <office@college.ac.il>','University course registration',('SPAM',))]
        signed_by_id={}
        if os.environ.get('FOCUSED_FIXTURE')=='1':
            classifier.registry.propose('research','Research','User-created research category')
            proposal=next(p for p in classifier.registry.proposals() if p['category_id']=='research')
            classifier.registry.approve(proposal['id'])
            data += [(f'focused-job-{i}','Hiring <hr@employer.example>',subject,('INBOX',)) for i,subject in enumerate([
                'Thank you for applying - application received',
                'Your application for the Engineer position: interview invitation',
                'Your application for the Engineer position: we are not moving forward',
                'Your application for the Engineer role: your employment offer letter',
                'Update on your application for the Engineer role'])]
            data += [('focused-science-1','Team <science@example.com>','River ecology project notes',('INBOX',)),
                ('focused-science-2','Team <field@example.net>','River ecology field results',('INBOX',)),
                ('focused-ambiguous-1','Colleague','An unrelated note',('INBOX',)),
                ('focused-ambiguous-2','Colleague','A different subject',('INBOX',))]
        header_reads=[]; model_calls=[]
        remote_labels={}; events=[]; attachment_reads=[]; unsubscribe_events=[]
        verifier=None; unsubscribe_sender=None; signed_raw=None
        if extra:
            import dkim
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization
            from email_agent.infrastructure.http.dkim_unsubscribe import DKIMUnsubscribeVerifier
            key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption())
            public=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo))
            raw=b'From: Vendor <offers@vendor.invalid>\r\nList-ID: Vendor offers <offers.vendor.invalid>\r\nList-Unsubscribe: <https://vendor.invalid/unsubscribe>\r\nList-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n\r\nA promotional fixture\r\n'
            signed_raw=dkim.sign(raw,b'fixture',b'vendor.invalid',private,include_headers=[b'from',b'list-id',b'list-unsubscribe',b'list-unsubscribe-post'])+raw
            def fixture_send(url, *, verified_dkim_coverage=False):
                assert verified_dkim_coverage is True and url=='https://vendor.invalid/unsubscribe'
                unsubscribe_events.append({'url':url,'method':'POST'})
                return {'state':'requested'}
            unsubscribe_sender=fixture_send
            if batch33:
                for id,identity,target in [('esp','updates.openrouter.example','https://vendor.invalid/unsubscribe'),('unsafe-offer','unsafe.list.example','https://127.0.0.1/unsubscribe')]:
                    original=('From: OpenRouter <postmark-synthetic-3@openrouter.ai>\r\nList-ID: Updates <'+identity+'>\r\nList-Unsubscribe: <'+target+'>\r\nList-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n\r\nPromotional fixture\r\n').encode()
                    signed_by_id[id]=dkim.sign(original,b'fixture',b'mailer.example.net',private,include_headers=[b'from',b'list-id',b'list-unsubscribe',b'list-unsubscribe-post'])+original
        if batch34:
            original=b'From: postmark-synthetic-1@mail.reolinksupport.com\r\nList-ID: Offers <offers.reolink.example>\r\nList-Unsubscribe: <https://vendor.invalid/unsubscribe>\r\nList-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n\r\nPromotion fixture\r\n'
            signed_by_id['reolink-unknown']=dkim.sign(original,b'fixture',b'mail.reolinksupport.com',private,include_headers=[b'from',b'list-id',b'list-unsubscribe',b'list-unsubscribe-post'])+original
        if batch35:
            small=rsa.generate_private_key(public_exponent=65537,key_size=1024)
            small_private=small.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption())
            small_public=base64.b64encode(small.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo))
            for id,identity,author,selector,private_key in [('expired-list','expired.openrouter.example','postmark-synthetic-3@openrouter.ai',b'fixture',private),('weak-list','weak.reolink.example','postmark-synthetic-1@mail.reolinksupport.com',b'weak',small_private)]:
                original=('From: '+author+'\r\nList-ID: <'+identity+'>\r\nList-Unsubscribe: <https://vendor.invalid/manual>, <mailto:leave@vendor.invalid?subject=Unsubscribe>\r\nList-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n\r\nPromotional fixture\r\n').encode()
                signature=dkim.sign(original,selector,author.split('@')[1].encode(),private_key,include_headers=[b'from',b'list-id',b'list-unsubscribe',b'list-unsubscribe-post'])
                if id=='expired-list':signature=signature.replace(b'v=1;',b'v=1; x=1;',1)
                signed_by_id[id]=signature+signature+original
        for account in ('personal','university'):
            for index,(id,sender,subject,labels) in enumerate(data):
                message=EmailMessage(id,account,sender,subject,1793000000000-index*1000,labels)
                messages.save(message)
                messages.record_search_content(account,id,subject,complete=mail_fixture or cleanup_fixture)
                if id != 'unknown': classifier.classify(message)
                if id.startswith(('focused-science-','focused-ambiguous-')):classifier.manual(account,id,'research','User selected Research')
                if mail_fixture and id=='mm-manual':classifier.manual(account,id,'orders','Fixture protected purchase')
                if id=='secure': classifier.manual(account,id,'security','Fixture security notification')
                if id=='uni-new': classifier.manual(account,id,'university','Fixture confirmed course mail')
                remote_labels[(account,id)]=labels
                if batch33 and id=='index-only': messages.record_search_content(account,id,'Indexed text from علي اكسبرس for '+account)
                if batch33 and id in ('esp','unsafe-offer'):
                    messages.record_list_headers(account,id,{'list-id':'<updates.openrouter.example>' if id=='esp' else '<unsafe.list.example>','list-unsubscribe':'<https://vendor.invalid/unsubscribe>' if id=='esp' else '<https://127.0.0.1/unsubscribe>','list-unsubscribe-post':'List-Unsubscribe=One-Click'})
                if batch34 and id=='reolink-unknown':
                    messages.record_list_headers(account,id,{'list-id':'<offers.reolink.example>','list-unsubscribe':'<https://vendor.invalid/unsubscribe>','list-unsubscribe-post':'List-Unsubscribe=One-Click'})
                if batch34 and id=='manual-list':
                    messages.record_list_headers(account,id,{'list-id':'<manual.news.example>','list-unsubscribe':'<mailto:leave@manual.example?subject=Unsubscribe>, <https://vendor.invalid/manual>'})
                if batch35 and id in ('expired-list','weak-list'):
                    messages.record_list_headers(account,id,{'list-id':'<expired.openrouter.example>' if id=='expired-list' else '<weak.reolink.example>','list-unsubscribe':'<https://vendor.invalid/manual>, <mailto:leave@vendor.invalid?subject=Unsubscribe>','list-unsubscribe-post':'List-Unsubscribe=One-Click'})
                if core and id.startswith('web-'):
                    case=id[4:]
                    headers={'list-id':'<'+case+'.website.example>'}
                    if case=='simple':headers.update({'list-unsubscribe':'<https://letters.example.test/simple>','list-unsubscribe-post':'List-Unsubscribe=One-Click'})
                    messages.record_list_headers(account,id,headers)
                if id.startswith('sale'):
                    messages.record_list_headers(account,id,{'list-id':'Vendor offers <offers.vendor.invalid>','list-unsubscribe':'<https://vendor.invalid/unsubscribe>','list-unsubscribe-post':'List-Unsubscribe=One-Click'})
        remote_extra={};metadata_reads=[];list_reads=[]
        if mail_fixture:
            for account in ('personal','university'):
                for id in ['mm-discovered']+['mm-history-'+str(i) for i in range(25)]:
                    labels=('TRASH',) if id=='mm-discovered' or id=='mm-history-0' else ('SPAM',) if id=='mm-history-1' else ('INBOX','CATEGORY_PROMOTIONS')
                    remote_labels[(account,id)]=labels
                    remote_extra[(account,id)]={'id':id,'sender':'Historical Store <history@store.example>','subject':'Discovered Gmail Trash' if id=='mm-discovered' else 'Backfilled discount sale '+id,'internal_date_ms':100,'label_ids':labels,'list_headers':(('list-id','<historical.store.example>'),)}
        class Client:
            def __init__(self,account): self.account=account
            def get_profile(self):return {"emailAddress":self.account+"@example.test"}
            def get_full_message(self,id):
                message=messages.get(self.account,id)
                if not message: raise ValueError('Unknown fixture message')
                html=b'<p>Safe formatted fixture</p><img src="cid:logo" alt="Fixture image"><img src="https://tracker.invalid/pixel" alt="Remote image"><script>window.parent.pwned=true</script><form action="https://evil.invalid"><input></form>'
                cached_headers=dict(messages.list_header_rows(self.account)).get(id,{}) if batch33 else {}
                body_text='Indexed text from علي اكسبرس for '+self.account if id=='index-only' else f'Full message fixture for {self.account}.'
                return {'id':id,'labelIds':remote_labels[(self.account,id)] or [],'payload':{'mimeType':'multipart/mixed','headers':[{'name':'From','value':message.sender},{'name':'Subject','value':message.subject}]+[{'name':name,'value':value} for name,value in cached_headers.items()],'parts':[
                    {'mimeType':'multipart/alternative','parts':[{'mimeType':'text/plain','body':{'data':encode(body_text.encode())}},{'mimeType':'text/html','body':{'data':encode(html)}}]},
                    {'mimeType':'image/png','filename':'logo.png','headers':[{'name':'Content-ID','value':'<logo>'}],'body':{'attachmentId':'fixture-image','size':len(PNG)}},
                    {'mimeType':'application/pdf','filename':'invoice.pdf','body':{'data':encode(b'%PDF-fixture'),'size':12}}]}}
            def list_message_page(self,page_token=None,page_size=20,include_spam_trash=True,label_ids=None):
                list_reads.append({'account':self.account,'size':page_size,'labels':label_ids,'token':page_token,'includeSpamTrash':include_spam_trash})
                ids=sorted(id for (account,id),labels in remote_labels.items() if account==self.account and label_ids[0] in (labels or ())) if label_ids in (['TRASH'],['SPAM']) else ['mm-history-'+str(i) for i in range(25)]
                start=int(page_token or 0);page=ids[start:start+page_size]
                return [{'id':id} for id in page],str(start+page_size) if start+page_size<len(ids) else None
            def get_message(self,id,max_attempts=6):
                metadata_reads.append({'account':self.account,'id':id})
                if (self.account,id) in remote_extra:return {**remote_extra[(self.account,id)],'label_ids':remote_labels[(self.account,id)]}
                header_reads.append({'account':self.account,'message_id':id})
                if id=='metadata-error': raise TimeoutError('Synthetic metadata timeout')
                headers={'list-unsubscribe':'<https://reolink.example/leave?list=offers>','list-unsubscribe-post':'List-Unsubscribe=One-Click'} if id.startswith('reolink-') else {}
                message=messages.get(self.account,id)
                return {'id':id,'sender':message.sender,'subject':message.subject,'label_ids':remote_labels[(self.account,id)],'list_headers':tuple(headers.items())}
            def get_raw_message(self,id):
                if id in signed_by_id: return signed_by_id[id]
                if signed_raw is None or not id.startswith('sale'):
                    raise ValueError('No signed original in this fixture')
                return signed_raw
            def get_attachment(self,id,attachment):
                attachment_reads.append({'account':self.account,'message':id})
                return {'data':encode(PNG),'size':len(PNG)}
        class Operations:
            def __init__(self,account): self.account=account
            def labels(self,id): return remote_labels[(self.account,id)]
            def apply(self,operation,id,labels):
                events.append({'account':self.account,'operation':operation,'message_id':id})
                if id in ('error','cleanup-error'): raise TimeoutError('Synthetic unknown outcome')
                if id in ('cleanup-uncertain','mm-uncertain'):
                    remote_labels[(self.account,id)]=('TRASH',)
                    raise TimeoutError('Synthetic lost acknowledgement')
                value=('TRASH',) if operation=='trash' else tuple(label for label in labels if label!='TRASH') if mail_fixture and 'TRASH' in labels else ('INBOX',)
                remote_labels[(self.account,id)]=value
                return value
        class Gateway:
            def client(self,account,write=False): return Client(account)
            def capabilities(self,account): return {'account_id':account,'gmail_modify':account=='personal' and (not batch32 or cleanup_fixture),'scope':'gmail.modify' if account=='personal' and (not batch32 or cleanup_fixture) else 'gmail.readonly','upgrade_command':f'Fixture only: upgrade {account} explicitly'}
            def actions(self,account):
                if account!='personal' or (batch32 and not cleanup_fixture): raise PermissionError('Fixture account is read only')
                return Operations(account)
        if extra:
            verifier=DKIMUnsubscribeVerifier(Gateway().client, lambda name, **kwargs: b'v=DKIM1; k=rsa; p='+(small_public if batch35 and name.startswith(b'weak.') else public))
        class Planner:
            def generate_organization(self,system,data,schema):
                model_calls.append(data)
                return json.dumps(dict(frames=[dict(id=m['id'],primary_intent='Ecological research',message_type='Field report',status='Sampling',entity=m['sender_domain'],sensitivities=['personal'],confidence=.95,evidence=[dict(field='subject',quote=m['subject'][:100])],attributes=[],related_ids=[]) for m in data['messages']],changes=[dict(action='create',name='Field research',sources=[],message_ids=[m['id'] for m in data['messages'][:2]],dimension=None,reason='Independent field correspondence with shared purpose')]))
            def generate_semantic(self,system,summary,schema):
                if 'messages' in summary:
                    model_calls.append(summary)
                    return json.dumps({'sections':[{'name':'Research follow-ups','message_ids':[item['message_id'] for item in summary['messages']]}]})
                assert set(summary)=={'sender_domain','subject','labels','labels_known'}
                model_calls.append(summary)
                return json.dumps({'proposed_category':'other','confidence':0.6,'reasons':['Fixture ambiguity'],
                    'protected_evidence':['Unverified application context'],'suggested_new_category':None,'requires_review':True})
            def generate(self,prompt):
                data=json.loads(prompt.split('UNTRUSTED_JSON_DATA: ')[1])
                data={'text':data['user_request']}
                model_calls.append(data['text'])
                return json.dumps({'reply':'Mock Gemini response: '+data['text'],'calls':[]})
            def list_models(self,reserve):
                assert reserve()
                return {'models':[{'id':'gemini-3.5-flash-lite','actions':['generateContent']}],'pages':1,'complete':True}
            def test_connection(self):
                from email_agent.application.ai_configuration import ProviderFailure
                probe_calls.append('one fixed harmless request')
                if probe_mode['value']=='429':raise ProviderFailure('provider_rate_limited','Synthetic Google 429: project temporarily rate-limited',429)
                if probe_mode['value']=='failure':raise ProviderFailure('model_request_failed','Synthetic Google 404: model unavailable for this project',404)
                return 'POSTMARK_CONNECTION_OK'
            def generate_agent(self,system,data):
                if not batch34:
                    return self.generate(system+'\nUNTRUSTED_JSON_DATA: '+json.dumps(data))
                model_calls.append(data['user_request'])
                if completion and probe_mode['value']=='model404':
                    error=RuntimeError('Synthetic model not found for this key');error.code=404;error.status='NOT_FOUND';raise error
                if completion and probe_mode['value']=='malformed':return 'not structured JSON'
                if 'ممكن' in data['user_request']:
                    if not data['tool_observations']:
                        return json.dumps({'calls':[{'tool':'search_messages','args':{'search':'AliExpress'}}],'reply':'','continue':True})
                    id=data['tool_observations'][0]['messages'][1]['message_id']
                    return json.dumps({'calls':[{'tool':'assign_messages','args':{'message_ids':[id],'category_id':'orders'}}],'reply':'','continue':False})
                if 'rate limit' in data['user_request']:
                    error=RuntimeError('Synthetic provider limit');error.code=429;raise error
                return json.dumps({'reply':'Mock Gemini advice without mailbox claims','calls':[],'continue':False})
        if batch32:
            from email_agent.infrastructure.ai.usage import SQLiteAIQuota
            SQLiteAIQuota(root/'data'/'ai_usage.db').attest_provider(True,100)
        if batch33:
            from email_agent.infrastructure.ai.usage import SQLiteAIQuota
            from email_agent.infrastructure.database.action_store import SQLiteActionStore
            SQLiteAIQuota(root/'data'/'ai_usage.db').configure(0)
            SQLiteActionStore(messages.database_path).save_preferences('personal',True,False)
        if batch33 and not batch34:
            quota=SQLiteAIQuota(root/'data'/'ai_usage.db');quota.set_chat_ceiling(1,True);quota.reserve('personal','connection-test')
        if batch34:
            SQLiteAIQuota(root/'data'/'ai_usage.db').configure(12)
        if batch35:
            SQLiteAIQuota(root/'data'/'ai_usage.db').configure(3)
            for _ in range(3):SQLiteAIQuota(root/'data'/'ai_usage.db').reserve('personal','fixture-prior-usage')
        app=FastAPI()
        @app.middleware('http')
        async def fixture_origin(request,call_next):
            request.scope['headers']=[(key,b'http://127.0.0.1:8000' if key==b'origin' and value==b'http://127.0.0.1:8012' else value) for key,value in request.scope['headers']]
            return await call_next(request)
        @app.post('/fixture/ai')
        def ai_fixture(payload:dict):
            # Fixture-only controls operate solely on this temporary application's adapters.
            mode=payload['mode']
            probe_mode['value']=mode
            inner.state.ai_configuration.configured=mode!='missing_key'
            if mode in ('unverified','missing_key'):
                inner.state.ai_quota.attest_provider(False,None)
                inner.state.chat_store.save_preferences('personal',False,False,False)
            if mode=='consent_required':inner.state.chat_store.save_preferences('personal',True,False,False)
            if mode=='exhausted':inner.state.ai_quota.set_chat_ceiling(1,True)
            if mode=='legacy_zero':inner.state.ai_quota.configure(0)
            if mode=='saved_one':inner.state.ai_quota.attest_provider(True,1,True)
            return {'mode':mode}
        if cleanup_fixture:
            from email_agent.cleanup import build_cleanup
            import sqlite3
            import time
            from contextlib import closing
            events_path=root/'data'/'worker_state.db'
            with closing(sqlite3.connect(events_path)) as db,db:
                db.execute("CREATE TABLE inbox_events(account_id TEXT,message_id TEXT,detected_at TEXT,status TEXT DEFAULT 'pending',PRIMARY KEY(account_id,message_id))")
            @app.post('/fixture/cleanup')
            def cleanup_control(payload:dict):
                account=payload.get('account','personal')
                if account not in ('personal','university'):raise HTTPException(400,'Fixture account')
                if payload['operation']=='receive':
                    id=payload['message_id']
                    if not isinstance(id,str) or not id.startswith('cleanup-') or len(id)>100:raise HTTPException(400,'Fixture ID')
                    subject='Your order has shipped' if payload.get('kind')=='order' else 'Cleanup fixture discount sale '+id
                    message=EmailMessage(id,account,'Fixture Newsletter <news@cleanup.example>',subject,int(time.time()*1000)+5000,('INBOX','CATEGORY_PROMOTIONS'),(('list-id','<cleanup.example>'),))
                    messages.save(message);messages.record_search_content(account,id,subject,complete=mail_fixture or cleanup_fixture);classifier.classify(message);remote_labels[(account,id)]=message.label_ids
                    with closing(sqlite3.connect(events_path)) as db,db:db.execute("INSERT OR IGNORE INTO inbox_events(account_id,message_id,detected_at) VALUES (?,?,datetime('now'))",(account,id))
                elif payload['operation']=='tick':build_cleanup(messages,events_path,Gateway()).run(account)
                else:raise HTTPException(400,'Fixture operation')
                return {'ok':True}
        if mail_fixture:
            @app.post('/fixture/mail')
            def mail_control(payload:dict):
                account=payload.get('account','personal')
                if account not in ('personal','university'):raise HTTPException(400,'Fixture account')
                if payload['operation']=='external-restore':remote_labels[(account,payload['message_id'])]=()
                elif payload['operation']=='incremental':
                    from email_agent.application.mail_management import MailManagement
                    cleanup=build_cleanup(messages,root/'data'/'worker_state.db',Gateway())
                    MailManagement(messages,classifier,cleanup,None,None).incremental(account)
                else:raise HTTPException(400,'Fixture operation')
                return {'ok':True}
        @app.get('/fixture/state')
        def state(): return {'events':events,'attachments':attachment_reads,'paid_calls':0,'unsubscribe_events':unsubscribe_events,'model_calls':len(model_calls),'connection_calls':len(probe_calls),'website_events':site_transport.events if site_transport else [],'header_reads':header_reads,'metadata_reads':metadata_reads,'list_reads':list_reads}
        @app.post('/api/sync/{account}')
        @app.post('/api/analyze/{account}')
        def disabled(account:str): raise HTTPException(403,'Live sync and analysis disabled in fixture')
        if core:
            from core_browser_fixture import SiteTransport,OriginalReader
            from email_agent.infrastructure.http.website_browser import PlaywrightUnsubscribeBrowser
            site_transport=SiteTransport();website_browser=PlaywrightUnsubscribeBrowser(ROOT,site_transport,keep_sessions=completion);original_reader=OriginalReader(messages)
        inner=create_app(messages,gmail_gateway=Gateway(),unsubscribe_verifier=verifier,unsubscribe_sender=unsubscribe_sender,chat_model_factory=(lambda:Planner()) if batch32 else None,unsubscribe_browser=website_browser,unsubscribe_reader=original_reader,manual_validator=(lambda url:('vendor.invalid','93.184.216.34','/manual')) if batch34 else None)
        if batch32:
            # Full-message consent fixtures use verified local text, never live reads.
            for i in range(12):
                message=EmailMessage('full-research-'+str(i),'personal','Research <field@example.test>','Ecological field observations '+str(i),1800000000000+i,('INBOX',))
                messages.save(message)
                remote_labels[('personal',message.id)]=message.label_ids
                messages.record_search_content('personal',message.id,'Fixture current message. '+message.subject,complete=True)
                classifier.classify(message)
        if os.environ.get('SEMANTIC_REVIEW_FIXTURE')=='1':
            from email_agent.application.semantic_mail import local_frame
            engine=inner.state.semantic_organizer
            for i in range(4):
                message=EmailMessage('review-receipt-'+str(i),'personal','Billing <billing@example.test>','Your receipt '+str(i),1760000000000+i,('INBOX',))
                messages.save(message);classifier.classify(message)
                row=engine.store.message_row('personal',message.id);frame=local_frame(row)
                if i==0:frame.update(message_type='order_receipt',source='gemini',input_coverage=dict(body_supplied=False,excerpt_characters=0,body_complete=False))
                engine.store.put_frame(row,frame,'fixture')
            for suffix,subject,body in [('rejected','تحديث طلبك للوظيفة','نأسف لعدم متابعة طلب توظيفك'),('offer','עדכון למועמדותך למשרה','התקבלת לתפקיד המפתח'),('interview','Your application for the Engineer role','We invite you to an interview with our team')]:
                message=EmailMessage('review-job-'+suffix,'personal','Recruitment <hiring@example.test>',subject,1760000001000,('INBOX',))
                messages.save(message);messages.record_search_content('personal',message.id,body);classifier.classify(message)
            for label in ['SPAM','TRASH']:
                message=EmailMessage('review-boundary-'+label,'personal','Long Sender <sender@example.test>','رسالة طويلة '+('UnbrokenSubject'*20),1760000002000,(label,))
                messages.save(message);classifier.classify(message)
        if os.environ.get('PUBLICATION_FIXTURE')=='1':
            from email_agent.application.semantic_mail import local_frame,SEMANTIC_VERSION
            engine=inner.state.semantic_organizer
            ids=[]
            for i in range(3):
                message=EmailMessage('publication-'+str(i),'personal',f'Observer <observer{i}@example.test>','Environmental observations '+str(i),1760000020000+i,('INBOX',))
                messages.save(message);messages.record_search_content('personal',message.id,'Independent environmental measurements and habitat observations from this survey.',complete=True);classifier.classify(message);ids.append(message.id)
                row=engine.store.message_row('personal',message.id);frame=local_frame(row)
                frame.update(understood=True,confidence=.99,analysis_reason=None,primary_intent='Environmental observations',message_type='environmental_report',source='local',status=None,suggested_category_id=None)
                engine.store.put_frame(row,frame,SEMANTIC_VERSION)
            proposal_id=engine.propose_change('personal',dict(action='create',name='Environmental observations',sources=[],message_ids=ids,dimension=None,icon_key='file-text',reason='Independent observations share one environmental monitoring purpose'))
            @app.get('/fixture/publication')
            def publication_state():
                proposal=next(p for p in engine.store.proposals('personal') if p['id']==proposal_id)
                target=next(op['id'] for op in proposal['payload']['operations'] if op['kind']=='create')
                with engine.store.connect() as db:
                    count=db.execute("SELECT COUNT(*) FROM message_category_assignments WHERE account_id='personal' AND category_id=?",(target,)).fetchone()[0]
                    rows=db.execute('SELECT id,name,icon_key FROM taxonomy.categories WHERE id=?',(target,)).fetchall()
                return dict(proposal_id=proposal_id,fingerprint=proposal['fingerprint'],status=proposal['status'],category_id=target,categories=[dict(r) for r in rows],members=count,model_calls=len(model_calls))
        app.mount('/',inner)
        uvicorn.run(app,host='127.0.0.1',port=8012,log_level='warning')


if __name__=='__main__': main()

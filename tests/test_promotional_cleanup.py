"""Future-mail cleanup regression: temporary SQLite, mocked Gmail, no AI/network."""
import hashlib
import sqlite3
import time
from contextlib import closing
from unittest.mock import Mock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_batch3 import Base
from email_agent.application.promotional_cleanup import PromotionalCleanup
from email_agent.application.gmail_actions import GmailActions
from email_agent.application.subscriptions import subscription_identity
from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.database.cleanup_store import SQLiteCleanupStore
from email_agent.api.cleanup import register_cleanup_routes


class CleanupTests(Base):
    def setUp(self):
        super().setUp()
        self.events=self.root/'data'/'worker_state.db'
        with closing(sqlite3.connect(self.events)) as db,db:
            db.execute("CREATE TABLE inbox_events(account_id TEXT,message_id TEXT,detected_at TEXT,status TEXT DEFAULT 'pending',PRIMARY KEY(account_id,message_id))")
        self.cleanup_store=SQLiteCleanupStore(self.messages.database_path,self.events)
        self.labels={};self.writes=[];self.providers={};self.modify={'personal':True,'university':True}
        def provider(account):
            if account not in self.providers:
                value=Mock()
                value.labels.side_effect=lambda id:self.labels[(account,id)]
                def apply(operation,id,labels):
                    self.writes.append((account,operation,id))
                    updated=tuple(sorted((set(labels)-{'INBOX'}|{'TRASH'}) if operation=='trash' else (set(labels)-{'TRASH'}|{'INBOX'})))
                    self.labels[(account,id)]=updated
                    return updated
                value.apply.side_effect=apply;self.providers[account]=value
            return self.providers[account]
        self.gateway=Mock(side_effect=provider)
        self.can_modify=Mock(side_effect=lambda account:self.modify[account])
        self.cleanup=PromotionalCleanup(self.messages,self.assignments,self.cleanup_store,self.gateway,self.can_modify)
        self.remote=GmailActions(self.messages,self.assignments,self.store,self.gateway)

    def ad(self,id,account='personal',subject='Special discount sale',sender='AliExpress <news@store.example>',labels=('INBOX','CATEGORY_PROMOTIONS'),old=False):
        message=EmailMessage(id,account,sender,subject,100 if old else int(time.time()*1000)+5000,labels,(('list-id','<offers.store.example>'),))
        self.messages.save(message)
        self.messages.record_search_content(message.account_id,message.id,message.subject,complete=True);self.classifier.classify(message);self.labels[(account,id)]=labels
        return message

    def event(self,id,account='personal'):
        with closing(sqlite3.connect(self.events)) as db,db:db.execute("INSERT OR IGNORE INTO inbox_events(account_id,message_id,detected_at) VALUES (?,?,datetime('now'))",(account,id))

    def enable(self,mode='automatic',account='personal',exclusions=None):
        return self.cleanup.configure(account,True,mode,exclusions or [],mode=='automatic')

    def status(self,id,account='personal'):return self.cleanup_store.item(account,id)['status']

    def test_partial_legacy_body_never_authorizes_automatic_cleanup(self):
        self.enable();self.ad('partial');self.event('partial')
        with closing(sqlite3.connect(self.messages.database_path)) as db,db:db.execute("DELETE FROM message_content_state WHERE message_id='partial'")
        self.cleanup.run('personal');self.assertEqual(self.writes,[]);self.assertEqual(self.status('partial'),'skipped')

    def test_default_disabled_review_and_explicit_account_authorization(self):
        self.ad('new');self.event('new');self.cleanup.run('personal')
        self.assertFalse(self.cleanup.settings('personal')['policy']['enabled'])
        self.assertEqual(self.cleanup.settings('personal')['policy']['mode'],'review');self.gateway.assert_not_called()
        with self.assertRaises(ValueError):self.cleanup.configure('personal',True,'automatic',[],False)
        self.assertEqual(self.cleanup_store.history('personal')['items'],[])
        self.modify['personal']=False
        with self.assertRaises(PermissionError):self.enable()
        self.assertFalse(self.cleanup_store.policy('personal')['enabled']);self.assertEqual(self.writes,[])

    def test_review_only_is_persisted_without_remote_access(self):
        self.enable('review');self.ad('new');self.event('new');self.cleanup.run('personal')
        self.assertEqual(self.status('new'),'review');self.gateway.assert_not_called()
        reopened=SQLiteCleanupStore(self.messages.database_path,self.events)
        self.assertEqual(reopened.item('personal','new')['status'],'review')
        self.assertEqual(reopened.policy('personal')['mode'],'review')

    def test_mixed_aliexpress_and_all_sensitive_exclusions(self):
        self.enable()
        for id,subject in [('ad','Special discount sale'),('order','Your order has shipped'),('mixed','Order confirmation discount sale'),('bill','Invoice discount sale'),('security','Security alert discount sale'),('job','Interview application discount sale'),('uni','University course discount sale'),('personal','Re: Discount sale'),('uncertain','A plain note'),('arabic-receipt','إيصال دفع وخصم'),('arabic-account','رمز التحقق خصم'),('arabic-order','تفاصيل الطلب مع تخفيض'),('hebrew-receipt','קבלה הנחה'),('hebrew-shipping','משלוח מבצע'),('otp','One time code discount sale')]:
            self.ad(id,subject=subject);self.event(id)
        self.ad('important',labels=('INBOX','IMPORTANT','CATEGORY_PROMOTIONS'));self.event('important')
        self.ad('academic',sender='teacher@campus.ac.il');self.event('academic')
        self.ad('manual');self.classifier.manual('personal','manual','promotions','Explicit user decision');self.event('manual')
        # First bounded cycle is ten; another processes remaining protected entries.
        self.cleanup.run('personal');self.cleanup.run('personal')
        self.assertEqual(self.writes,[('personal','trash','ad')])
        self.assertEqual(self.status('ad'),'succeeded')
        for id in ['order','mixed','bill','security','job','uni','personal','uncertain','important','academic','manual','arabic-receipt','arabic-account','arabic-order','hebrew-receipt','hebrew-shipping','otp']:self.assertEqual(self.status(id),'skipped')
        self.assertEqual(self.assignments.message_details('personal','manual')['source'],'manual')

    def test_no_historical_cleanup_on_enable_or_mode_change(self):
        self.ad('before');self.event('before');self.enable('review')
        self.ad('reviewed');self.event('reviewed');self.cleanup.run('personal')
        self.assertEqual(self.status('reviewed'),'review');self.enable()
        self.ad('old-detected-late',old=True);self.event('old-detected-late')
        self.ad('future');self.event('future');self.cleanup.run('personal')
        self.assertEqual(self.writes,[('personal','trash','future')]);self.assertEqual(self.status('old-detected-late'),'skipped')
        with self.assertRaises(LookupError):self.cleanup_store.item('personal','before')
        self.assertEqual(self.status('reviewed'),'review')

    def test_duplicate_events_and_restart_never_repeat_trash(self):
        self.enable();self.ad('new');self.event('new');self.cleanup.run('personal');self.event('new')
        other=PromotionalCleanup(self.messages,self.assignments,SQLiteCleanupStore(self.messages.database_path,self.events),self.gateway,self.can_modify)
        other.run('personal');self.assertEqual(self.writes,[('personal','trash','new')])
        self.assertEqual(self.cleanup_store.history('personal')['cleaned_today'],1)

    def test_account_isolation_with_identical_message_ids(self):
        self.enable()
        for account in ('personal','university'):self.ad('new',account);self.event('new',account)
        self.cleanup.run('personal');self.cleanup.run('university')
        self.assertEqual(self.writes,[('personal','trash','new')]);self.assertNotIn('TRASH',self.messages.get('university','new').label_ids)
        self.assertEqual(self.cleanup_store.history('university')['items'],[])
        with self.assertRaises(LookupError):self.cleanup.protect('university','missing')
        with self.assertRaises(ValueError):self.cleanup.run('third-account')

    def test_revoked_and_disabled_policies_do_not_move_messages(self):
        self.enable();self.ad('new');self.event('new');self.cleanup_store.ingest('personal')
        self.cleanup.configure('personal',False,'automatic',[],False);self.cleanup.run('personal')
        self.assertEqual(self.status('new'),'cancelled')
        self.assertEqual(self.writes,[]);self.enable();self.ad('another');self.event('another')
        self.modify['personal']=False;self.cleanup.run('personal')
        self.assertEqual(self.status('another'),'failed');self.assertFalse(self.cleanup_store.policy('personal')['enabled']);self.assertEqual(self.writes,[])

    def test_revocation_between_label_read_and_write_is_respected(self):
        self.enable();self.ad('new');self.event('new')
        provider=self.gateway('personal')
        def read(id):
            self.cleanup.configure('personal',False,'review',[],False)
            return self.labels[('personal',id)]
        provider.labels.side_effect=read;self.cleanup.run('personal')
        self.assertEqual(self.writes,[]);self.assertEqual(self.status('new'),'skipped')

    def test_remote_importance_and_manual_override_changes_are_protected(self):
        self.enable();self.ad('new');self.event('new');self.labels[('personal','new')]=('INBOX','IMPORTANT')
        self.cleanup.run('personal');self.assertEqual(self.writes,[]);self.assertEqual(self.status('new'),'skipped')
        self.ad('changed');self.event('changed')
        provider=self.gateway('personal')
        def read(id):
            self.classifier.manual('personal',id,'orders','User protected this purchase')
            return self.labels[('personal',id)]
        provider.labels.side_effect=read;self.cleanup.run('personal')
        self.assertEqual(self.writes,[]);self.assertFalse(self.cleanup_store.policy('personal')['enabled'])

    def test_unknown_remote_outcome_is_reconciled_read_only(self):
        self.enable();self.ad('new');self.event('new');provider=self.gateway('personal')
        original=provider.apply.side_effect
        def timeout(operation,id,labels):original(operation,id,labels);raise TimeoutError('Response lost')
        provider.apply.side_effect=timeout;self.cleanup.run('personal')
        self.assertEqual(self.status('new'),'uncertain');self.assertFalse(self.cleanup_store.policy('personal')['enabled'])
        self.cleanup.run('personal');self.assertEqual(len(self.writes),1)
        self.cleanup.reconcile('personal','new');self.assertEqual(self.status('new'),'succeeded');self.assertEqual(len(self.writes),1)
        self.assertFalse(self.cleanup_store.policy('personal')['enabled'])

    def test_unverified_or_failed_readback_pauses_without_false_success(self):
        self.enable();self.ad('new');self.event('new');provider=self.gateway('personal')
        provider.apply.return_value=('INBOX',);provider.apply.side_effect=None
        self.cleanup.run('personal');self.assertEqual(self.status('new'),'uncertain');self.assertFalse(self.cleanup_store.policy('personal')['enabled'])
        self.cleanup.reconcile('personal','new');self.assertEqual(self.status('new'),'uncertain');self.assertEqual(provider.apply.call_count,1)

    def test_read_failure_pauses_without_mutation(self):
        self.enable();self.ad('new');self.event('new');self.gateway('personal').labels.side_effect=TimeoutError()
        self.cleanup.run('personal');self.assertEqual(self.status('new'),'failed');self.assertEqual(self.writes,[])
        self.assertFalse(self.cleanup_store.policy('personal')['enabled'])

    def test_bounded_workload_and_lease_prevent_parallel_jobs(self):
        self.enable()
        for i in range(25):self.ad('ad'+str(i));self.event('ad'+str(i))
        owner=self.cleanup_store.acquire('personal');self.cleanup.run('personal');self.assertEqual(self.writes,[])
        self.cleanup_store.release('personal',owner);self.cleanup.run('personal');self.assertEqual(len(self.writes),10)
        self.cleanup.run('personal');self.assertEqual(len(self.writes),20)
        self.cleanup.run('personal');self.assertEqual(len(self.writes),25)

    def test_interrupted_claim_and_changed_revision_do_not_repeat_write(self):
        self.enable();self.ad('new');self.event('new');self.cleanup_store.ingest('personal')
        revision=self.cleanup_store.policy('personal')['revision']
        self.assertTrue(self.cleanup_store.claim('personal','new',revision,{'reason':'Started'}))
        self.assertFalse(self.cleanup_store.claim('personal','new',revision,{}))
        self.cleanup.run('personal');self.assertEqual(self.status('new'),'uncertain');self.assertEqual(self.writes,[])
        self.enable();self.cleanup.run('personal');self.assertEqual(self.writes,[]);self.assertFalse(self.cleanup_store.policy('personal')['enabled'])

    def test_trash_restore_and_sender_protection_reuse_existing_approval(self):
        self.enable();self.ad('new');self.event('new');self.cleanup.run('personal')
        preview=self.remote.preview('personal','restore',['new'])
        with self.assertRaises(ValueError):self.remote.confirm('personal',preview['id'],False)
        self.assertEqual(self.remote.confirm('personal',preview['id'],True)['status'],'succeeded')
        self.assertNotIn('TRASH',self.messages.get('personal','new').label_ids)
        self.cleanup.protect('personal','new','sender');self.ad('another');self.event('another');self.cleanup.run('personal')
        self.assertEqual(self.status('another'),'skipped');self.assertEqual(self.writes,[('personal','trash','new'),('personal','restore','new')])

    def test_unsubscribed_list_sending_again_keeps_cancellation_history(self):
        self.enable();message=self.ad('new');self.event('new')
        identity,_=subscription_identity(dict(message.list_headers),message.sender);id=hashlib.sha256(identity.encode()).hexdigest()[:24]
        value=self.store.preview('personal','unsubscribe:browser',{'subscription_id':id,'identity':identity,'items':[]})
        self.store.claim('personal',value['id']);self.store.start_subscription('personal',value['id'],id)
        self.store.finish_browser('personal',value['id'],id,{'state':'cancelled','reason':'website_confirmation','evidence':'Unsubscribed'})
        states=self.store.subscription_states('personal');self.cleanup.run('personal')
        self.assertEqual(self.status('new'),'succeeded');self.assertEqual(self.store.subscription_states('personal'),states)
        self.assertEqual(self.cleanup_store.item('personal','new')['detail']['subscription_status'],'cancelled')

    def test_incremental_worker_recovers_committed_events_after_interruption(self):
        from email_agent.workers import inbox_worker
        reader=Mock();reader.list_recent.return_value=[self.ad('baseline',old=True)]
        gateway=Mock();gateway.actions=self.gateway;gateway.capabilities.side_effect=lambda account:{'gmail_modify':self.modify[account]}
        with patch.object(inbox_worker,'STATE_PATH',self.events),patch.object(inbox_worker,'build_reader',return_value=reader),patch('email_agent.infrastructure.gmail.capabilities.AccountGmail',return_value=gateway):
            inbox_worker.initialize_state();self.assertEqual(inbox_worker.sync_account('personal',self.messages),0)
            self.enable();reader.list_recent.return_value.append(self.ad('future'))
            broken=Mock();broken.classify.side_effect=RuntimeError('Interrupted classification')
            with patch('email_agent.intelligence.build_classifier',return_value=broken):
                with self.assertRaises(RuntimeError):inbox_worker.sync_account('personal',self.messages)
            self.assertEqual(self.writes,[])
            self.assertEqual(inbox_worker.sync_account('personal',self.messages),0)
            self.assertEqual(self.writes,[('personal','trash','future')])
            inbox_worker.sync_account('personal',self.messages);self.assertEqual(len(self.writes),1)
            self.assertEqual(inbox_worker.DEFAULT_INTERVAL,600)

    def test_university_policy_is_independent_and_protects_academic_mail(self):
        self.enable(account='university')
        self.ad('external-ad','university');self.event('external-ad','university')
        self.ad('course','university',subject='University course discount sale');self.event('course','university')
        self.cleanup.run('university')
        self.assertEqual(self.writes,[('university','trash','external-ad')])
        self.assertEqual(self.status('course','university'),'skipped')
        self.assertFalse(self.cleanup_store.policy('personal')['enabled'])

    def test_migration_is_additive_and_idempotent(self):
        before=[self.messages.get(account,'sale1') for account in ('personal','university')]
        self.enable('review');SQLiteCleanupStore(self.messages.database_path,self.events)
        self.assertEqual(before,[self.messages.get(account,'sale1') for account in ('personal','university')])
        self.assertTrue(self.cleanup_store.policy('personal')['enabled'])
        with closing(self.cleanup_store.connect()) as db:self.assertEqual(db.execute('SELECT version FROM cleanup_schema_versions').fetchall(),[(1,),(2,)])

    def test_stale_setup_cannot_reenable_after_revocation_or_remove_protection(self):
        self.cleanup.configure('personal',True,'automatic',[],True,expected_revision='')
        revision=self.cleanup_store.policy('personal')['revision']
        self.cleanup.configure('personal',False,'automatic',[],False,expected_revision=revision)
        with self.assertRaises(ValueError):self.cleanup.configure('personal',True,'automatic',[],True,expected_revision='')
        self.assertFalse(self.cleanup_store.policy('personal')['enabled'])
        self.enable();self.ad('protected')
        old_revision=self.cleanup_store.policy('personal')['revision']
        self.cleanup.protect('personal','protected','sender')
        with self.assertRaises(ValueError):self.cleanup.configure('personal',True,'automatic',[],True,expected_revision=old_revision)
        self.assertEqual(self.cleanup_store.policy('personal')['exclusions'],['news@store.example'])

    def test_api_authorization_validation_history_and_cross_account_restore(self):
        gateway=Mock();gateway.actions=self.gateway;gateway.capabilities.side_effect=lambda account:{'gmail_modify':self.modify[account]}
        app=FastAPI();register_cleanup_routes(app,self.messages,self.root,gateway,self.remote)
        with TestClient(app,client=('127.0.0.1',1)) as api:
            path='/api/cleanup/personal'
            self.assertFalse(api.get(path).json()['policy']['enabled'])
            payload={'enabled':True,'mode':'automatic','exclusions':[],'approved':True,'expected_revision':''}
            self.assertEqual(api.post(path,json=payload).status_code,403)
            self.assertEqual(api.post(path,headers={**self.headers,'Origin':'https://evil.invalid'},json=payload).status_code,403)
            self.assertEqual(api.post(path,headers=self.headers,json={**payload,'approved':False}).status_code,400)
            self.assertEqual(api.post(path,headers=self.headers,json={**payload,'exclusions':['not-an-email']}).status_code,400)
            self.assertEqual(api.post(path,headers=self.headers,json={**payload,'enabled':'true'}).status_code,422)
            self.assertEqual(api.post(path,headers=self.headers,json=payload).status_code,200)
            self.ad('only-personal');self.event('only-personal');self.cleanup.run('personal')
            self.assertEqual(api.post('/api/cleanup/university/restore-preview',headers=self.headers,json={'message_id':'only-personal'}).status_code,404)
            self.assertEqual(api.post(path+'/restore-preview',headers=self.headers,json={'message_id':'only-personal'}).status_code,200)
            self.assertFalse(api.get('/api/cleanup/university').json()['policy']['enabled'])
            self.assertEqual(self.writes,[('personal','trash','only-personal')])

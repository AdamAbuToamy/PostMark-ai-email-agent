"""Mail management acceptance with temporary SQLite and mocked Gmail only."""
import sqlite3,time
from contextlib import closing
from unittest.mock import Mock,patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_batch3 import Base
from email_agent.application.mail_management import MailManagement,assess
from email_agent.application.promotional_cleanup import PromotionalCleanup
from email_agent.application.gmail_actions import GmailActions
from email_agent.infrastructure.database.cleanup_store import SQLiteCleanupStore
from email_agent.infrastructure.gmail.mail_discovery import GmailMailDiscovery
from email_agent.domain.message import EmailMessage
from email_agent.api.batch3 import register_batch3_routes


class MailManagementTests(Base):
    def setUp(self):
        super().setUp()
        self.cs=SQLiteCleanupStore(self.messages.database_path,self.root/'data'/'worker_state.db')
        self.remote_labels={};self.calls=[]
        def labels(id):return self.remote_labels.get(id,('INBOX','CATEGORY_PROMOTIONS'))
        def apply(op,id,before):
            self.calls.append((op,id));after=tuple(sorted((set(before)-{'INBOX'}|{'TRASH'}) if op=='trash' else set(before)-{'TRASH'}));self.remote_labels[id]=after;return after
        provider=Mock();provider.labels.side_effect=labels;provider.apply.side_effect=apply
        self.gateway=Mock();self.gateway.actions.return_value=provider;self.gateway.capabilities.return_value={'gmail_modify':True}
        self.cleanup=PromotionalCleanup(self.messages,self.assignments,self.cs,self.gateway.actions,lambda account:True)
        self.discovery=Mock();self.discovery.progress.return_value={'completed':0,'pages_completed':0,'messages_processed':0}
        def guard(account,id,ignore=None):
            message=self.messages.get(account,id)
            return assess(message,self.assignments.message_details(account,id) or {},self.cs.policy(account)['exclusions'],self.cs.protection(account,message,ignore),self.assignments.cleanup_evidence(account,id))['verdict']=='eligible'
        self.actions=GmailActions(self.messages,self.assignments,self.store,self.gateway.actions,on_restored=self.cs.keep_message,cleanup_guard=guard)
        self.manager=MailManagement(self.messages,self.classifier,self.cleanup,self.actions,self.discovery)

    def add_mail(self,id,subject='Special discount sale',account='personal',labels=('INBOX','CATEGORY_PROMOTIONS'),sender='Store <news@store.example>',list_id='offers.store.example'):
        message=EmailMessage(id,account,sender,subject,int(time.time()*1000),labels,(('list-id','<'+list_id+'>'),))
        self.messages.save(message)
        self.messages.record_search_content(message.account_id,message.id,message.subject,complete=True);self.classifier.classify(message);self.remote_labels[id]=labels
        return message

    def scan_all(self,account='personal',limit=100):
        value=self.manager.scan(account,True,limit)
        while value['scan']['status']!='completed':value=self.manager.scan(account,False,limit)
        return value

    def test_historical_and_new_mail_visible_without_mutation_or_ai(self):
        self.add_mail('old','Special discount sale');self.add_mail('new','Special discount sale')
        result=self.scan_all(limit=3)
        self.assertEqual(result['scan']['scanned'],self.messages.count_by_account('personal'))
        listing=self.manager.listing('personal');self.assertTrue({'old','new'}<={row['message_id'] for row in listing['items']})
        self.assertEqual(self.calls,[]);self.gateway.client.assert_not_called();self.gateway.actions.assert_not_called()

    def test_protected_reasons_are_message_specific_and_manual_decisions_survive(self):
        for id,subject in [('ad','Special discount sale'),('order','Your order has shipped'),('receipt','Invoice and receipt'),('security','Security authentication alert'),('jobs','Application interview'),('university','University course timetable'),('personal','Re: a personal conversation')]:self.add_mail(id,subject)
        self.add_mail('manual');self.classifier.manual('personal','manual','other','User protected correspondence')
        self.add_mail('important',labels=('INBOX','IMPORTANT'));self.add_mail('unknown',labels=None)
        self.scan_all();rows={row['message_id']:row for row in self.manager.listing('personal','protected')['items']}
        for id in ['order','receipt','security','jobs','university','personal','manual','important','unknown']:self.assertIn(id,rows)
        self.assertIn('Order',rows['order']['reason']);self.assertIn('Manual',rows['manual']['reason']);self.assertEqual(self.assignments.message_details('personal','manual')['source'],'manual')
        self.assertEqual(self.manager.evaluate('personal','ad')['verdict'],'eligible')

    def test_checkpoint_restarts_without_duplicates_and_survives_failed_page_commit(self):
        for i in range(9):self.add_mail('old'+str(i))
        self.manager.scan('personal',True,3);cursor=self.cs.management.scan_state('personal')['cursor']
        reopened=SQLiteCleanupStore(self.messages.database_path,self.root/'data'/'worker_state.db')
        self.assertEqual(reopened.management.scan_state('personal')['cursor'],cursor)
        original=self.cs.management._assessment
        with patch.object(self.cs.management,'_assessment',side_effect=RuntimeError('Interrupted transaction')):
            with self.assertRaises(RuntimeError):self.manager.scan('personal',False,3)
        self.assertEqual(self.cs.management.scan_state('personal')['cursor'],cursor)
        self.assertEqual(self.cs.management.summary('personal')['assessed'],3)
        value=self.manager.scan('personal',False,20);self.assertEqual(value['scan']['status'],'completed')
        self.assertEqual(value['scan']['scanned'],self.messages.count_by_account('personal'));self.assertEqual(value['counts']['assessed'],value['scan']['scanned'])

    def test_large_mailbox_pagination_fixed_snapshot_and_no_protected_full_scan_on_get(self):
        with closing(sqlite3.connect(self.messages.database_path)) as db,db:
            for i in range(1001):
                self.messages.save_in_transaction(db,EmailMessage('large'+str(i),'personal','Store','Special discount sale',i+1,('INBOX','CATEGORY_PROMOTIONS')))
                db.execute('INSERT INTO message_search_content VALUES (?,?,?)',('personal','large'+str(i),'Special discount sale on clothes. Shop now.'))
                import hashlib
                body='Special discount sale on clothes. Shop now.'
                db.execute("INSERT INTO message_content_state VALUES(?,?,'complete',?,?,'fixture')",('personal','large'+str(i),len(body),hashlib.sha256(body.encode()).hexdigest()))
        value=self.manager.scan('personal',True,200);total=value['scan']['total'];self.add_mail('late')
        while value['scan']['status']!='completed':value=self.manager.scan('personal',False,200)
        self.assertEqual(value['scan']['scanned'],total);self.assertEqual(value['counts']['remaining_local'],1)
        first=self.manager.listing('personal','eligible',limit=25);second=self.manager.listing('personal','eligible',offset=25,limit=25)
        self.assertEqual(len(first['items']),25);self.assertEqual(first['next_offset'],25)
        self.assertFalse({row['message_id'] for row in first['items']}&{row['message_id'] for row in second['items']})
        self.assertEqual(self.calls,[])

    def test_batch_is_bounded_confirmed_revalidated_and_replay_safe(self):
        for id in ['a','b']:self.add_mail(id)
        self.scan_all();preview=self.manager.batch('personal',['a','b']);self.assertEqual(self.calls,[])
        with self.assertRaises(ValueError):self.actions.confirm('personal',preview['id'],False)
        value=self.actions.confirm('personal',preview['id'],True);self.assertEqual(value['status'],'succeeded');self.assertEqual(len(self.calls),2)
        self.actions.confirm('personal',preview['id'],True);self.assertEqual(len(self.calls),2)
        with self.assertRaises(ValueError):self.manager.batch('personal',['a']*21)
        self.add_mail('c');preview=self.manager.batch('personal',['c']);self.manager.keep('personal','c')
        result=self.actions.confirm('personal',preview['id'],True);self.assertNotEqual(result['status'],'succeeded');self.assertEqual(len(self.calls),2)

    def test_keep_and_narrow_scope_choices_never_exclude_a_domain_implicitly(self):
        self.add_mail('a');self.add_mail('b');self.add_mail('c',sender='Different <other@store.example>')
        self.manager.keep('personal','a');self.assertEqual(self.manager.evaluate('personal','a')['verdict'],'protected');self.assertEqual(self.manager.evaluate('personal','b')['verdict'],'eligible')
        self.manager.protect('personal','b','sender');self.assertEqual(self.manager.evaluate('personal','b')['verdict'],'protected');self.assertEqual(self.manager.evaluate('personal','c')['verdict'],'eligible')
        self.add_mail('d',sender='Else <new@other.example>',list_id='second.list.example');self.add_mail('e',sender='Other <mail@another.example>',list_id='second.list.example')
        self.manager.protect('personal','d','list');self.assertEqual(self.manager.evaluate('personal','e')['verdict'],'protected')
        with self.assertRaises(ValueError):self.manager.protect('personal','c','domain')

    def test_scope_protection_updates_large_candidate_set_and_manual_override_counts(self):
        for i in range(63):self.add_mail('list'+str(i))
        self.scan_all();self.manager.protect('personal','list0','sender')
        eligible=self.manager.listing('personal')['items']
        self.assertFalse(any(row['message_id'].startswith('list') for row in eligible))
        self.assertGreaterEqual(self.manager.listing('personal','protected')['total'],63)
        self.add_mail('manual-change',sender='Separate <unique@new.example>');self.scan_all();self.classifier.manual('personal','manual-change','orders','Protected order')
        self.assertFalse(any(row['message_id']=='manual-change' for row in self.manager.listing('personal')['items']))

    def test_restore_removes_trash_without_claiming_inbox_and_keep_survives_sync(self):
        self.add_mail('archive',labels=('TRASH',));preview=self.manager.restore('personal','archive')
        self.assertTrue(preview['payload']['keep_after_restore']);value=self.actions.confirm('personal',preview['id'],True)
        self.assertEqual(value['status'],'succeeded');self.assertEqual(self.messages.get('personal','archive').label_ids,())
        self.messages.save(EmailMessage('archive','personal','Store','Special discount sale',int(time.time()*1000),('INBOX','CATEGORY_PROMOTIONS')))
        self.scan_all();self.assertEqual(self.manager.evaluate('personal','archive')['verdict'],'protected')
        self.assertEqual(self.calls,[('restore','archive')])

    def test_trash_and_spam_restore_does_not_require_an_invented_inbox(self):
        self.add_mail('both',labels=('TRASH','SPAM'))
        preview=self.manager.restore('personal','both');value=self.actions.confirm('personal',preview['id'],True)
        self.assertEqual(value['status'],'succeeded');self.assertEqual(set(self.messages.get('personal','both').label_ids),{'SPAM'})
        self.assertNotIn('INBOX',value['result']['items'][0]['labels_after'])

    def test_trash_is_real_label_not_cleanup_history_and_account_isolated(self):
        self.add_mail('external',labels=('TRASH',));self.add_mail('same','Sale',account='university',labels=('TRASH',))
        self.add_mail('ad');self.scan_all();preview=self.manager.batch('personal',['ad']);self.actions.confirm('personal',preview['id'],True)
        rows=self.manager.listing('personal','trash')['items'];self.assertEqual({row['message_id'] for row in rows},{'external','ad'})
        self.assertEqual(next(row for row in rows if row['message_id']=='external')['trash_origin'],'Unknown / another client')
        self.assertEqual(next(row for row in rows if row['message_id']=='ad')['trash_origin'],'Postmark Trash record')
        self.assertEqual(self.manager.listing('university','trash')['total'],1)
        self.messages.update_labels('personal','ad',());self.assertEqual(self.manager.listing('personal','trash')['total'],1)
        self.assertGreater(self.manager.listing('personal','history')['total'],0)

    def test_trash_refresh_discovers_external_and_rechecks_external_restore(self):
        external=EmailMessage('remote','personal','Remote','Old message',1,('TRASH',))
        self.discovery.trash_page.return_value=([external],None)
        value=self.manager.refresh_trash('personal');self.assertTrue(value['trash_sync']['completed']);self.assertEqual(self.manager.listing('personal','trash')['total'],1)
        self.discovery.trash_page.return_value=([],None);self.discovery.message.return_value=EmailMessage('remote','personal','Remote','Old message',1,())
        self.manager.refresh_trash('personal');self.assertEqual(self.manager.listing('personal','trash')['total'],0)
        self.assertIsNotNone(self.messages.get('personal','remote'))
        self.assertEqual(self.calls,[])

    def test_trash_failure_preserves_checkpoint_and_missing_is_not_deleted(self):
        self.add_mail('cached',labels=('TRASH',));self.discovery.trash_page.side_effect=TimeoutError()
        with self.assertRaises(ValueError):self.manager.refresh_trash('personal')
        state=self.cs.management.label_state('personal');self.assertFalse(state['completed']);self.assertEqual(state['processed'],0)
        self.discovery.trash_page.side_effect=None;self.discovery.trash_page.return_value=([],None);self.discovery.message.side_effect=LookupError()
        value=self.manager.refresh_trash('personal');self.assertGreaterEqual(value['trash_sync']['errors'],1);self.assertEqual(self.manager.listing('personal','trash')['total'],1)
        self.assertEqual(self.messages.get('personal','cached').label_ids,('TRASH',))

    def test_history_uncertain_is_read_only_and_not_blindly_retried(self):
        self.add_mail('a');preview=self.manager.batch('personal',['a']);self.store.claim('personal',preview['id'])
        self.store.finish('personal',preview['id'],'failed',{'items':[{'message_id':'a','status':'uncertain'}]})
        self.assertEqual(self.manager.listing('personal','history')['items'][0]['status'],'uncertain')
        with self.assertRaises(ValueError):self.manager.batch('personal',['a'])
        self.discovery.message.return_value=EmailMessage('a','personal','Store','Special discount sale',1,('TRASH',))
        result=self.manager.reconcile_action('personal',preview['id']);self.assertEqual(result['status'],'succeeded');self.assertEqual(self.calls,[])

    def test_scan_errors_visible_and_invalid_accounts_or_bounds_rejected(self):
        self.add_mail('bad');original=self.classifier.classify
        def classify(message):
            if message.id=='bad':raise ValueError('Bad fixture')
            return original(message)
        with patch.object(self.classifier,'classify',side_effect=classify):result=self.scan_all()
        self.assertEqual(result['counts']['error'],1);self.assertEqual(result['scan']['errors'],1)
        with self.assertRaises(ValueError):self.manager.scan('wrong')
        with self.assertRaises(ValueError):self.manager.listing('personal',limit=1000)

    def test_gmail_backfill_is_bounded_resumable_and_includes_spam_trash(self):
        client=Mock();self.gateway.client.return_value=client
        client.list_message_page.side_effect=[([{'id':'page1'},{'id':'gone'}],'next'),TimeoutError(),([{'id':'page2'}],None)]
        from googleapiclient.errors import HttpError
        from httplib2 import Response
        def metadata(id,**kw):
            if id=='gone':raise HttpError(Response({'status':'404'}),b'{}')
            return {'id':id,'sender':'Store','subject':'Special discount sale','internal_date_ms':1,'label_ids':('SPAM',) if id=='page1' else ('TRASH',),'list_headers':()}
        client.get_message.side_effect=metadata
        discovery=GmailMailDiscovery(self.gateway,self.messages.database_path,self.root/'absent.db')
        self.manager.discovery=discovery;self.manager.backfill('personal')
        self.assertEqual(discovery.progress('personal')['pages_completed'],1)
        with self.assertRaises(ValueError):self.manager.backfill('personal')
        self.assertEqual(discovery.progress('personal')['pages_completed'],1)
        self.assertEqual(self.manager.progress('personal')['gmail_history']['unavailable'],1);self.assertEqual(self.manager.progress('personal')['gmail_history']['read_errors'],1)
        self.manager.backfill('personal');self.assertEqual(discovery.progress('personal')['completed'],1)
        for call in client.list_message_page.call_args_list:self.assertEqual(call.kwargs['page_size'],20);self.assertTrue(call.kwargs['include_spam_trash'])
        self.assertEqual(self.manager.evaluate('personal','page1')['verdict'],'protected');self.assertEqual(self.calls,[])

    def test_root_confirmation_route_injects_review_guard_restore_keep_and_lock(self):
        self.add_mail('api-ad')
        app=FastAPI();register_batch3_routes(app,self.messages,self.root,self.gateway)
        with TestClient(app,client=('127.0.0.1',1)) as api:
            preview=api.post('/api/mail-management/personal/batch-preview',headers=self.headers,json={'message_ids':['api-ad']}).json()
            result=api.post('/api/gmail/personal/actions/'+preview['id']+'/confirm',headers=self.headers,json={'approved':True})
            self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.json()['status'],'succeeded',result.json())
            self.assertEqual(self.calls,[('trash','api-ad')])
            restore=api.post('/api/mail-management/personal/restore-preview',headers=self.headers,json={'message_id':'api-ad'}).json()
            result=api.post('/api/gmail/personal/actions/'+restore['id']+'/confirm',headers=self.headers,json={'approved':True})
            self.assertEqual(result.json()['status'],'succeeded',result.json())
            self.assertEqual(self.manager.evaluate('personal','api-ad')['verdict'],'protected')
            self.assertEqual(self.calls,[('trash','api-ad'),('restore','api-ad')])

    def test_incremental_review_skips_assessed_backlog_without_delaying_new_mail(self):
        events=self.root/'data'/'worker_state.db'
        with closing(sqlite3.connect(events)) as db,db:
            db.execute('CREATE TABLE inbox_events(account_id TEXT,message_id TEXT,detected_at TEXT)')
            for i in range(220):
                self.add_mail('event'+str(i));db.execute("INSERT INTO inbox_events VALUES ('personal',?,datetime('now'))",('event'+str(i),))
        self.scan_all();self.add_mail('fresh-event')
        with closing(sqlite3.connect(events)) as db,db:db.execute("INSERT INTO inbox_events VALUES ('personal','fresh-event',datetime('now'))")
        self.manager.incremental('personal')
        self.assertEqual(self.manager.listing('personal',search='')['counts']['remaining_local'],0)
        self.assertEqual(self.cs.management.event_watermark('personal'),221)
        self.assertEqual(self.calls,[]);self.gateway.client.assert_not_called()

    def test_offline_cli_scans_all_local_pages_and_preserves_manual_category(self):
        import importlib.util,io
        from contextlib import redirect_stdout
        spec=importlib.util.spec_from_file_location('local_scan',__import__('pathlib').Path(__file__).resolve().parents[1]/'scripts'/'scan_local_mail.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.add_mail('offline-ad');self.add_mail('offline-manual');self.classifier.manual('personal','offline-manual','other','Keep manual choice')
        offline=self.root/'offline';(offline/'data').mkdir(parents=True)
        target=offline/'data'/'email_agent.db'
        with closing(sqlite3.connect(self.messages.database_path)) as source,closing(sqlite3.connect(target)) as destination:source.backup(destination)
        with redirect_stdout(io.StringIO()),patch('socket.create_connection',side_effect=AssertionError('No remote calls')):
            first=module.scan(offline,'personal',page_size=1,max_pages=1)
            final=module.scan(offline,'personal',page_size=2)
        self.assertEqual(first['scan']['status'],'paused');self.assertEqual(final['counts']['remaining_local'],0)
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute("SELECT source FROM message_category_assignments WHERE account_id='personal' AND message_id='offline-manual'").fetchone()[0],'manual')
            self.assertEqual(db.execute("SELECT verdict FROM cleanup_assessments WHERE account_id='personal' AND message_id='offline-ad'").fetchone()[0],'eligible')
        self.gateway.client.assert_not_called();self.gateway.actions.assert_not_called();self.assertEqual(self.calls,[])

    def test_api_scan_and_pages_never_call_gmail_and_mutations_require_local_csrf(self):
        app=FastAPI();register_batch3_routes(app,self.messages,self.root,self.gateway)
        with TestClient(app,client=('127.0.0.1',1)) as api:
            self.add_mail('old')
            self.assertEqual(api.post('/api/mail-management/personal/scan',json={}).status_code,403)
            result=api.post('/api/mail-management/personal/scan',headers=self.headers,json={'restart':True,'limit':100});self.assertEqual(result.status_code,200,result.text)
            rows=api.get('/api/mail-management/personal/messages?view=eligible').json();self.assertIn('old',{row['message_id'] for row in rows['items']})
            self.assertEqual(api.get('/api/mail-management/third/messages').status_code,400)
            self.assertEqual(api.post('/api/mail-management/personal/backfill',headers=self.headers,json={'approved':False}).status_code,400)
            self.gateway.client.assert_not_called();self.gateway.actions.assert_not_called()
            self.assertEqual(api.post('/api/mail-management/personal/batch-preview',headers=self.headers,json={'message_ids':['old']}).status_code,200)
            self.assertEqual(self.calls,[])

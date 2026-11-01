"""Durable queue behavior without Gmail, model calls or cleanup."""
import json,time
from unittest.mock import Mock,patch
from email_agent.application.continuous_mail import ContinuousMail
from email_agent.infrastructure.database.continuous_store import ContinuousStore,ReadBudgetExceeded
from email_agent.infrastructure.database.sqlite_history_store import SQLiteHistoryStore
from email_agent.domain.message import EmailMessage
import test_semantic_engine as engine


class ContinuousTests(engine.SemanticEngineTests):
    def prepare(self):
        SQLiteHistoryStore(self.messages.database_path)
        self.work=ContinuousStore(self.messages.database_path,self.messages);self.work.initialize();self.work.configure('personal',True)
        self.reader=Mock();self.reader.read.return_value={'body':'Your password was changed successfully.','complete':True,'headers':{}}
        self.pipeline=ContinuousMail(self.work,self.messages,self.classifier,lambda a,item:self.reader)

    def test_restart_safe_body_once_classification_and_account_isolation(self):
        self.seed(2);self.prepare()
        result=self.pipeline.step('personal',0,2,10)
        self.assertEqual(result['bodies_complete'],2);self.assertEqual(self.reader.read.call_count,2)
        self.pipeline.step('personal',0,2,10);self.assertEqual(self.reader.read.call_count,2)
        self.assertEqual(self.store.message_row('personal','personal-0')['decision']['category_id'],'security')
        self.assertFalse(self.pipeline.step('university',0,2,10)['enabled'])

    def test_attachment_only_is_terminal_not_complete_or_ai_reviewed(self):
        self.seed(1);self.prepare();self.reader.read.return_value={'body':'','complete':True,'mime_types':{'application/pdf':1}}
        self.pipeline.step('personal',0,1,10);self.pipeline.step('personal',0,1,10)
        self.assertEqual(self.reader.read.call_count,1);self.assertFalse(self.work.has_body('personal','personal-0'))
        self.assertIn({'stage':'body','status':'attachment_only','count':1},self.work.progress('personal')['queues'])

    def test_read_budget_atomic_shared_and_survives_reconstruction(self):
        self.seed(1);self.prepare();run=self.work.begin_run(2)
        self.work.reserve_read(run,'personal','gmail');self.work.reserve_read(run,'university','oauth')
        other=ContinuousStore(self.work.path,self.messages)
        with self.assertRaises(ReadBudgetExceeded):other.reserve_read(run,'personal','gmail')

    def test_expired_lease_is_resumed_and_failures_have_bounded_retries(self):
        self.seed(1);self.prepare();self.work.seed('personal');first=self.work.claim('personal','body');self.assertIsNotNone(first)
        with self.work.connect() as db:db.execute("UPDATE mail_work SET lease=0 WHERE stage='body'")
        second=self.work.claim('personal','body');self.assertEqual(second['message_id'],first['message_id'])
        self.work.failed(second,TimeoutError())
        with self.work.connect() as db:db.execute("UPDATE mail_work SET due=0 WHERE stage='body'")
        third=self.work.claim('personal','body');self.work.failed(third,TimeoutError())
        self.assertIsNone(self.work.claim('personal','body'))

    def test_metadata_pending_distinct_from_remote_inventory_completion(self):
        self.prepare();h=SQLiteHistoryStore(self.work.path);h.commit_observed_page('personal',None,[],None,['not-yet-fetched'])
        self.reader.get_metadata.return_value=EmailMessage('not-yet-fetched','personal','Account','Hello',1,('INBOX',))
        result=self.pipeline.step('personal',1,1,10)
        self.assertTrue(result['inventory_complete']);self.assertEqual(result['bodies_complete'],1)
        self.assertEqual(h.coverage('personal')['fetched'],1)

    def test_body_fetch_preserves_manual_category_and_audit(self):
        self.seed(1);self.prepare();self.management.change_category('personal','personal-0','other')
        self.pipeline.step('personal',0,1,10)
        row=self.store.message_row('personal','personal-0');self.assertEqual(row['decision']['source'],'manual');self.assertEqual(row['decision']['category_id'],'other')

    def test_unchanged_local_work_not_repeated_but_new_body_invalidates_it(self):
        self.seed(1);self.prepare();self.pipeline.step('personal',0,0,10)
        with self.work.connect() as db:before=db.execute("SELECT COUNT(*) FROM automation_events WHERE stage='local'").fetchone()[0]
        self.pipeline.step('personal',0,0,10)
        with self.work.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM automation_events WHERE stage='local'").fetchone()[0],before)
        self.pipeline.step('personal',0,1,10)
        with self.work.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM automation_events WHERE stage='local'").fetchone()[0],before+1)

    def test_delta_pages_commit_with_work_and_deleted_mail_is_retained(self):
        self.seed(1);self.prepare()
        with self.work.connect() as db:db.execute("INSERT INTO automation_delta(account_id,start_history_id) VALUES('personal','100')")
        first=dict(history=[dict(id='110',messagesAdded=[dict(message={'id':'new'})],labelsAdded=[dict(message={'id':'personal-0'})])],nextPageToken='next',historyId='200')
        self.work.commit_delta('personal','100',None,first)
        with self.work.connect() as db:self.assertEqual(tuple(db.execute('SELECT start_history_id,next_token FROM automation_delta').fetchone()),('100','next'))
        with self.assertRaises(ValueError):self.work.commit_delta('personal','100',None,first)
        self.work.commit_delta('personal','100','next',dict(history=[dict(id='120',messagesDeleted=[dict(message={'id':'personal-0'})])],historyId='200'))
        self.assertIsNotNone(self.messages.get('personal','personal-0'))
        with self.work.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM automation_remote_absence').fetchone()[0],1)
            self.assertEqual(tuple(db.execute('SELECT start_history_id,next_token FROM automation_delta').fetchone()),('200',None))

    def test_label_only_updates_do_not_invalidate_accepted_content_analysis(self):
        from email_agent.application.semantic_organizer import row_fingerprint
        self.seed(1);self.prepare();row=self.store.message_row('personal','personal-0');before=row_fingerprint(row);row['labels']=['SPAM']
        self.assertEqual(row_fingerprint(row),before);row['body']='Changed content';self.assertNotEqual(row_fingerprint(row),before)

    def test_verified_inventory_reuses_metadata_and_preserves_legacy_audit(self):
        from email_agent.workers.continuous_mail import inventory
        self.seed(1);self.prepare();history=SQLiteHistoryStore(self.work.path)
        history.commit_page('personal',None,[self.messages.get('personal','personal-0')],None)
        reader=Mock();reader.list_message_page.return_value=([{'id':'personal-0'}],None)
        with patch('email_agent.workers.continuous_mail.client',return_value=reader):inventory(self.root,'personal',self.work,self.messages,history,self.work.begin_run(),1)
        self.assertFalse(history.coverage('personal')['metrics_partial']);reader.get_message.assert_not_called()
        with self.work.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM automation_history_archive').fetchone()[0],1)

    def test_fresh_proposal_refresh_preserves_suggested_state_and_audit(self):
        import copy
        self.framed();proposal=self.propose();payload=copy.deepcopy(proposal['payload'])
        payload['evidence_fingerprints']={k:'new evidence fingerprint' for k in payload['evidence_fingerprints']}
        id=self.store.propose('personal',proposal['proposal_key'],payload)
        refreshed=next(p for p in self.store.proposals('personal') if p['id']==id)
        self.assertEqual(id,proposal['id']);self.assertEqual(refreshed['status'],'suggested');self.assertNotEqual(refreshed['fingerprint'],proposal['fingerprint'])

    def test_stale_semantic_frames_cannot_create_structural_proposals(self):
        rows=self.framed();self.messages.record_search_content('personal',rows[0]['message_id'],'Changed purpose')
        with self.assertRaises(ValueError):self.propose()

    def test_unknown_legacy_labels_are_refetched_even_when_metadata_exists(self):
        self.prepare();self.messages.save(EmailMessage('legacy','personal','Sender','Notice',1,None))
        self.reader.get_metadata.return_value=EmailMessage('legacy','personal','Sender','Notice',1,('SPAM',))
        self.pipeline.step('personal',1,0,10)
        self.assertEqual(self.messages.get('personal','legacy').label_ids,('SPAM',))

    def test_complete_body_reconciles_interrupted_final_attempt_without_network(self):
        self.seed(1);self.prepare();self.work.seed('personal')
        self.messages.record_search_content('personal','personal-0','Complete body',complete=True)
        with self.work.connect() as db:db.execute("UPDATE mail_work SET status='running',attempts=3,lease=0 WHERE stage='body'")
        self.pipeline.step('personal',0,1,10);self.reader.read.assert_not_called()
        with self.work.connect() as db:self.assertEqual(db.execute("SELECT status FROM mail_work WHERE stage='body'").fetchone()[0],'done')

    def test_existing_approved_category_is_reused_by_body_purpose_without_new_structure(self):
        self.prepare();self.classifier.registry.propose('letters','Conversations','Personal threads');self.classifier.registry.approve(next(p['id'] for p in self.classifier.registry.proposals() if p['category_id']=='letters'))
        message=EmailMessage('reply','personal','Person <person@example.test>','Re: meeting',1,('INBOX',),(('thread-id','thread-1'),))
        self.messages.save(message);self.messages.record_search_content('personal','reply','Thanks, I will see you tomorrow.',complete=True)
        self.classifier.classify(message);self.assertEqual(self.store.message_row('personal','reply')['decision']['category_id'],'letters')

    def test_known_job_purpose_with_unknown_outcome_still_enters_semantic_queue(self):
        self.prepare();message=EmailMessage('unclear-job','personal','Talent <talent@example.test>','Your application for the Engineer role',1,('INBOX',))
        self.messages.save(message);self.messages.record_search_content('personal',message.id,'An update about your application will follow later.',complete=True);self.classifier.classify(message)
        self.work.queue_semantic('personal',message.id)
        with self.work.connect() as db:self.assertEqual(db.execute("SELECT status FROM mail_work WHERE message_id='unclear-job' AND stage='semantic'").fetchone()[0],'pending')

    def test_manual_job_category_is_preserved_without_excluding_missing_outcome_analysis(self):
        self.prepare();self.add('manual-job','Your application for the Engineer role');self.classifier.manual('personal','manual-job','job_applications','Actually submitted','Application reference123')
        self.messages.record_search_content('personal','manual-job','An update will follow later.',complete=True);self.classifier.classify(self.messages.get('personal','manual-job'));self.work.queue_semantic('personal','manual-job')
        self.assertEqual(self.store.message_row('personal','manual-job')['decision']['source'],'manual')
        with self.work.connect() as db:self.assertEqual(db.execute("SELECT status FROM mail_work WHERE message_id='manual-job' AND stage='semantic'").fetchone()[0],'pending')

    def test_pause_stops_claiming_after_the_inflight_message(self):
        self.seed(2);self.prepare()
        def read(id):self.work.configure('personal',False);return dict(body='Complete message',complete=True)
        self.reader.read.side_effect=read;self.pipeline.step('personal',0,2,10)
        self.assertEqual(self.reader.read.call_count,1);self.assertEqual(self.work.progress('personal')['bodies_complete'],1)

    def test_pending_remote_delta_is_not_reported_as_up_to_date(self):
        self.seed(1);self.prepare();self.pipeline.step('personal',0,1,10)
        with self.work.connect() as db:
            db.execute("INSERT OR REPLACE INTO history_progress(account_id,completed) VALUES('personal',1)")
            db.execute("INSERT OR REPLACE INTO automation_delta VALUES('personal','100','next','2026-10-29')")
        progress=self.work.progress('personal');self.assertTrue(progress['incremental_pending']);self.assertEqual(progress['status'],'Organizing')

for name in engine.SemanticEngineTests.__dict__:
    if name.startswith('test_') and name not in ContinuousTests.__dict__:setattr(ContinuousTests,name,None)

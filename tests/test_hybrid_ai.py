"""Real persistence with mocked providers; no remote inference in the suite."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
from email_agent.application.hybrid_ai import ModelCapability,ModelRouter,HybridInference,InferenceResult,ClassificationQualityGate,message_language
from email_agent.application.hybrid_organizer import HybridOrganizer
from email_agent.application.model_errors import ModelFailure
from email_agent.application.semantic_mail import SCHEMA,SYSTEM,public_summary,local_frame
from email_agent.infrastructure.database.hybrid_store import SQLiteHybridStore,LocalSecretStore
from email_agent.infrastructure.ai.hybrid_providers import OpenAICompatibleProvider,LocalModelProvider,JSONTransport,local_endpoint
import test_local_organization_agent as fixture


class HybridTests(unittest.TestCase):
    add=fixture.LocalAgentTests.add
    seed=fixture.LocalAgentTests.seed
    def setUp(self):
        fixture.LocalAgentTests.setUp(self)
        from email_agent.infrastructure.database.continuous_store import ContinuousStore
        from email_agent.infrastructure.database.sqlite_history_store import SQLiteHistoryStore
        SQLiteHistoryStore(self.path)
        ContinuousStore(self.path,self.messages).initialize()
        self.hybrid=SQLiteHybridStore(self.path);self.hybrid.initialize()
    def cap(self,provider='ollama',quality=.95,model='evaluated-fixture'):
        cap=ModelCapability(provider,model,64000,structured=True,free=True,validated=True,quality={'en':quality,'he':quality,'ar':quality},fingerprint='fixture-digest')
        self.hybrid.register(cap,time.time()+86400);self.hybrid.configure(provider,True)
        if provider!='ollama':self.hybrid.record_model_check('personal',cap,{'passed':True,'source':'fixture'},'fixture')
        if provider=='openai':
            self.hybrid.authorize_paid('personal',cap,10,300000,60000,100,'1','5','10','fixture-paid');self.hybrid.free_only('personal',False)
        elif provider!='ollama':self.hybrid.grant('personal',provider,[model],10,300000,60000,100,'Explicit fixture consent')
        return cap
    def inference(self,failures,preference='cloud'):
        self.cap('gemini',.99);self.cap('groq',.98);self.cap('openrouter',.97);self.cap('cerebras',.96);self.cap('ollama',.95)
        self.providers={}
        for p in ('gemini','groq','openrouter','cerebras','ollama'):
            mock=Mock();mock.analyze.side_effect=failures[p] if p in failures else lambda m,s,d,c,**kwargs:InferenceResult('{}',m.provider,m.model,{},.1)
            self.providers[p]=mock
        return HybridInference(self.hybrid,self.providers.__getitem__).run('personal','fixture-key',['message'],SYSTEM,{},SCHEMA,'en',preference)[0]
    def test_gemini_429_routes_to_independently_authorized_groq(self):
        result=self.inference({'gemini':ModelFailure('provider_rate_limited','limited',429)})
        self.assertEqual(result.provider,'groq');self.assertFalse(self.hybrid.healthy('gemini','evaluated-fixture'))
        self.providers['gemini'].analyze.assert_called_once()
    def test_groq_429_routes_to_zero_price_verified_openrouter(self):
        result=self.inference({'gemini':ModelFailure('model_unavailable','404',404),'groq':ModelFailure('provider_rate_limited','429',429)})
        self.assertEqual(result.provider,'openrouter')
    def test_openrouter_unavailable_routes_to_cerebras(self):
        self.cap('openrouter',.98);self.cap('cerebras',.97)
        a,b=Mock(),Mock();a.analyze.side_effect=ModelFailure('provider_unavailable','503',503);b.analyze.return_value=InferenceResult('{}','cerebras','evaluated-fixture',{},.1)
        result,_=HybridInference(self.hybrid,{'openrouter':a,'cerebras':b}.__getitem__).run('personal','scope',['m'],SYSTEM,{},SCHEMA,'en','cloud')
        self.assertEqual(result.provider,'cerebras')
    def test_cloud_503_falls_back_local_and_retry_after_is_persistent(self):
        cloud=self.cap('gemini',.99);local=self.cap('ollama');failure=ModelFailure('provider_unavailable','503',503);failure.retry_after=720
        a,b=Mock(),Mock();a.analyze.side_effect=failure;b.analyze.return_value=InferenceResult('{}','ollama',local.model,{},.1)
        result,_=HybridInference(self.hybrid,{'gemini':a,'ollama':b}.__getitem__).run('personal','scope',['m'],SYSTEM,{},SCHEMA,'en','cloud')
        self.assertEqual(result.provider,'ollama');self.assertFalse(SQLiteHybridStore(self.path).healthy('gemini',cloud.model))
        with self.hybrid.connect() as db:self.assertGreater(db.execute("SELECT until FROM hybrid_health WHERE provider='gemini'").fetchone()[0],time.time()+700)
    def test_auto_prefers_highest_measured_quality_not_provider_order(self):
        result=self.inference({},'auto');self.assertEqual(result.provider,'gemini');self.providers['ollama'].analyze.assert_not_called()
    def test_unverified_quality_and_unknown_free_eligibility_never_route(self):
        model=self.cap();self.hybrid.register(replace(model,quality={'en':.8}),time.time()+86400)
        self.assertEqual(ModelRouter(self.hybrid).select('personal','en',100),[])
        self.hybrid.register(replace(model,free=False),time.time()+86400)
        self.assertEqual(ModelRouter(self.hybrid).select('personal','en',100),[])
    def test_external_consent_account_isolation_and_immutable_budget_scope(self):
        model=self.cap('groq')
        self.assertEqual(self.hybrid.candidates('university'),[])
        id=self.hybrid.grant('personal','groq',[model.model],1,1000,1000,2,'Explicit','scope')
        attempt=self.hybrid.reserve('personal',model,'one',['m'],500,500)
        self.hybrid.finish(attempt,'failed',error=ModelFailure('network_unavailable','offline'))
        self.assertEqual(id,self.hybrid.grant('personal','groq',[model.model],1,1000,1000,2,'Explicit','scope'))
        with self.assertRaises(ValueError):self.hybrid.reserve('personal',model,'two',['m'],100,100)
        with self.assertRaises(ValueError):self.hybrid.grant('personal','groq',[model.model],2,1000,1000,2,'No expansion','scope')
    def test_invalid_key_404_and_schema_failure_are_distinct(self):
        model=self.cap('groq')
        id=self.hybrid.reserve('personal',model,'one',['m'],100,100);self.hybrid.finish(id,'failed',error=ModelFailure('authentication_failed','invalid',401))
        self.assertFalse(self.hybrid.healthy('groq',model.model))
        with self.hybrid.connect() as db:self.assertEqual(db.execute("SELECT disabled FROM hybrid_health WHERE provider='groq'").fetchone()[0],1)
        other=self.cap('cerebras');id=self.hybrid.reserve('personal',other,'two',['m'],100,100);self.hybrid.finish(id,'failed',error=ModelFailure('malformed_output','bad schema'))
        with self.hybrid.connect() as db:self.assertEqual(db.execute("SELECT last_error FROM hybrid_health WHERE provider='cerebras'").fetchone()[0],'malformed_output')
    def test_restart_never_replays_a_completed_or_unknown_attempt(self):
        model=self.cap();self.hybrid.reserve('personal',model,'one',['m'],100,100)
        restarted=SQLiteHybridStore(self.path);restarted.initialize()
        with self.assertRaises(ValueError):restarted.reserve('personal',model,'one',['m'],100,100)
        with self.assertRaises(ValueError):restarted.reserve('personal',model,'another',['m'],100,100)

    def test_returned_response_survives_crash_before_classification_persistence(self):
        model=self.cap();attempt=self.hybrid.reserve('personal',model,'committed',['m'],100,100)
        response=InferenceResult('{"frames":[],"changes":[]}','ollama',model.model,{'output_tokens':12},.2)
        self.hybrid.finish(attempt,'done',response)
        provider=Mock();restarted=HybridInference(SQLiteHybridStore(self.path),lambda _:provider)
        result,_=restarted.run('personal','committed',['m'],SYSTEM,{},SCHEMA,'en')
        self.assertEqual(result.text,response.text);provider.analyze.assert_not_called()
    def test_last_authorized_response_can_apply_after_budget_exhaustion_offline(self):
        model=self.cap('groq');self.hybrid.grant('personal','groq',[model.model],1,10000,4096,2,'Explicit last request')
        attempt=self.hybrid.reserve('personal',model,'last',['m'],100,100)
        self.hybrid.finish(attempt,'done',InferenceResult('{}','groq',model.model,{},.1))
        with self.hybrid.connect() as db:db.execute("UPDATE hybrid_providers SET last_state='offline' WHERE provider='groq'")
        self.assertEqual(self.hybrid.candidates('personal'),[])
        provider=Mock();result,_=HybridInference(self.hybrid,lambda _:provider).run('personal','last',['m'],SYSTEM,{},SCHEMA,'en')
        self.assertEqual(result.provider,'groq');provider.analyze.assert_not_called()
    def test_key_replacement_does_not_clear_project_quota_or_spend(self):
        model=self.cap('groq');attempt=self.hybrid.reserve('personal',model,'quota',['m'],100,100)
        self.hybrid.finish(attempt,'failed',error=ModelFailure('provider_rate_limited','quota',429))
        self.hybrid.credential_changed('groq')
        self.assertFalse(self.hybrid.healthy('groq',model.model))
        with self.hybrid.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM hybrid_attempts').fetchone()[0],1)
    def test_low_quality_frame_is_not_reused_forever_instead_of_cloud_escalation(self):
        model=self.cap();self.seed();self.messages.record_search_content('personal','personal-0','A new informational purpose requiring contextual interpretation of the actual current body.',complete=True)
        self.store.local_policy('personal',True,True,'Explicit')
        def analyze(m,system,data,schema,timeout=85):
            return InferenceResult(json.dumps({'frames':[dict(id='m0',category_id=None,primary_intent='Uncertain purpose',message_type='Notice',status=None,entity=None,sensitivities=[],confidence=.6,evidence=[dict(field='excerpt',quote='A new informational purpose')],attributes=[],related_ids=[])],'changes':[]}),m.provider,m.model,{},.1)
        provider=Mock();provider.analyze.side_effect=analyze;legacy=Mock(return_value={'status':'waiting_budget'})
        agent=HybridOrganizer(self.hybrid,self.engine,lambda _:provider,legacy)
        agent.step('personal')
        with self.hybrid.connect() as db:db.execute('UPDATE hybrid_tasks SET due=0')
        agent.step('personal');provider.analyze.assert_called_once();legacy.assert_called_once()
    def test_local_only_never_invokes_cloud_when_local_unavailable(self):
        self.seed();self.messages.record_search_content('personal','personal-0','Unfamiliar complete body that must remain safely queued.',complete=True)
        self.store.local_policy('personal',True,True,'Explicit');self.hybrid.preference('personal','local')
        legacy=Mock();HybridOrganizer(self.hybrid,self.engine,Mock(),legacy).step('personal');legacy.assert_not_called()
        self.assertEqual(self.hybrid.other_progress('personal')['total'],4)

    def test_compatible_adapter_requires_schema_and_disables_hidden_router_fallback(self):
        adapter=OpenAICompatibleProvider('openrouter','fixture-key');adapter.http=Mock()
        adapter.http.request.return_value={'model':'fixture:free','choices':[{'finish_reason':'stop','message':{'content':'{}'}}],'usage':{'prompt_tokens':10,'completion_tokens':5}}
        result=adapter.analyze(ModelCapability('openrouter','fixture:free',8192),SYSTEM,{},SCHEMA)
        payload=adapter.http.request.call_args.args[1]
        self.assertTrue(payload['response_format']['json_schema']['strict']);self.assertFalse(payload['provider']['allow_fallbacks']);self.assertEqual(payload['provider']['max_price'],{'prompt':0,'completion':0});self.assertEqual(result.provider,'openrouter')
        adapter.http.request.return_value={'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}
        with self.assertRaises(ModelFailure):adapter.analyze(ModelCapability('openrouter','fixture:free',8192),SYSTEM,{},SCHEMA)
    def test_free_discovery_rejects_nonzero_request_fees_and_unknown_pricing(self):
        adapter=OpenAICompatibleProvider('openrouter','fixture');adapter.http=Mock();adapter.http.request.return_value={'data':[{'id':'zero:free','pricing':{'prompt':'0','completion':'0'}},{'id':'fee:free','pricing':{'prompt':'0','completion':'0','request':'0.1'}},{'id':'unknown:free'}]}
        self.assertEqual([m['free'] for m in adapter.discover()],[True,False,False])
    def test_ollama_cloud_proxy_and_context_truncation_are_forbidden(self):
        provider=LocalModelProvider();provider.http=Mock();provider.http.request.return_value={'models':[{'name':'remote:cloud'},{'name':'hidden','remote_host':'https://cloud.example'},{'name':'installed','digest':'abc'}]}
        self.assertEqual([m['model'] for m in provider.discover()],['installed'])
        provider.http.request.reset_mock()
        with self.assertRaises(ModelFailure):provider.analyze(ModelCapability('ollama','installed',4096),SYSTEM,{'body':'x'*5000},SCHEMA)
        provider.http.request.assert_not_called()
    def test_only_one_bounded_known_availability_retry(self):
        model=self.cap('groq')
        for i in range(2):
            attempt=self.hybrid.reserve('personal',model,'same',['m'],100,100)
            self.hybrid.finish(attempt,'failed',error=ModelFailure('provider_unavailable','503'))
            with self.hybrid.connect() as db:db.execute('UPDATE hybrid_health SET until=0')
        with self.assertRaises(ValueError):self.hybrid.reserve('personal',model,'same',['m'],100,100)
    def test_secrets_are_owner_only_never_in_status_or_registry(self):
        secret=LocalSecretStore(self.root/'private');secret.put('groq','fixture-private-value')
        self.assertEqual(secret.get('groq'),'fixture-private-value');self.assertEqual((self.root/'private/groq').stat().st_mode&0o077,0)
        self.assertNotIn('fixture-private-value',json.dumps(self.hybrid.overview('personal')))
    def test_public_and_unauthenticated_lan_endpoints_rejected(self):
        with self.assertRaises(ValueError):local_endpoint('http://8.8.8.8:11434')
        with self.assertRaises(ValueError):local_endpoint('http://192.168.1.2:11434')
        with self.assertRaises(ValueError):local_endpoint('http://127.0.0.1:11434/private')
        self.assertEqual(local_endpoint('http://127.0.0.1:11434'),'http://127.0.0.1:11434')
    def test_other_all_messages_have_nonoverlapping_persisted_states(self):
        self.seed();self.messages.record_search_content('personal','personal-0','Meaningful unknown message body that has not yet been semantically interpreted.',complete=True)
        result=self.hybrid.other_progress('personal');self.assertEqual(result['total'],4);self.assertEqual(sum(result['counts'].values()),4)
        self.assertEqual(len(self.hybrid.task_rows('personal',[f'personal-{i}' for i in range(4)])),4)
        self.assertEqual(self.hybrid.other_progress('university')['total'],0)
        self.hybrid.task('personal','personal-0','local_processing','working')
        with self.hybrid.connect() as db:db.execute('UPDATE hybrid_tasks SET lease=0')
        self.hybrid.reconcile_other('personal');self.assertEqual(self.hybrid.task_rows('personal',['personal-0'])['personal-0']['status'],'waiting_cloud')
    def test_existing_local_and_gmail_actions_not_in_model_registry(self):
        from email_agent.application.local_organization_agent import LocalOrganizationAgent
        tools=LocalOrganizationAgent(self.engine,self.store).registry()
        self.assertIn('create_local_category',tools);self.assertFalse(any(x in tools for x in ('trash','send_email','shell','sql','unsubscribe')))
    def test_language_routing_uses_script_and_preserves_other_languages(self):
        self.assertEqual(message_language('تم استلام طلبك للوظيفة'),'ar');self.assertEqual(message_language('לא נוכל להתקדם עם מועמדותך'),'he');self.assertEqual(message_language('領収書です。合計'),'other')

    def test_wire_schema_binds_message_and_category_identity_without_repair(self):
        from email_agent.application.hybrid_ai import organization_schema
        bound=organization_schema({'messages':[{'id':'m0'}],'catalog':[{'id':'bills'}]},SCHEMA)
        fields=bound['properties']['frames']['items']['properties']
        self.assertEqual(fields['id']['enum'],['m0']);self.assertEqual(fields['category_id']['enum'],['bills',None]);self.assertEqual(bound['properties']['changes']['maxItems'],0)
        row=dict(account_id='personal',message_id='receipt',subject='Receipt',sender='different@example.invalid',headers={},body='Payment received. Total paid: 25 USD.',labels=[],content_complete=True,decision={})
        summary=public_summary(row,'m0','full')
        response=json.dumps({'frames':[dict(id='f1',category_id='bills',primary_intent='Payment receipt',message_type='Receipt',status='Paid',entity=None,sensitivities=['financial'],confidence=.96,evidence=[dict(field='excerpt',quote='Payment received.')],attributes=[],related_ids=[])],'changes':[]})
        with self.assertRaises(ValueError):ClassificationQualityGate().accept(response,[summary],{'m0':row},[{'id':'bills'}],self.cap())
    def test_observed_category_meaning_is_derived_from_own_current_frames(self):
        fixture.LocalAgentTests.prepare(self)
        with self.hybrid.connect() as db:db.execute("UPDATE mail_work SET status='done' WHERE stage='local'")
        own=next(c for c in self.store.model_catalog('personal') if c['id']=='other')
        other=next(c for c in self.store.model_catalog('university') if c['id']=='other')
        self.assertIn('Field report',own['observed_types']);self.assertEqual(other['observed_types'],[])
        self.assertNotIn('observed_types',self.store.catalog('personal')[0])
    def test_provider_revocation_blocks_old_and_new_authority_without_reset(self):
        self.hybrid.configure('gemini',True);self.hybrid.adopt_legacy();self.assertTrue(self.hybrid.account_allows('personal','gemini',True))
        self.hybrid.revoke('personal','gemini')
        self.assertFalse(self.hybrid.account_allows('personal','gemini',True));self.assertFalse(self.hybrid.account_allows('personal','gemini'))
        self.hybrid.grant('personal','gemini',['another-model'],1,10000,4096,2,'New independently approved scope')
        self.assertTrue(self.hybrid.account_allows('personal','gemini'));self.assertFalse(self.hybrid.account_allows('personal','gemini',True))
    def test_settings_changes_do_not_fabricate_worker_activity(self):
        self.seed();self.hybrid.preference('personal','local')
        self.assertIsNone(self.hybrid.other_progress('personal')['last_activity'])
        self.hybrid.task('personal','personal-0','waiting_cloud','Waiting for an authorized model')
        self.assertIsNotNone(self.hybrid.other_progress('personal')['last_activity'])
    def test_mixed_language_input_requires_each_script_to_be_evaluated(self):
        from email_agent.application.hybrid_ai import message_languages
        model=self.cap();self.hybrid.register(replace(model,quality={'en':1,'he':.5}))
        languages=message_languages('Thank you. '*20+'נדחה')
        self.assertIn('en',languages);self.assertIn('he',languages);self.assertEqual(ModelRouter(self.hybrid).select('personal',languages,100),[])
    def test_local_endpoint_cannot_silently_proxy_a_cloud_model(self):
        provider=LocalModelProvider();provider.http=Mock();provider.http.request.return_value={'model_info':{'general.architecture':'remote'},'remote_model':'external-model'}
        with patch('pathlib.Path.read_text',return_value='MemAvailable: 6000000 kB'):
            with self.assertRaises(ModelFailure) as error:provider.analyze(ModelCapability('ollama','looks-local',16384),SYSTEM,{'messages':[{'id':'m0'}],'catalog':[]},SCHEMA)
        self.assertEqual(error.exception.state,'local_model_not_verified');provider.http.request.assert_called_once()
    def test_each_provider_can_publish_a_new_semantic_category_and_preserve_manual(self):
        for provider in ('ollama','gemini','groq','openrouter','cerebras','mistral','openai'):
            with self.subTest(provider=provider):
                # Independent databases ensure no provider inherits another's
                # accepted frame, classification or prior category approval.
                case=HybridTests('test_auto_prefers_highest_measured_quality_not_provider_order');case.setUp()
                try:case.assert_provider_publication(provider)
                finally:case.doCleanups()
    def assert_provider_publication(self,provider):
        cap=self.cap(provider);self.seed();self.store.local_policy('personal',True,True,'Explicit fixture consent')
        for i in range(4):self.messages.record_search_content('personal',f'personal-{i}','Habitat sampling report from this expedition. Independent environmental survey observations.',complete=True)
        self.classifier.manual('personal','personal-3','other','User explicitly chose Other')
        model=Mock()
        def analyze(m,system,data,schema,timeout=85):
            return InferenceResult(json.dumps(dict(frames=[dict(id=v['id'],category_id=None,primary_intent='Field research',message_type='Field report',status=None,entity=None,sensitivities=['personal'],confidence=.96,evidence=[dict(field='excerpt',quote='Habitat sampling report from this expedition.')],attributes=[],related_ids=[]) for v in data['messages']],changes=[])),provider,m.model,{},.1)
        model.analyze.side_effect=analyze;agent=HybridOrganizer(self.hybrid,self.engine,lambda _:model)
        for _ in range(3):agent.step('personal')
        self.engine.organize_local('personal',200)
        published=[c for c in self.store.catalog('personal') if c['id'].startswith('sem_')]
        self.assertEqual(len(published),1)
        self.assertTrue(published[0]['icon_key'])
        manual=self.classifier.assignments.message_details('personal','personal-3')
        self.assertEqual(manual['category_id'],'other')
        self.assertEqual(manual['source'],'manual')
        current=self.hybrid.other_progress('personal');self.assertEqual(current['total'],1)



    def test_cloud_ready_task_is_not_starved_behind_ineligible_queue_head(self):
        cap=self.cap('mistral',.98)
        self.hybrid.register(replace(cap,quality={'en':.98}),time.time()+86400)
        for i in range(17):
            id=f'queued-{i:02d}';self.add(id,'Unsorted message')
            body='هذه رسالة باللغة العربية تحتاج إلى تحليل وتصنيف مضمونها.' if i<16 else 'Please review the available message content and project correspondence.'
            self.messages.record_search_content('personal',id,body,complete=True)
        self.hybrid.reconcile_other('personal')
        self.assertNotIn('queued-16',self.hybrid.select('personal',12))
        agent=HybridOrganizer(self.hybrid,self.engine,Mock())
        agent.refresh_eligibility('personal',100)
        self.assertEqual(self.hybrid.select('personal',1),['queued-16'])
        with self.hybrid.connect() as db:
            task=db.execute("SELECT reason,provider FROM hybrid_tasks WHERE message_id='queued-16'").fetchone()
        self.assertEqual(task['reason'],'Ready for eligible provider');self.assertIsNone(task['provider'])
    def test_expired_provider_circuit_recovers_only_for_proven_language(self):
        cap=self.cap('mistral',.98)
        self.hybrid.register(replace(cap,quality={'en':.98}),time.time()+86400)
        with self.hybrid.connect() as db:db.execute("INSERT INTO hybrid_health VALUES('mistral','*',1,?,0,'provider_rate_limited')",(time.time()-1,))
        self.assertTrue(ModelRouter(self.hybrid).select('personal','en',100))
        self.assertEqual(ModelRouter(self.hybrid).select('personal',('en','he'),100),[])

    def test_candidate_enumeration_releases_read_cursor_before_nested_connections(self):
        import sqlite3
        self.cap(model='first');self.cap(model='second')
        def policy(account,model):
            with sqlite3.connect(self.path,timeout=.2) as writer:
                writer.execute("INSERT INTO hybrid_events(event,detail) VALUES('concurrent-status-fixture','safe')")
            return False
        with patch.object(self.hybrid,'payment_allowed',side_effect=policy):
            self.assertEqual(self.hybrid.candidates('personal'),[])

if __name__=='__main__':unittest.main()


class ProviderConcurrencyTests(unittest.TestCase):
    setUp=HybridTests.setUp
    add=HybridTests.add
    seed=HybridTests.seed
    cap=HybridTests.cap

    def test_provider_capacity_is_independent_and_task_claims_are_exclusive(self):
        local=self.cap('ollama');cloud=self.cap('gemini')
        a=self.hybrid.reserve('personal',local,'local-task',['local-message'],100,100)
        self.assertFalse(self.hybrid.capacity('ollama'));self.assertTrue(self.hybrid.capacity('gemini'))
        b=self.hybrid.reserve('personal',cloud,'cloud-task',['cloud-message'],100,100)
        self.assertFalse(self.hybrid.capacity('gemini'))
        with self.assertRaises(ValueError):self.hybrid.reserve('personal',cloud,'other-cloud-task',['another-message'],100,100)
        with self.hybrid.connect() as db:db.execute("INSERT INTO hybrid_tasks(account_id,message_id,status) VALUES('personal','same','waiting_cloud')")
        self.assertTrue(self.hybrid.claim_messages('personal',['same']))
        self.assertFalse(SQLiteHybridStore(self.path).claim_messages('personal',['same']))
        self.hybrid.release_messages('personal',['same']);self.assertTrue(self.hybrid.claim_messages('personal',['same']))
        self.hybrid.finish(a,'interrupted');self.hybrid.finish(b,'interrupted')

    def test_auto_cloud_first_and_busy_cloud_does_not_block_independent_fallback(self):
        local=self.cap('ollama',1.0);cloud=self.cap('gemini',.95)
        self.assertEqual(ModelRouter(self.hybrid).select('personal','en',100)[0].provider,'gemini')
        a=self.hybrid.reserve('personal',cloud,'busy',['message'],100,100)
        self.assertEqual(ModelRouter(self.hybrid).select('personal','en',100)[0].provider,'ollama')
        self.hybrid.finish(a,'interrupted')

    def test_compound_daily_retry_delay_and_structured_provider_retry_are_not_minutes(self):
        from email_agent.application.model_errors import retry_delay,classify_error
        self.assertAlmostEqual(retry_delay('Please retry in 18h14m18.9s.'),65658.9)
        self.assertEqual(retry_delay('try again in 2m30s'),150)
        error=Exception('quota');error.status_code=429;error.message='Quota exhausted';error.details={'error':{'details':[{'retryDelay':'65000s'}]}}
        self.assertEqual(classify_error(error).retry_after,65000)

    def test_zai_free_flash_only_and_json_mode_no_live_calls(self):
        provider=OpenAICompatibleProvider('zai','fixture-private-key')
        provider.http.request=Mock(return_value={'data':[{'id':'glm-4.7'},{'id':'glm-4.7-flash'}]})
        models=provider.discover();free=[m for m in models if m['free']]
        self.assertEqual({m['model'] for m in free},{'glm-4.7-flash','glm-4.5-flash'})
        self.assertEqual(next(m for m in free if m['model']=='glm-4.7-flash')['context'],200000)
        paid=ModelCapability('zai','glm-4.7',200000,free=True)
        self.assertFalse(self.hybrid.payment_allowed('personal',paid))
        provider.http.request=Mock(return_value={'choices':[{'finish_reason':'stop','message':{'content':'{}'}}],'usage':{}})
        model=ModelCapability('zai','glm-4.7-flash',200000,free=True,output_mode='json_object')
        provider.analyze(model,'untrusted mail boundaries',{},SCHEMA)
        payload=provider.http.request.call_args.args[1]
        self.assertEqual(payload['response_format'],{'type':'json_object'})
        self.assertEqual(payload['thinking'],{'type':'disabled'})
        self.assertFalse(self.hybrid.provider('zai')['enabled'])
        self.assertFalse(self.hybrid.account_allows('university','zai'))

"""Qualification lifecycle and standing Free-only isolation. No real providers."""
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
import test_hybrid_ai as fixture
from email_agent.application.hybrid_ai import ModelCapability,InferenceResult,ModelRouter
from email_agent.application.provider_qualification import ProviderQualification,request_data
from email_agent.application.qualification_rubric import score_frame
from email_agent.infrastructure.database.qualification_store import SQLiteQualificationStore
from email_agent.infrastructure.ai.usage import SQLiteAIQuota

CASES=json.loads(Path('src/email_agent/resources/semantic_gold_v3.json').read_text())['cases']

class QualificationTests(unittest.TestCase):
    setUp=fixture.HybridTests.setUp
    seed=fixture.HybridTests.seed
    add=fixture.HybridTests.add
    def setup_engine(self):
        self.cap=ModelCapability('groq','fixture-qualified',64000,free=True)
        self.hybrid.register(self.cap);self.hybrid.configure('groq',True)
        self.q=SQLiteQualificationStore(self.hybrid,CASES,lambda _:True);self.q.initialize()
        self.adapter=Mock();self.adapter.discover.return_value=[dict(model=self.cap.model,context=64000,free=True)]
        def response(cap,system,data,schema,**kwargs):
            frames=[]
            for m in data['messages']:
                case=next(c for c in CASES if c['subject']==m['subject']);g=case['gold']
                attrs=[]
                if g.get('workflow'):attrs.append(dict(key='workflow_stage',value=g['workflow']))
                if g.get('payment'):attrs.append(dict(key='payment_status',value=g['payment']))
                if g['categories']==['job_applications']:attrs.append(dict(key='application_context',value='submitted'))
                excerpt=m['excerpt'];evidence=[dict(field='excerpt',quote=excerpt[:70])]
                if excerpt[-60:]!=excerpt[:70]:evidence.append(dict(field='excerpt',quote=excerpt[-60:]))
                frames.append(dict(id=m['id'],category_id=g['categories'][0],primary_intent='Field research' if case.get('cluster') else 'Grounded message purpose',message_type='field_report' if isinstance(g['document_type'],dict) else g['document_type'] or 'correspondence',status=g.get('workflow') or g.get('payment'),entity=None,sensitivities=[g['sensitivity']],confidence=.99,evidence=evidence,attributes=attrs,related_ids=[]))
            changes=[]
            if all('River' in m['subject'] or 'נחל' in m['subject'] or 'النهر' in m['subject'] for m in data['messages']):changes=[dict(action='create',name='Field research',sources=[],message_ids=[m['id'] for m in data['messages']],dimension=None,reason='Independent current field reports share a research purpose')]
            return InferenceResult(json.dumps(dict(frames=frames,changes=changes)),'groq',cap.model,{'prompt_tokens':100,'output_tokens':300},.01)
        self.adapter.analyze.side_effect=response
        self.engine=ProviderQualification(self.q,lambda _:self.adapter,CASES)
        self.plan=self.q.preview(['groq'])
    def test_no_calls_before_gold_and_scope_approval_then_restart_safe_certification(self):
        self.setup_engine();self.assertEqual(self.engine.step()['status'],'idle');self.adapter.discover.assert_not_called()
        with self.assertRaises(ValueError):self.q.approve(self.plan['id'],'wrong-hash',['groq'],20,True)
        self.q.approve(self.plan['id'],self.plan['gold_hash'],['groq'],20,True)
        for _ in range(20):self.engine.step()
        state=self.q.state(self.plan['id']);self.assertEqual(state['status'],'completed',state)
        self.assertEqual(self.adapter.analyze.call_count,18)
        certificate=state['certificates'][0];self.assertTrue(certificate['quality_qualified'],certificate['languages'])
        self.assertEqual(set(certificate['category_quality']),{'en','ar','he'})
        self.assertEqual(self.hybrid.candidates('personal'),[])  # Synthetic permission is not mailbox permission.
        self.hybrid.authorize_standing('personal',['groq'],'Explicit fixture full-body Free-only access')
        self.assertEqual(len(self.hybrid.candidates('personal')),1);self.assertEqual(self.hybrid.candidates('university'),[])
        restarted=ProviderQualification(SQLiteQualificationStore(self.hybrid,CASES,lambda _:True),lambda _:self.adapter,CASES)
        restarted.step();self.assertEqual(self.adapter.analyze.call_count,18)
        self.hybrid.revoke('personal','groq');self.assertEqual(self.hybrid.candidates('personal'),[])
    def test_standing_free_only_removes_app_cap_without_enabling_paid_or_other_accounts(self):
        quota=SQLiteAIQuota(self.root/'data'/'quota.db');quota.configure(0)
        self.assertFalse(quota.reserve('personal','hybrid:groq'))
        quota.authorize_free_automation(['groq'],'Explicit user Free-only standing scope')
        for _ in range(25):self.assertTrue(quota.reserve('personal','hybrid:groq'))
        self.assertFalse(quota.reserve('personal','hybrid:openai'));self.assertFalse(quota.reserve('personal','chat-anything'))
        self.assertEqual(quota.status()['used'],25)
    def test_receipt_category_alternative_requires_correct_independent_dimensions(self):
        case=next(c for c in CASES if c['id']=='receipt-en')
        raw=dict(category_id='orders',message_type='Receipt',status='paid',entity=None,sensitivities=['financial'],evidence=[dict(field='excerpt',quote='Total paid')],attributes=[])
        good=score_frame(case,raw,True,[]);self.assertTrue(good['correct'])
        self.assertFalse(score_frame(case,dict(raw,status='completed'),True,[])['category_correct'])
        self.assertFalse(score_frame(case,dict(raw,message_type='Invoice'),True,[])['document_correct'])
    def test_input_envelopes_fit_approved_bounds_without_truncation(self):
        self.assertEqual(len(CASES),54)
        for i in range(0,len(CASES),3):
            _,summaries,_,_,bound=request_data(CASES[i:i+3]);self.assertLessEqual(bound,16384);self.assertTrue(all(s['body_complete'] for s in summaries))

    def test_certificate_recovery_uses_committed_response_without_replay(self):
        self.setup_engine();self.q.approve(self.plan['id'],self.plan['gold_hash'],['groq'],20,True)
        self.engine.step();self.engine.step()
        with self.hybrid.connect() as db:db.execute('DELETE FROM qualification_certificates')
        plan=self.q.runnable();self.q.claim(plan)
        self.assertEqual(self.adapter.analyze.call_count,1)
        self.assertEqual(self.q.state(self.plan['id'])['certificates'][0]['requests'],1)
    def test_unknown_inflight_outcome_is_blocked_and_provider_finishes(self):
        self.setup_engine();self.q.approve(self.plan['id'],self.plan['gold_hash'],['groq'],20,True);self.engine.step()
        with self.hybrid.connect() as db:
            db.execute("UPDATE qualification_jobs SET status='running',lease=1")
        self.q.claim(self.q.runnable())
        state=self.q.state(self.plan['id']);self.assertEqual(state['status'],'completed');self.assertEqual(self.adapter.analyze.call_count,0)
        self.assertTrue(all(r['status']=='blocked' for r in state['jobs']))
    def test_routing_prefers_measured_quality_and_uses_next_provider_when_busy(self):
        fixture.HybridTests.cap(self,'groq',.97,'fast')
        fixture.HybridTests.cap(self,'mistral',.96,'less-busy')
        self.hybrid.model_load=Mock(return_value={('groq','fast'):{'recent_requests':40,'mean_seconds':1},('mistral','less-busy'):{'recent_requests':0,'mean_seconds':2}})
        self.assertEqual(ModelRouter(self.hybrid).select('personal','en',100)[0].provider,'groq')
        busy=next(m for m in self.hybrid.candidates('personal') if m.provider=='groq')
        self.hybrid.reserve('personal',busy,'busy-independent',['busy-message'],100,100)
        self.assertEqual(ModelRouter(self.hybrid).select('personal','en',100)[0].provider,'mistral')
    def test_entity_grounding_uses_only_actually_supplied_sources(self):
        from email_agent.application.qualification_rubric import score_frame
        case=next(c for c in CASES if c['id']=='receipt-en')
        frame=dict(category_id='bills',message_type='Receipt',status='Paid',entity='synthetic.invalid',sensitivities=['financial'],evidence=[dict(field='excerpt',quote='Total paid')],attributes=[])
        self.assertTrue(score_frame(case,frame,True,[],source_texts=['synthetic.invalid'])['entity_grounded'])
        self.assertFalse(score_frame(case,dict(frame,entity='Fabricated Corporation'),True,[],source_texts=['synthetic.invalid'])['entity_grounded'])
    def test_mistral_text_chunks_exclude_thinking_and_use_supported_reasoning(self):
        from email_agent.infrastructure.ai.hybrid_providers import OpenAICompatibleProvider
        p=OpenAICompatibleProvider('mistral','synthetic-test-key-never-used')
        p.http.request=Mock(return_value={'model':'mistral-large-4','choices':[{'finish_reason':'stop','message':{'content':[{'type':'thinking','thinking':'must not persist this'},{'type':'text','text':'{"frames":[],"changes":[]}'}]}}]})
        response=p.analyze(ModelCapability('mistral','mistral-large-4',64000), 'Public synthetic',{'messages':[],'catalog':[]},fixture.SCHEMA)
        self.assertNotIn('temperature',p.http.request.call_args.args[1]);self.assertNotIn('must not persist',response.text);self.assertEqual(p.http.request.call_args.args[1]['reasoning_effort'],'low')
    def test_contract_covers_exact_public_catalog_and_planning_input(self):
        from email_agent.application.provider_qualification import qualification_contract
        from unittest.mock import patch
        before=qualification_contract(CASES)
        with patch('email_agent.application.provider_qualification.ORGANIZATION_PLANNING','Changed typed planning contract'):
            self.assertNotEqual(qualification_contract(CASES),before)
    def test_two_transient_connection_failures_advance_model_without_quota_rotation(self):
        from email_agent.application.model_errors import ModelFailure
        from unittest.mock import patch
        self.setup_engine();second=replace(self.cap,model='second-fixture');self.hybrid.register(second)
        self.adapter.discover.return_value.append(dict(model=second.model,context=64000,free=True))
        self.plan=self.q.preview(['groq']);self.q.approve(self.plan['id'],self.plan['gold_hash'],['groq'],20,True)
        self.engine.step();job=self.q.claim(self.q.runnable())
        failure=ModelFailure('network_unavailable','Synthetic timeout')
        self.q.fail_job(job,failure,1);self.q.fail_job(job,failure,2)
        import time
        with patch('email_agent.infrastructure.database.qualification_store.time.time',return_value=time.time()+1000):
            next_job=self.q.claim(self.q.runnable())
        self.assertEqual(next_job['candidate'],1)
        self.assertEqual(self.q.state(self.plan['id'])['providers'][0]['stage'],'testing')
    def test_mistral_provider_declared_aliases_pin_canonical_model_without_guessing(self):
        from email_agent.infrastructure.ai.hybrid_providers import OpenAICompatibleProvider
        p=OpenAICompatibleProvider('mistral','synthetic-test-key-never-used')
        p.http.request=Mock(return_value={'data':[{'id':'versioned-fixture','aliases':['latest-fixture'],'capabilities':{'completion_chat':True}}, {'id':'latest-fixture','aliases':[],'capabilities':{'completion_chat':True}}]})
        self.assertEqual([m['model'] for m in p.discover()],['versioned-fixture'])
    def test_public_synthetic_error_diagnostics_remove_credentials(self):
        from email_agent.infrastructure.ai.hybrid_providers import JSONTransport
        from email_agent.application.model_errors import ModelFailure
        from unittest.mock import MagicMock,patch
        response=MagicMock();response.__enter__.return_value=response;response.status_code=400;response.headers={}
        response.iter_content.return_value=iter([json.dumps({'message':'Unsupported synthetic schema. synthetic-private-key-42','type':'invalid_request_invalid_args','code':'3051'}).encode()])
        session=MagicMock();session.__enter__.return_value=session;session.request.return_value=response
        client=JSONTransport('https://synthetic.invalid','synthetic-private-key-42');client.public_diagnostics=True
        with patch('email_agent.infrastructure.ai.hybrid_providers.requests.Session',return_value=session):
            with self.assertRaises(ModelFailure) as caught:client.request('/synthetic',{})
        diagnostic=caught.exception.diagnostic();self.assertNotIn('synthetic-private-key-42',json.dumps(diagnostic));self.assertIn('Unsupported synthetic schema',diagnostic['context']['synthetic_error'])
    def test_novel_semantic_family_requires_grounded_creation_and_preserves_safety(self):
        case=next(c for c in CASES if c['id']=='research-en-0')
        raw=dict(category_id='other',message_type='environmental_report',status=None,entity=None,sensitivities=['personal'],evidence=[dict(field='excerpt',quote='river water quality')],attributes=[])
        self.assertFalse(score_frame(case,raw,True,[])['category_correct'])
        self.assertTrue(score_frame(case,raw,True,[],new_category_plan=True)['correct'])
        self.assertFalse(score_frame(case,dict(raw,message_type='invoice'),True,[],new_category_plan=True)['document_correct'])
        self.assertFalse(score_frame(case,dict(raw,sensitivities=['promotional']),True,[],new_category_plan=True)['correct'])
        self.assertFalse(score_frame(case,dict(raw,entity='Fabricated Company'),True,[],new_category_plan=True)['correct'])
        self.assertFalse(score_frame(case,raw,False,['evidence_quote_not_in_input'],new_category_plan=True)['correct'])
    def test_labs_entitlement_does_not_disable_other_models_or_erase_quota(self):
        import time
        with self.hybrid.connect() as db:
            attempt=db.execute("INSERT INTO hybrid_attempts(account_id,provider,model,task_key,status,error,created_at) VALUES('personal','mistral','labs-fixture','public:credential:0','failed','authentication_failed',?)",(time.time(),)).lastrowid
            db.execute('INSERT INTO hybrid_attempt_diagnostics VALUES(?,?)',(attempt,json.dumps({'http_status':403,'kind':'authentication_failed','context':{'type':'labs_not_enabled'}})))
            db.execute("INSERT INTO hybrid_health VALUES('mistral','labs-fixture',1,0,1,'authentication_failed')")
            db.execute("INSERT INTO hybrid_health VALUES('mistral','*',1,0,1,'authentication_failed')")
            db.execute("INSERT INTO hybrid_health VALUES('mistral','quota-fixture',1,?,0,'provider_rate_limited')",(time.time()+600,))
        self.hybrid.initialize()
        with self.hybrid.connect() as db:
            self.assertEqual(db.execute('SELECT error FROM hybrid_attempts WHERE id=?',(attempt,)).fetchone()[0],'model_access_denied')
            self.assertEqual(db.execute("SELECT last_error FROM hybrid_health WHERE provider='mistral' AND model='*'").fetchone()[0],'provider_rate_limited')
        self.assertFalse(self.hybrid.healthy('mistral','ordinary-fixture')) # Preserved actual quota.
    def test_genuine_invalid_credentials_are_not_cleared_by_entitlement_repair(self):
        import time
        with self.hybrid.connect() as db:
            lab=db.execute("INSERT INTO hybrid_attempts(account_id,provider,model,task_key,status,error,created_at) VALUES('personal','mistral','labs-fixture','lab:credential:0','failed','authentication_failed',?)",(time.time(),)).lastrowid
            db.execute('INSERT INTO hybrid_attempt_diagnostics VALUES(?,?)',(lab,json.dumps({'http_status':403,'context':{'type':'labs_not_enabled'}})))
            db.execute("INSERT INTO hybrid_attempts(account_id,provider,model,task_key,status,error,created_at) VALUES('personal','mistral','ordinary','private:credential:0','failed','authentication_failed',?)",(time.time(),))
            db.execute("INSERT INTO hybrid_health VALUES('mistral','*',1,0,1,'authentication_failed')")
        self.hybrid.initialize()
        with self.hybrid.connect() as db:self.assertEqual(db.execute("SELECT disabled,last_error FROM hybrid_health WHERE provider='mistral' AND model='*'").fetchone()[1],'authentication_failed')
    def test_groq_minute_limit_uses_actual_retry_after_not_an_assumed_daily_reset(self):
        from email_agent.infrastructure.ai.hybrid_providers import JSONTransport
        from email_agent.application.model_errors import ModelFailure
        from unittest.mock import MagicMock,patch
        response=MagicMock();response.__enter__.return_value=response;response.status_code=429;response.headers={}
        response.iter_content.return_value=iter([json.dumps({'error':{'message':'tokens per minute (TPM). Please try again in11.445s.'.replace('in11','in 11'),'type':'tokens','code':'rate_limit_exceeded'}}).encode()])
        session=MagicMock();session.__enter__.return_value=session;session.request.return_value=response
        with patch('email_agent.infrastructure.ai.hybrid_providers.requests.Session',return_value=session):
            with self.assertRaises(ModelFailure) as caught:JSONTransport('https://synthetic.invalid').request('/synthetic',{})
        self.assertEqual(caught.exception.safe_context['limit_window'],'minute');self.assertAlmostEqual(caught.exception.retry_after,11.445)
    def test_proven_native_schema_rejection_gets_only_one_persisted_json_mode_recovery(self):
        import time
        from email_agent.application.model_errors import ModelFailure
        from unittest.mock import patch
        self.setup_engine();self.q.approve(self.plan['id'],self.plan['gold_hash'],['groq'],20,True);self.engine.step()
        plan=self.q.runnable();job=self.q.claim(plan)
        f=ModelFailure('provider_request_rejected','Public synthetic incompatible native grammar',400);f.safe_context={'code':'json_validate_failed'}
        with self.hybrid.connect() as db:db.execute("UPDATE qualification_jobs SET status='retry' WHERE plan_id=? AND id<>?",(plan['id'],job['id']))
        self.q.fail_job(job,f,1)
        with self.hybrid.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM qualification_jobs WHERE plan_id=? AND status='retry' AND json_extract(capability,'$.output_mode')<>'json_object'",(plan['id'],)).fetchone()[0],0)
        with patch('email_agent.infrastructure.database.qualification_store.time.time',return_value=time.time()+61):recovered=self.q.claim(plan)
        self.assertEqual(recovered['id'],job['id']);self.assertEqual(recovered['capability']['output_mode'],'json_object')
        self.q.fail_job(recovered,f,2)
        with self.hybrid.connect() as db:self.assertEqual(db.execute('SELECT status FROM qualification_jobs WHERE id=?',(job['id'],)).fetchone()[0],'failed')
        self.adapter.analyze.assert_not_called()
    def test_payment_facts_respect_negation_and_current_body_in_three_languages(self):
        from email_agent.application.payment_evidence import payment_state
        cases=[('Invoice','Payment of 100 USD is due Friday. This invoice has not been paid.','Payment due'),('Receipt','Total paid:29.90 USD. No further payment is due.','Paid'),('إيصال','تم دفع مبلغ29.90 دولار. لا توجد دفعة مستحقة.','Paid'),('فاتورة','يجب دفع100 دولار. لم يتم سداد هذه الفاتورة.','Payment due'),('חשבונית','החשבונית טרם שולמה. יש לשלם100 דולר.','Payment due'),('קבלה','סכום ששולם:29.90 דולר. אין צורך לשלם שוב.','Paid'),('Payment','If you have paid, keep the receipt.',None),('Payment','Total paid 100 USD. Balance due 20 USD.',None),('Payment','Not yet paid.\n> Previous: total paid 100 USD.','Payment due')]
        for subject,body,expected in cases:self.assertEqual(payment_state(subject,body),expected,(subject,body))
    def test_real_multi_message_tools_create_publish_assign_and_deduplicate_for_each_provider(self):
        from email_agent.application.hybrid_ai import HybridInference,ClassificationQualityGate
        from email_agent.application.hybrid_group_organizer import HybridGroupOrganizer
        from email_agent.application.semantic_mail import local_frame,SEMANTIC_VERSION
        import time
        for provider in ('gemini','groq','openrouter','cerebras','mistral','ollama'):
            with self.subTest(provider=provider):
                self.setUp();self.seed();self.store.local_policy('personal',True,True,'Explicit fixture organization')
                for i in range(3):
                    id='personal-'+str(i);self.messages.record_search_content('personal',id,'Independent river field survey. Habitat sampling report from this expedition.',complete=True)
                    row=self.store.message_row('personal',id);frame=dict(local_frame(row),source='local',understood=True,analysis_reason=None,primary_intent='Field research',message_type='field_report',confidence=.99,evidence=[{'field':'excerpt','quote':'Habitat sampling report from this expedition.'}])
                    self.store.put_frame(row,frame,SEMANTIC_VERSION)
                cap=fixture.HybridTests.cap(self,provider,.99,'typed-group-fixture')
                cap=replace(cap,workflow_quality={'en':1,'he':1,'ar':1},task_quality={lang:{'taxonomy':1} for lang in ('en','he','ar')})
                self.hybrid.register(cap,time.time()+86400)
                adapter=Mock()
                def answer(model,system,data,schema,**kwargs):
                    return InferenceResult(json.dumps({'frames':[dict(id=m['id'],category_id=None,primary_intent='Field research',message_type='field_report',status=None,entity=None,sensitivities=['personal'],confidence=.99,evidence=[{'field':'excerpt','quote':'Habitat sampling report from this expedition.'}],attributes=[],related_ids=[]) for m in data['messages']],'changes':[dict(action='create',name='Habitat studies',sources=['other'],message_ids=[m['id'] for m in data['messages']],dimension='category',reason='Independent field reports document one coherent research purpose',icon_key=None if model.provider=='groq' else 'file-text')]}),model.provider,model.model,{},.01)
                adapter.analyze.side_effect=answer
                engine=HybridGroupOrganizer(self.hybrid,self.engine,HybridInference(self.hybrid,lambda _:adapter),ClassificationQualityGate())
                from unittest.mock import patch
                with patch('email_agent.domain.category_icon_resolver.resolve_category_icon',return_value='folder-open'):
                    result=engine.step('personal')
                self.assertEqual(result['status'],'group_organized',result);self.assertEqual(result['moved'],3,result)
                category=next(c for c in self.management.categories('personal') if c['name']=='Habitat studies')
                if provider=='groq':
                    self.assertEqual(category['icon_key'],'folder-open')
                    self.assertTrue(self.store.repair_generated_icons({category['id']:'file-text'}))
                    category=next(c for c in self.management.categories('personal') if c['id']==category['id'])
                self.assertEqual(category['icon_key'],'file-text')
                self.assertEqual(self.store.repair_generated_icons({category['id']:'bell'}),[])
                self.assertEqual(self.management.mailbox('personal',category['id'],'all')['total'],3)
                self.assertNotIn(category['id'],{c['id'] for c in self.management.categories('university')})
                self.assertIsNone(engine.step('personal'));adapter.analyze.assert_called_once()
                with self.hybrid.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM hybrid_group_runs WHERE status='done'").fetchone()[0],1)
    def test_compatible_nullable_schema_preserves_null_and_exact_category_membership(self):
        from email_agent.infrastructure.ai.hybrid_providers import compatible_schema
        schema=compatible_schema({'type':['string','null'],'enum':['bills',None],'maxLength':40})
        self.assertEqual(schema['anyOf'][0],{'type':'string','enum':['bills'],'maxLength':40})
        self.assertEqual(schema['anyOf'][1],{'type':'null','enum':[None]})
    def test_official_free_pool_extension_preserves_existing_jobs_and_excludes_paid(self):
        self.setup_engine()
        from email_agent.infrastructure.ai.free_catalog import verified_free
        self.assertTrue(verified_free('gemini-3.8-flash'));self.assertFalse(verified_free('gemini-4-argon'))
        self.hybrid.register(ModelCapability('gemini','gemini-3.5-flash-lite',64000,free=True));self.hybrid.register(ModelCapability('gemini','gemini-3.8-flash',64000))
        self.plan=self.q.preview(['gemini'])
        with self.hybrid.connect() as db:
            p=json.loads(db.execute('SELECT payload FROM qualification_plans WHERE id=?',(self.plan['id'],)).fetchone()[0]);p['providers'][0]['allowed_pool']=['gemini-3.5-flash-lite'];p['providers'][0]['candidate_limit']=1
            db.execute('UPDATE qualification_plans SET payload=? WHERE id=?',(json.dumps(p),self.plan['id']))
        self.q.approve(self.plan['id'],self.plan['gold_hash'],['gemini'],20,True)
        extended=self.q.refresh_verified_free_pool(self.plan['id'],'gemini','Explicit fixture all-verified-Free standing scope')
        self.assertIn('gemini-3.8-flash',extended['selection'][0]['allowed_pool']);self.assertNotIn('gemini-4-argon',extended['selection'][0]['allowed_pool'])
        self.assertEqual(extended['per_request_output'],2048)

    def test_repaired_wire_recheck_preserves_success_and_is_once_per_adapter_version(self):
        import time
        self.setup_engine();self.q.approve(self.plan['id'],self.plan['gold_hash'],['groq'],20,True);self.engine.step();self.engine.step()
        with self.hybrid.connect() as db:
            good=db.execute("SELECT id FROM qualification_jobs WHERE status='done'").fetchone()[0]
            bad=db.execute("SELECT id FROM qualification_jobs WHERE status='pending' ORDER BY part LIMIT 1").fetchone()[0]
            attempt=db.execute("INSERT INTO hybrid_attempts(account_id,provider,model,task_key,status,error,created_at) VALUES('personal','groq',?,'qual:fixture','failed','provider_request_rejected',?)",(self.cap.model,time.time())).lastrowid
            db.execute('INSERT INTO hybrid_attempt_diagnostics VALUES(?,?)',(attempt,json.dumps({'http_status':400,'context':{'code':'json_validate_failed'}})))
            db.execute("UPDATE qualification_jobs SET status='failed',error='provider_request_rejected',attempt_id=? WHERE id=?",(attempt,bad))
        self.assertEqual(self.q.recheck_repaired_wire(self.plan['id'],'Approved repair')['requeued'],1)
        self.assertEqual(self.q.recheck_repaired_wire(self.plan['id'],'Repeated repair')['requeued'],0)
        with self.hybrid.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM qualification_jobs WHERE id=?',(good,)).fetchone()[0],'done')
            self.assertEqual(db.execute('SELECT status FROM qualification_jobs WHERE id=?',(bad,)).fetchone()[0],'retry')

    def test_synthetic_qualification_is_not_the_active_mailbox_model(self):
        import time
        with self.hybrid.connect() as db:
            for task,model in [('mailbox-body','actual-local'),('qual:fixture','synthetic-cloud'),('probe:fixture','synthetic-probe'),('eval:fixture','synthetic-eval')]:
                db.execute("INSERT INTO hybrid_attempts(account_id,provider,model,task_key,status,created_at) VALUES('personal','ollama',?,?,'done',?)",(model,task,time.time()))
        self.assertEqual(self.hybrid.overview('personal')['active']['model'],'actual-local')

    def test_qualification_backlog_does_not_globally_pause_authorized_local_processing(self):
        from email_agent.application.hybrid_organizer import HybridOrganizer
        from unittest.mock import patch
        agent=HybridOrganizer(self.hybrid,Mock(),Mock(),qualification_ready=lambda:True)
        agent.step=Mock(return_value={'status':'waiting_model'})
        with patch('email_agent.application.hybrid_group_organizer.HybridGroupOrganizer.step',return_value=None):
            agent.batch('personal')
        agent.step.assert_called_once()

    def test_routine_category_does_not_require_novel_taxonomy_or_document_authority(self):
        from email_agent.application.qualification_rubric import summarize
        self.setup_engine();self.q.approve(self.plan['id'],self.plan['gold_hash'],['groq'],20,True)
        for _ in range(20):self.engine.step()
        outcomes=self.q.state(self.plan['id'])['certificates'][0]['outcomes']
        for outcome in outcomes:
            if outcome['case'].startswith('research-'):outcome.update(category_correct=False,document_correct=False)
            elif outcome['case'].startswith(('receipt-','payment-','invoice-')):outcome['document_correct']=False
        assessed=summarize(CASES,outcomes,[],True)
        self.assertEqual(assessed['category_quality']['en'],1)
        self.assertFalse(assessed['languages']['en']['qualified'])
        self.assertEqual(assessed['task_quality']['en']['document'],0)
        next(o for o in outcomes if o['case']=='receipt-en')['protection_recalled']=False
        self.assertEqual(summarize(CASES,outcomes,[],True)['category_quality']['en'],0)
    def test_local_network_status_does_not_depend_on_inference_quality_or_cooldown(self):
        import time
        with self.hybrid.connect() as db:
            db.execute("UPDATE hybrid_providers SET last_state='metadata_connected',last_checked=? WHERE provider='ollama'",(time.time(),))
            db.execute("INSERT INTO hybrid_health VALUES('ollama','qwen3.5:4b',1,?,0,'network_unavailable')",(time.time()+60,))
        self.assertEqual(self.hybrid.overview('personal')['local'],'Connected')
    def test_stored_task_qualification_is_idempotent_and_preserves_original_certificate(self):
        self.setup_engine();self.q.approve(self.plan['id'],self.plan['gold_hash'],['groq'],20,True)
        for _ in range(20):self.engine.step()
        before=self.q.state(self.plan['id'])['certificates']
        self.assertTrue(self.q.refresh_task_capabilities())
        self.assertEqual(self.q.refresh_task_capabilities(),[])
        self.assertEqual(before,self.q.state(self.plan['id'])['certificates'])
        cap=next(m for m in self.hybrid.models() if m['provider']=='groq')['payload']
        self.assertEqual(cap['task_quality']['en']['taxonomy'],0)
        self.adapter.analyze.assert_called();self.assertEqual(self.adapter.analyze.call_count,18)

"""Standing consent is account-bound and cannot reset by new campaigns."""
from unittest.mock import Mock
from email_agent.infrastructure.database.automation_ai_store import AutomationAIStore
from email_agent.infrastructure.ai.bounded_organizer import BoundedOrganizerModel
import test_semantic_engine as engine


class AutomationAITests(engine.SemanticEngineTests):
    def prepare(self):
        self.budget=AutomationAIStore(self.messages.database_path,self.messages);self.budget.initialize_ai()
        self.budget.grant('task','personal','fixture',200,50,300000,60000,approval_reference='Fixture explicit approval')

    def test_shared_cap_counts_failed_attempts_and_cannot_reset(self):
        self.prepare()
        for i in range(50):self.budget.reserve_ai('task','c'+str(i),'count',1,0,['m'])
        self.budget.grant('task','personal','fixture',200,50,300000,60000,approval_reference='Fixture explicit approval')
        with self.assertRaises(ValueError):self.budget.reserve_ai('task','another','generate',1,1,['m'])
        self.assertEqual(self.budget.ai_progress('personal')['requests_reserved'],50)
        self.assertIsNone(self.budget.current_grant('university'))

    def test_input_output_and_message_budgets_are_independent(self):
        self.prepare();self.budget.reserve_ai('task','a','count',299999,0,['m'])
        with self.assertRaises(ValueError):self.budget.reserve_ai('task','b','generate',2,0,['m'])
        with self.assertRaises(ValueError):self.budget.reserve_ai('task','b','generate',0,60001,['m'])
        with self.assertRaises(ValueError):self.budget.reserve_ai('task','b','generate',0,0,[str(i) for i in range(201)])

    def test_count_and_generate_are_both_reserved_before_external_call(self):
        self.prepare();model=Mock();model.count_organization.return_value=100;model.generate_organization.return_value='{}';model.last_usage={'total_tokens':123}
        bounded=BoundedOrganizerModel(model,self.budget,'task','campaign',['m'],lambda:True)
        self.assertEqual(bounded.generate_organization('Trusted instructions',{'messages':[]},{}),'{}')
        progress=self.budget.ai_progress('personal');self.assertEqual(progress['requests_reserved'],2);self.assertEqual(progress['output_reserved'],123)
        self.assertEqual(progress['original_reservations']['output'],4096)
        with self.assertRaises(ValueError):bounded.generate_organization('Trusted instructions',{'messages':[]},{})
        self.assertEqual(model.generate_organization.call_count,1)

    def test_failed_counter_never_generates_or_releases_budget(self):
        self.prepare();model=Mock();model.count_organization.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):BoundedOrganizerModel(model,self.budget,'task','campaign',['m'],lambda:True).generate_organization('System',{}, {})
        model.generate_organization.assert_not_called();self.assertEqual(self.budget.ai_progress('personal')['requests_reserved'],1)

    def test_successful_provider_usage_settles_without_refunding_failures_or_requests(self):
        self.prepare()
        self.budget.reserve_ai('task','failed','count',40000,0,['m'])
        self.budget.record_ai('failed','count','failed',100)
        self.budget.reserve_ai('task','ok','count',100000,0,['m'])
        self.budget.record_ai('ok','count','done',2000)
        self.budget.reserve_ai('task','ok','generate',10000,4096,['m'])
        self.budget.record_ai('ok','generate','done',3000)
        usage=self.budget.ai_progress('personal')
        self.assertEqual((usage['requests_reserved'],usage['input_reserved'],usage['output_reserved']),(3,45000,3000))
        self.assertEqual(usage['original_reservations']['input'],150000)
        self.budget.initialize_ai();self.budget.initialize_ai()
        self.budget.record_ai('ok','generate','done',1)
        self.budget.record_ai('failed','count','done',1)
        self.assertEqual(self.budget.ai_progress('personal')['input_reserved'],45000)
        self.assertIsNone(self.budget.current_grant('university'))
        with self.assertRaises(ValueError):self.budget.grant('task','personal','fixture',200,51,300000,60000,approval_reference='Not an expansion')

    def test_missing_invalid_and_interrupted_usage_retain_full_reservation(self):
        self.prepare()
        for i,tokens in enumerate((None,0,-1,10001,True,'100')):
            name='invalid'+str(i)
            self.budget.reserve_ai('task',name,'generate',10000,4096,['m'])
            # Provider type validation normally prevents strings and bools; the
            # ledger also refuses them before SQLite's integer coercion.
            self.budget.record_ai(name,'generate','done',tokens)
        self.budget.reserve_ai('task','interrupted','generate',10000,4096,['m'])
        usage=self.budget.ai_progress('personal')
        self.assertEqual(usage['input_reserved'],70000)
        self.assertEqual(usage['output_reserved'],7*4096)

    def test_developer_token_counter_uses_full_rest_envelope_not_vertex_only_fields(self):
        from email_agent.infrastructure.ai.gemini_model import GeminiTextModel
        model=object.__new__(GeminiTextModel);model._model='fixture';model._client=Mock();model._client.models.count_tokens.return_value.total_tokens=123
        self.assertEqual(model.count_organization('Trusted system',{'messages':['שלום']},{}),123)
        kwargs=model._client.models.count_tokens.call_args.kwargs
        self.assertIsNone(kwargs['contents']);config=kwargs['config'];self.assertIsNone(config.system_instruction)
        envelope=config.http_options.extra_body['generateContentRequest'];self.assertEqual(envelope['systemInstruction']['parts'][0]['text'],'Trusted system');self.assertIn('שלום',envelope['contents'][0]['parts'][0]['text'])

    def test_full_text_redacts_contextual_authentication_secrets_without_clipping(self):
        from email_agent.application.semantic_mail import safe_text
        text='Context. '*200+'Passcode: 123456\nAuthentication code is 654321\nקוד אימות: 4321\nرمز التحقق هو 987654'
        result=safe_text(text)
        for secret in ['123456','654321','4321','987654']:self.assertNotIn(secret,result)
        self.assertGreater(len(result),600);self.assertIn('Authentication code',result)

    def test_remaining_budget_reduces_message_count_without_clipping_or_new_grant(self):
        from email_agent.application.semantic_dispatch import SemanticDispatch
        queue=Mock();queue.current_grant.return_value={'account_id':'personal','model':'fixture'};queue.select_semantic.return_value=['a','b','c','d'];queue.dispatch_ready.side_effect=[False,True]
        engine=Mock();engine.settings.return_value={'usage':{'remaining':20}};engine.preview.side_effect=[dict(id='large',message_count=4),dict(id='small',message_count=2)];holder={}
        SemanticDispatch(queue,lambda grant:(engine,holder)).step('personal')
        engine.run.assert_called_once_with('small',True);queue.settle_semantic.assert_called_once_with('personal',['a','b'],'small');self.assertEqual(holder['ids'],['a','b'])

    def test_transport_failure_is_neither_schema_rejection_nor_quota_failure(self):
        from email_agent.application.model_errors import classify_error
        class TransportError(Exception):pass
        class ConnectError(TransportError):pass
        error=classify_error(ConnectError('Temporary failure in name resolution'))
        self.assertEqual(error.state,'network_unavailable');self.assertIsNone(error.http_status)

for name in engine.SemanticEngineTests.__dict__:
    if name.startswith('test_') and name not in AutomationAITests.__dict__:setattr(AutomationAITests,name,None)

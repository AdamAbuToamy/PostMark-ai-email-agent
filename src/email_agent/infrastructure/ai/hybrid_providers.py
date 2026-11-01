"""Bounded provider adapters. Fixed cloud origins; no model-controlled network."""
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
import ipaddress
import json
import re
import socket
import time
from decimal import Decimal,InvalidOperation
from urllib.parse import urlsplit
import requests
from email_agent.application.hybrid_ai import InferenceResult,organization_schema
from email_agent.application.model_errors import ModelFailure


def cloudflare_endpoint(account):
    if not re.fullmatch(r'[0-9a-fA-F]{32}',account or ''):raise ValueError('Cloudflare account ID must contain32 hexadecimal characters')
    return 'https://api.cloudflare.com/client/v4/accounts/'+account.lower()+'/ai/v1'

ORIGINS={'siliconflow':'https://api.siliconflow.cn/v1','nvidia':'https://integrate.api.nvidia.com/v1','sambanova':'https://api.sambanova.ai/v1','cloudflare':'https://api.cloudflare.com','zai':'https://api.z.ai/api/paas/v4','groq':'https://api.groq.com/openai/v1','openrouter':'https://openrouter.ai/api/v1','cerebras':'https://api.cerebras.ai/v1','mistral':'https://api.mistral.ai/v1','openai':'https://api.openai.com/v1'}


SILICONFLOW_ORIGINS={'international':'https://api.siliconflow.com/v1','china':'https://api.siliconflow.cn/v1'}
def siliconflow_endpoint(region):
    if region not in SILICONFLOW_ORIGINS:raise ValueError('Choose SiliconFlow International (.com) or China (.cn) explicitly')
    return SILICONFLOW_ORIGINS[region]


def local_endpoint(endpoint,token='',trusted=False):
    parsed=urlsplit(endpoint)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/') or parsed.scheme not in ('http','https'):raise ValueError('Use a trusted origin without credentials, path or query')
    try:
        if parsed.hostname!='localhost':ipaddress.ip_address(parsed.hostname)
        addresses={ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(parsed.hostname,parsed.port or 11434,type=socket.SOCK_STREAM)}
    except (OSError,ValueError):raise ValueError('Local endpoint cannot be resolved') from None
    networks=tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','fc00::/7'))
    if not addresses or any(not(a.is_loopback or any(a in n for n in networks if a.version==n.version)) for a in addresses):raise ValueError('Ollama must remain loopback or trusted private LAN')
    if not all(a.is_loopback for a in addresses) and not(trusted and token and parsed.scheme=='https'):raise ValueError('LAN inference requires explicit trust, HTTPS and authentication')
    return endpoint.rstrip('/')


class JSONTransport:
    def __init__(self,origin,key='',timeout=90):self.origin,self.key,self.timeout=origin,key,timeout;self.public_diagnostics=False

    def request(self,path,payload=None):
        self.last_capacity={}
        headers={'Accept':'application/json'}
        if self.key:headers['Authorization']='Bearer '+self.key
        try:
            with requests.Session() as session:
                session.trust_env=False
                with session.request('GET' if payload is None else 'POST',self.origin+path,json=payload,headers=headers,timeout=(4,self.timeout),allow_redirects=False,stream=True) as response:
                    self.last_capacity={k.lower():str(v)[:120] for k,v in response.headers.items() if re.fullmatch(r'(?i)(?:x-)?ratelimit[-_](?:limit|remaining|reset)(?:[-_](?:requests|tokens|input-tokens|output-tokens|day|minute|hour))*|retry-after',k)}
                    if response.status_code!=200:
                        code=response.status_code;state='authentication_failed' if code in (401,403) else 'provider_rate_limited' if code==429 else 'model_unavailable' if code==404 else 'provider_unavailable' if code>=500 else 'provider_request_rejected'
                        failure=ModelFailure(state,'Provider request failed; content and credential details withheld',code,code)
                        failure.safe_context={'quota_headers':self.last_capacity}
                        # Retain only diagnostic identifiers, never free-text echoes
                        # of prompts, email bodies, credentials or generated content.
                        try:
                            chunks=[];size=0
                            for chunk in response.iter_content(4096):
                                chunks.append(chunk);size+=len(chunk)
                                if size>=16384:break
                            raw=b''.join(chunks)[:16384]
                            error_payload=json.loads(raw)
                            error=error_payload.get('error') or error_payload
                            if isinstance(error,str):error={'message':error}
                            if isinstance(error,dict):
                                failure.safe_context.update({k:str(error[k]) for k in ('type','code','param') if k in error and re.fullmatch(r'[a-zA-Z0-9_.\[\]-]{1,120}',str(error[k]))})
                                message=str(error.get('message') or error.get('detail') or '')
                                if self.public_diagnostics and message:
                                    from email_agent.application.model_errors import safe_reason
                                    failure.safe_context['synthetic_error']=re.sub(r'\b(?:org|proj|project)[_-][A-Za-z0-9_-]+\b','[account removed]',safe_reason(message.replace(self.key,'[credential removed]') if self.key else message))[:600]
                                for phrase,kind in (('no endpoints','no_compatible_endpoints'),('service tier capacity','service_tier_capacity'),('rate limit','rate_limit'),('model not found','model_not_found'),('not supported','unsupported_request_option'),('unsupported','unsupported_request_option'),('invalid structured output syntax','schema_compatibility'),('invalid schema','invalid_schema'),('json schema','schema_compatibility')):
                                    if phrase in message.casefold():failure.safe_context['reason_class']=kind;break
                                keywords=[k for k in ('uniqueItems','minItems','maxItems','max_tokens','max_completion_tokens','response_format','json_schema','strict','temperature','reasoning_effort') if k in message]
                                if keywords:failure.safe_context['rejected_fields']=keywords
                                if code==403 and error.get('type')=='labs_not_enabled':
                                    failure.state='model_access_denied';failure.detail='This model requires provider Labs permission; other models can still be tested.'
                                if code==429:
                                    if re.search(r'\b(?:TPM|RPM)\b|per minute',message,re.I):failure.safe_context['limit_window']='minute'
                                    elif re.search(r'\b(?:TPD|RPD)\b|per day',message,re.I):failure.safe_context['limit_window']='day'
                                    from email_agent.application.model_errors import retry_delay
                                    failure.body_retry_after=retry_delay(message)
                        except (ValueError,TypeError):pass
                        retry=response.headers.get('Retry-After','')
                        try:failure.retry_after=min(86400,max(1,float(retry)))
                        except ValueError:
                            try:failure.retry_after=min(86400,max(1,(parsedate_to_datetime(retry)-datetime.now(timezone.utc)).total_seconds()))
                            except (ValueError,TypeError):failure.retry_after=getattr(failure,'body_retry_after',None)
                        raise failure
                    chunks=[];size=0
                    for chunk in response.iter_content(16384):
                        size+=len(chunk)
                        if size>2_000_000:raise ModelFailure('malformed_output','Provider response exceeds bounded size')
                        chunks.append(chunk)
                    value=json.loads(b''.join(chunks))
                    if not isinstance(value,dict):raise ValueError()
                    return value
        except requests.RequestException as exc:
            failure=ModelFailure('network_unavailable','Inference connection failed; task retained');failure.safe_context={'transport_error':type(exc).__name__};raise failure from None
        except (ValueError,UnicodeError):raise ModelFailure('malformed_output','Provider did not return a JSON object') from None


class LocalModelProvider:
    def __init__(self,endpoint='http://127.0.0.1:11434',token='',trusted=False,context=16384,resource_check=lambda:True):
        self.resource_check=resource_check
        self.http=JSONTransport(local_endpoint(endpoint,token,trusted),token,90);self.context=context
    def discover(self):
        return [dict(provider='ollama',model=m['name'],context=self.context,structured=False,free=True,fingerprint=m.get('digest',''),size=m.get('size')) for m in self.http.request('/api/tags').get('models',[]) if isinstance(m,dict) and isinstance(m.get('name'),str) and not m.get('remote_host') and not m.get('remote_model') and not m['name'].endswith(':cloud')]
    def analyze(self,model,system,data,schema,timeout=85):
        from pathlib import Path
        if not self.resource_check():raise ModelFailure('local_memory_pressure','Local admission paused for memory pressure; queued content retained')
        schema=organization_schema(data,schema)
        upper=len((system+json.dumps(data,ensure_ascii=False)+json.dumps(schema)).encode())+512
        if upper+2048>min(model.context,self.context):raise ModelFailure('context_exceeded','Complete input does not fit the configured local context; not truncated')
        memory=next((int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')),0)
        if memory<1_500_000:raise ModelFailure('local_memory_pressure','Local inference paused to preserve server memory')
        start=time.monotonic();self.http.timeout=min(timeout,85)
        details=self.http.request('/api/show',{'model':model.model,'verbose':False})
        if details.get('remote_host') or details.get('remote_model') or not details.get('model_info'):raise ModelFailure('local_model_not_verified','Only verified installed weights may run as local AI')
        self.http.timeout=max(.1,timeout-(time.monotonic()-start))
        payload=dict(model=model.model,messages=[dict(role='system',content=system),dict(role='user',content=json.dumps(data,ensure_ascii=False,separators=(',',':')))],format=schema,stream=False,think=False,keep_alive='2m',options=dict(temperature=0,num_ctx=min(model.context,self.context),num_predict=2048))
        value=self.http.request('/api/chat',payload)
        if value.get('done') is not True or value.get('done_reason') in ('length','limit') or value.get('model')!=model.model:raise ModelFailure('malformed_output','Local output is incomplete or model identity changed')
        text=(value.get('message') or {}).get('content')
        if not isinstance(text,str) or not text.strip():raise ModelFailure('malformed_output','Empty local structured output')
        usage={k:value[source] for k,source in [('prompt_tokens','prompt_eval_count'),('output_tokens','eval_count')] if type(value.get(source))is int}
        usage.update({k:value[k] for k in ('load_duration','total_duration','eval_duration','prompt_eval_duration') if type(value.get(k))is int})
        return InferenceResult(text,'ollama',model.model,usage,time.monotonic()-start)


def compatible_schema(value):
    """Use explicit anyOf unions supported by compatible constrained decoders."""
    if isinstance(value,list):return [compatible_schema(v) for v in value]
    if not isinstance(value,dict):return value
    node={k:compatible_schema(v) for k,v in value.items()}
    if isinstance(node.get('type'),list):
        kinds=node.pop('type');choices=[]
        for kind in kinds:
            branch={k:v for k,v in node.items() if kind!='null' or k not in ('maxLength','minLength','pattern')}
            branch['type']=kind
            if 'enum' in branch:branch['enum']=[v for v in branch['enum'] if (v is None)==(kind=='null')]
            choices.append(branch)
        return {'anyOf':choices}
    return node


class OpenAICompatibleProvider:
    def __init__(self,provider,key,endpoint=None):
        if provider not in ORIGINS:raise ValueError('Unknown cloud provider')
        self.provider=provider
        if provider=='cloudflare':
            match=re.fullmatch(r'https://api.cloudflare.com/client/v4/accounts/([0-9a-f]{32})/ai/v1',endpoint or '')
            if not match:raise ValueError('Configure a Cloudflare account ID before testing')
        if provider=='siliconflow' and endpoint is not None and endpoint not in SILICONFLOW_ORIGINS.values():raise ValueError('SiliconFlow endpoint must be the selected official regional API')
        self.region=next((r for r,v in SILICONFLOW_ORIGINS.items() if v==endpoint),'china') if provider=='siliconflow' else None
        self.http=JSONTransport(endpoint if provider in ('cloudflare','siliconflow') and endpoint else ORIGINS[provider],key)
    def discover(self):
        models=[]
        if self.provider=='cloudflare':
            catalog=JSONTransport(self.http.origin.removesuffix('/v1'),self.http.key,20)
            self.discovery_info={'verified_at':time.time(),'account_free_verified':False,'free_verification_reason':'Workers Free account plan not verified','catalog_complete':True}
            def metadata(transport,path):
                reserve=getattr(self,'metadata_reserve',None)
                if reserve is not None and not reserve():raise ValueError('Approved metadata request scope exhausted; no inference was sent')
                return transport.request(path)
            for page in range(1,3):
                try:listing=metadata(catalog,f'/models/search?per_page=100&page={page}&task=Text%20Generation')
                except ModelFailure as exc:
                    if exc.http_status in (401,403):raise ValueError(f'Cloudflare HTTP {exc.http_status}: check API Token, Account ID and Workers AI Read permission') from None
                    raise
                entries=listing.get('result')
                if listing.get('success') is not True or not isinstance(entries,list):raise ValueError('Unsupported Cloudflare model-catalog response: expected success and a result array')
                for item in entries:
                    if not isinstance(item,dict) or not isinstance(item.get('name'),str) or not item['name'].startswith('@cf/'):continue
                    task=item.get('task') or {}
                    if not isinstance(task,dict) or task.get('name')!='Text Generation':continue
                    props={v['property_id']:v.get('value') for v in item.get('properties',[]) if isinstance(v,dict) and isinstance(v.get('property_id'),str)}
                    if str(props.get('lora','false')).lower()=='true':continue
                    try:context=int(props.get('context_window') or 8192)
                    except (ValueError,TypeError):context=8192
                    # Catalog availability is independent of account pricing and quality.
                    # Workers Free allocation covers models not marked paid-required.
                    allocation_eligible=str(props.get('require_workers_paid','false')).lower()!='true'
                    models.append(dict(provider='cloudflare',model=item['name'],context=context,generation=True,structured=False,free=allocation_eligible,tools=str(props.get('function_calling','false')).lower()=='true',output_mode='json_schema'))
                info=listing.get('result_info') or {}
                if len(entries)<100 or isinstance(info.get('total_pages'),int) and page>=info['total_pages']:break
                if page==2:self.discovery_info['catalog_complete']=False
            if not models:raise ValueError('Cloudflare returned zero compatible text-generation models; no empty selector is usable')
            # A checked box is not proof that this Cloudflare account cannot bill.
            account=JSONTransport(self.http.origin.rsplit('/ai/',1)[0],self.http.key,20)
            try:
                plans=metadata(account,'/subscriptions')
                if plans.get('success') is True and isinstance(plans.get('result'),list):
                    active=[p for p in plans['result'] if isinstance(p,dict) and p.get('state') in ('Provisioned','Paid')]
                    free=[p for p in active if (p.get('rate_plan') or {}).get('id')=='workers_free' and p.get('price')==0]
                    paid=[p for p in active if 'workers' in str((p.get('rate_plan') or {}).get('id','')).lower() and (p.get('rate_plan') or {}).get('id')!='workers_free']
                    if free and not paid:self.discovery_info.update(account_free_verified=True,free_verification_reason='Provider reports an active zero-price Workers Free plan',free_verification_source='provider_subscriptions')
                    else:self.discovery_info['free_verification_reason']='Account API did not report an active zero-price Workers Free plan; Free-only inference is blocked'
                else:self.discovery_info['free_verification_reason']='Account plan response was unsupported; Free-only eligibility remains unknown'
            except ModelFailure as exc:
                self.discovery_info['free_verification_reason']=f'Account plan verification HTTP {exc.http_status or "unavailable"}; Billing Read permission may be required. Model discovery succeeded; Free-only inference is blocked'
            except ValueError as exc:self.discovery_info['free_verification_reason']=str(exc)
            return list({m['model']:m for m in models}.values())
        if self.provider=='siliconflow':self.http.timeout=min(self.http.timeout,20)
        listing=self.http.request('/models?type=text&sub_type=chat' if self.provider=='siliconflow' else '/models')
        entries=listing.get('data',[])
        if self.provider=='nvidia' and not isinstance(listing.get('data'),list):raise ModelFailure('malformed_output','NVIDIA catalog response is missing its model data array')
        if not isinstance(entries,list):raise ModelFailure('malformed_output','Invalid model listing')
        if self.provider=='siliconflow':
            from email_agent.infrastructure.ai.free_catalog import siliconflow_free
            if not isinstance(listing.get('data'),list):raise ModelFailure('malformed_output','SiliconFlow catalog did not return a model data array')
            for item in entries:
                if not isinstance(item,dict):continue
                evidence=siliconflow_free(item.get('id'),self.region)
                if not evidence:
                    if self.region=='international' and isinstance(item.get('id'),str):models.append(dict(provider='siliconflow',model=item['id'],context=min(2_000_000,max(2048,int(item.get('context_length') or 8192))),generation=True,structured=False,free=False,tools=False,output_mode='json_object',fingerprint='siliconflow:international:chat:json-object:v1'))
                    continue
                pricing=item.get('pricing')
                if pricing is not None:
                    try:
                        if not isinstance(pricing,dict) or any(Decimal(str(pricing.get(k,'NaN')))!=0 for k in ('prompt','completion')):continue
                    except (InvalidOperation,ValueError,TypeError):continue
                models.append(dict(provider='siliconflow',model=item['id'],context=evidence['context'],generation=True,structured=False,free=True,tools=False,output_mode=evidence['output_mode'],fingerprint='siliconflow:'+self.region+':chat:json-object:v1'))
            models.sort(key=lambda m:-(siliconflow_free(m['model'],self.region) or {}).get('priority',0))
            free=[m for m in models if m['free']]
            self.discovery_info=dict(region=self.region,authentication_verified=True,catalog_models=len(entries),pricing_source='https://www.siliconflow.com/pricing' if self.region=='international' else 'https://www.siliconflow.cn/pricing',zero_price_standard_models=bool(free),model_access_verified=False,account_entitlement='Catalog only; account restrictions may still apply',free_candidates=[dict(model=m['model'],**{k:siliconflow_free(m['model'],self.region)[k] for k in ('open_weights','license','license_source')}) for m in free],blocker=None if free else 'Authentication succeeded. No verified zero-price text model for the selected region; Chinese free pricing and introductory credits do not authorize international inference',note='No inference performed. Quality and mailbox permission remain separate')
            return models
        aliases={}
        if self.provider=='mistral':
            for item in entries:
                if isinstance(item,dict) and isinstance(item.get('id'),str):
                    for alias in item.get('aliases') or []:
                        if isinstance(alias,str):aliases[alias]=item['id']
        for item in entries[:500]:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str):continue
            capabilities=item.get('capabilities') or {}
            if self.provider=='mistral' and (item.get('archived') or not capabilities.get('completion_chat')):continue
            pricing=item.get('pricing') or {};params=item.get('supported_parameters') or []
            # Only literal zero-priced free variants with schema support are
            # machine-verifiable. Other providers need a dated account-tier
            # attestation AND a successful schema/quality evaluation.
            try:zero=all(k in pricing for k in ('prompt','completion')) and all(Decimal(str(v))==0 for v in pricing.values())
            except (InvalidOperation,ValueError):zero=False
            free=self.provider=='openrouter' and item['id'].endswith(':free') and zero or self.provider=='zai' and item['id'] in ('glm-4.7-flash','glm-4.5-flash')
            mode='json_object' if self.provider=='zai' else 'json_object' if self.provider=='openrouter' and 'response_format' in params and 'structured_outputs' not in params else 'json_schema'
            models.append(dict(provider=self.provider,model=aliases.get(item['id'],item['id']),context=item.get('max_context_length',item.get('context_length',item.get('context_window',200000 if self.provider=='zai' and item['id']=='glm-4.7-flash' else 128000 if self.provider=='zai' and item['id']=='glm-4.5-flash' else 8192))),generation=self.provider!='openai',tools=bool(capabilities.get('function_calling')),structured='structured_outputs' in params or 'response_format' in params,free=free,pricing=pricing,output_mode=mode,reasoning='reasoning' in params))
        if self.provider=='nvidia':
            self.discovery_info=dict(usage_policy='developer_evaluation_only',production_permitted=False,model_access_verified=False,terms_url='https://docs.api.nvidia.com/nim/docs/product',note='Authenticated catalog listing is not inference entitlement or production permission')
            if not models:raise ValueError('NVIDIA returned no model candidates; check API Catalog access')
            models.sort(key=lambda m:(not ('nemotron' in m['model'].lower()),m['model']))
        if self.provider=='zai':
            # The authenticated catalog can lag the public free model catalog.
            # Suggested candidates remain unqualified until a real approved test.
            present={m['model'] for m in models}
            for name,context in [('glm-4.7-flash',200000),('glm-4.5-flash',128000)]:
                if name not in present:models.append(dict(provider='zai',model=name,context=context,generation=True,structured=False,free=True,output_mode='json_object',catalog_source='https://docs.z.ai/guides/overview/pricing'))
        return list({m['model']:m for m in models}.values())
    def analyze(self,model,system,data,schema,timeout=85):
        if self.provider=='nvidia' and data.get('organization_scope',{}).get('synthetic') is not True:raise ValueError('NVIDIA developer endpoints are restricted to synthetic evaluation; production mail is forbidden')
        if self.provider=='siliconflow':
            from email_agent.infrastructure.ai.free_catalog import siliconflow_free
            if not model.free or not siliconflow_free(model.model,self.region):raise ValueError('SiliconFlow pricing is unknown or nonzero; inference blocked')
        self.http.timeout=min(timeout,85)
        self.http.public_diagnostics=data.get('organization_scope',{}).get('synthetic') is True
        schema=organization_schema(data,schema)
        start=time.monotonic();payload=dict(model=model.model,messages=[dict(role='system',content=system),dict(role='user',content=json.dumps(data,ensure_ascii=False,separators=(',',':')))],temperature=0,max_tokens=2048,stream=False,response_format=dict(type='json_schema',json_schema=dict(name='postmark_organization',strict=True,schema=schema)))
        if model.output_mode=='json_schema':payload['response_format']['json_schema']['schema']=compatible_schema(schema)
        if self.provider=='cloudflare':payload['response_format']={'type':'json_schema','json_schema':schema}
        if model.output_mode=='json_object':
            payload['response_format']={'type':'json_object'}
            payload['messages'][0]['content']+=' Return JSON matching this schema: '+json.dumps(schema,separators=(',',':'))
        if self.provider=='siliconflow' and siliconflow_free(model.model,self.region).get('disable_thinking'):payload['enable_thinking']=False
        if self.provider=='zai':
            payload['thinking']={'type':'disabled'}
        if self.provider=='mistral':
            # Actual API3054 rejects greedy sampling on current Mistral Large.
            # Use its supported sampling default; quality is still measured.
            payload.pop('temperature')
            if model.model.startswith('mistral-large-4'):payload['reasoning_effort']='low'
            elif model.model.startswith(('mistral-small-2603','mistral-small-latest','mistral-medium-3-5','mistral-medium-3.5','mistral-medium-2604')):payload['reasoning_effort']='none'
        if self.provider=='groq' and model.model=='qwen/qwen3.8-27b':
            payload.update(reasoning_effort='none',reasoning_format='hidden')
        if self.provider=='groq' and model.model in ('openai/gpt-oss-120b','openai/gpt-oss-20b'):payload['reasoning_effort']='low'
        if self.provider in ('openai','groq'):
            # Keep vendor subset compatibility; full validation remains local.
            def subset(node):
                omitted={'uniqueItems'} if self.provider=='openai' else {'uniqueItems','minItems','maxItems','minLength','maxLength','pattern'}
                if isinstance(node,dict):return {k:subset(v) for k,v in node.items() if k not in omitted}
                if isinstance(node,list):return [subset(v) for v in node]
                return node
            if model.output_mode=='json_schema':payload['response_format']['json_schema']['schema']=subset(payload['response_format']['json_schema']['schema'])
            payload['max_completion_tokens']=payload.pop('max_tokens')
            if self.provider=='openai':payload.pop('temperature');payload['store']=False
        if self.provider=='openrouter' and model.reasoning:payload['reasoning']={'effort':'low','exclude':True}
        if self.provider=='openrouter':payload['provider']={'require_parameters':True,'allow_fallbacks':False,'max_price':{'prompt':0,'completion':0}}
        value=self.http.request('/chat/completions',payload);choices=value.get('choices')
        if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict) or choices[0].get('finish_reason')!='stop':
            failure=ModelFailure('malformed_output','Incomplete structured provider response')
            if isinstance(choices,list) and choices and isinstance(choices[0],dict) and choices[0].get('finish_reason') in ('length','model_length','tool_calls','error'):failure.safe_context={'finish_reason':choices[0]['finish_reason']}
            raise failure
        actual=value.get('model')
        alias=self.provider=='openrouter' and model.free and model.model.endswith(':free') and actual==model.model[:-5]
        if actual and actual!=model.model and not alias:raise ModelFailure('model_identity_mismatch','Unexpected provider model; result withheld')
        message=choices[0].get('message')
        if not isinstance(message,dict) or message.get('refusal'):raise ModelFailure('model_refusal','Provider refused the bounded analysis')
        text=message.get('content')
        if self.provider=='mistral' and isinstance(text,list):
            text=''.join(c['text'] for c in text if isinstance(c,dict) and c.get('type')=='text' and isinstance(c.get('text'),str))
        if not isinstance(text,str) or not text.strip():raise ModelFailure('malformed_output','Missing provider message')
        raw=value.get('usage') or {};usage={k:raw[v] for k,v in [('prompt_tokens','prompt_tokens'),('output_tokens','completion_tokens'),('total_tokens','total_tokens')] if type(raw.get(v))is int}
        cached=(raw.get('prompt_tokens_details') or {}).get('cached_tokens')
        if type(cached)is int:usage['cached_tokens']=cached
        details=raw.get('completion_tokens_details') or {}
        reasoning=details.get('reasoning_tokens') if isinstance(details,dict) else None
        if type(raw.get('neurons')) in (int,float):usage['neurons']=raw['neurons']
        if type(reasoning)is int:usage['reasoning_tokens']=reasoning
        if getattr(self.http,'last_capacity',None):usage['quota_headers']=self.http.last_capacity
        return InferenceResult(text,self.provider,model.model,usage,time.monotonic()-start)


class GeminiProvider:
    def __init__(self,key):self.key=key;self.metadata_reserve=lambda:True
    def discover(self):
        from email_agent.infrastructure.ai.gemini_model import GeminiTextModel
        data=GeminiTextModel(self.key).list_models(self.metadata_reserve)
        return [dict(provider='gemini',model=m['id'],generation=True,structured=False,free=False,context=m.get('input_limit') or 8192) for m in data['models'] if 'generateContent' in m['actions']]
    def analyze(self,model,system,data,schema,timeout=85):
        from email_agent.infrastructure.ai.gemini_model import GeminiTextModel
        start=time.monotonic();adapter=GeminiTextModel(self.key,model.model,timeout_ms=max(100,min(30000,int(timeout*1000))))
        text=adapter.generate_organization(system,data,schema,max_output_tokens=2048)
        return InferenceResult(text,'gemini',model.model,adapter.last_usage,time.monotonic()-start)

"""Provider-independent failure contract. Never turn a parser failure into a quota verdict."""
import re

def retry_delay(text):
    """Provider duration grammar, including compound hours/minutes/seconds."""
    match=re.search(r'(?:retry|try again) in\s+((?:[0-9.]+\s*(?:ms|[dhms])\s*)+)',str(text),re.I)
    if not match:return None
    parts=re.findall(r'([0-9.]+)\s*(ms|[dhms])',match[1],re.I)
    return min(86400,max(1,sum(float(n)*{'ms':.001,'s':1,'m':60,'h':3600,'d':86400}[unit.lower()] for n,unit in parts)))


class ModelFailure(Exception):
    def __init__(self,state,reason,code=None,http_status=None,provider_status=None):
        super().__init__(reason)
        self.state,self.detail,self.code,self.http_status,self.provider_status=state,reason,code,http_status if http_status is not None else code if type(code) is int and 100<=code<=599 else None,provider_status
    def diagnostic(self):
        return {"kind":self.state,"reason":self.detail,"provider_code":self.code,"http_status":self.http_status,"provider_status":self.provider_status,"context":getattr(self,'safe_context',{}),"retry_after":getattr(self,'retry_after',None)}

def safe_reason(value):
    text=str(value)
    text=re.sub(r"AIza[0-9A-Za-z_-]+","[redacted]",text)
    text=re.sub(r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|authorization|token)[\"']?\s*[:=]\s*)[^\s,;&}]+",r"\1[redacted]",text)
    text=re.sub(r"https?://[^\s\"'<>]+","[provider URL removed]",text)
    return text[:1000]

def classify_error(exc):
    if isinstance(exc,ModelFailure):return exc
    reported=getattr(exc,"status_code",None)
    code=getattr(exc,"code",None)
    if code is None:code=reported
    response=getattr(exc,"response",None)
    status=getattr(response,"status_code",None)
    if type(status) is not int:status=reported if type(reported) is int else code if type(code) is int else None
    provider_status=getattr(exc,"status",None)
    if not isinstance(provider_status,str):provider_status=None
    detail=safe_reason(getattr(exc,"message",None) or str(exc))
    transport=isinstance(exc,(TimeoutError,ConnectionError)) or any(base.__name__ in {'TransportError','ConnectError','ReadTimeout','ConnectTimeout','PoolTimeout','ServerNotFoundError','gaierror'} for base in type(exc).__mro__)
    if transport and status is None:kind='network_unavailable'
    elif status==429:kind="provider_rate_limited"
    elif status==401 or status==403 or 'api_key_invalid' in detail.lower() or 'api key not valid' in detail.lower():kind="authentication_failed"
    elif status==404:kind="model_unavailable"
    elif status==400 and re.search(r'invalid.*model|model.*invalid',detail,re.I):kind="invalid_model"
    elif status and status>=500:kind="provider_unavailable"
    elif status:kind="provider_request_rejected"
    else:kind="harness_error"
    failure=ModelFailure(kind,detail or "No safe error details supplied",code if isinstance(code,(int,str)) else None,status,provider_status)
    if kind=='provider_rate_limited':
        delays=[]
        def inspect(value):
            if isinstance(value,dict):
                duration=value.get('retryDelay')
                if isinstance(duration,str) and re.fullmatch(r'[0-9.]+s',duration):delays.append(float(duration[:-1]))
                for item in value.values():inspect(item)
            elif isinstance(value,list):
                for item in value:inspect(item)
        inspect(getattr(exc,'details',None))
        parsed=retry_delay(detail)
        if parsed:delays.append(parsed)
        if delays:failure.retry_after=min(86400,max(1,max(delays)))
    return failure

def actionable(failure):
    return {"provider_rate_limited":"Google temporarily rate-limited this model. Local tools still work; no retry or paid fallback was made (provider rate limit).",
      "authentication_failed":"Google rejected the API credentials or project permission. Review the private key configuration.",
      "invalid_model":"The model ID is invalid. Validate it against this project's model list, or return to the last verified model.",
      "model_unavailable":"This model was not found or is unavailable to this key/project. Validate the model or return to the last verified working model.",
      "malformed_output":"Gemini returned malformed structured output outside the typed tool limits. No tools from that step ran. You can return to the last verified working model.",
      "harness_error":"Postmark could not process the SDK response. This is an internal harness error, not a Google quota verdict.",
      "provider_request_rejected":"Google rejected this generation configuration. Inspect the preserved provider reason and validate the model.",
      "provider_unavailable":"Google could not serve this request. No automatic retry or model switch was made.",
      "network_unavailable":"The provider connection failed. The bounded queue keeps its checkpoint; no paid fallback was made."}.get(failure.state,"Model request failed. Inspect the saved diagnostic; no automatic model switch.")

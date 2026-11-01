import json
from google import genai
from google.genai import types
from email_agent.application.model_errors import ModelFailure,classify_error


class GeminiTextModel:
    def __init__(self, api_key: str, model: str = "gemini-3.5-flash-lite", timeout_ms: int = 30000):
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
            timeout=max(100,min(30000,timeout_ms)), retry_options=types.HttpRetryOptions(attempts=1),
        ))
        self._model = model
        self.model_name=model
        self.last_usage={}

    def generate(self, prompt: str) -> str:
        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
                max_output_tokens=1600,
                automatic_continuation=False,
            ),
        )

        self._capture_usage(response)
        if not response.text:
            raise ModelFailure("malformed_output","Gemini returned an empty text response")

        return response.text

    def generate_agent(self, system: str, data: dict) -> str:
        response=self._client.models.generate_content(
            model=self._model,contents=json.dumps(data,ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=system,response_mime_type="application/json",
                response_json_schema={"type":"object","properties":{
                    "reply":{"type":"string","maxLength":2000},
                    "continue":{"type":"boolean"},
                    "calls":{"type":"array","maxItems":4,"items":{"type":"object","properties":{
                        "tool":{"type":"string","enum":list(data["tools"])},
                        "args":{"type":"object","properties":{key:value for tool in data["tools"].values() for key,value in tool["arguments"]["properties"].items()},"additionalProperties":False}},"required":["tool","args"],"additionalProperties":False}}},
                    "required":["reply","calls","continue"],"additionalProperties":False},
                thinking_config=self._thinking(),max_output_tokens=1600,automatic_continuation=False))
        self._capture_usage(response)
        if not response.text: raise ModelFailure("malformed_output","Gemini returned an empty text response")
        return response.text

    def generate_semantic(self, system: str, summary: dict, schema: dict) -> str:
        response=self._client.models.generate_content(model=self._model,
            contents=json.dumps(summary,ensure_ascii=False),
            config=types.GenerateContentConfig(system_instruction=system,
                response_mime_type="application/json",response_json_schema=schema,
                temperature=0,max_output_tokens=600,thinking_config=self._thinking(),
                automatic_continuation=False))
        self._capture_usage(response)
        if not response.text:raise ModelFailure("malformed_output","Empty semantic response")
        return response.text

    def generate_organization(self, system: str, data: dict, schema: dict, max_output_tokens: int = 4096) -> str:
        from email_agent.application.hybrid_ai import organization_schema
        schema=organization_schema(data,schema)
        response=self._client.models.generate_content(model=self._model,
            contents=json.dumps(data,ensure_ascii=False),
            config=types.GenerateContentConfig(system_instruction=system,
                response_mime_type='application/json',response_json_schema=self._organization_wire_schema(schema),
                temperature=0,max_output_tokens=max_output_tokens,thinking_config=self._thinking(),
                automatic_continuation=False))
        self._capture_usage(response)
        if not response.text:raise ModelFailure('malformed_output','Empty organizer response')
        return response.text

    def count_organization(self,system: str,data: dict,schema: dict)->int:
        # The SDK's CountTokensConfig.system_instruction is Vertex-only. The
        # Developer API supports the full generateContentRequest REST envelope.
        envelope={'model':'models/'+self._model,'contents':[{'role':'user','parts':[{'text':json.dumps(data,ensure_ascii=False)}]}],
            'systemInstruction':{'parts':[{'text':system}]}}
        response=self._client.models.count_tokens(model=self._model,contents=None,
            config=types.CountTokensConfig(http_options=types.HttpOptions(extra_body={'generateContentRequest':envelope})))
        return response.total_tokens

    @staticmethod
    def _organization_wire_schema(value):
        # Deep bounded arrays plus numeric constraints can exceed Google's
        # constrained-decoding state budget. The application validates all
        # original bounds and exact membership before accepting any output.
        if isinstance(value,list):return [GeminiTextModel._organization_wire_schema(item) for item in value]
        if isinstance(value,dict):return {key:GeminiTextModel._organization_wire_schema(item) for key,item in value.items() if key not in {'minItems','maxItems','minLength','maxLength','minimum','maximum'}}
        return value

    def test_connection(self):
        try:
            response=self._client.models.generate_content(model=self._model,contents="Reply with POSTMARK_CONNECTION_OK. Do not use tools.",config=types.GenerateContentConfig(thinking_config=self._thinking(),max_output_tokens=256,automatic_continuation=False))
            self._capture_usage(response)
            if not response.text:raise ModelFailure("malformed_output","Provider returned an empty text response")
            return response.text
        except Exception as exc:raise classify_error(exc) from exc

    def list_models(self,reserve):
        items=[];pages=0;pager=None
        try:
            for _ in range(3):
                if pager is not None and not pager.config.get('page_token'):break
                if not reserve():raise ModelFailure("short_term_throttle","Please wait before another model metadata request")
                pages+=1
                if pager is None:pager=self._client.models.list(config={'page_size':100,'query_base':True})
                else:pager.next_page()
                for model in pager.page:
                    limit=getattr(model,'input_token_limit',None)
                    items.append({'id':str(model.name or '').removeprefix('models/'),'actions':list(model.supported_actions or []),'input_limit':limit if type(limit)is int else None})
            return {'models':items,'pages':pages,'complete':not bool(pager.config.get('page_token'))}
        except Exception as exc:raise classify_error(exc) from exc

    def _thinking(self):
        if self._model in ("gemini-3.5-flash-lite","gemini-3.1-flash-lite"):return types.ThinkingConfig(thinking_level="minimal")
        if self._model in ("gemini-3.8-flash","gemini-3.7-flash"):return types.ThinkingConfig(thinking_level="low")
        return None

    def _capture_usage(self,response):
        metadata=getattr(response,"usage_metadata",None)
        keys={"prompt_token_count":"prompt_tokens","candidates_token_count":"output_tokens","thoughts_token_count":"thinking_tokens","total_token_count":"total_tokens","cached_content_token_count":"cached_tokens"}
        self.last_usage={target:getattr(metadata,source) for source,target in keys.items() if type(getattr(metadata,source,None)) is int}

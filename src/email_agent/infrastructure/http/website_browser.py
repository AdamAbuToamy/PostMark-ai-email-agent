"""Pinned HTTPS broker and isolated Playwright process; never attaches to a user browser."""
import base64
import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import time
import zlib
from threading import BoundedSemaphore,Lock,Timer
from dataclasses import dataclass,field
import uuid
from email_agent.application.model_errors import safe_reason
from urllib.parse import urlsplit,parse_qs,unquote
import re
from email_agent.infrastructure.http.safe_unsubscribe import public_destination, PinnedHTTPSConnection


class UnsafeDestination(ValueError):
    pass


def scope_url(url,allow_all=False):
    parsed=urlsplit(url)
    if len(url)>2000 or any(ord(c)<33 for c in url) or parsed.scheme!='https' or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.port not in (None,443):raise UnsafeDestination("Only a bounded credential-free HTTPS unsubscribe destination is allowed")
    if re.search(r'/(?:billing|checkout|payments?|purchase|password|oauth|login|logout)(?:/|$)|/(?:account|profile)/(?:delete|close|cancel)|/delete[-_/]?(?:account|profile)',unquote(parsed.path),re.I):raise UnsafeDestination("Destination exceeds mailing-list cancellation scope")
    if not allow_all and re.search(r'unsubscribe[-_/]?all|all[-_/]subscriptions',unquote(parsed.path),re.I):raise UnsafeDestination('Destination expands mailing-list scope')
    for key,values in parse_qs(parsed.query).items():
        if key.lower() in ('action','op','operation','cmd') and any(value.lower() not in ('unsubscribe','optout','opt_out','confirm','cancel','remove','submit','update_preferences') for value in values):raise UnsafeDestination("Unexpected operation in unsubscribe destination")
    return parsed


def form_scope(path,headers,body,allow_all=False,recipient_email=None):
    if not re.search(r'unsubscribe|opt[-_]?out|newsletter|subscription|email[-_/]?preferences|preferences|/(?:confirm|cancel|leave|remove|submit|continue|next|u)/?$',path,re.I):raise ValueError("Unrecognized final cancellation endpoint")
    kind=next((v for k,v in headers.items() if k.lower()=='content-type'),'').split(';')[0]
    if kind=='application/x-www-form-urlencoded':fields=parse_qs(body.decode('utf-8'),keep_blank_values=True)
    elif kind=='application/json':
        raw=json.loads(body)
        if not isinstance(raw,dict):raise ValueError("Unsupported website payload")
        fields={k:[v] for k,v in raw.items()}
    elif not body:fields={}
    else:raise ValueError("Unsupported website form encoding")
    if len(fields)>30:raise ValueError("Unexpected form scope")
    for key,values in fields.items():
        if re.search(r'password|credit|payment|billing|delete.account|close.account',key,re.I):raise UnsafeDestination("Sensitive or destructive form scope")
        if re.search(r'(?:delete|close)[_-]?account',key,re.I) or any(str(v).strip().lower()=='account' for v in values):raise UnsafeDestination("Account-level mutation is never authorized")
        if not allow_all and (re.search(r'(?:unsubscribe|remove)[_-]?all|all[_-]?(?:lists|subscriptions|newsletters)',key,re.I) or any(str(v).strip().lower() in ('all','all_lists','all_subscriptions','all_newsletters','*') for v in values)):raise UnsafeDestination("Form expands the approved mailing-list scope")
        for value in values:
            if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',str(value)) and (not recipient_email or str(value).casefold()!=recipient_email.casefold()):raise UnsafeDestination("Recipient-address submission needs exact account-bound consent")
        if key.lower() in ('action','op','operation','cmd') and any(str(v).lower() not in ('unsubscribe','optout','opt_out','confirm','cancel','remove','submit','update_preferences') for v in values):raise ValueError("Unexpected form operation")


class WebsiteTransport:
    def validate(self,url):
        scope_url(url)
        public_destination(url)
        return urlsplit(url).hostname.encode("idna").decode("ascii")

    def fetch(self,url,method,headers,body):
        host,address,path=public_destination(url)
        connection=PinnedHTTPSConnection(host,address)
        safe={key:value for key,value in headers.items() if key.lower() in ('accept','content-type','cookie','origin','referer') and isinstance(value,str) and len(value)<=8000 and '\r' not in value and '\n' not in value}
        safe.update({'User-Agent':'Postmark-Unsubscribe/1','Accept-Encoding':'identity'})
        try:
            connection.request(method,path,body=body or None,headers=safe)
            response=connection.getresponse();raw=response.read(2_000_001)
            if len(raw)>2_000_000:raise ValueError("Website response exceeds the bound")
            result={key.lower():value for key,value in response.getheaders() if key.lower() not in ('transfer-encoding','content-length','connection','content-security-policy','content-encoding')}
            cookies=[value for key,value in response.getheaders() if key.lower()=='set-cookie']
            if cookies:result['set-cookie']='\n'.join(cookies)
            encoding=response.getheader('Content-Encoding','').lower()
            if encoding in ('gzip','deflate'):
                decoder=zlib.decompressobj(31 if encoding=='gzip' else zlib.MAX_WBITS)
                raw=decoder.decompress(raw,2_000_001)
                if len(raw)>2_000_000 or decoder.unconsumed_tail:raise ValueError("Expanded response exceeds the bound")
            elif encoding not in ('','identity'):raise ValueError("Unsupported website content encoding")
            result['content-security-policy']="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; form-action 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'"
            result['permissions-policy']='camera=(), microphone=(), geolocation=(), payment=(), usb=()'
            result['x-dns-prefetch-control']='off'
            return {'status':response.status,'headers':result,'body':base64.b64encode(raw).decode()}
        finally:connection.close()


@dataclass
class _Session:
    process: object
    selector: object
    scope: dict
    id: str=field(default_factory=lambda:uuid.uuid4().hex)
    buffer: bytes=b''
    requests: int=0
    total: int=0
    attempted: bool=False
    cancellation_submitted: bool=False
    final_attempted: bool=False
    result: dict=field(default_factory=dict)
    lock: object=field(default_factory=Lock)
    timer: object=None
    closed: bool=False
    expires: float=field(default_factory=lambda:time.monotonic()+300)


class PlaywrightUnsubscribeBrowser:
    _slots=BoundedSemaphore(2)
    def __init__(self,root,transport=None,node=None,keep_sessions=False):
        self.root,self.transport,self.keep_sessions=root,transport or WebsiteTransport(),keep_sessions
        self.node=node or shutil.which('node') or next(iter(sorted((__import__('pathlib').Path.home()/'.nvm/versions/node').glob('*/bin/node'),reverse=True)),None)
        self._sessions={};self._lock=Lock()

    def close(self):
        with self._lock:sessions=list(self._sessions.values())
        for session in sessions:self._close(session)

    def _close(self,session):
        with self._lock:
            if session.closed:return
            session.closed=True;self._sessions.pop(session.id,None)
        if session.timer:session.timer.cancel()
        try:os.killpg(session.process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:session.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:os.killpg(session.process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            session.process.wait()
        session.selector.close()
        for stream in (session.process.stdin,session.process.stdout):
            try:stream.close()
            except (OSError,BrokenPipeError):pass
        self._slots.release()

    def state(self,id,account):
        with self._lock:session=self._sessions.get(id)
        if not session or session.scope.get('account_id')!=account or session.closed:raise ValueError('Isolated session expired; no automatic reopening or resubmission')
        return session.result

    def discard(self,id,account):
        self.state(id,account)
        with self._lock:session=self._sessions[id]
        self._close(session)

    def run(self,scope,emit):
        self.transport.validate(scope['url'])
        if not self.node:return {'state':'failed','reason':'browser_runtime_unavailable'}
        if not self._slots.acquire(False):return {'state':'needs_action','reason':'isolated_browser_busy'}
        try:
            env={key:os.environ[key] for key in ('PATH','HOME','TMPDIR','LANG','PLAYWRIGHT_BROWSERS_PATH') if key in os.environ}
            env['PATH']=str(__import__('pathlib').Path(self.node).parent)+':/usr/bin:/bin'
            process=subprocess.Popen([str(self.node),str(self.root/'browser/worker.mjs')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,start_new_session=True)
            selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
            session=_Session(process,selector,dict(scope))
        except Exception:self._slots.release();raise
        with self._lock:self._sessions[session.id]=session
        session.timer=Timer(300,lambda:self._close(session));session.timer.daemon=True;session.timer.start()
        return self._drive(session,{'type':'start','url':scope['url'],'keep_session':self.keep_sessions},emit)

    def resume(self,scope,emit):
        id=scope['session_id'];self.state(id,scope['account_id'])
        with self._lock:session=self._sessions[id]
        if not session.lock.acquire(False):raise ValueError('Browser is already handling an approved step')
        try:
            session.scope.update({key:scope.get(key) for key in ('allow_all','recipient_email','topic_ids')})
            return self._drive(session,{'type':'continue','expected_snapshot':scope['snapshot_id'],'allow_all':scope.get('allow_all',False),'recipient_email':scope.get('recipient_email'),'topic_ids':scope.get('topic_ids',[]),'inspect_only':scope.get('inspect_only',False)},emit)
        finally:session.lock.release()

    def _drive(self,session,command,emit):
        process=session.process;approved=urlsplit(session.scope['url']);retained=False
        try:
            process.stdin.write((json.dumps(command)+'\n').encode());process.stdin.flush()
            deadline=time.monotonic()+65
            while not session.closed and time.monotonic()<deadline:
                if not session.selector.select(min(1,max(0,deadline-time.monotonic()))):
                    if process.poll() is not None:break
                    continue
                data=os.read(process.stdout.fileno(),65536)
                if not data:break
                session.buffer+=data
                if len(session.buffer)>3_000_000:raise ValueError('Browser protocol bound')
                while b'\n' in session.buffer:
                    line,session.buffer=session.buffer.split(b'\n',1);message=json.loads(line)
                    if message['type']=='result':
                        if message.get('state') not in ('cancelled','requested','needs_action','failed'):raise ValueError('Invalid result')
                        if message.get('proposed_destination'):
                            try:self.transport.validate(message['proposed_destination'])
                            except Exception:message.pop('proposed_destination',None);message['reason']='unsafe_destination'
                        result={key:value for key,value in message.items() if key in ('state','reason','evidence','trace','network_requests','proposed_destination','page_state','failure','http_status','inspect_only','submitted','submission_attempted')}
                        candidate=message.get('page_url')
                        if isinstance(candidate,str):
                            try:
                                location=scope_url(candidate,bool(session.scope.get('allow_all')))
                                if (location.scheme,location.netloc)==(approved.scheme,approved.netloc):result['page_url']=candidate
                            except ValueError:pass
                        retained=bool(not session.closed and self.keep_sessions and message.get('resumable') and result.get('page_state',{}).get('origin')==f'https://{approved.netloc}')
                        if retained:result.update(session_id=session.id,session_expires_in=max(0,int(session.expires-time.monotonic())),resumable=True)
                        session.result=result
                        return result
                    if message['type']=='event':emit('website_control',message['detail']);continue
                    if message['type']!='request':raise ValueError('Invalid browser protocol')
                    session.requests+=1
                    dispatched=False
                    try:
                        parsed=scope_url(message['url'],bool(session.scope.get('allow_all')))
                        if (parsed.scheme,parsed.hostname,parsed.port or 443)!=(approved.scheme,approved.hostname,approved.port or 443):raise ValueError('Unapproved destination')
                        method=message['method']
                        if method not in ('GET','HEAD') and not(method=='POST' and message.get('approved_action') is True):raise ValueError('Unapproved website action')
                        body=base64.b64decode(message.get('body',''),validate=True)
                        if len(body)>65536 or session.requests>60:raise ValueError('Browser request bound')
                        if method=='POST':
                            form_scope(parsed.path,message.get('headers',{}),body,bool(session.scope.get('allow_all')),session.scope.get('recipient_email'))
                            session.attempted=True
                        dispatched=bool(message.get('approved_action') and message.get('action_kind','cancel')=='cancel' and method in ('POST','GET'))
                        if dispatched:
                            emit('website_final_request_started',{'method':method,'host':parsed.hostname,'path_hash':hashlib.sha256(parsed.path.encode()).hexdigest(),'recipient_authorized':bool(session.scope.get('recipient_email')),'all_scope_authorized':bool(session.scope.get('allow_all'))})
                            session.final_attempted=True
                        elif method=='POST':emit('website_step_request_started',{'method':method,'host':parsed.hostname})
                        response=self.transport.fetch(message['url'],method,message.get('headers',{}),body)
                        session.total+=len(response['body'])
                        if session.total>12_000_000:raise ValueError('Browser response budget')
                        if dispatched:session.cancellation_submitted=True;emit('website_final_response_observed',{'http_status':response['status']})
                        reply={'seq':message['seq'],'cancellation_submitted':dispatched,'cancellation_attempted':dispatched,**response}
                    except Exception as exc:
                        emit('website_request_stopped',{'kind':type(exc).__name__,'reason':safe_reason(exc)})
                        reply={'seq':message['seq'],'cancellation_submitted':False,'cancellation_attempted':dispatched,'error':'Website request blocked or unavailable; no retry','error_kind':'unsafe_destination' if isinstance(exc,UnsafeDestination) else 'network_unavailable'}
                    process.stdin.write((json.dumps(reply)+'\n').encode());process.stdin.flush()
        except Exception as exc:
            return {'state':'needs_action','reason':'result_uncertain','failure':{'kind':type(exc).__name__,'reason':safe_reason(exc)},'page_state':session.result.get('page_state'),'submitted':session.cancellation_submitted,'submission_attempted':session.final_attempted}
        finally:
            if not retained:self._close(session)
        return {'state':'needs_action' if session.attempted else 'failed','reason':'result_uncertain' if session.attempted else 'browser_deadline_or_failure','failure':{'kind':'DeadlineOrProcessExit','reason':'Isolated worker ended without an observed confirmation; no replay'},'submitted':session.cancellation_submitted,'submission_attempted':session.final_attempted}

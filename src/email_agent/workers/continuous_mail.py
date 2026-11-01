"""Read-only continuous organizer composition root. Never invokes cleanup."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import time
from email_agent.application.continuous_mail import ContinuousMail
from email_agent.infrastructure.database.continuous_store import ContinuousStore,ReadBudgetExceeded
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
from email_agent.infrastructure.database.sqlite_history_store import SQLiteHistoryStore
from email_agent.infrastructure.gmail.auth import GmailAuthenticator
from email_agent.infrastructure.gmail.client import GmailClient
from email_agent.infrastructure.gmail.reader import GmailEmailReader
from email_agent.infrastructure.gmail.content_reader import GmailEvidenceReader
from email_agent.infrastructure.gmail.request_policy import sync_lock
from email_agent.workers.fill_content import fill_lock
from email_agent.intelligence import build_classifier

ROOT=Path(__file__).resolve().parents[3]


def client(root,account,reserve):
    import requests
    from google.auth.transport.requests import Request
    session=requests.Session();session.mount('https://',requests.adapters.HTTPAdapter(max_retries=0));delegate=Request(session=session)
    def refresh(*args,**kwargs):
        reserve('oauth');kwargs['allow_redirects']=False
        kwargs['timeout']=min(float(kwargs.get('timeout',20) or 20),20)
        return delegate(*args,**kwargs)
    credentials=GmailAuthenticator(root/'credentials.json',root/'tokens'/f'{account}.json').authenticate(allow_interactive=False,refresh_request=refresh)
    return GmailClient(credentials,before_attempt=lambda:reserve('gmail'),max_attempts=2,timeout=12)


class Readers:
    def __init__(self,client):self.metadata,self.evidence=GmailEmailReader(client),GmailEvidenceReader(client)
    def get_metadata(self,account,id):return self.metadata.get_metadata(account,id)
    def read(self,id):return self.evidence.read(id)


class AccountReaders:
    """One authenticated, pooled transport; every wire attempt keeps its owner."""
    def __init__(self,root,account,store,run):
        self.root,self.account,self.store,self.run=root,account,store,run
        self.item=None;self.readers=None
    def __call__(self,account,item):
        if account!=self.account:raise ValueError('Cross-account reader reuse rejected')
        self.item=item
        if self.readers is None:
            self.readers=Readers(client(self.root,account,lambda kind:self.store.reserve_read(self.run,account,self.item['stage']+'_'+kind,self.item)))
        return self.readers


def incremental(root,account,store,history,run):
    reader=client(root,account,lambda kind:store.reserve_read(run,account,'delta_'+kind))
    with store.connect() as db:state=db.execute('SELECT * FROM automation_delta WHERE account_id=?',(account,)).fetchone()
    if not state:
        marker=str(reader.get_profile()['historyId'])
        with store.connect() as db:db.execute('INSERT OR IGNORE INTO automation_delta(account_id,start_history_id) VALUES(?,?)',(account,marker))
        return
    try:
        response=reader.list_history_changes(state['start_history_id'],state['next_token'])
        store.commit_delta(account,state['start_history_id'],state['next_token'],response)
    except Exception as exc:
        if getattr(getattr(exc,'resp',None),'status',None)!=404:raise
        # Expired delta requires a full reference traversal, not a fake success.
        progress=history.progress(account)
        with store.connect() as db:
            observations=[dict(r) for r in db.execute('SELECT * FROM history_observations WHERE account_id=?',(account,))]
            db.execute('INSERT INTO automation_history_archive(account_id,state,observations) VALUES(?,?,?)',(account,json.dumps(progress),json.dumps(observations)))
        marker=str(reader.get_profile()['historyId']);history.restart(account)
        with store.connect() as db:db.execute('UPDATE automation_delta SET start_history_id=?,next_token=NULL WHERE account_id=?',(marker,account))


def inventory(root,account,store,messages,history,run,pages=3):
    with store.connect() as db:
        retry=db.execute('SELECT * FROM automation_inventory_retry WHERE account_id=?',(account,)).fetchone()
    if retry and (retry['due']>time.time() or retry['attempts']>=5):return
    try:
        state=history.progress(account)
        with store.connect() as db:legacy=db.execute('SELECT legacy_pages FROM history_observation_runs WHERE account_id=?',(account,)).fetchone()
        # A reference-only verification traversal is necessary for legacy pages
        # without observed IDs; metadata/bodies are reused, not downloaded again.
        if state['completed'] and legacy and legacy[0]:
            with store.connect() as db:
                observations=[dict(r) for r in db.execute('SELECT * FROM history_observations WHERE account_id=?',(account,))]
                db.execute('INSERT INTO automation_history_archive(account_id,state,observations) VALUES(?,?,?)',(account,json.dumps(state),json.dumps(observations)))
            history.restart(account);state=history.progress(account)
        if state['completed']:return
        reader=client(root,account,lambda kind:store.reserve_read(run,account,'inventory_'+kind))
        for _ in range(pages):
            state=history.progress(account)
            if state['completed']:break
            refs,next_token=reader.list_message_page(state['next_page_token'],200,True)
            ids=[r['id'] for r in refs]
            if len(ids)!=len(set(ids)) or next_token and next_token==state['next_page_token']:raise ValueError('Invalid inventory page')
            cached=[m for id in ids if (m:=messages.get(account,id)) is not None and m.label_ids is not None]
            history.commit_observed_page(account,state['next_page_token'],cached,next_token,ids)
        with store.connect() as db:db.execute('DELETE FROM automation_inventory_retry WHERE account_id=?',(account,))
    except Exception as exc:
        code=getattr(getattr(exc,'resp',None),'status',None)
        with store.connect() as db:
            prior=db.execute('SELECT attempts FROM automation_inventory_retry WHERE account_id=?',(account,)).fetchone();attempt=(prior[0] if prior else 0)+1
            kind='expired_checkpoint' if code==400 else type(exc).__name__
            db.execute('INSERT OR REPLACE INTO automation_inventory_retry VALUES(?,?,?,?)',(account,attempt,time.time()+min(3600,60*4**attempt),kind))
            db.execute('UPDATE automation_accounts SET last_error=? WHERE account_id=?',(kind,account))


def run(root=ROOT,accounts=('personal','university'),bodies=24,pages=3):
    root=Path(root);messages=SQLiteMessageRepository(root/'data/email_agent.db');messages.initialize();classifier=build_classifier(messages)
    history=SQLiteHistoryStore(messages.database_path);store=ContinuousStore(messages.database_path,messages);store.initialize()
    from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore
    SQLiteSemanticOrganizerStore(messages.database_path,root/'data/categories.db').initialize()
    result={}
    with fill_lock(root/'data'):
        for account in accounts:
            if not store.enabled(account) and not store.sync_enabled(account):continue
            try:
                with sync_lock(account,root/'data'):
                    with store.connect() as db:db.execute('UPDATE automation_accounts SET last_error=NULL,wait_reason=NULL WHERE account_id=?',(account,))
                    run_id=store.begin_run(48)
                    history.import_legacy(account,root/'data/historical_sync.db')
                    if store.sync_enabled(account):store.sync_activity(account,'Discovering mail')
                    try:
                        if store.sync_enabled(account):incremental(root,account,store,history,run_id)
                    except Exception as exc:
                        with store.connect() as db:db.execute('UPDATE automation_accounts SET last_error=? WHERE account_id=?',('delta_'+type(exc).__name__,account))
                    if store.sync_enabled(account):inventory(root,account,store,messages,history,run_id,pages)
                    readers=AccountReaders(root,account,store,run_id)
                    result[account]=ContinuousMail(store,messages,classifier,readers).step(account,24,bodies,100)
                    with store.connect() as db:db.execute('UPDATE automation_accounts SET last_run=CURRENT_TIMESTAMP WHERE account_id=?',(account,))
            except Exception as exc:
                result[account]=dict(error_kind=type(exc).__name__,checkpoint_retained=True)
            finally:store.sync_activity(account)
        # Local grouping and bounded AI dispatch use the loopback service with
        # their own durable scope, after releasing account synchronization locks.
    from urllib.request import Request,urlopen
    for account in accounts:
        if not store.enabled(account):continue
        try:
            request=Request('http://127.0.0.1:8000/api/organizer/'+account+'/automation-step',data=b'{}',headers={'Content-Type':'application/json','X-Email-Agent-Action':'message-management'})
            with urlopen(request,timeout=100) as response:result.setdefault(account,{})['grouping']=json.load(response)
        except Exception as exc:result.setdefault(account,{})['grouping']={'error_kind':type(exc).__name__,'checkpoint_retained':True}
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--account',choices=['all','personal','university'],default='all');p.add_argument('--bodies',type=int,default=24);p.add_argument('--pages',type=int,default=3);p.add_argument('--enable',action='store_true');args=p.parse_args()
    if not 0<=args.pages<=5 or not 0<=args.bodies<=32:p.error('pages<=5; bodies<=32')
    accounts=('personal','university') if args.account=='all' else (args.account,)
    if args.enable:
        messages=SQLiteMessageRepository(ROOT/'data/email_agent.db');messages.initialize();SQLiteHistoryStore(messages.database_path);store=ContinuousStore(messages.database_path,messages);store.initialize()
        for account in accounts:store.configure(account,True)
    print(json.dumps(run(accounts=accounts,bodies=args.bodies,pages=args.pages)))

if __name__=='__main__':main()

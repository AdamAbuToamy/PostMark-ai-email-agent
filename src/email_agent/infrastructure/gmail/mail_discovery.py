"""Bounded Gmail metadata reads; no write API or Gemini usage."""
from googleapiclient.errors import HttpError
from email_agent.domain.message import EmailMessage
from email_agent.application.historical_sync import HistoricalSync
from email_agent.infrastructure.database.sqlite_history_store import SQLiteHistoryStore
from email_agent.infrastructure.gmail.reader import GmailEmailReader


class CapturedHistory:
    def __init__(self,reader):self.reader,self.messages=reader,[]
    def list_history_page(self,account_id,page_token=None,page_size=20):
        messages,token=self.reader.list_history_page(account_id,page_token,page_size)
        self.messages=messages
        self.last_discovered_ids=getattr(self.reader,"last_discovered_ids",None)
        self.last_unavailable=getattr(self.reader,"last_skipped",0)
        return messages,token


class GmailMailDiscovery:
    PAGE_SIZE=20
    def __init__(self,gmail,path,legacy):
        self.gmail,self.store,self.legacy=gmail,SQLiteHistoryStore(path),legacy
        self.last_unavailable=0
    def progress(self,account):
        self.store.import_legacy(account,self.legacy)
        state=self.store.progress(account)
        return {**{key:state[key] for key in ('pages_completed','messages_processed','completed')}, **self.store.coverage(account)}
    def backfill(self,account,restart=False):
        state=self.progress(account)
        self.last_unavailable=0
        if restart:self.store.restart(account)
        elif state["completed"]:return []
        reader=CapturedHistory(GmailEmailReader(self.gmail.client(account)))
        HistoricalSync(reader,self.store).execute(account,1,self.PAGE_SIZE)
        self.last_unavailable=getattr(reader,"last_unavailable",0)
        return reader.messages
    def message(self,account,id):
        try:data=self.gmail.client(account).get_message(id,max_attempts=2)
        except HttpError as exc:
            if int(exc.resp.status)==404:raise LookupError('Message unavailable; stored metadata retained') from exc
            raise
        if data['id']!=id:raise ValueError('Gmail identifier mismatch')
        return EmailMessage(id,account,data['sender'],data['subject'],data.get('internal_date_ms',0),data.get('label_ids'),data.get('list_headers'))
    def trash_page(self,account,token):
        return self.label_page(account,token,'TRASH')
    def label_page(self,account,token,label):
        if label not in ('SPAM','TRASH'):raise ValueError('Unsupported mailbox label')
        references,next_token=self.gmail.client(account).list_message_page(page_token=token,page_size=self.PAGE_SIZE,include_spam_trash=True,label_ids=[label])
        messages=[]
        self.last_unavailable=0
        for reference in references:
            try:messages.append(self.message(account,reference['id']))
            except LookupError:self.last_unavailable+=1;continue
        return messages,next_token

"""One durable readonly content campaign. No cleanup, Gemini or Gmail mutations."""
import argparse
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
from email_agent.application.fill_content import FillContent
from email_agent.infrastructure.database.content_fill_store import SQLiteContentFillStore
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
from email_agent.infrastructure.gmail.auth import GmailAuthenticator
from email_agent.infrastructure.gmail.client import GmailClient
from email_agent.infrastructure.gmail.content_reader import GmailEvidenceReader
from email_agent.infrastructure.gmail.request_policy import sync_lock
from email_agent.intelligence import build_classifier


@contextmanager
def fill_lock(directory):
    with (directory/'content-fill.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise ValueError('One content fill is already running across accounts') from exc
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


def build_fill(root):
    from google.auth.transport.requests import Request
    messages=SQLiteMessageRepository(root/'data/email_agent.db');messages.initialize()
    classifier=build_classifier(messages,root/'data/categories.db')
    store=SQLiteContentFillStore(messages.database_path,messages);store.initialize()
    def readers(account,reserve):
        import requests
        session=requests.Session();session.mount('https://',requests.adapters.HTTPAdapter(max_retries=0))
        delegate=Request(session=session)
        def refresh(*args,**kwargs):
            reserve('oauth')
            kwargs['allow_redirects']=False
            return delegate(*args,**kwargs)
        credentials=GmailAuthenticator(root/'credentials.json',root/'tokens'/f'{account}.json').authenticate(allow_interactive=False,refresh_request=refresh)
        return GmailEvidenceReader(GmailClient(credentials,before_attempt=lambda:reserve('gmail_raw')))
    return FillContent(store,messages,classifier,readers)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign',required=True);parser.add_argument('--step',type=int,default=8)
    args=parser.parse_args();root=Path(__file__).resolve().parents[3]
    with fill_lock(root/'data'),sync_lock('personal',root/'data'),sync_lock('university',root/'data'):
        service=build_fill(root)
        print(json.dumps(service.run(args.campaign,args.step)))

if __name__=='__main__':main()

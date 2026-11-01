"""Explicit resumable local-only scan. No Gmail client, AI model or remote operations."""
import argparse
import json
from pathlib import Path
from email_agent.application.mail_management import MailManagement
from email_agent.application.promotional_cleanup import PromotionalCleanup
from email_agent.infrastructure.database.cleanup_store import SQLiteCleanupStore
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
from email_agent.infrastructure.database.sqlite_history_store import SQLiteHistoryStore
from email_agent.intelligence import build_classifier


class LocalHistory:
    def __init__(self,path):self.store=SQLiteHistoryStore(path)
    def progress(self,account):
        value=self.store.progress(account)
        return {key:value[key] for key in ('pages_completed','messages_processed','completed')}


def no_remote(*args,**kwargs):raise RuntimeError('Remote operations are unavailable in the local scanner')


def scan(root,account,page_size=100,restart=False,max_pages=0):
    messages=SQLiteMessageRepository(root/'data'/'email_agent.db')
    messages.initialize()
    classifier=build_classifier(messages,root/'data'/'categories.db')
    cleanup=PromotionalCleanup(messages,classifier.assignments,SQLiteCleanupStore(messages.database_path,root/'data'/'worker_state.db'),no_remote,lambda account:False)
    service=MailManagement(messages,classifier,cleanup,None,LocalHistory(messages.database_path))
    pages=0
    while True:
        value=service.scan(account,restart=restart and pages==0,limit=page_size)
        pages+=1
        print(json.dumps({'account':account,'counts':value['counts'],'scan':value['scan']}),flush=True)
        if value['scan']['status']=='completed' or (max_pages and pages>=max_pages):return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account',choices=('personal','university'),required=True)
    parser.add_argument('--run',action='store_true',help='Explicitly persist local classifications and review proposals')
    parser.add_argument('--restart',action='store_true',help='Recheck all local mail, preserving manual decisions')
    parser.add_argument('--page-size',type=int,default=100,choices=range(1,201),metavar='1..200')
    parser.add_argument('--max-pages',type=int,default=0,help='Stop after this many committed pages; 0 completes the local snapshot')
    args=parser.parse_args()
    if not args.run or args.max_pages<0:parser.error('Pass --run explicitly and use a non-negative --max-pages')
    scan(Path(__file__).resolve().parents[1],args.account,args.page_size,args.restart,args.max_pages)


if __name__=='__main__':main()

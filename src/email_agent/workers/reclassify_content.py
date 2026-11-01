"""Explicit bounded content review; readonly Gmail, durable local audit only.

Example: --account personal --limit 200 --read-cap 0 (local first), then resume
with --read-cap 5. At most ten messages/twenty HTTP attempts per invocation.
No Gemini, labels, send, cleanup worker, interactive OAuth or permission changes.
"""
import argparse
import json
from pathlib import Path
from email_agent.application.content_review import ContentReview
from email_agent.infrastructure.database.content_review_store import SQLiteContentReviewStore
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
from email_agent.infrastructure.gmail.auth import GmailAuthenticator
from email_agent.infrastructure.gmail.client import GmailClient
from email_agent.infrastructure.gmail.content_reader import GmailEvidenceReader
from email_agent.infrastructure.gmail.request_policy import sync_lock
from email_agent.intelligence import build_classifier


def build(root):
    messages=SQLiteMessageRepository(root/'data'/'email_agent.db')
    classifier=build_classifier(messages,root/'data'/'categories.db')
    store=SQLiteContentReviewStore(messages.database_path,messages)
    store.initialize()
    def readers(account):
        credentials=GmailAuthenticator(root/'credentials.json',root/'tokens'/f'{account}.json').authenticate(allow_interactive=False)
        return GmailEvidenceReader(GmailClient(credentials))
    return ContentReview(messages,classifier,store,readers)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account',choices=('personal','university'),required=True)
    parser.add_argument('--limit',type=int,default=100)
    parser.add_argument('--read-cap',type=int,default=0)
    parser.add_argument('--retry-failed',action='store_true')
    args=parser.parse_args()
    if not 1<=args.limit<=200 or not 0<=args.read_cap<=10:parser.error('Use limit1–200 and read-cap0–10')
    root=Path(__file__).resolve().parents[3]
    with sync_lock(args.account,root/'data'):
        service=build(root)
        print(json.dumps(service.run(args.account,args.limit,args.read_cap,args.retry_failed)))


if __name__=='__main__':main()

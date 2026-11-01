"""Resumable, explicitly bounded local reclassification; never writes Gmail."""
from typing import Protocol
from email_agent.application.classify_messages import decide, VERSION


class ContentReader(Protocol):
    def read(self, message_id: str) -> dict: ...


class ContentReviewStore(Protocol):
    def prepare(self, account: str, version: str, retry_failed: bool) -> None: ...
    def page(self, account: str, limit: int) -> list[str]: ...
    def cached(self, account: str, id: str) -> str: ...
    def save(self, account: str, id: str, evidence: dict) -> None: ...
    def finish(self, account: str, id: str, state: str, basis: str, changed: bool) -> None: ...
    def progress(self, account: str) -> dict: ...


class ContentReview:
    def __init__(self, messages, classifier, store: ContentReviewStore, readers):
        self.messages,self.classifier,self.store,self.readers=messages,classifier,store,readers

    def run(self, account: str, limit: int = 100, read_cap: int = 0, retry_failed: bool = False):
        if account not in ('personal','university') or type(limit) is not int or not 1<=limit<=200 or type(read_cap) is not int or not 0<=read_cap<=10:
            raise ValueError('Use 1–200 local messages and 0–10 explicitly authorized body reads per run')
        self.store.prepare(account,VERSION,retry_failed)
        reads=processed=0
        reader=None
        for id in self.store.page(account,limit):
            message=self.messages.get(account,id)
            before=self.classifier.assignments.message_details(account,id)
            if message is None or (before or {}).get('source')=='manual':
                self.store.finish(account,id,'skipped','manual_or_missing',False)
                continue
            body=self.store.cached(account,id)
            _,status,reason=decide(message,body)
            # Sender-domain and broad promotion heuristics cannot replace body
            # inspection for this audit. Explicit receipts/security need no read.
            decisive=reason.startswith(('content_','explicit_submitted_application_')) or reason in {
                'transaction_subject','explicit_transaction_context','github_workflow_structure',
                'github_pull_request_structure','security_evidence','billing_evidence','billing_subject',
                'academic_subject','academic_domain_and_subject','verified_submitted_application_thread'}
            basis='cached_body' if body else 'metadata'
            if not body and not decisive:
                if reads>=read_cap:
                    self.store.finish(account,id,'awaiting_body','body_needed',False)
                    continue
                reads+=1
                try:
                    if reader is None:reader=self.readers(account)
                    evidence=reader.read(id)
                    if not isinstance(evidence,dict) or not isinstance(evidence.get('body'),str) or not evidence['body'].strip():raise ValueError('Invalid decoded body')
                    self.store.save(account,id,evidence)
                    body=evidence['body']
                    basis='gmail_body'
                except Exception:
                    # Unknown read outcome is counted, held for review and never
                    # retried inside this run; only an explicit retry can reopen.
                    self.store.finish(account,id,'failed','body_unavailable',False)
                    break  # Stop this read batch if connectivity/auth is unavailable.
            self.classifier.classify(message)
            after=self.classifier.assignments.message_details(account,id)
            changed=any((before or {}).get(key)!=(after or {}).get(key) for key in ('category_id','status'))
            self.store.finish(account,id,'done',basis,changed)
            processed+=1
        return dict(**self.store.progress(account),processed_this_run=processed,body_reads_this_run=reads,
            max_body_reads=read_cap,max_http_attempts=read_cap*2,gmail_mutations=0,ai_requests=0)

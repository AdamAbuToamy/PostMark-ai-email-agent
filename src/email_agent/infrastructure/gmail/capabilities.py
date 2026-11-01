"""Write credentials are separate from the existing read-only tokens."""
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from email_agent.infrastructure.gmail.auth import GmailAuthenticator
from email_agent.infrastructure.gmail.client import GmailClient

DELETE_SCOPE = "https://mail.google.com/"
MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"


class AccountGmail:
    def __init__(self, root: Path):
        self.root = root

    def capabilities(self, account):
        self._validate(account)
        path = self.root / "tokens" / f"{account}.modify.json"
        enabled = False
        if path.is_file():
            try:
                credentials = Credentials.from_authorized_user_file(str(path))
                enabled = credentials.has_scopes([MODIFY_SCOPE]) and bool(credentials.valid or credentials.refresh_token)
            except (ValueError, OSError):
                pass
        delete_path=self.root/'tokens'/f'{account}.delete.json'
        permanent=False
        if delete_path.is_file():
            try:
                deletion=Credentials.from_authorized_user_file(str(delete_path));permanent=deletion.has_scopes([DELETE_SCOPE]) and bool(deletion.valid or deletion.refresh_token)
            except (ValueError,OSError):pass
        return {"gmail_permanent_delete":bool(permanent),"delete_upgrade_command":f"PYTHONPATH=src .venv/bin/python -m email_agent.workers.upgrade_oauth --account {account} --approve-scope-upgrade --approve-permanent-delete-scope","account_id": account, "gmail_modify": bool(enabled), "scope": "gmail.modify" if enabled else "gmail.readonly",
                "upgrade_command": f"PYTHONPATH=src .venv/bin/python -m email_agent.workers.upgrade_oauth --account {account} --approve-scope-upgrade"}

    def client(self, account, write=False):
        self._validate(account)
        if not write:
            credentials = GmailAuthenticator(self.root / "credentials.json", self.root / "tokens" / f"{account}.json").authenticate()
        else:
            if not self.capabilities(account)["gmail_modify"]:
                raise PermissionError("This account requires the optional gmail.modify OAuth upgrade")
            credentials = Credentials.from_authorized_user_file(str(self.root / "tokens" / f"{account}.modify.json"))
            if credentials.expired and credentials.refresh_token:
                try:
                    credentials.refresh(Request())
                except Exception as exc:
                    raise PermissionError("Write authorization is unavailable; reauthorize this account explicitly") from exc
            if not credentials.valid or not credentials.has_scopes([MODIFY_SCOPE]):
                raise PermissionError("Write authorization expired; reauthorize this account explicitly")
        return GmailClient(credentials)

    @staticmethod
    def _validate(account):
        if account not in ("personal", "university"):
            raise ValueError("Unknown account")

    def actions(self, account):
        return GmailActionAdapter(self.client(account, write=True))

    def delete_actions(self,account):
        self._validate(account)
        if not self.capabilities(account)['gmail_permanent_delete']:raise PermissionError('Permanent deletion requires separately approved https://mail.google.com/ OAuth scope; read-only and modify tokens are unchanged')
        credentials=Credentials.from_authorized_user_file(str(self.root/'tokens'/f'{account}.delete.json'))
        if credentials.expired and credentials.refresh_token:credentials.refresh(Request())
        if not credentials.valid or not credentials.has_scopes([DELETE_SCOPE]):raise PermissionError('Permanent-delete authorization expired')
        return GmailDeleteAdapter(GmailClient(credentials))

    def content_client(self,account):
        self._validate(account)
        credentials=GmailAuthenticator(self.root/'credentials.json',self.root/'tokens'/f'{account}.json').authenticate(allow_interactive=False)
        return GmailClient(credentials)


class GmailActionAdapter:
    def __init__(self, client):
        self.client = client

    def labels(self, message_id):
        return self.client.get_message(message_id, max_attempts=2)["label_ids"]

    def apply(self, operation, message_id, labels):
        if operation not in ("trash", "restore"):
            raise ValueError("Unsupported Gmail operation")
        self.client.trash(message_id) if operation == "trash" else self.client.restore(message_id, labels)
        # Mutation responses are not proof: independently read back Gmail labels.
        updated = self.labels(message_id)
        if updated is None or (operation == "trash" and "TRASH" not in updated) or (operation == "restore" and ("TRASH" in updated or ("TRASH" not in labels and "SPAM" in labels and ("SPAM" in updated or "INBOX" not in updated)))):
            raise ValueError("Gmail did not verify the requested label change; reconcile before retrying")
        return tuple(updated)


class GmailListHeaderReader:
    def __init__(self, client):
        self.client = client

    def read_list_headers(self, message_id):
        return dict(self.client.get_message(message_id,max_attempts=2)["list_headers"])


class GmailDeleteAdapter(GmailActionAdapter):
    def delete(self,message_id):
        # Never automatically retry an irreversible request.
        self.client.delete_permanently(message_id)
    def exists(self,message_id):
        from googleapiclient.errors import HttpError
        try:self.labels(message_id);return True
        except HttpError as exc:
            if exc.resp.status==404:return False
            raise

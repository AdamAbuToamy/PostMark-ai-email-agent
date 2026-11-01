"""Optional, explicitly invoked upgrade; preserves the read-only token file."""
import argparse
import os
import tempfile
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from email_agent.main import PROJECT_ROOT
from email_agent.infrastructure.gmail.capabilities import MODIFY_SCOPE,DELETE_SCOPE
from email_agent.infrastructure.gmail.client import GmailClient


def upgrade(root, account, permanent=False):
    if account not in ("personal", "university"):
        raise ValueError("Unknown account")
    old_path = root / "tokens" / f"{account}.json"
    if not old_path.is_file():
        raise ValueError("Connect the read-only account first")
    old = Credentials.from_authorized_user_file(str(old_path))
    if old.expired and old.refresh_token:
        old.refresh(Request())  # In memory only: preserve the original token file.
    expected = GmailClient(old).get_profile()["emailAddress"].casefold()
    scope=DELETE_SCOPE if permanent else MODIFY_SCOPE
    flow = InstalledAppFlow.from_client_secrets_file(str(root / "credentials.json"), [scope])
    new = flow.run_local_server(host="127.0.0.1", port=0, open_browser=False, prompt="consent", login_hint=expected)
    if not new.has_scopes([scope]) or GmailClient(new).get_profile()["emailAddress"].casefold() != expected:
        raise ValueError("Wrong Google account or scope; no token saved")
    fd, temporary = tempfile.mkstemp(prefix=f"{account}.upgrade-", dir=old_path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(new.to_json())
        os.replace(temporary, old_path.with_name(f"{account}.{'delete' if permanent else 'modify'}.json"))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", choices=("personal", "university"), required=True)
    parser.add_argument("--approve-scope-upgrade", action="store_true")
    parser.add_argument("--approve-permanent-delete-scope",action="store_true",help="Explicitly authorize full Gmail scope in a separate delete token; does not delete mail")
    args = parser.parse_args()
    if not args.approve_scope_upgrade:
        parser.error("Explicit --approve-scope-upgrade is required; no OAuth flow started")
    upgrade(PROJECT_ROOT, args.account, args.approve_permanent_delete_scope)
    print("Separate explicitly approved credentials saved. Existing read-only/modify credentials preserved. No messages changed.")


if __name__ == "__main__":
    main()

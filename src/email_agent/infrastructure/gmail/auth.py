from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


class GmailAuthenticator:
    """Handles OAuth authentication for one Gmail account."""

    SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly"
    ]

    def __init__(
        self,
        credentials_path: Path,
        token_path: Path,
    ):
        self.credentials_path = credentials_path
        self.token_path = token_path

    def authenticate(self, allow_interactive: bool = True, refresh_request=None) -> Credentials:
        credentials = None

        if self.token_path.exists():
            credentials = Credentials.from_authorized_user_file(
                str(self.token_path),
                self.SCOPES,
            )

        if (
            credentials
            and credentials.expired
            and credentials.refresh_token
        ):
            credentials.refresh(refresh_request or Request())

        if not credentials or not credentials.valid:
            if not allow_interactive:
                raise PermissionError('Existing Gmail readonly authorization is unavailable; no interactive authorization was started')
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_path),
                self.SCOPES,
            )

            credentials = flow.run_local_server(
                host="127.0.0.1",
                port=0,
                open_browser=False,
            )

        self.token_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.token_path.write_text(
            credentials.to_json(),
            encoding="utf-8",
        )

        self.token_path.chmod(0o600)

        return credentials

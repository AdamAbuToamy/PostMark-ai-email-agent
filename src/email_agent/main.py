import argparse
from pathlib import Path

from email_agent.application.list_recent_emails import ListRecentEmails
from email_agent.application.sync_recent_emails import SyncRecentEmails
from email_agent.infrastructure.database.account_repository import (
    SQLiteAccountRepository,
)
from email_agent.infrastructure.database.message_repository import (
    SQLiteMessageRepository,
)
from email_agent.domain.account import EmailAccount
from email_agent.infrastructure.gmail.auth import GmailAuthenticator
from email_agent.infrastructure.gmail.client import GmailClient
from email_agent.infrastructure.gmail.reader import GmailEmailReader

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = PROJECT_ROOT / "data" / "email_agent.db"

ACCOUNTS = {
    "personal": {
        "token_path": PROJECT_ROOT / "tokens" / "personal.json",
        "provider": "gmail",
    },
    "university": {
        "token_path": PROJECT_ROOT / "tokens" / "university.json",
        "provider": "gmail",
    },
}


def build_reader(account_id: str) -> GmailEmailReader:
    if account_id not in ACCOUNTS:
        raise ValueError(f"Unknown account: {account_id}")

    authenticator = GmailAuthenticator(
        credentials_path=PROJECT_ROOT / "credentials.json",
        token_path=ACCOUNTS[account_id]["token_path"],
    )

    credentials = authenticator.authenticate()
    client = GmailClient(credentials)
    return GmailEmailReader(client)


def initialize_database() -> tuple[SQLiteAccountRepository, SQLiteMessageRepository]:
    account_repository = SQLiteAccountRepository(DATABASE_PATH)
    message_repository = SQLiteMessageRepository(DATABASE_PATH)

    account_repository.initialize()
    message_repository.initialize()

    for account_id, config in ACCOUNTS.items():
        account_repository.save(
            EmailAccount(
                id=account_id,
                provider=config["provider"],
            )
        )

    return account_repository, message_repository


def main() -> None:
    parser = argparse.ArgumentParser(description="Email Agent CLI")

    parser.add_argument(
        "--account",
        choices=ACCOUNTS.keys(),
        default="personal",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )

    args = parser.parse_args()

    _, message_repository = initialize_database()

    reader = build_reader(args.account)

    from email_agent.intelligence import build_classifier
    sync_use_case = SyncRecentEmails(
        reader=reader,
        message_repository=message_repository,
        classifier=build_classifier(message_repository),
    )

    list_use_case = ListRecentEmails(reader=reader)

    sync_use_case.execute(
        account_id=args.account,
        limit=args.limit,
    )

    messages = list_use_case.execute(
        account_id=args.account,
        limit=args.limit,
    )

    print(f"\nAccount: {args.account}")
    print(f"Messages found: {len(messages)}")
    print(f"Saved in database: {message_repository.count_by_account(args.account)}\n")

    for message in messages:
        print(f"From: {message.sender}")
        print(f"Subject: {message.subject}")
        print("-" * 50)


if __name__ == "__main__":
    main()

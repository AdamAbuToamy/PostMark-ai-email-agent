"""Isolated browser fixture. Never reads production mail, tokens, or AI keys."""
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    import uvicorn
    with tempfile.TemporaryDirectory(prefix="postmark-batch2-") as directory:
        root = Path(directory)
        (root / "frontend").symlink_to(ROOT / "frontend", target_is_directory=True)
        os.environ["EMAIL_AGENT_PROJECT_ROOT"] = str(root)
        from fastapi import FastAPI, HTTPException
        from email_agent.api.app import create_app
        from email_agent.domain.message import EmailMessage
        from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
        from email_agent.intelligence import build_classifier

        messages = SQLiteMessageRepository(root / "data" / "email_agent.db")
        messages.initialize()
        classifier = build_classifier(messages)
        classifier.registry.propose("security", "Security", "Account and sign-in notifications")
        classifier.registry.approve(classifier.registry.proposals()[0]["id"])
        classifier.registry.propose("travel", "Travel", "Flight and reservation updates")
        classifier.registry.propose("scholarships", "Scholarships", "Scholarship notices")
        for account in ("personal", "university"):
            for i, (sender, subject) in enumerate((
                ("AliExpress <orders@example.invalid>", "Your order has shipped"),
                ("Northwind Store <receipts@example.invalid>", "Order confirmation - desk lamp"),
                ("AliExpress <orders@example.invalid>", "Your package was delivered"),
                ("Paper & Co <billing@example.invalid>", "Refund processed"),
                ("Northwind Store <receipts@example.invalid>", "Payment received"),
                ("AliExpress <deals@example.invalid>", "Big sale - shop now"),
                ("Account Team <security@example.invalid>", "New sign-in to your account"),
                ("Account Team <security@example.invalid>", "Your password was updated"),
            )):
                message = EmailMessage(f"fixture-{i}", account, sender, subject, 1793000000000 - i * 86400000, ("INBOX",))
                messages.save(message)
                classifier.classify(message)
                if i >= 6:
                    classifier.manual(account, message.id, "security", "Fixture account notification")
        fixture = FastAPI()
        @fixture.middleware("http")
        async def fixture_origin(request, call_next):
            # The isolated test port stands in for the existing production
            # loopback origin. Keep production origin policy unchanged.
            request.scope["headers"] = [
                (key, b"http://127.0.0.1:8000" if key == b"origin" and value == b"http://127.0.0.1:8011" else value)
                for key, value in request.scope["headers"]
            ]
            return await call_next(request)

        @fixture.get("/api/messages/{account_id}/{message_id}/content")
        def content(account_id: str, message_id: str):
            message = messages.get(account_id, message_id)
            if message is None:
                raise HTTPException(404, "Fixture message not found")
            return {"id": message_id, "sender": message.sender, "recipient": f"{account_id}@example.invalid", "cc": "", "subject": message.subject, "date": "26 October 2026", "body": f"Full message fixture for {account_id}.\n\nThis is synthetic mail for the Postmark browser smoke test."}

        @fixture.post("/api/sync/{account_id}")
        @fixture.post("/api/analyze/{account_id}")
        def network_disabled(account_id: str):
            raise HTTPException(403, "Live Gmail and AI are disabled in the fixture")

        fixture.mount("/", create_app(messages))
        uvicorn.run(fixture, host="127.0.0.1", port=8011, log_level="warning")


if __name__ == "__main__":
    main()

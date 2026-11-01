"""Local classification and review commands; never calls Gmail or Gemini."""
import argparse
import json
from email_agent.main import DATABASE_PATH
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
from email_agent.intelligence import build_classifier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", choices=("personal", "university"), required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run")
    commands.add_parser("status")
    listing = commands.add_parser("list")
    listing.add_argument("--after", default="")
    listing.add_argument("--category")
    review_list = commands.add_parser("reviews")
    review_list.add_argument("--after", type=int, default=0)
    manual = commands.add_parser("assign")
    manual.add_argument("message_id")
    manual.add_argument("category_id")
    manual.add_argument("--reason", required=True)
    manual.add_argument("--application-ref", default="")
    review = commands.add_parser("review")
    review.add_argument("review_id", type=int)
    review.add_argument("decision", choices=("dismissed", "confirmed_for_manual_action"))
    review.add_argument("--reason", required=True)
    args = parser.parse_args()
    messages = SQLiteMessageRepository(DATABASE_PATH)
    messages.initialize()
    service = build_classifier(messages)
    store = service.assignments
    if args.command == "run":
        service.run(args.account)
    elif args.command == "assign":
        service.manual(args.account, args.message_id, args.category_id, args.reason, args.application_ref)
    elif args.command == "review":
        store.review(args.account, args.review_id, args.decision, args.reason)
    elif args.command == "reviews":
        print(json.dumps(store.reviews(args.account, args.after), ensure_ascii=False))
    elif args.command == "list":
        print(json.dumps(store.items(args.account, args.category, args.after), ensure_ascii=False))
    print(json.dumps(store.progress(args.account)))


if __name__ == "__main__":
    main()

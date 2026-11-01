"""Composition for future promotional cleanup, shared by web and incremental worker."""
from email_agent.application.promotional_cleanup import PromotionalCleanup
from email_agent.infrastructure.database.cleanup_store import SQLiteCleanupStore
from email_agent.infrastructure.database.sqlite_message_category_repository import SQLiteMessageCategoryRepository


def build_cleanup(messages, events_path, gmail):
    assignments=SQLiteMessageCategoryRepository(messages.database_path)
    assignments.initialize()
    return PromotionalCleanup(messages,assignments,SQLiteCleanupStore(messages.database_path,events_path),
                              gmail.actions,lambda account:gmail.capabilities(account)["gmail_modify"])

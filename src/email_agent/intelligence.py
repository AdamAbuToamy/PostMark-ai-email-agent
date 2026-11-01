"""Composition root for local-only email intelligence."""
from email_agent.application.classify_messages import ClassifyMessages
from email_agent.application.category_registry import CategoryRegistry
from email_agent.infrastructure.database.sqlite_category_repository import SQLiteCategoryRepository
from email_agent.infrastructure.database.sqlite_message_category_repository import SQLiteMessageCategoryRepository


def build_classifier(messages, categories_path=None):
    assignments = SQLiteMessageCategoryRepository(messages.database_path,categories_path)
    assignments.initialize()
    registry = CategoryRegistry(SQLiteCategoryRepository(categories_path or messages.database_path.parent / "categories.db"))
    registry.initialize()
    return ClassifyMessages(messages, assignments, registry)


def refresh_special_labels(messages, account, reader):
    """Composition only; caller holds the existing account synchronization lock."""
    from email_agent.application.incremental_labels import IncrementalLabels
    from email_agent.infrastructure.database.sqlite_history_store import SQLiteHistoryStore
    return IncrementalLabels(reader,SQLiteHistoryStore(messages.database_path)).execute(account)

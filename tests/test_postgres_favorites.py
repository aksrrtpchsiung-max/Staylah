"""Conversation favorites survive repository restarts and retain ownership."""
import json
import os
from pathlib import Path
import unittest
from uuid import uuid4

from sqlalchemy import delete

from property_agent.persistence.database import build_engine, build_session_factory
from property_agent.persistence.models import ConversationFavoriteRow, ConversationRow
from property_agent.persistence.repositories import SqlChatRepository, SqlConversationFavoriteRepository


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "set TEST_DATABASE_URL for PostgreSQL tests")
class PostgresFavoritesTests(unittest.TestCase):
    def setUp(self):
        self.engine = build_engine(os.environ["TEST_DATABASE_URL"])
        self.sessions = build_session_factory(self.engine)
        self.conversation = "refactor-favorites-" + uuid4().hex
        self.user = "owner-" + uuid4().hex
        SqlChatRepository(self.sessions).ensure_conversation(self.conversation, user_id=self.user)
        self.favorites = SqlConversationFavoriteRepository(self.sessions)
        case = json.loads((Path(__file__).parent / "fixtures/refactor/live_search.json").read_text())
        self.listing = case["search_result"]["data"]["items"][0]["listing_key"]

    def tearDown(self):
        with self.sessions.begin() as session:
            session.execute(delete(ConversationFavoriteRow).where(
                ConversationFavoriteRow.conversation_id == self.conversation))
            session.execute(delete(ConversationRow).where(
                ConversationRow.conversation_id == self.conversation))
        self.engine.dispose()

    def test_retry_and_restart_restore_one_favorite_and_remove_is_idempotent(self):
        first = self.favorites.add(self.conversation, user_id=self.user, listing_key=self.listing)
        self.assertEqual(first, self.favorites.add(
            self.conversation, user_id=self.user, listing_key=self.listing))
        self.engine.dispose()
        restored = SqlConversationFavoriteRepository(build_session_factory(self.engine))
        self.assertEqual(restored.list(self.conversation, user_id=self.user), [first])
        self.assertEqual(restored.counts_by_conversation(
            [self.conversation, self.conversation], user_id=self.user), {self.conversation: 1})
        self.assertTrue(restored.remove(self.conversation, user_id=self.user, listing_key=self.listing))
        self.assertFalse(restored.remove(self.conversation, user_id=self.user, listing_key=self.listing))
        self.assertEqual(restored.list(self.conversation, user_id=self.user), [])

    def test_other_user_cannot_read_change_or_count_saved_homes(self):
        self.favorites.add(self.conversation, user_id=self.user, listing_key=self.listing)
        for operation in ("list", "add", "remove"):
            kwargs = {"user_id": "different-user"}
            if operation != "list":
                kwargs["listing_key"] = self.listing
            with self.subTest(operation=operation), self.assertRaises(PermissionError):
                getattr(self.favorites, operation)(self.conversation, **kwargs)
        self.assertEqual(self.favorites.counts_by_conversation(
            [self.conversation], user_id="different-user"), {})
        self.assertEqual(len(self.favorites.list(self.conversation, user_id=self.user)), 1)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gamma.config import settings
from gamma.memory.service import MemoryService
from gamma.schemas.response import MemoryCandidate


class MemoryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original_database_url = settings.database_url
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        db_path = Path(self._temp_dir.name) / "memory.db"
        settings.database_url = f"sqlite:///{db_path}"
        self.service: MemoryService | None = None

    def tearDown(self) -> None:
        if self.service is not None:
            self.service._engine.dispose()
        settings.database_url = self._original_database_url

    def test_preference_contradiction_replaces_old_fact(self) -> None:
        self.service = MemoryService()
        saved = self.service.persist_candidates(
            [
                MemoryCandidate(type="profile", text="I like jasmine tea.", importance=0.8, tags=["preference"]),
                MemoryCandidate(type="profile", text="I do not like jasmine tea.", importance=0.9, tags=["preference"]),
            ]
        )
        self.assertEqual(saved, 2)
        facts = self.service.get_profile_facts(limit=10)
        fact_texts = [fact.fact_text for fact in facts]
        self.assertIn("I do not like jasmine tea", fact_texts)
        self.assertNotIn("I like jasmine tea", fact_texts)

    def test_near_duplicate_episodic_items_merge(self) -> None:
        self.service = MemoryService()
        first = MemoryCandidate(
            type="episodic",
            text="User said: I am working on Gamma routing. | Assistant replied: Nice.",
            importance=0.6,
            tags=["conversation", "project"],
        )
        second = MemoryCandidate(
            type="episodic",
            text="User said: I am working on Gamma routing | Assistant replied: Sounds good.",
            importance=0.7,
            tags=["conversation", "project"],
        )
        saved = self.service.persist_candidates([first, second], session_id="sess-1")
        self.assertEqual(saved, 1)
        results = self.service.search_memories("Gamma routing", session_id="sess-1", limit=10)
        self.assertEqual(len(results), 1)

    def test_known_person_accounts_resolve_across_platforms(self) -> None:
        self.service = MemoryService()
        saved = self.service.save_known_person(
            {
                "name": "Example Viewer",
                "relationship_to_user": "friend",
                "trust": "trusted",
                "notes": "Test identity",
                "accounts": [
                    {"platform": "twitch", "platform_user_id": "tw-123", "display_name": "Example"},
                    {"platform": "discord", "platform_user_id": "dc-456", "display_name": "Example"},
                ],
            }
        )
        self.assertEqual(saved["name"], "Example Viewer")
        self.assertEqual(len(saved["accounts"]), 2)
        resolved = self.service.resolve_person_identity("twitch", "tw-123")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["trust"], "trusted")

    def test_memory_items_can_be_updated_and_deleted(self) -> None:
        self.service = MemoryService()
        self.service.persist_candidates(
            [MemoryCandidate(type="profile", text="I like tea.", importance=0.8, tags=["preference"])]
        )
        item = self.service.recent_items(limit=1)[0]
        updated = self.service.update_item(
            str(item["kind"]),
            int(item["id"]),
            {"summary": "I like jasmine tea.", "category": "preference", "confidence": 0.9},
        )
        self.assertEqual(updated["summary"], "I like jasmine tea")
        removed = self.service.clear_selected([{"kind": item["kind"], "id": item["id"]}])
        self.assertEqual(removed["cleared_total"], 1)


if __name__ == "__main__":
    unittest.main()

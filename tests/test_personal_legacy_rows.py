"""Fictional fixtures for legacy import identity and provenance boundaries."""

import copy
import json
import math
import re
import unittest

from gamma.coordination.legacy_rows import convert_legacy


class LegacyRowsTests(unittest.TestCase):
    def test_stable_ids_private_unverified_and_original_sources(self):
        tables = {
            "profilefact": [{"id": 17, "category": "preference", "fact_text": "Likes tea",
                "confidence": .9, "subject_type": "primary_user", "created_at": "2026-01-01T12:00:00",
                "attribution": json.dumps({"kind": "self_report", "reporter_name": "Fixture Owner"})}],
            "episodicmemory": [{"id": 9, "summary": "Decided to repair the bicycle",
                "subject_type": "primary_user", "created_at": "2026-01-02"}],
        }
        original = copy.deepcopy(tables)
        first = convert_legacy("fixture-memory", tables)
        self.assertEqual(first, convert_legacy("fixture-memory", dict(reversed(list(tables.items())))))
        self.assertEqual(tables, original)
        self.assertEqual(len(first["records"]), 2)
        self.assertEqual(len(first["sources"]), 2)
        self.assertTrue(all(row["scope"] == "owner_private" and row["certainty"] == "legacy_unverified"
                            for row in first["records"]))
        self.assertTrue(all(row["subject_id"] == "owner" for row in first["records"]))
        self.assertEqual(next(row for row in first["sources"] if row["legacy_id"] == 17)["occurred_at"],
                         "2026-01-01T12:00:00")
        self.assertNotEqual(first["records"][0]["id"], convert_legacy("other-memory", tables)["records"][0]["id"])

    def test_accounts_use_ids_never_same_names_or_trust_as_authority(self):
        result = convert_legacy("fixture", {
            "knownperson": [{"id": 1, "name": "Mira", "trust": "owner"},
                            {"id": 2, "name": "Mira", "trust": "guest"}],
            "personidentity": [{"id": 4, "person_id": 1, "platform": "discord", "platform_user_id": "111", "display_name": "Mira"},
                               {"id": 5, "person_id": 2, "platform": "twitch", "platform_user_id": "222", "display_name": "Mira"}],
        })
        self.assertEqual(len(result["people"]), 2)
        self.assertNotEqual(result["people"][0]["id"], result["people"][1]["id"])
        self.assertEqual(len(result["accounts"]), 2)
        self.assertNotEqual(result["accounts"][0]["person_id"], result["accounts"][1]["person_id"])
        self.assertTrue(all("permissions" not in row and "is_owner" not in row for row in result["people"]))

    def test_ambiguous_account_quarantines_every_binding_and_retains_sources(self):
        result = convert_legacy("fixture", {
            "knownperson": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
            "personidentity": [{"id": 8, "person_id": 1, "platform": "discord", "platform_user_id": "same"},
                               {"id": 9, "person_id": 2, "platform": "discord", "platform_user_id": "same"}],
        })
        self.assertEqual(result["accounts"], [])
        self.assertEqual({row["id"] for row in result["quarantine"]
                          if row["reason"] == "ambiguous_account_link"}, {8, 9})
        sources = [row for row in result["sources"] if row["legacy_table"] == "personidentity"]
        self.assertEqual(len(sources), 2)
        self.assertEqual({row["status"] for row in sources}, {"quarantined"})

    def test_manual_sources_and_other_person_names_do_not_invent_dialogue_or_links(self):
        result = convert_legacy("fixture", {
            "knownperson": [{"id": 1, "name": "Mira"}],
            "profilefact": [{"id": 6, "category": "fact", "fact_text": "Reported hobby",
                "subject_type": "other_person", "subject_name": "Mira", "attribution": '{"kind":"manual"}'}],
            "conversationjournalentry": [{"id": 30, "exchange_id": "old", "role": "user",
                "text": "Original statement", "completion_status": "completed", "privacy_scope": "local_private",
                "speaker_name": "Mira", "occurred_at": "2026-01-01"}],
        })
        self.assertTrue(result["records"][0]["subject_id"].startswith("unresolved-"))
        source = next(row for row in result["sources"] if row["legacy_table"] == "profilefact")
        self.assertEqual((source["origin_kind"], source["source_kind"]), ("manual", "legacy_admin"))
        journals = [row for row in result["sources"] if row["source_kind"] == "legacy_journal"]
        self.assertEqual(len(journals), 1)
        self.assertEqual(journals[0]["text"], "Original statement")
        self.assertIsNone(journals[0]["actor_person_id"])
        self.assertEqual(len(result["records"]), 1)

    def test_entity_ids_survive_mutable_payload_edits_but_sources_are_versions(self):
        tables = {
            "knownperson": [{"id": 1, "name": "First", "notes": "Old note"}],
            "personidentity": [{"id": 2, "person_id": 1, "platform": "discord", "platform_user_id": "123", "display_name": "First"}],
            "profilefact": [{"id": 3, "fact_text": "Old fact", "subject_type": "other_person", "subject_name": "First", "confidence": .2}],
        }
        original = convert_legacy("fixture", tables)
        updated_tables = copy.deepcopy(tables)
        updated_tables["knownperson"][0].update(name="Second", notes="New note")
        updated_tables["personidentity"][0]["display_name"] = "Second"
        updated_tables["profilefact"][0].update(fact_text="New fact", subject_name="Second", confidence=.6)
        updated = convert_legacy("fixture", updated_tables)
        for key, prefix in (("records", "mem"), ("people", "person"), ("accounts", "account")):
            self.assertEqual(original[key][0]["id"], updated[key][0]["id"])
            self.assertRegex(updated[key][0]["id"], rf"^{prefix}-[0-9a-f]{{24}}$")
            self.assertNotEqual(original[key][0]["source_id"], updated[key][0]["source_id"])
        self.assertEqual(original["records"][0]["subject_id"], updated["records"][0]["subject_id"])
        self.assertTrue(all(re.fullmatch(r"src-[0-9a-f]{24}", row["id"]) for row in updated["sources"]))
        self.assertEqual(updated["records"][0]["history"], [])
        self.assertEqual(updated["records"][0]["revision"], 1)

    def test_confidence_is_preserved_clamped_and_never_promotes_certainty(self):
        values = [-4, 0, .37, 1, 8]
        result = convert_legacy("fixture", {"profilefact": [
            {"id": index, "fact_text": f"Fact {index}", "confidence": value, "subject_type": "primary_user"}
            for index, value in enumerate(values)
        ]})
        self.assertEqual({row["legacy_id"]: row["confidence"] for row in result["records"]},
                         {0: 0.0, 1: 0.0, 2: .37, 3: 1.0, 4: 1.0})
        self.assertEqual({row["certainty"] for row in result["records"]}, {"legacy_unverified"})

    def test_nonfinite_boolean_and_nonnumeric_confidence_are_quarantined(self):
        for value in (True, False, float("nan"), float("inf"), -float("inf"), "0.9", None, 10**400):
            with self.subTest(type=type(value).__name__):
                result = convert_legacy("fixture", {"profilefact": [{"id": 1, "fact_text": "Claim", "confidence": value}]})
                self.assertEqual(result["records"], [])
                self.assertEqual(result["sources"][0]["status"], "quarantined")
                self.assertEqual(result["quarantine"][0]["reason"], "invalid_confidence")

    def test_episode_importance_is_not_confidence_and_missing_subject_is_unresolved(self):
        result = convert_legacy("fixture", {"episodicmemory": [{"id": 1, "summary": "Event", "importance": 1}]})
        record = result["records"][0]
        self.assertEqual(record["confidence"], .5)
        self.assertTrue(record["subject_id"].startswith("unresolved-"))

    def test_only_explicit_subject_types_bind_owner_or_self(self):
        subjects = [None, "unknown", "other_person", "owner", "primary_user", "assistant", "shana", [], True]
        result = convert_legacy("fixture", {"episodicmemory": [
            {"id": index, "summary": "Event", "subject_type": value, "subject_name": "Fixture Owner"}
            for index, value in enumerate(subjects)
        ]})
        by_id = {row["legacy_id"]: row["subject_id"] for row in result["records"]}
        self.assertEqual([by_id[index] for index in (4, 5, 6)], ["owner", "self", "self"])
        self.assertTrue(all(by_id[index].startswith("unresolved-") for index in (0, 1, 2, 3, 7, 8)))

    def test_relationship_notes_and_trust_are_historical_only(self):
        person = convert_legacy("fixture", {"knownperson": [{"id": 1, "name": "Mira",
            "relationship_to_user": "Friend", "notes": "Enjoys walking", "trust": "owner"}]})["people"][0]
        self.assertEqual((person["relationship"], person["notes"], person["legacy_trust_label"]),
                         ("Friend", "Enjoys walking", "owner"))
        self.assertTrue({"permissions", "is_owner", "authority", "trust"}.isdisjoint(person))

    def test_nullable_relationship_and_display_name_match_legacy_models(self):
        result = convert_legacy("fixture", {
            "knownperson": [{"id": 1, "name": "Mira", "relationship_to_user": None}],
            "personidentity": [{"id": 2, "person_id": 1, "platform": "discord", "platform_user_id": "123", "display_name": None}],
        })
        self.assertEqual(result["quarantine"], [])
        self.assertEqual(result["people"][0]["relationship"], "")
        self.assertEqual(result["accounts"][0]["display_name"], "")

    def test_exact_account_tuples_do_not_normalize_case_whitespace_or_type(self):
        result = convert_legacy("fixture", {
            "knownperson": [{"id": 1, "name": "Mira"}],
            "personidentity": [
                {"id": 1, "person_id": 1, "platform": "Discord", "platform_user_id": "123"},
                {"id": 2, "person_id": 1, "platform": "discord", "platform_user_id": "123 "},
                {"id": 3, "person_id": 1, "platform": "discord", "platform_user_id": 123},
                {"id": 4, "person_id": "1", "platform": "discord", "platform_user_id": "456"},
                {"id": 5, "person_id": True, "platform": "discord", "platform_user_id": "789"},
            ],
        })
        self.assertEqual({(row["platform"], row["platform_user_id"]) for row in result["accounts"]},
                         {("Discord", "123"), ("discord", "123 ")})
        self.assertEqual({row["id"] for row in result["quarantine"]}, {3, 4, 5})

    def test_conflicting_primary_keys_cannot_choose_arbitrary_people_or_facts(self):
        tables = {
            "knownperson": [{"id": 1, "name": "First"}, {"id": 1, "name": "Second"}],
            "profilefact": [{"id": 2, "fact_text": "First"}, {"id": 2, "fact_text": "Second"}],
            "personidentity": [{"id": 3, "person_id": 1, "platform": "discord", "platform_user_id": "123"}],
        }
        result = convert_legacy("fixture", tables)
        self.assertEqual(result["people"], [])
        self.assertEqual(result["records"], [])
        self.assertEqual(result["accounts"], [])
        self.assertEqual(len(result["sources"]), 5)
        self.assertTrue(all(row["status"] == "quarantined" for row in result["sources"]))
        reversed_rows = {key: list(reversed(rows)) for key, rows in tables.items()}
        self.assertEqual(result, convert_legacy("fixture", reversed_rows))

    def test_identical_rows_deduplicate_without_quarantine(self):
        row = {"id": 1, "fact_text": "Statement", "subject_type": "primary_user"}
        result = convert_legacy("fixture", {"profilefact": [row, copy.deepcopy(row)]})
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(len(result["sources"]), 1)
        self.assertEqual(result["quarantine"], [])

    def test_bad_primary_keys_are_quarantined_without_collisions_or_raw_values(self):
        bad_keys = [None, True, 1.0, "", "\n", "\ud800", [], {}, -1, 2**63]
        rows = [{"id": value, "fact_text": "PRIVATE_FIXTURE_TEXT"} for value in bad_keys]
        result = convert_legacy("fixture", {"profilefact": rows + ["PRIVATE_FIXTURE_TEXT", {}]})
        self.assertEqual(result["records"], [])
        self.assertEqual(result["sources"], [])
        self.assertNotIn("PRIVATE_FIXTURE_TEXT", json.dumps(result["quarantine"]))
        self.assertEqual({row["reason"] for row in result["quarantine"]}, {"invalid_primary_key", "invalid_row"})

    def test_kind_labels_match_original_provenance_not_legacy_table(self):
        origins = ["manual", "reported", "self_report", "tool", "observed", "unrecognized"]
        result = convert_legacy("fixture", {"profilefact": [
            {"id": index, "fact_text": "Statement", "attribution": {"kind": kind}}
            for index, kind in enumerate(origins)
        ]})
        expected = ["legacy_admin", "legacy_reported", "legacy_reported", "legacy_tool", "legacy_observation", "legacy_profile"]
        self.assertEqual({row["legacy_id"]: row["source_kind"] for row in result["records"]}, dict(enumerate(expected)))
        sources = {row["id"]: row for row in result["sources"]}
        self.assertTrue(all(row["source_kind"] == sources[row["source_id"]]["source_kind"] for row in result["records"]))

    def test_bad_attribution_never_creates_authority_or_breaks_conversion(self):
        for value in ('invalid-json', '[]', {"kind": {}}, {"kind": "manual", "permissions": ["all"]}):
            result = convert_legacy("fixture", {"profilefact": [{"id": 1, "fact_text": "Statement", "attribution": value}]})
            self.assertEqual(len(result["records"]), 1)
            self.assertNotIn("permissions", json.dumps(result))

    def test_journals_preserve_experiences_without_guessing_people_from_names(self):
        rows = [{"id": index, "role": role, "speaker_name": name, "text": "Original statement",
                 "completion_status": "completed", "privacy_scope": ("public", "local_generic", "local_private")[index],
                 "occurred_at": "original-time"}
                for index, (role, name) in enumerate((('user', 'shana'), ('user', 'primary_user'), ('assistant', 'Mira')))]
        result = convert_legacy("fixture", {"conversationjournalentry": rows})
        self.assertEqual(result["records"], [])
        by_id = {row["legacy_id"]: row for row in result["sources"]}
        self.assertEqual([by_id[index]["actor_person_id"] for index in range(3)], [None, None, "self"])
        self.assertTrue(all(row["scope"] == "owner_private" for row in by_id.values()))
        self.assertEqual(by_id[0]["legacy_metadata"]["privacy_scope"], "public")
        self.assertEqual(by_id[1]["legacy_metadata"]["privacy_scope"], "local_generic")
        self.assertEqual(by_id[0]["occurred_at"], "original-time")

    def test_incomplete_and_malformed_journals_stay_quarantined_sources(self):
        result = convert_legacy("fixture", {"conversationjournalentry": [
            {"id": 1, "role": "assistant", "text": "Partial", "completion_status": "pending"},
            {"id": 2, "role": "system", "text": "Unknown role", "completion_status": "completed"},
            {"id": 3, "role": "user", "text": {}, "completion_status": "completed"},
        ]})
        self.assertEqual(result["records"], [])
        self.assertEqual(len(result["sources"]), 3)
        self.assertTrue(all(row["status"] == "quarantined" for row in result["sources"]))

    def test_type_and_separator_boundaries_prevent_id_aliasing(self):
        a = convert_legacy("fixture|a", {"profilefact": [{"id": 1, "fact_text": "Text"}]})
        b = convert_legacy("fixture", {"profilefact": [{"id": "a|1", "fact_text": "Text"}]})
        c = convert_legacy("fixture", {"profilefact": [{"id": 1, "fact_text": "Text"}, {"id": "1", "fact_text": "Text"}]})
        self.assertNotEqual(a["records"][0]["id"], b["records"][0]["id"])
        self.assertEqual(len({row["id"] for row in c["records"]}), 2)

    def test_invalid_inputs_and_nested_payloads_are_safe_and_output_is_detached(self):
        for namespace in (None, "", True, "bad\nnamespace", "\ud800"):
            with self.assertRaises(ValueError):
                convert_legacy(namespace, {})
        self.assertEqual(convert_legacy("fixture", None)["quarantine"][0]["reason"], "invalid_tables")
        self.assertEqual(convert_legacy("fixture", {"profilefact": {}})["quarantine"][0]["reason"], "invalid_table_rows")
        row = {"id": 1, "fact_text": "Statement", "attribution": {"kind": "manual", "reporter_name": "Mira"}}
        result = convert_legacy("fixture", {"profilefact": [row]})
        result["sources"][0]["legacy_metadata"]["attribution"]["reporter_name"] = "Changed"
        self.assertEqual(row["attribution"]["reporter_name"], "Mira")
        self.assertTrue(math.isfinite(result["records"][0]["confidence"]))


if __name__ == "__main__":
    unittest.main()

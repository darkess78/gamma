import tempfile
import unittest
from pathlib import Path
from gamma.coding.lessons import LessonStore


def record(identifier='practice-abc', status='verified_practice', attempts=2):
    return {'id':identifier, 'version':1, 'production_verified':False,
            'tasks':[{'id':'dispatch', 'skill':'feature_verification', 'status':status, 'attempt_count':attempts}]}


class CodingLessonsTests(unittest.TestCase):
    def test_observations_are_deduplicated_and_editable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LessonStore(Path(directory))
            store.ingest(record())
            store.ingest(record())
            lesson = store.list()[0]
            self.assertEqual(lesson['observation_count'],1)
            self.assertEqual(lesson['authority'],'observed_practice')
            changed = store.edit(lesson['id'], text='Check the feature through its real interface.', active=False)
            self.assertEqual(changed['authority'],'owner_edited')
            store.ingest(record('practice-def'))
            retained = store.list()[0]
            self.assertEqual(retained['text'],'Check the feature through its real interface.')
            self.assertFalse(retained['active'])
            self.assertEqual(retained['observation_count'],2)

    def test_infrastructure_blocker_is_not_a_model_lesson(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LessonStore(Path(directory))
            store.ingest(record(status='blocked'))
            self.assertEqual(store.list(),[])

    def test_unsafe_ids_and_oversized_edits_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LessonStore(Path(directory))
            store.ingest(record())
            with self.assertRaises(ValueError): store.edit('../outside',text='hello',active=True)
            with self.assertRaises(ValueError): store.edit(store.list()[0]['id'],text='a'*2001,active=True)

    def test_symlink_storage_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'real').mkdir()
            (root/'link').symlink_to(root/'real',target_is_directory=True)
            with self.assertRaises(ValueError): LessonStore(root/'link').ingest(record())


if __name__=='__main__': unittest.main()

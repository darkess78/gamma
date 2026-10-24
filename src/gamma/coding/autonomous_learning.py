"""Observe actual applied work without changing its application outcome."""
import importlib


def observe_applied_work(root, record):
    try:
        from . import lessons
        store = importlib.reload(lessons).LessonStore(root)
        store.ingest_autonomous(record)
        matching = [item['id'] for item in store.list()
                    if record['id'] in item['evidence_runs']]
        return {'recorded': bool(matching), 'lesson_ids': matching}
    except Exception as exc:
        # Learning is a separate outcome; an observer error must not trigger
        # replay or rollback of successfully installed source.
        return {'recorded': False, 'error': type(exc).__name__}

"""Follow a live experiment without retransmitting scientific payloads."""
import json
import time
import unittest
from . import test_service as fixture


class UpdatesCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def test_completion_reappears_after_queued_operation_was_seen(self):
        run = self.client.submit([{'id':'wait','device':'stage','operation':'wait','arguments':{'duration':.2}}])
        page = run.updates()
        cursor = page['cursor']
        run.result(5)
        seen = []
        while True:
            page = run.updates(cursor, limit=1, wait=1)
            seen.extend(page['changes'])
            cursor = page['cursor']
            if not page['has_more']: break
        self.assertIn('operation.completed', [e['kind'] for e in seen])
        self.assertTrue(all('result' not in e for e in seen))
        self.assertEqual(run.updates(cursor,wait=1)['changes'], [])

    def test_item_numbers_never_replace_event_cursor_including_old_records(self):
        run = self.client.start_experiment()
        for kind in ('stream.chunk','episode.transition'):
            with self.engine.store.transaction():
                self.engine.store.event(kind,run.id,sequence=0,result={'large':list(range(10000))})
            page = run.updates()
            last = page['changes'][-1]
            self.assertGreater(last['sequence'], 0)
            self.assertEqual(last['chunk_sequence' if kind=='stream.chunk' else 'transition_sequence'], 0)
            self.assertNotIn('result', last)
            self.assertLess(len(json.dumps(page)), 4000)
            self.assertEqual(run.updates(page['cursor'])['changes'], [])
        run.finish()

    def test_file_pages_are_incremental_and_preserve_legacy_listing(self):
        run = self.client.start_experiment()
        for i in range(5): run.save_file(str(i)+'.txt', str(i))
        cursor, files = 0, []
        while True:
            page = run.file_page(cursor, 2)
            files.extend(page['files']); cursor=page['cursor']
            if not page['has_more']: break
        self.assertEqual(len(files),5)
        self.assertEqual(len(run.files()),5)
        run.save_file('later.txt','later')
        self.assertEqual([f['name'] for f in run.file_page(cursor)['files']],['output/later.txt'])
        run.finish()

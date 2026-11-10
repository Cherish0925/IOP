import json
from pathlib import Path
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from ai4instruments.iop import IOPError
from ai4instruments.iop.engine import Engine
from ai4instruments.iop.server import Server
from . import test_service as fixture


class EpisodesCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def prepare(self):
        run = self.client.start_experiment(resources=['stage'])
        op = run.invoke_record('stage', 'position', request_id=run.id + '-initial')
        args = dict(task={'id': 'positioning', 'version': '1'}, observation=op['result'], seed=19,
                    policy={'id': 'controller', 'version': '2'}, info={'operation_ids': [op['id']]})
        episode = run.begin_episode('sample-0', **args)
        return run, episode, op, args

    def test_ordered_records_export_reconnect_and_no_repeated_instrument_actions(self):
        run, episode, first, args = self.prepare()
        self.assertEqual(run.begin_episode('sample-0', **args).info(), episode.info())
        for i in range(3):
            op = run.invoke_record('stage', 'move_to', {'angle_deg': i + 1}, request_id=f'{run.id}-{i}')
            fields = dict(action={'angle_deg': i+1}, observation=op['result'], reward=-abs(i-1),
                          operation_ids=[op['id']], truncated=i == 2)
            saved = episode.record(i, **fields)
            self.assertEqual(episode.record(i, **fields), saved)
        self.assertEqual(episode.info()['state'], 'ended')
        self.assertEqual(len(run.operations()['operations']), 4)
        page = episode.transitions(limit=2)
        self.assertEqual(page['cursor'], 1)
        self.assertEqual(episode.transitions(after=1)['transitions'][0]['sequence'], 2)
        target = Path(self.temp.name) / 'experience.jsonl'
        episode.export(target)
        records = [json.loads(line) for line in target.read_text().splitlines()]
        self.assertEqual(len(records), 4)
        self.assertEqual(records[0]['observation'], first['result'])
        self.assertTrue(records[-1]['truncated'])
        run.finish()
        self.server.shutdown(); self.server.server_close(); self.engine.close()
        self.engine = Engine(self.temp.name, self.config)
        self.server = Server(('127.0.0.1', 0), self.engine)
        self.engine.start()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client.endpoint = self.engine.endpoint
        self.assertEqual(episode.transitions()['transitions'][-1], saved)

    def test_conflicting_concurrent_records_are_not_silently_overwritten(self):
        run, episode, op, _ = self.prepare()
        def record(action):
            try:
                return episode.record(0, action=action, observation=0, operation_ids=[op['id']])
            except IOPError as exc:
                return exc
        with ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(record, [1, 2]))
        self.assertEqual(sum(isinstance(r, dict) for r in outcomes), 1)
        self.assertEqual(episode.info()['next_sequence'], 1)
        run.finish()

    def test_rejects_missing_steps_wrong_run_and_unfinished_operations(self):
        run, episode, op, args = self.prepare()
        fields = dict(action=1, observation=1, operation_ids=[op['id']])
        for seq, replacement in [(1, {}), (0, {'reward': float('nan')}), (0, {'terminated': 1}), (0, {'operation_ids': []})]:
            with self.assertRaises((IOPError, ValueError)):
                episode.record(seq, **{**fields, **replacement})
        with self.assertRaises(IOPError):
            run.begin_episode('sample-0', **{**args, 'seed': 20})
        other = self.client.submit([{'id': 'p', 'device': 'aux', 'operation': 'position'}])
        other.result(5)
        with self.assertRaises(IOPError):
            episode.record(0, **{**fields, 'operation_ids': [other.operations()['operations'][0]['id']]})
        unfinished = self.client.request('/v1/runs/' + run.id + '/operations', {
            'device': 'stage', 'operation': 'wait', 'arguments': {'duration': 0.3}, 'request_id': 'pending'})
        with self.assertRaises(IOPError):
            episode.record(0, **{**fields, 'operation_ids': [unfinished['id']]})
        self.client.request('/v1/operations/' + unfinished['id'] + '?wait=2')
        episode.record(0, **fields, terminated=True)
        with self.assertRaises(IOPError):
            episode.record(1, **fields)
        run.finish()

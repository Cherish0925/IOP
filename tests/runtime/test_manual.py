"""Manual steps wait for a person, are scheduled with instruments and recorded like operations."""
import tempfile
import time
import unittest
from ai4instruments.iop import IOPError
from ai4instruments.iop.config import initialize
from ai4instruments.iop.engine import Engine
from . import test_service as fixture

BENCH = {'id': 'hands', 'driver': 'manual', 'config': {'name': 'Bench operator', 'operations': {
    'load_sample': {'instruction': 'Place sample {sample} on the stage and weigh it',
                    'parameters': {'sample': {'type': 'string'}},
                    'readings': {'mass_g': {'type': 'number', 'minimum': 0, 'unit': 'g'}}},
    'inspect_beam': {'instruction': 'Is the beam visible on the card?', 'effect': 'observe', 'parameters': {},
                     'readings': {'visible': {'type': 'boolean'}}}}}}


class ManualCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def waiting(self, count=1):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            tasks = self.client.manual_tasks()
            if len(tasks) == count:
                return tasks
            time.sleep(0.02)
        self.fail('Manual task did not appear')

    def test_reported_readings_complete_the_step_and_release_dependent_work(self):
        self.client.register(BENCH)
        run = self.client.submit([
            {'id': 'load', 'device': 'hands', 'operation': 'load_sample', 'arguments': {'sample': 'S-12'}},
            {'id': 'move', 'device': 'stage', 'operation': 'move_to', 'arguments': {'angle_deg': 5}, 'after': ['load']}])
        task = self.waiting()[0]
        self.assertEqual(task['instruction'], 'Place sample S-12 on the stage and weigh it')
        self.assertEqual(task['readings'], {'mass_g': {'type': 'number', 'minimum': 0, 'unit': 'g'}})
        self.assertIn({'action': 'manual_tasks', 'count': 1}, self.client.context()['next_steps'])
        with self.assertRaisesRegex(IOPError, 'Missing parameter: mass_g'):
            self.client.report_manual(task['operation_id'], 'done', {})
        self.assertEqual(len(self.client.manual_tasks()), 1)
        self.client.report_manual(task['operation_id'], 'done', {'mass_g': 1.25}, note='slight tilt', by='Wu')
        result = run.result(5)
        self.assertEqual((result['load']['readings'], result['load']['by'], result['load']['source']),
                         ({'mass_g': 1.25}, 'Wu', 'person'))
        self.assertEqual(result['move']['angle_actual'], 5)
        with self.assertRaises(IOPError):
            self.client.report_manual(task['operation_id'], 'done', {'mass_g': 2})

    def test_unsure_changes_hold_resources_and_unsure_observations_only_fail(self):
        self.client.register(BENCH)
        change = self.client.submit([{'id': 'load', 'device': 'hands', 'operation': 'load_sample', 'arguments': {'sample': 'S-13'}}])
        self.client.report_manual(self.waiting()[0]['operation_id'], 'unsure', note='sample may have slipped')
        self.assertEqual(change.wait(5)['state'], 'failed')
        operation = change.info()['operation_records'][0]
        self.assertTrue(operation['outcome_unknown'])
        self.assertIn('sample may have slipped', operation['error'])
        self.assertIn('device:hands', self.engine.resource_holds)
        self.client.resolve_resources(['device:hands'], 'Sample S-13 found on the stage')

        look = self.client.submit([{'id': 'look', 'device': 'hands', 'operation': 'inspect_beam'}])
        self.client.report_manual(self.waiting()[0]['operation_id'], 'unsure')
        self.assertEqual(look.wait(5)['state'], 'failed')
        self.assertNotIn('outcome_unknown', look.info()['operation_records'][0])
        self.assertFalse(self.engine.resource_holds)

    def test_cancelled_steps_leave_the_task_list(self):
        self.client.register(BENCH)
        run = self.client.submit([{'id': 'load', 'device': 'hands', 'operation': 'load_sample', 'arguments': {'sample': 'S-14'}}])
        self.waiting()
        run.cancel()
        self.assertEqual(run.wait(5)['state'], 'cancelled')
        self.assertEqual(self.client.manual_tasks(), [])


class WorkerCase(unittest.TestCase):
    def test_waiting_steps_do_not_occupy_instrument_workers(self):
        with tempfile.TemporaryDirectory() as home:
            config = initialize(home, True, 0); config.update(discovery_interval=0, workers=1)
            engine = Engine(home, config); engine.start()
            try:
                engine.register(BENCH)
                engine.submit({'steps': [{'id': 'load', 'device': 'hands', 'operation': 'load_sample', 'arguments': {'sample': 'S-15'}}]})
                read = engine.submit({'steps': [{'id': 'read', 'device': 'stage', 'operation': 'position'}]})
                deadline = time.monotonic() + 3
                while engine.store.get('runs', read['id'])['state'] != 'completed' and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(engine.store.get('runs', read['id'])['state'], 'completed')
            finally:
                engine.close()

    def test_manual_steps_cannot_run_in_a_worker_process(self):
        from ai4instruments.iop.drivers import make_driver
        with self.assertRaisesRegex(ValueError, 'remove execution'):
            make_driver({**BENCH, 'execution': {'mode': 'process'}}, {})
        with self.assertRaisesRegex(ValueError, 'describe instruction parameter sample'):
            make_driver({'id': 'x', 'driver': 'manual', 'config': {'operations': {'load': {'instruction': 'Load {sample}'}}}}, {})


if __name__ == '__main__':
    unittest.main()

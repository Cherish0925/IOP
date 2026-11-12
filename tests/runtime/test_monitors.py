"""Monitors read idle instruments periodically, record state changes and can hold resources."""
import time
import unittest
from ai4instruments.iop import IOPError
from . import test_service as fixture


def arm(**monitor):
    return {'id': 'arm', 'driver': 'simulated', 'config': {'kind': 'stage'},
            'monitors': [{'id': 'angle', 'operation': 'position', 'interval_s': 0.1, 'value': ['angle_actual'],
                          'maximum': 10, **monitor}]}


class MonitorCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def state(self, wanted, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = self.client.request('/v1/devices/arm').get('monitors', [])
            if rows and rows[0]['state'] == wanted:
                return rows[0]
            time.sleep(0.05)
        self.fail(f'Monitor did not reach {wanted}: {rows}')

    def events(self, kind):
        return [e for e in self.client.events()['events'] if e['kind'] == kind]

    def move(self, angle):
        return self.client.submit([{'id': 'move', 'device': 'arm', 'operation': 'move_to',
                                    'arguments': {'angle_deg': angle, 'duration': 0}}])

    def test_violation_is_recorded_and_hold_stops_experiments_until_resolved(self):
        self.client.register(arm(on_violation='hold', hold=['aux']))
        self.assertEqual(self.state('ok')['value'], 0)
        self.move(20).result(5)
        alert = self.state('violation')
        self.assertEqual(alert['value'], 20)
        self.assertEqual(len(self.events('monitor.violation')), 1)
        self.assertIn('device:aux', self.engine.resource_holds)
        self.assertIn('inspect_monitors', [s['action'] for s in self.client.context()['next_steps']])
        stopped = self.client.submit([{'id': 'wait', 'device': 'aux', 'operation': 'wait', 'arguments': {'duration': 0}}])
        self.assertEqual(stopped.wait(5)['state'], 'interrupted')

        self.engine.resolve_resources(sorted(self.engine.resource_holds), 'Read 20 degrees; moving the arm back')
        time.sleep(0.4)
        self.assertFalse(self.engine.resource_holds)
        self.move(0).result(5)
        self.state('ok')
        self.assertEqual(len(self.events('monitor.ok')), 1)
        self.client.submit([{'id': 'wait', 'device': 'aux', 'operation': 'wait', 'arguments': {'duration': 0}}]).result(5)
        self.move(15).result(5)
        self.state('violation')
        self.assertIn('device:aux', self.engine.resource_holds)

    def test_recording_monitor_does_not_hold_and_reserved_devices_are_left_alone(self):
        self.client.register(arm())
        self.state('ok')
        with self.client.start_experiment(resources=['arm']) as run:
            run.invoke('arm', 'move_to', {'angle_deg': 30, 'duration': 0})
            time.sleep(0.4)
            self.assertEqual(self.state('ok')['value'], 0)
        self.state('violation')
        self.assertFalse(self.engine.resource_holds)
        self.move(5).result(5)

    def test_definitions_are_checked(self):
        for monitor, message in (({'operation': 'move_to'}, 'effect "observe"'), ({'operation': 'nothing'}, 'unknown operation'),
                                 ({'interval_s': 0.01}, 'at least 0.1'), ({'on_violation': 'stop'}, 'record or hold')):
            with self.assertRaisesRegex(IOPError, message):
                self.client.register(arm(**monitor))
        record = arm(); del record['monitors'][0]['maximum']
        with self.assertRaisesRegex(IOPError, 'minimum, maximum or equals'):
            self.client.register(record)


if __name__ == '__main__':
    unittest.main()

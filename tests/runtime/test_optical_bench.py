"""The simulated fiber bench of examples/iop/optical-bench: its physics kept as operating conditions."""
import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from ai4instruments.iop import cli
from . import test_service as fixture

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples/iop/optical-bench'


def example(name):
    return json.loads((EXAMPLE / name).read_text(encoding='utf-8'))


class OpticalBenchCase(unittest.TestCase):
    def setUp(self):
        fixture.ServiceCase.setUp(self)
        # The example's time constants are minutes and seconds; here they are fractions of a second.
        devices = example('devices.json')
        for record in devices:
            kind = record['config']['kind']
            if kind == 'laser':
                record['config']['warmup_s'] = 1
                record['conditions'][0]['correct']['arguments']['seconds'] = 6
            elif kind == 'table':
                # Long enough that a slow machine still finds the table ringing when it looks.
                record['config']['quiet_s'] = 2
        self.client.register_many(devices)
        bench = example('platform.json')
        quiet = next(c for c in bench['conditions'] if c['id'] == 'table.quiet')
        quiet['correct']['arguments']['seconds'] = 6
        self.client.configure_platform(bench)

    tearDown = fixture.ServiceCase.tearDown

    def maintain(self):
        report = self.client.maintain('fiber-bench', timeout=120)
        return report, {entry['condition']: entry for entry in report['report']}

    def test_the_bench_is_brought_to_its_operating_point_in_the_order_of_its_physics(self):
        report, entries = self.maintain()
        self.assertTrue(report['holds'], report)
        order = [entry['condition'] for entry in report['report']]
        self.assertEqual(order.index('fiber.coupled'), 2)
        self.assertEqual(order[3:], ['image.linear', 'image.dark'])
        # The laser was just switched on, the mirror starts off the beam, and 2 ms saturates the camera.
        self.assertEqual((entries['laser.warm']['result'], entries['laser.warm']['by']), ('corrected', 'laser.wait'))
        self.assertEqual(entries['table.quiet']['result'], 'verified')
        self.assertEqual((entries['fiber.coupled']['found'], entries['fiber.coupled']['by']),
                         ('drifted', 'tune mirror.x, mirror.y'))
        self.assertGreaterEqual(entries['fiber.coupled']['value'], 8.0)
        self.assertEqual(entries['image.linear']['by'], 'tune camera.exposure')
        self.assertEqual(entries['image.dark']['result'], 'verified')
        # An image taken while they hold carries the dark level at its exposure.
        spec = example('acquire.json')
        run = self.client.run(self.client.request('/v1/runs', spec)['id'])
        image = run.result(10)['image']
        self.assertTrue(1500 <= image['peak'] <= 3700 and image['saturated'] == 0, image)
        recorded = run.info()['conditions']
        self.assertEqual(set(recorded), {'laser.warm', 'table.quiet', 'fiber.coupled', 'image.linear', 'image.dark'})
        self.assertAlmostEqual(recorded['image.dark']['reference']['dark'], 100.5, delta=3)
        self.assertEqual(self.client.runs(search=spec['project'])[0]['id'], run.id)
        self.assertEqual(self.client.request('/v1/runs', spec)['id'], run.id)

    def test_a_knock_is_waited_out_and_the_beam_found_again(self):
        self.maintain()
        self.client.submit([{'id': 'knock', 'device': 'table', 'operation': 'disturb', 'arguments': {'severity': 1}}]).result(10)
        # The operator reports it at the bench.
        command = ['iop', '--home', self.temp.name, '--endpoint', self.client.endpoint,
                   'conditions', '--platform', 'fiber-bench', '--disturbance', 'knock']
        with patch('sys.argv', command), patch.dict(os.environ, {'IOP_TOKEN': self.config['token']}), \
                contextlib.redirect_stdout(io.StringIO()):
            cli.main()
        states = {row['id']: row['state'] for row in self.client.conditions('fiber-bench')}
        self.assertEqual({k: v for k, v in states.items() if v == 'due'},
                         {'table.quiet': 'due', 'fiber.coupled': 'due', 'image.linear': 'due', 'image.dark': 'due'})
        report, entries = self.maintain()
        self.assertTrue(report['holds'], report)
        self.assertEqual(entries['laser.warm']['result'], 'held')
        self.assertEqual((entries['table.quiet']['found'], entries['table.quiet']['by']), ('drifted', 'table.wait'))
        # The knock moved the beam's best position: the mirror is tuned again.
        self.assertEqual(entries['fiber.coupled']['result'], 'corrected')
        self.assertGreaterEqual(entries['fiber.coupled']['value'], 8.0)
        reasons = [r.get('reason') for r in self.client.condition('table.quiet')['records'] if r['type'] == 'invalidated']
        self.assertIn('knock: The table was knocked or a door slammed: it rings, and the beam may move', reasons)

    def test_a_zero_taken_with_light_on_offsets_later_readings(self):
        self.maintain()
        run = self.client.submit([{'id': 'zero', 'device': 'power_meter', 'operation': 'zero'},
                                  {'id': 'read', 'device': 'power_meter', 'operation': 'read_power', 'after': ['zero']}])
        self.assertLess(abs(run.result(10)['read']['value']), 0.1)
        run = self.client.submit([{'id': 'block', 'device': 'laser', 'operation': 'shutter', 'arguments': {'open': False}},
                                  {'id': 'zero', 'device': 'power_meter', 'operation': 'zero', 'after': ['block']},
                                  {'id': 'open', 'device': 'laser', 'operation': 'shutter', 'arguments': {'open': True}, 'after': ['zero']},
                                  {'id': 'read', 'device': 'power_meter', 'operation': 'read_power', 'after': ['open']}])
        self.assertGreaterEqual(run.result(10)['read']['value'], 8.0)


if __name__ == '__main__':
    unittest.main()

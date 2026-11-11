"""Calibration records travel with each operation and can stop use after they expire."""
import json
import tempfile
import unittest
from pathlib import Path
from ai4instruments.iop import IOPError
from ai4instruments.iop import calibration
from . import test_service as fixture

CURRENT = {'id': 'CAL-2026-014', 'date': '2026-03-01', 'valid_until': '2099-12-31', 'certificate': 'https://lab.example/cal/014',
           'uncertainty': {'position': {'value': 0.02, 'unit': 'deg', 'coverage_factor': 2}}}


def slide(**fields):
    return {'id': 'slide', 'driver': 'simulated', 'config': {'kind': 'stage'}, **fields}


class FormatCase(unittest.TestCase):
    def test_dates_and_fields_are_checked(self):
        self.assertTrue(calibration.expired({'id': 'a', 'valid_until': '2020-01-01'}))
        self.assertFalse(calibration.expired({'id': 'a', 'valid_until': '2099-01-01T00:00:00+08:00'}))
        for bad, message in (({}, 'calibration.id'), ({'id': 'a', 'valid_until': '31/12/2026'}, 'ISO date'),
                             ({'id': 'a', 'valid_until': '2026-12-31T10:00:00'}, 'UTC offset'),
                             ({'id': 'a', 'enforce': True}, 'needs valid_until'),
                             ({'id': 'a', 'uncertainty': {'read': 0.1}}, 'uncertainty objects')):
            with self.assertRaisesRegex(ValueError, message):
                calibration.check(bad)


class RuntimeCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def test_operations_and_acquisitions_name_the_calibration(self):
        description = self.client.register(slide(calibration=CURRENT))
        self.assertEqual(description['calibration']['id'], 'CAL-2026-014')
        with self.client.start_experiment(resources=['slide']) as run:
            op = run.invoke_record('slide', 'position')
            self.assertEqual(op['calibration'], 'CAL-2026-014')
            self.assertEqual(op['uncertainty'], {'value': 0.02, 'unit': 'deg', 'coverage_factor': 2})
            self.assertNotIn('calibration_expired', op)
            saved = run.save_acquisition('point.json', [op['id']])
            with tempfile.TemporaryDirectory() as folder:
                run.download(saved['id'], Path(folder) / 'point.json')
                document = json.loads((Path(folder) / 'point.json').read_text())
            configuration = next(iter(document['device_configurations'].values()))
            self.assertEqual(configuration['definition']['calibration']['certificate'], 'https://lab.example/cal/014')
        summary = self.client.context()
        self.assertEqual(next(d for d in summary['devices'] if d['id'] == 'slide')['calibration'],
                         {'id': 'CAL-2026-014', 'valid_until': '2099-12-31'})

    def test_expired_calibration_is_marked_or_refused(self):
        self.client.register(slide(calibration={'id': 'CAL-OLD', 'valid_until': '2020-01-01'}))
        run = self.client.submit([{'id': 'read', 'device': 'slide', 'operation': 'position'}])
        run.result(5)
        self.assertTrue(run.info()['operation_records'][0]['calibration_expired'])
        self.assertIn({'action': 'renew_calibration', 'devices': ['slide']}, self.client.context()['next_steps'])

        current = self.client.device_history('slide')[0]
        self.client.configure_device('slide', slide(calibration={'id': 'CAL-OLD', 'valid_until': '2020-01-01', 'enforce': True}),
                                     current['revision'])
        with self.assertRaises(IOPError) as raised:
            self.client.submit([{'id': 'read', 'device': 'slide', 'operation': 'position'}])
        self.assertEqual(raised.exception.details.get('code'), 'calibration_expired')

        current = self.client.device_history('slide')[0]
        self.client.configure_device('slide', slide(calibration={**CURRENT, 'enforce': True}), current['revision'])
        self.assertEqual(self.client.submit([{'id': 'read', 'device': 'slide', 'operation': 'position'}]).result(5)['read']['unit'], 'deg')

    def test_invalid_calibration_is_refused_when_saved(self):
        with self.assertRaisesRegex(IOPError, 'calibration.id'):
            self.client.register(slide(calibration={'valid_until': '2027-01-01'}))


if __name__ == '__main__':
    unittest.main()

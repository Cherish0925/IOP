"""Conditions any instrument can state: steady readings, and agreement with a setpoint or a reference value."""
import math
import random
import time
import unittest
from collections import deque
from ai4instruments.iop.conditions import check
from ai4instruments.iop.drivers import Driver
from . import test_service as fixture

# A controlled quantity of any kind, a temperature, a pressure, a flow or a voltage: the value approaches the
# setpoint with one time constant, may drift and carries noise or a given sequence of deviations.
PROCESS = {}


class Loop(Driver):
    def describe(self):
        value = {'type': 'number'}
        return {'operations': {
            'set_setpoint': {'parameters': {'value': value}},
            'setpoint': {'effect': 'observe', 'parameters': {}},
            'read': {'effect': 'observe', 'parameters': {}},
            'wait': {'effect': 'observe', 'parameters': {'seconds': {'type': 'number', 'minimum': 0, 'maximum': 60}}},
            'set_offset': {'parameters': {'value': {'type': 'number', 'minimum': -1, 'maximum': 1}}},
            'offset': {'effect': 'observe', 'parameters': {}}}}

    def invoke(self, operation, arguments, cancelled):
        p, now = PROCESS, time.monotonic()
        if operation == 'set_setpoint':
            p.update(start=self.present(now), setpoint=arguments['value'], since=now)
        elif operation == 'set_offset':
            p['offset'] = arguments['value']
        elif operation == 'wait':
            cancelled.wait(arguments['seconds'])
        elif operation == 'setpoint':
            return {'value': p['setpoint']}
        elif operation == 'offset':
            return {'value': p['offset']}
        elif operation == 'read':
            p['reads'] += 1
            extra = p['sequence'][0] if p['sequence'] else p['rng'].gauss(0, p['noise'])
            p['sequence'].rotate(-1)
            return {'value': self.present(now) + extra}
        return {}

    def present(self, now):
        p = PROCESS
        settling = (p['start'] - p['setpoint']) * math.exp(-(now - p['since']) / p['tau'])
        return p['setpoint'] + settling + p['offset'] + p['drift'] * (now - p['since'])


class FormsCase(unittest.TestCase):
    def setUp(self):
        fixture.ServiceCase.setUp(self)
        PROCESS.update(setpoint=20.0, start=20.0, since=time.monotonic(), tau=0.2, offset=0.0, drift=0.0, noise=0.0,
                       sequence=deque(), rng=random.Random(3), reads=0)
        self.client.register({'id': 'loop', 'driver': __name__ + ':Loop'})

    tearDown = fixture.ServiceCase.tearDown

    def bench(self, *conditions):
        self.client.configure_platform({'schema_version': 1, 'id': 'bench', 'devices': ['loop'], 'conditions': list(conditions)})

    def entry(self, **options):
        report = self.client.maintain('bench', **options)
        return report['report'][-1]

    def test_a_value_agrees_with_the_setpoint_the_instrument_holds(self):
        self.bench({'id': 'loop.at_setpoint', 'kind': 'equilibrium', 'description': 'The value has reached its setpoint',
                    'verify': {'device': 'loop', 'operation': 'read'},
                    'accept': {'target': {'device': 'loop', 'operation': 'setpoint'}, 'within': 0.5},
                    'correct': {'device': 'loop', 'operation': 'wait', 'arguments': {'seconds': 4}}})
        # A time constant a slow machine still sees the value approach with.
        PROCESS['tau'] = 0.5
        self.client.submit([{'id': 'heat', 'device': 'loop', 'operation': 'set_setpoint', 'arguments': {'value': 300}}]).result(5)
        entry = self.entry()
        # 280 below the setpoint just after the change; eight time constants later within half a unit of it.
        self.assertEqual((entry['found'], entry['result'], entry['by']), ('drifted', 'corrected', 'loop.wait'))
        self.assertLess(abs(entry['value']), 0.5)
        records = [r for r in self.client.condition('loop.at_setpoint')['records'] if r['type'] == 'verified']
        self.assertLess(records[0]['value'], -200)
        # A new setpoint is a new target: the same tolerance follows it.
        self.client.submit([{'id': 'cool', 'device': 'loop', 'operation': 'set_setpoint', 'arguments': {'value': 77}}]).result(5)
        self.assertEqual(self.entry(force=True)['found'], 'drifted')

    def test_readings_are_judged_by_their_spread_and_drift(self):
        steady = {'id': 'loop.steady', 'kind': 'equilibrium', 'description': 'Readings are steady',
                  'verify': {'device': 'loop', 'operation': 'read'}, 'samples': 6, 'interval': 0.05,
                  'accept': {'statistic': 'span', 'maximum': 0.05}}
        self.bench(steady)
        entry = self.entry()
        self.assertEqual((entry['result'], entry['value']), ('verified', 0))
        self.assertEqual(PROCESS['reads'], 6)
        PROCESS['noise'] = 1.0
        self.assertEqual(self.entry(force=True)['result'], 'lost')
        # A slow drift has a small spread over a short window, and a slope.
        drifting = {**steady, 'accept': {'statistic': 'slope', 'maximum': 0.2}}
        self.client.configure_platform({'schema_version': 1, 'id': 'bench', 'devices': ['loop'], 'conditions': [drifting]},
                                       expected_revision=1)
        PROCESS.update(noise=0.0, drift=1.0)
        entry = self.entry(force=True)
        self.assertEqual(entry['result'], 'lost')
        self.assertAlmostEqual(entry['value'], 1.0, delta=0.15)
        PROCESS['drift'] = 0.0
        self.assertEqual(self.entry(force=True)['result'], 'verified')

    def test_a_reference_standard_agrees_with_its_certified_value(self):
        # A standard certified at 100 units is read through the instrument; 0.1 % is the tolerance.
        PROCESS.update(setpoint=100.0, start=100.0, offset=0.3)
        self.bench({'id': 'loop.calibrated', 'kind': 'calibration', 'description': 'The standard reads its certified value',
                    'verify': {'device': 'loop', 'operation': 'read'},
                    'accept': {'target': 100.0, 'relative': 0.001},
                    'correct': {'tune': {'device': 'loop', 'operation': 'set_offset', 'argument': 'value',
                                         'read': {'device': 'loop', 'operation': 'offset'},
                                         'step': 0.1, 'resolution': 0.01, 'minimum': -1, 'maximum': 1}}})
        entry = self.entry()
        self.assertEqual((entry['found'], entry['result'], entry['by']), ('drifted', 'corrected', 'tune loop.value'))
        self.assertLess(abs(entry['value']), 0.01)

    def test_a_reference_taken_over_several_samples_is_their_mean(self):
        PROCESS['sequence'].extend([-0.2, 0.1, 0.3, -0.2])
        self.bench({'id': 'loop.baseline', 'kind': 'reference', 'description': 'The baseline is known',
                    'verify': {'device': 'loop', 'operation': 'read'}, 'samples': 4,
                    'accept': {'statistic': 'std', 'maximum': 1}, 'reference': {'baseline': ['value']}})
        self.assertTrue(self.client.maintain('bench')['holds'])
        self.assertAlmostEqual(self.client.condition('loop.baseline')['reference']['baseline'], 20.0, places=9)

    def test_an_instrument_states_its_own_agreement_with_its_setpoint(self):
        own = {'id': 'at_setpoint', 'description': 'At its setpoint', 'verify': {'operation': 'read'},
               'accept': {'target': {'operation': 'setpoint'}, 'within': 0.5}}
        self.client.register({'id': 'loop2', 'driver': __name__ + ':Loop', 'conditions': [own]})
        self.assertTrue(self.client.maintain(device='loop2')['holds'])
        self.assertEqual(self.client.condition('loop2.at_setpoint')['accept']['target']['device'], 'loop2')


class FormDefinitionCase(unittest.TestCase):
    def test_forms_are_checked(self):
        base = {'id': 'c', 'description': 'A condition', 'verify': {'device': 'loop', 'operation': 'read'}}
        for change, message in (
                ({'accept': {'target': 5, 'within': 1, 'maximum': 6}}, 'a target takes within or relative'),
                ({'accept': {'target': 5}}, 'give within, an absolute tolerance, or relative'),
                ({'accept': {'maximum': 6, 'within': 1}}, 'within and relative belong to a target'),
                ({'accept': {'target': 5, 'within': -1}}, 'within is a nonnegative number'),
                ({'accept': {'target': {'device': 'loop'}, 'within': 1}}, 'target is one operation'),
                ({'accept': {'target': 'five', 'within': 1}}, 'target is a number, or the reading that gives it'),
                ({'accept': {'statistic': 'median', 'maximum': 1}}, 'statistic is one of mean, std, span, slope'),
                ({'accept': {'statistic': 'std', 'maximum': 1}}, 'std, span and slope compare several readings'),
                ({'accept': {'maximum': 1}, 'samples': 0}, 'samples is the number of readings'),
                ({'accept': {'maximum': 1}, 'samples': 3, 'interval': -1}, 'interval is the time between samples')):
            with self.assertRaisesRegex(ValueError, message):
                check([{**base, **change}])
        checked = check([{**base, 'accept': {'target': {'device': 'loop', 'operation': 'setpoint'}, 'within': 1}}])[0]
        self.assertEqual(checked['accept']['target']['value'], ['value'])


if __name__ == '__main__':
    unittest.main()

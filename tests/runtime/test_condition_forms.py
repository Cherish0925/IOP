"""Conditions any instrument can state: steady readings, and agreement with a setpoint or a reference value."""
import math
import random
import threading
import time
import unittest
from collections import deque
from unittest.mock import patch
from ai4instruments.iop import IOPError
from ai4instruments.iop.conditions import check, outcome
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
            'setpoint': {'effect': 'observe', 'parameters': {}, 'result': {'unit': self.config.get('setpoint_unit', 'K')}},
            'read': {'effect': 'observe', 'parameters': {}, 'result': {'unit': 'K'}},
            'raw': {'effect': 'observe', 'parameters': {}},
            'wait': {'effect': 'observe', 'parameters': {'seconds': {'type': 'number', 'minimum': 0, 'maximum': 60}}},
            'set_offset': {'parameters': {'value': {'type': 'number', 'minimum': -1, 'maximum': 1}}},
            'offset': {'effect': 'observe', 'parameters': {}},
            # A status register for each channel, and the command that clears it.
            'status': {'effect': 'observe', 'parameters': {'channel': {'type': 'integer', 'minimum': 1, 'maximum': 4}}},
            'clear': {'parameters': {'channel': {'type': 'integer', 'minimum': 1, 'maximum': 4}}}}}

    def invoke(self, operation, arguments, cancelled):
        p, now = PROCESS, time.monotonic()
        if operation == 'set_setpoint':
            p.update(start=self.present(now), setpoint=arguments['value'], since=now)
        elif operation == 'set_offset':
            p['offset'] = arguments['value']
        elif operation == 'wait':
            cancelled.wait(arguments['seconds'])
        elif operation == 'setpoint':
            return {'value': p['setpoint'] + self.config.get('setpoint_offset', 0)}
        elif operation == 'offset':
            return {'value': p['offset']}
        elif operation == 'status':
            return {'value': p['status'].get(arguments['channel'], 0)}
        elif operation == 'clear':
            p['status'].pop(arguments['channel'], None)
        elif operation in ('read', 'raw'):
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
                       sequence=deque(), rng=random.Random(3), reads=0, status={})
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

    def test_tuning_stops_at_an_accepted_trial_even_below_the_requested_gain(self):
        PROCESS['offset'] = 0.11
        self.bench({'id': 'loop.calibrated', 'description': 'The reference agrees within tolerance',
                    'verify': {'device': 'loop', 'operation': 'read'},
                    'accept': {'target': 20, 'within': 0.1},
                    'correct': {'tune': {'device': 'loop', 'operation': 'set_offset', 'argument': 'value',
                                         'read': {'device': 'loop', 'operation': 'offset'},
                                         'step': 0.02, 'resolution': 0.01, 'minimum': -1, 'maximum': 1},
                                'until': 'accept', 'gain': 0.05}})
        self.assertEqual(self.entry()['result'], 'corrected')
        self.assertAlmostEqual(PROCESS['offset'], 0.09)

    def test_a_target_reported_as_uncertain_cannot_establish_a_condition(self):
        target = {'device': 'loop', 'operation': 'setpoint'}
        self.bench({'id': 'loop.accepted', 'description': 'The value agrees with its setpoint',
                    'verify': {'device': 'loop', 'operation': 'read'},
                    'accept': {'target': target, 'within': 0.1}},
                   {'id': 'loop.usable', 'description': 'The setpoint makes the reading usable',
                    'verify': {'device': 'loop', 'operation': 'read'}, 'accept': {'minimum': 19},
                    'usable': {'target': target, 'within': 0.1}})
        driver = self.engine.devices['loop']
        invoke = driver.invoke

        def uncertain(operation, arguments, cancelled):
            result = invoke(operation, arguments, cancelled)
            if operation == 'setpoint':
                result['quality'] = {'level': 'uncertain', 'detail': 'The controller lost its reference'}
            return result

        with patch.object(driver, 'invoke', uncertain):
            report = self.client.maintain('bench')
        self.assertFalse(report['holds'])
        self.assertEqual([(r['result'], r['found']) for r in report['report']], [('lost', 'unusable')] * 2)
        self.assertTrue(self.client.maintain('bench')['holds'])

    def test_a_target_reading_is_compared_in_the_unit_of_the_verification(self):
        self.client.register({'id': 'thermostat', 'driver': __name__ + ':Loop',
                              'config': {'setpoint_unit': '°C', 'setpoint_offset': -273.15}})
        condition = {'id': 'loop.at_setpoint', 'description': 'The temperature agrees with the controller',
                     'verify': {'device': 'loop', 'operation': 'read'},
                     'accept': {'target': {'device': 'thermostat', 'operation': 'setpoint'}, 'within': 0.01}}
        self.client.configure_platform({'schema_version': 1, 'id': 'bench', 'devices': ['loop', 'thermostat'],
                                        'conditions': [condition]})
        entry = self.entry()
        self.assertEqual(entry['result'], 'verified')
        self.assertAlmostEqual(entry['value'], 0)

    def test_saved_readings_keep_the_quality_of_only_the_results_the_condition_uses(self):
        self.client.save_operation({
            'id': 'loop.readings', 'layer': 'standard', 'description': 'Read the quantity and a diagnostic',
            'steps': [{'id': 'diagnostic', 'device': 'loop', 'operation': 'raw'},
                      {'id': 'read', 'device': 'loop', 'operation': 'read'}],
            'result': {'value': {'step': 'read', 'path': ['value']},
                       'diagnostic': {'step': 'diagnostic', 'path': ['value']}}})
        self.client.save_operation({
            'id': 'loop.diagnostic', 'layer': 'standard', 'description': 'Read the diagnostic through another method',
            'steps': [{'id': 'readings', 'operation': 'loop.readings'}],
            'result': {'value': {'step': 'readings', 'path': ['diagnostic']}}})
        saved = {'operation': 'loop.readings'}
        self.bench({'id': 'reading.good', 'description': 'Use the good named reading', 'verify': saved,
                    'accept': {'minimum': 19, 'maximum': 21}},
                   {'id': 'reading.bad', 'description': 'Use the uncertain named reading', 'verify': saved,
                    'accept': {'value': ['diagnostic'], 'minimum': 19}},
                   {'id': 'target.good', 'description': 'Use the good named target',
                    'verify': {'device': 'loop', 'operation': 'read'},
                    'accept': {'target': {**saved, 'value': ['value']}, 'within': 0.1}},
                   {'id': 'target.bad', 'description': 'Use the uncertain named target',
                    'verify': {'device': 'loop', 'operation': 'read'},
                    'accept': {'target': {**saved, 'value': ['diagnostic']}, 'within': 0.1}},
                   {'id': 'nested.bad', 'description': 'The nested method still uses uncertain data',
                    'verify': {'operation': 'loop.diagnostic'}, 'accept': {'minimum': 19}},
                   {'id': 'reference.bad', 'description': 'The retained baseline must also be usable', 'verify': saved,
                    'accept': {'minimum': 19}, 'reference': {'baseline': ['diagnostic']}})
        driver = self.engine.devices['loop']
        invoke = driver.invoke

        def uncertain(operation, arguments, cancelled):
            result = invoke(operation, arguments, cancelled)
            if operation == 'raw':
                result['quality'] = {'level': 'uncertain', 'detail': 'Diagnostic input is overloaded'}
            return result

        with patch.object(driver, 'invoke', uncertain):
            report = self.client.maintain('bench')
        found = {r['condition']: r for r in report['report']}
        for key in ('reading.good', 'target.good'):
            self.assertEqual(found[key]['result'], 'verified')
        for key in ('reading.bad', 'target.bad', 'nested.bad', 'reference.bad'):
            self.assertEqual((found[key]['result'], found[key].get('found')), ('lost', 'unusable'))
        # Qualification for a condition does not rewrite the method's public numeric result.
        first = self.engine.store.batches(report['run_id'])[0]
        receipt = self.client.request('/v1/runs/' + report['run_id'] + '/batches?request_id=' + first['request_id'])
        self.assertEqual(receipt['results']['verify'], {'value': 20, 'diagnostic': 20})

    def test_revising_the_target_method_or_instrument_requires_a_new_observation(self):
        self.client.register({'id': 'reference', 'driver': __name__ + ':Loop'})
        method = {'id': 'reference.read', 'layer': 'standard', 'description': 'Read the reference setpoint',
                  'steps': [{'id': 'read', 'device': 'reference', 'operation': 'setpoint'}],
                  'result': {'value': {'step': 'read', 'path': ['value']}}}
        self.client.save_operation(method)
        self.client.configure_platform({'schema_version': 1, 'id': 'bench', 'devices': ['loop', 'reference'],
                                        'conditions': [
                                            {'id': name, 'description': 'Agree with the reference',
                                             'verify': {'device': 'loop', 'operation': 'read'},
                                             'accept': {'target': target, 'within': 0.1}}
                                            for name, target in (
                                                ('loop.method', {'operation': 'reference.read'}),
                                                ('loop.direct', {'device': 'reference', 'operation': 'setpoint'}))]})
        self.assertTrue(self.client.maintain('bench')['holds'])
        self.client.save_operation({**method, 'description': 'A revised reference method'})
        self.assertEqual(self.client.condition('loop.method')['state'], 'unknown')
        self.assertEqual(self.client.condition('loop.direct')['state'], 'holds')
        self.assertTrue(self.client.maintain('bench')['holds'])
        current = self.client.device_history('reference')[0]
        self.client.configure_device('reference', {**current['definition'], 'config': {'setpoint_offset': 1}},
                                     current['revision'])
        self.assertEqual([c['state'] for c in self.client.conditions('bench')], ['unknown', 'unknown'])
        self.assertFalse(self.client.maintain('bench')['holds'])

    def test_cancellation_interrupts_the_gap_between_samples(self):
        self.bench({'id': 'loop.steady', 'description': 'The readings are steady',
                    'verify': {'device': 'loop', 'operation': 'read'}, 'samples': 2, 'interval': 60,
                    'accept': {'statistic': 'span', 'maximum': 0.1}})
        observed = threading.Event()
        original = self.engine.conditions._execute

        def executed(*args, **kwargs):
            result = original(*args, **kwargs)
            observed.set()
            return result

        with patch.object(self.engine.conditions, '_execute', executed):
            job = self.client.request('/v1/conditions/maintain', {'platform': 'bench'})
            self.assertTrue(observed.wait(3))
            self.client.run(job['run_id']).cancel()
            ended = self.client.request('/v1/maintenance/' + job['id'] + '?wait=2')
        self.assertEqual(ended['state'], 'interrupted')
        self.assertEqual(PROCESS['reads'], 1)

    def test_a_reference_taken_over_several_samples_is_their_mean(self):
        PROCESS['sequence'].extend([-0.2, 0.1, 0.3, -0.2])
        self.bench({'id': 'loop.baseline', 'kind': 'reference', 'description': 'The baseline is known',
                    'verify': {'device': 'loop', 'operation': 'read'}, 'samples': 4,
                    'accept': {'statistic': 'std', 'maximum': 1}, 'reference': {'baseline': ['value']}})
        self.assertTrue(self.client.maintain('bench')['holds'])
        self.assertAlmostEqual(self.client.condition('loop.baseline')['reference']['baseline'], 20.0, places=9)

    def test_tolerances_are_given_in_any_unit_of_the_reading(self):
        read = {'device': 'loop', 'operation': 'read'}
        self.bench({'id': 'loop.near', 'description': 'Near 20 K', 'verify': read,
                    'accept': {'target': '-253.15 °C', 'within': '50 mK'}},
                   {'id': 'loop.quiet', 'description': 'Quiet', 'verify': read, 'samples': 3,
                    'accept': {'statistic': 'std', 'maximum': '5 mK'}},
                   {'id': 'loop.still', 'description': 'Not drifting', 'verify': read, 'samples': 3, 'interval': 0.02,
                    'accept': {'statistic': 'slope', 'maximum': '1 K/min'}},
                   {'id': 'loop.standard', 'description': 'Within 0.1 % of 20 K', 'verify': read,
                    'accept': {'target': 20, 'relative': '0.1 %'}},
                   {'id': 'loop.quantity', 'description': 'Near 20 K, with explicit quantity objects', 'verify': read,
                    'accept': {'target': {'value': -253.15, 'unit': '°C'}, 'within': {'value': 50, 'unit': 'mK'},
                               'relative': {'value': 0.1, 'unit': '%'}}})
        accepted = {c['id']: c['accept'] for c in self.client.platform('bench')['definition']['conditions']}
        self.assertAlmostEqual(accepted['loop.near']['target'], 20.0)
        self.assertAlmostEqual(accepted['loop.near']['within'], 0.05)
        self.assertAlmostEqual(accepted['loop.quiet']['maximum'], 0.005)
        self.assertAlmostEqual(accepted['loop.still']['maximum'], 1 / 60)
        self.assertAlmostEqual(accepted['loop.standard']['relative'], 0.001)
        self.assertAlmostEqual(accepted['loop.quantity']['target'], 20.0)
        self.assertAlmostEqual(accepted['loop.quantity']['within'], 0.05)
        self.assertAlmostEqual(accepted['loop.quantity']['relative'], 0.001)
        self.assertTrue(self.client.maintain('bench')['holds'])
        for change, message in ((dict(read, operation='raw'), 'the unit of the reading is not described'),
                                (read, 'the reading is in K: mV is not a unit of the same quantity as K')):
            with self.assertRaisesRegex(IOPError, message):
                self.client.configure_platform({'schema_version': 1, 'id': 'bench', 'devices': ['loop'], 'conditions': [
                    {'id': 'loop.near', 'description': 'Near', 'verify': change,
                     'accept': {'target': 20, 'within': '5 mV' if change is read else '5 mK'}}]}, expected_revision=1)

    def test_one_condition_is_written_for_every_channel_and_judged_by_status_bits(self):
        # Bit 2 (value 4) is a fault; bit 0 is information and does not matter.
        PROCESS['status'].update({2: 4, 3: 1})
        self.bench({'id': 'loop.channel{channel}_ok', 'description': 'Channel {channel} reports no fault',
                    'each': {'channel': [1, 2, 3]},
                    'verify': {'device': 'loop', 'operation': 'status', 'arguments': {'channel': '{channel}'}},
                    'accept': {'bits': 4},
                    'correct': {'device': 'loop', 'operation': 'clear', 'arguments': {'channel': '{channel}'}}})
        conditions = self.client.platform('bench')['definition']['conditions']
        self.assertEqual([c['id'] for c in conditions], ['loop.channel1_ok', 'loop.channel2_ok', 'loop.channel3_ok'])
        self.assertEqual(conditions[1]['verify']['arguments'], {'channel': 2})
        report = {e['condition']: e for e in self.client.maintain('bench')['report']}
        self.assertEqual(report['loop.channel1_ok']['result'], 'verified')
        self.assertEqual((report['loop.channel2_ok']['found'], report['loop.channel2_ok']['result']), ('drifted', 'corrected'))
        self.assertEqual((report['loop.channel3_ok']['result'], report['loop.channel3_ok']['value']), ('verified', 1))
        self.assertEqual(PROCESS['status'], {3: 1})

    def test_an_instrument_states_its_own_agreement_with_its_setpoint(self):
        own = {'id': 'at_setpoint', 'description': 'At its setpoint', 'verify': {'operation': 'read'},
               'accept': {'target': {'operation': 'setpoint'}, 'within': '500 mK'}}
        self.client.register({'id': 'loop2', 'driver': __name__ + ':Loop', 'conditions': [own]})
        self.assertTrue(self.client.maintain(device='loop2')['holds'])
        accepted = self.client.condition('loop2.at_setpoint')['accept']
        self.assertEqual((accepted['target']['device'], accepted['within']), ('loop2', 0.5))
        self.client.register({'id': 'loop3', 'driver': __name__ + ':Loop', 'conditions': [
            {**own, 'accept': {'target': {'value': 20, 'unit': 'K'}, 'within': {'value': 500, 'unit': 'mK'}}}]})
        self.assertTrue(self.client.maintain(device='loop3')['holds'])


class FormDefinitionCase(unittest.TestCase):
    def test_every_sample_must_be_usable_unless_a_statistic_is_explicit(self):
        base = {'id': 'c', 'description': 'Stable usable readings', 'verify': {'device': 'loop', 'operation': 'read'},
                'samples': 2, 'accept': {'statistic': 'span', 'maximum': 0.1},
                'usable': {'value': ['valid'], 'equals': True}}
        condition = check([base])[0]
        self.assertEqual(outcome(condition, [{'value': 20, 'valid': True}] * 2)[0], 'holds')
        self.assertEqual(outcome(condition, [{'value': 20, 'valid': True}, {'value': 20, 'valid': False}])[0], 'unusable')
        condition = check([{**base, 'accept': {'target': 1, 'within': 0.1}, 'usable': {'minimum': 0.05}}])[0]
        readings = [{'value': 0}, {'value': 2}]
        self.assertEqual(outcome(condition, readings)[0], 'unusable')
        # Explicit aggregation remains available when the statistic itself determines usability.
        condition['usable']['statistic'] = 'mean'
        self.assertEqual(outcome(condition, readings)[0], 'holds')

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
                ({'accept': {'maximum': 1}, 'samples': 3, 'interval': -1}, 'interval is the time between samples'),
                ({'accept': {'maximum': 1}, 'usable': 'valid'}, 'usable gives value'),
                ({'accept': {'bits': 4, 'maximum': 1}}, 'bits takes no minimum, maximum, equals or statistic'),
                ({'accept': {'bits': 0}}, 'bits is a positive integer mask'),
                ({'accept': {'maximum': 1}, 'each': {'channel': [1, 2]}}, r'the id names \{channel\}'),
                ({'accept': {'maximum': 1}, 'each': ['CH1', 'CH2']}, 'each maps names to the lists of values')):
            with self.assertRaisesRegex(ValueError, message):
                check([{**base, **change}])
        checked = check([{**base, 'accept': {'target': {'device': 'loop', 'operation': 'setpoint'}, 'within': 1}}])[0]
        self.assertEqual(checked['accept']['target']['value'], ['value'])
        with self.assertRaisesRegex(ValueError, 'target.*V is not a unit of the same quantity as K'):
            check([{**base, 'accept': {'target': {'device': 'loop', 'operation': 'setpoint'}, 'within': 1}}],
                  unit_of=lambda step: 'K' if step['operation'] == 'read' else 'V')

    def test_the_example_of_the_priors_guide_is_a_set_of_conditions(self):
        import json
        import re
        from ai4instruments.iop import mcp
        # A checkout may end lines with CR LF.
        example = json.loads(re.search(r'```json\r?\n(.*?)```', mcp.guide_text('priors'), re.S).group(1))
        checked = check(example)
        self.assertEqual([c['accept'].get('statistic') or type(c['accept'].get('target')).__name__ for c in checked],
                         ['float', 'slope', 'dict'])


if __name__ == '__main__':
    unittest.main()

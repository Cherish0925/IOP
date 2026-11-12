"""Operating conditions: verified, corrected in dependency order, trusted for a time, and relied on by experiments."""
import math
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from ai4instruments.iop import Client, IOPError
from ai4instruments.iop.conditions import check, outcome, summary
from ai4instruments.iop.config import initialize
from ai4instruments.iop.drivers import Driver
from ai4instruments.iop.engine import Engine
from ai4instruments.iop.server import Server
from . import test_service as fixture

# A signal chain whose physical state the cases change behind the Runtime's back:
# a source feeds a coupler, and a detector reads what comes through.
WORLD = {}


class Part(Driver):
    def describe(self):
        return {'operations': {
            'source': {'read': {'effect': 'observe', 'parameters': {}}, 'stabilize': {'parameters': {}}},
            'coupler': {'peak_up': {'parameters': {}}, 'nudge': {'parameters': {}},
                        'where': {'effect': 'observe', 'parameters': {}},
                        **{'set_' + axis: {'parameters': {'position': {'type': 'number', 'minimum': -1, 'maximum': 1}}}
                           for axis in 'xy'}},
            'detector': {'read': {'effect': 'observe', 'parameters': {}}},
        }[self.config['part']]}

    def invoke(self, operation, arguments, cancelled):
        WORLD['log'].append(f"{self.config['part']}.{operation}")
        if operation == 'stabilize':
            WORLD['level'] = 1.0
        elif operation == 'peak_up':
            WORLD['alignment'] = 1.0
        elif operation == 'nudge':
            WORLD['alignment'] = min(1.0, WORLD['alignment'] + 0.01)
        elif operation in ('set_x', 'set_y'):
            WORLD[operation[-1]] = arguments['position']
        elif operation == 'where':
            return {'x': WORLD['x'], 'y': WORLD['y']}
        elif self.config['part'] == 'source':
            return {'level': WORLD['level']}
        else:
            # The coupling falls off as a Gaussian around the point the beam has drifted to.
            offset = (WORLD['x'] - WORLD['x0']) ** 2 + (WORLD['y'] - WORLD['y0']) ** 2
            return {'signal': WORLD['level'] * WORLD['alignment'] * math.exp(-offset / 0.08), 'dark': 0.002}
        return {}


SOURCE = {'id': 'source.stable', 'kind': 'equilibrium', 'description': 'The source has reached its level',
          'verify': {'device': 'source', 'operation': 'read'}, 'accept': {'value': ['level'], 'minimum': 0.95, 'maximum': 1.05},
          'correct': {'device': 'source', 'operation': 'stabilize'}}
PATH = {'id': 'path.aligned', 'kind': 'alignment', 'description': 'The source reaches the detector', 'after': ['source.stable'],
        'verify': {'device': 'detector', 'operation': 'read'}, 'accept': {'value': ['signal'], 'minimum': 0.8},
        'usable': {'value': ['signal'], 'minimum': 0.05}, 'reference': {'dark': ['dark']}, 'valid_for': 3600,
        'correct': [{'device': 'coupler', 'operation': 'nudge'}, {'device': 'coupler', 'operation': 'peak_up'}]}


def platform(*conditions):
    return {'schema_version': 1, 'id': 'chain', 'devices': ['source', 'coupler', 'detector'], 'conditions': list(conditions)}


class ConditionsCase(unittest.TestCase):
    def setUp(self):
        fixture.ServiceCase.setUp(self)
        WORLD.update(level=1.0, alignment=1.0, x=0.0, y=0.0, x0=0.0, y0=0.0, log=[])
        for part in ('source', 'coupler', 'detector'):
            self.client.register({'id': part, 'driver': __name__ + ':Part', 'config': {'part': part}})
        self.client.configure_platform(platform(SOURCE, PATH))

    tearDown = fixture.ServiceCase.tearDown

    def states(self):
        return {row['id']: row['state'] for row in self.client.conditions('chain')}

    def test_a_platform_is_brought_to_its_working_state_in_dependency_order(self):
        self.assertEqual(self.states(), {'source.stable': 'unknown', 'path.aligned': 'unknown'})
        report = self.client.maintain('chain')
        self.assertTrue(report['holds'])
        self.assertEqual([(e['condition'], e['result']) for e in report['report']],
                         [('source.stable', 'verified'), ('path.aligned', 'verified')])
        self.assertEqual(WORLD['log'], ['source.read', 'detector.read'])
        self.assertEqual(self.states(), {'source.stable': 'holds', 'path.aligned': 'holds'})
        # Nothing is due: a second pass touches no instrument.
        again = self.client.maintain('chain')
        self.assertEqual([e['result'] for e in again['report']], ['held', 'held'])
        self.assertEqual(len(WORLD['log']), 2)
        run = self.client.run(report['run_id']).info()
        self.assertEqual((run['state'], run['result']['holds']), ('completed', True))
        described = self.client.condition('path.aligned')
        self.assertEqual((described['reference'], described['statistics']['verifications']), ({'dark': 0.002}, 1))
        self.assertIn('valid_until', described)

    def test_a_drifted_condition_is_corrected_with_the_first_correction_that_restores_it(self):
        self.client.maintain('chain')
        WORLD['alignment'] = 0.5
        report = self.client.maintain('chain', force=True)
        entry = report['report'][-1]
        self.assertEqual((entry['condition'], entry['result'], entry['found'], entry['by']),
                         ('path.aligned', 'corrected', 'drifted', 'coupler.peak_up'))
        self.assertEqual(WORLD['log'][-5:], ['detector.read', 'coupler.nudge', 'detector.read', 'coupler.peak_up', 'detector.read'])
        described = self.client.condition('path.aligned')
        self.assertEqual(described['statistics']['corrections'],
                         {'coupler.nudge': {'tried': 1, 'restored': 0}, 'coupler.peak_up': {'tried': 1, 'restored': 1}})
        self.assertEqual(described['statistics']['held']['count'], 1)
        attempts = [r for r in described['records'] if r['type'] == 'corrected']
        self.assertEqual([r['restored'] for r in attempts], [False, True])
        self.assertEqual(len([r for r in described['records'] if r['type'] == 'invalidated']), 2)

    def test_an_unusable_measurement_sends_the_search_upstream(self):
        self.client.maintain('chain')
        WORLD['level'] = 0.0
        # The state of the source still says it holds; only the detector is due.
        self.client.invalidate(['path.aligned'], 'table was bumped')
        self.assertEqual(self.states(), {'source.stable': 'holds', 'path.aligned': 'due'})
        del WORLD['log'][:]
        report = self.client.maintain(conditions=['path.aligned'])
        self.assertTrue(report['holds'])
        results = {e['condition']: e for e in report['report']}
        self.assertEqual((results['source.stable']['result'], results['source.stable']['by']), ('corrected', 'source.stabilize'))
        self.assertEqual(results['path.aligned']['result'], 'verified')
        # Nothing was done to the coupler: the cause was upstream.
        self.assertEqual(WORLD['log'], ['detector.read', 'source.read', 'source.stabilize', 'source.read', 'detector.read'])

    def test_a_correction_upstream_makes_what_depends_on_it_due(self):
        self.client.maintain('chain')
        WORLD['level'] = 0.5
        self.client.maintain(conditions=['source.stable'], force=True)
        states = {row['id']: row for row in self.client.conditions()}
        self.assertEqual((states['path.aligned']['state'], states['path.aligned']['reason']),
                         ('due', 'dependency disturbed: correction started: source.stabilize'))

    def test_a_verification_is_trusted_for_a_time(self):
        self.client.configure_platform(platform(SOURCE, {**PATH, 'valid_for': 0.3}), expected_revision=1)
        self.client.maintain('chain')
        self.assertEqual(self.states()['path.aligned'], 'holds')
        time.sleep(0.35)
        row = [r for r in self.client.conditions() if r['id'] == 'path.aligned'][0]
        self.assertEqual((row['state'], row['reason']), ('due', 'valid_for elapsed'))

    def test_upstream_disturbance_stays_due_downstream_until_observed_again(self):
        self.client.maintain('chain')
        self.client.invalidate(['source.stable'], 'source was disconnected')
        self.client.maintain(conditions=['source.stable'])
        self.assertEqual(self.states(), {'source.stable': 'holds', 'path.aligned': 'due'})
        self.client.maintain(conditions=['path.aligned'])
        self.assertEqual(self.states()['path.aligned'], 'holds')

    def test_revised_measurement_and_upstream_definition_require_new_observations(self):
        method = {'id': 'signal.read', 'layer': 'standard', 'description': 'Read a signal',
                  'steps': [{'id': 'read', 'device': 'detector', 'operation': 'read'}],
                  'result': {'signal': {'step': 'read', 'path': ['signal']}}}
        self.client.save_operation(method)
        path = {**PATH, 'verify': {'operation': 'signal.read'}, 'reference': {}}
        self.client.configure_platform(platform(SOURCE, path), expected_revision=1)
        self.client.maintain('chain')
        self.client.save_operation({**method, 'result': {'signal': {'step': 'read', 'path': ['dark']}}})
        self.assertEqual(self.states()['path.aligned'], 'unknown')
        self.client.configure_platform(platform(SOURCE, PATH), expected_revision=2)
        self.client.maintain('chain')
        self.client.configure_platform(platform({**SOURCE, 'accept': {'value': ['level'], 'minimum': 0.9}}, PATH),
                                       expected_revision=3)
        self.client.maintain(conditions=['source.stable'])
        self.assertEqual(self.states()['path.aligned'], 'unknown')

    def test_maintenance_request_cannot_be_reused_for_different_work(self):
        first = self.client.maintain('chain', request_id='prepare-chain')
        count = len(WORLD['log'])
        again = self.client.maintain('chain', request_id='prepare-chain')
        self.assertEqual((again['id'], len(WORLD['log'])), (first['id'], count))
        with self.assertRaisesRegex(IOPError, 'different maintenance'):
            self.client.maintain('chain', request_id='prepare-chain', force=True)

    def test_maintenance_preserves_requirements_of_saved_corrections(self):
        self.client.save_operation({'id': 'conditional.peak', 'layer': 'standard', 'description': 'Peak only with a reference',
                                    'requires': ['reference.ready'],
                                    'steps': [{'id': 'peak', 'device': 'coupler', 'operation': 'peak_up'}]})
        reference = {**SOURCE, 'id': 'reference.ready', 'correct': []}
        self.client.configure_platform(platform(SOURCE, reference, {**PATH, 'correct': {'operation': 'conditional.peak'}}),
                                       expected_revision=1)
        WORLD['alignment'] = 0.2
        report = self.client.maintain(conditions=['path.aligned'])
        self.assertFalse(report['holds'])
        self.assertNotIn('coupler.peak_up', WORLD['log'])
        self.client.maintain(conditions=['reference.ready'])
        report = self.client.maintain(conditions=['path.aligned'])
        self.assertTrue(report['holds'])

    def test_active_maintenance_keeps_used_methods_while_unrelated_methods_can_change(self):
        method = {'id': 'correct.peak', 'layer': 'standard', 'description': 'Restore coupling',
                  'steps': [{'id': 'peak', 'device': 'coupler', 'operation': 'peak_up'}]}
        self.client.save_operation(method)
        self.client.configure_platform(platform(SOURCE, {**PATH, 'correct': {'operation': 'correct.peak'}}), expected_revision=1)
        entered, release = threading.Event(), threading.Event()
        driver = self.engine.devices['coupler']
        original = driver.invoke
        def blocked(operation, arguments, cancelled):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('Correction was not released')
            return original(operation, arguments, cancelled)
        WORLD['alignment'] = 0.2
        with patch.object(driver, 'invoke', blocked):
            job = self.client.request('/v1/conditions/maintain', {'platform': 'chain'})
            try:
                self.assertTrue(entered.wait(3))
                with self.assertRaisesRegex(IOPError, 'in use by active maintenance'):
                    self.client.save_operation({**method, 'description': 'New method'})
                self.client.save_operation({**method, 'id': 'other.peak'})
            finally:
                release.set()
            self.assertTrue(self.client.request('/v1/maintenance/' + job['id'] + '?wait=5')['holds'])
        self.client.save_operation({**method, 'description': 'New method'})

    def test_correction_reserves_coupled_devices_but_not_independent_devices(self):
        entered, release = threading.Event(), threading.Event()
        driver = self.engine.devices['coupler']
        original = driver.invoke
        def blocked(operation, arguments, cancelled):
            if operation == 'peak_up':
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('Correction was not released')
            return original(operation, arguments, cancelled)
        WORLD['alignment'] = 0.3
        with patch.object(driver, 'invoke', blocked):
            job = self.client.request('/v1/conditions/maintain', {'platform': 'chain', 'request_id': 'coupled'})
            try:
                self.assertTrue(entered.wait(3))
                competing = self.client.submit([{'id': 'change', 'device': 'source', 'operation': 'stabilize'}])
                independent = self.client.submit([{'id': 'work', 'device': 'stage', 'operation': 'wait',
                                                   'arguments': {'duration': 0}}])
                independent.result(3)
                self.assertEqual(competing.info()['operation_records'][0]['state'], 'queued')
            finally:
                release.set()
            finished = self.client.request('/v1/maintenance/' + job['id'] + '?wait=5')
            self.assertTrue(finished['holds'])
            competing.result(5)
            self.assertGreaterEqual(competing.info()['operation_records'][0]['started'], finished['ended'])

    def test_a_queued_action_does_not_run_after_its_condition_is_disturbed(self):
        self.client.maintain('chain')
        hold = self.client.start_experiment(resources=['detector'])
        hold.batch([{'id': 'read', 'device': 'detector', 'operation': 'read'}])
        before = len(WORLD['log'])
        waiting = self.client.submit([{'id': 'read', 'device': 'detector', 'operation': 'read'}], requires=['path.aligned'])
        self.client.invalidate(['path.aligned'], 'sample changed')
        hold.finish()
        with self.assertRaises(IOPError):
            waiting.result(5)
        self.assertEqual(len(WORLD['log']), before)
        op = waiting.info()['operation_records'][0]
        self.assertEqual(op['state'], 'failed')
        self.assertIn('path.aligned', op['error'])
        self.assertNotIn('outcome_unknown', op)

    def test_nested_maintenance_requires_its_full_resource_reservation(self):
        self.client.maintain('chain')
        experiment = self.client.start_experiment(resources=['detector'], requires=['path.aligned'])
        before = len(WORLD['log'])
        with self.assertRaises(IOPError) as refused:
            experiment.maintain(force=True)
        self.assertEqual(refused.exception.code, 'maintenance_resources')
        self.assertEqual(len(WORLD['log']), before)
        experiment.finish()

    def test_disturbance_marks_active_data_without_marking_completed_earlier_work(self):
        self.client.maintain('chain')
        earlier = self.client.submit([{'id': 'read', 'device': 'detector', 'operation': 'read'}], requires=['path.aligned'])
        earlier.result(5)
        current = self.client.start_experiment(requires=['path.aligned'])
        self.client.invalidate(['source.stable'], 'enclosure opened')
        self.assertNotIn('in_doubt', earlier.info())
        self.assertEqual(current.info()['in_doubt']['path.aligned']['outcome'], 'disturbed')
        current.finish()

    def test_a_condition_without_a_working_correction_is_lost_and_blocks_what_depends_on_it(self):
        self.client.configure_platform(platform({k: v for k, v in SOURCE.items() if k != 'correct'}, PATH), expected_revision=1)
        WORLD['level'] = 0.5
        report = self.client.maintain('chain')
        self.assertFalse(report['holds'])
        self.assertEqual([(e['condition'], e['result']) for e in report['report']],
                         [('source.stable', 'lost'), ('path.aligned', 'blocked')])
        self.assertEqual(report['report'][1]['by'], 'source.stable')
        context = self.client.context()
        self.assertEqual(context['setup']['conditions'], {'lost': ['source.stable'], 'unknown': ['path.aligned']})
        self.assertIn({'action': 'maintain_conditions', 'platform': 'chain', 'conditions': ['path.aligned', 'source.stable'],
                       'submit': '/v1/conditions/maintain'}, context['next_steps'])

    def test_experiments_are_accepted_while_what_they_rely_on_holds(self):
        steps = [{'id': 'read', 'device': 'detector', 'operation': 'read'}]
        with self.assertRaisesRegex(IOPError, 'path.aligned is unknown') as refused:
            self.client.submit(steps, requires=['path.aligned'])
        self.assertEqual((refused.exception.code, list(refused.exception.details['conditions'])),
                         ('conditions_not_held', ['source.stable', 'path.aligned']))
        self.client.maintain('chain')
        run = self.client.submit(steps, requires=['path.aligned'])
        run.result(10)
        info = run.info()
        self.assertEqual((info['requires'], info['conditions']['path.aligned']['reference']), (['path.aligned'], {'dark': 0.002}))
        self.assertNotIn('in_doubt', info)
        # A later verification finds the condition lost: data taken since the accepted one are in doubt.
        WORLD['alignment'] = 0.2
        self.client.configure_platform(platform(SOURCE, {**PATH, 'correct': []}), expected_revision=1)
        self.client.maintain('chain', force=True)
        doubt = run.info()['in_doubt']['path.aligned']
        self.assertEqual((doubt['outcome'], doubt['since']), ('drifted', info['conditions']['path.aligned']['accepted']))

    def test_saved_operations_name_the_conditions_they_require(self):
        self.client.save_operation({'id': 'signal.read', 'layer': 'standard', 'description': 'Read the signal',
                                    'requires': ['path.aligned'],
                                    'steps': [{'id': 'read', 'device': 'detector', 'operation': 'read'}],
                                    'result': {'signal': {'step': 'read', 'path': ['signal']}}})
        with self.assertRaisesRegex(IOPError, 'conditions_not_held|path.aligned is unknown'):
            self.client.submit([{'id': 's', 'operation': 'signal.read'}])
        self.client.maintain('chain')
        self.assertEqual(self.client.submit([{'id': 's', 'operation': 'signal.read'}]).result(10)['s'], {'signal': 1.0})
        self.client.save_operation({'id': 'other.read', 'layer': 'standard', 'description': 'Read', 'requires': ['gone'],
                                    'steps': [{'id': 'read', 'device': 'detector', 'operation': 'read'}]})
        self.assertEqual(self.client.saved_operation('other.read')['unavailable'], ['gone: no such condition'])

    def test_a_continuous_experiment_maintains_what_it_relies_on_as_it_goes(self):
        self.client.configure_platform(platform(SOURCE, {**PATH, 'valid_for': 0.4}), expected_revision=1)
        self.client.maintain('chain')
        experiment = self.client.start_experiment(resources=['source', 'coupler', 'detector'], requires=['path.aligned'])
        read = [{'id': 'read', 'device': 'detector', 'operation': 'read'}]
        experiment.batch(read)
        time.sleep(0.45)
        WORLD['alignment'] = 0.3
        with self.assertRaises(IOPError) as refused:
            experiment.batch(read)
        self.assertEqual(refused.exception.code, 'conditions_not_held')
        # The same batch with maintain: verified inside the experiment, corrected, then run.
        operations = experiment.batch(read, maintain=True)
        self.assertEqual(operations[0]['result']['signal'], 1.0)
        self.assertEqual(experiment.info()['in_doubt']['path.aligned']['outcome'], 'drifted')
        experiment.finish()

    def tuned(self, **options):
        knob = lambda axis: {'device': 'coupler', 'operation': 'set_' + axis, 'argument': 'position', 'step': 0.1,
                             'resolution': 0.01, 'minimum': -1, 'maximum': 1,
                             'read': {'device': 'coupler', 'operation': 'where', 'value': [axis]}}
        return platform(SOURCE, {**PATH, 'correct': {'tune': [knob('x'), knob('y')], **options}})

    def test_the_runtime_tunes_several_settings_jointly_until_the_value_is_best(self):
        self.client.configure_platform(self.tuned(), expected_revision=1)
        self.client.maintain('chain')
        # The beam walked away in both directions: neither setting alone restores the coupling.
        WORLD.update(x0=0.33, y0=-0.24)
        report = self.client.maintain('chain', force=True)
        entry = report['report'][-1]
        self.assertEqual((entry['result'], entry['found'], entry['by']), ('corrected', 'drifted', 'tune coupler.position, coupler.position'))
        self.assertGreater(entry['value'], 0.98)
        self.assertLess(abs(WORLD['x'] - 0.33) + abs(WORLD['y'] + 0.24), 0.05)
        record = self.client.condition('path.aligned')['records'][-1]
        self.assertEqual((record['type'], record['restored'], record['tuned']['from']), ('corrected', True, [0.0, 0.0]))
        self.assertLessEqual(record['tuned']['evaluations'], 40)
        self.assertEqual(record['tuned']['to'], [WORLD['x'], WORLD['y']])

    def test_tuning_can_stop_once_the_tolerance_is_met_and_never_leaves_its_range(self):
        self.client.configure_platform(self.tuned(until='accept'), expected_revision=1)
        self.client.maintain('chain')
        WORLD.update(x0=0.33, y0=-0.24)
        self.client.maintain('chain', force=True)
        quick = self.client.condition('path.aligned')['records'][-1]['tuned']['evaluations']
        self.assertLess(quick, 12)
        self.assertGreaterEqual(self.client.condition('path.aligned')['value'], 0.8)
        # The optimum is outside the allowed range: the search ends at the limit and the condition is lost.
        # Even small signals are valid observations in this case.
        definition = self.tuned(until='accept')
        definition['conditions'][1].pop('usable')
        self.client.configure_platform(definition, expected_revision=2)
        WORLD.update(x0=3.0, y0=0.0)
        report = self.client.maintain('chain', force=True)
        self.assertEqual(report['report'][-1]['result'], 'lost')
        self.assertAlmostEqual(WORLD['x'], 1)
        self.assertLessEqual(WORLD['x'], 1)

    def test_tuning_applies_an_out_of_range_readback_before_using_that_position(self):
        definition = self.tuned(evaluations=1)
        definition['conditions'][1].pop('usable')
        self.client.configure_platform(definition, expected_revision=1)
        WORLD.update(x=1.5, x0=1.0)
        report = self.client.maintain('chain')
        self.assertTrue(report['holds'])
        self.assertEqual(WORLD['x'], 1.0)
        tuned = self.client.condition('path.aligned')['records'][-1]['tuned']
        self.assertEqual((tuned['from'][0], tuned['to'][0], tuned['evaluations']), (1.5, 1.0, 1))

    def test_tuning_does_not_prefer_an_unusable_larger_reading(self):
        definition = self.tuned(evaluations=5)
        definition['conditions'][1]['usable'] = {'value': ['valid'], 'equals': True}
        self.client.configure_platform(definition, expected_revision=1)
        driver = self.engine.devices['detector']
        with patch.object(driver, 'invoke', lambda *args: {'signal': 10 + WORLD['x'], 'valid': False}):
            report = self.client.maintain('chain')
        self.assertFalse(report['holds'])
        self.assertEqual((WORLD['x'], WORLD['y']), (0, 0))

    def test_a_monitored_reading_outside_its_limits_makes_conditions_due(self):
        current = self.client.device_history('source')[0]
        self.client.configure_platform({'schema_version': 1, 'id': 'chain', 'devices': ['source', 'coupler', 'detector']}, expected_revision=1)
        self.client.configure_device('source', {**current['definition'], 'monitors': [
            {'id': 'level', 'operation': 'read', 'interval_s': 0.1, 'value': ['level'], 'maximum': 1.2}]}, current['revision'])
        self.client.configure_platform(platform(SOURCE, PATH), expected_revision=2)
        self.client.maintain('chain')
        WORLD['level'] = 2.0
        deadline = time.monotonic() + 5
        while self.states()['source.stable'] == 'holds' and time.monotonic() < deadline:
            time.sleep(0.05)
        rows = {row['id']: row for row in self.client.conditions()}
        self.assertEqual((rows['source.stable']['state'], rows['source.stable']['reason']),
                         ('due', 'monitor source.level left its limits'))
        self.assertEqual(rows['path.aligned']['state'], 'due')
        self.assertIn('monitor source.level', rows['path.aligned']['reason'])

    def test_states_and_records_survive_a_restart(self):
        self.client.maintain('chain')
        self.server.shutdown(); self.server.server_close(); self.engine.close()
        self.engine = Engine(self.temp.name, self.config)
        self.server = Server(('127.0.0.1', 0), self.engine)
        self.engine.start()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = Client(self.engine.endpoint, token=self.config['token'])
        self.assertEqual(self.states(), {'source.stable': 'holds', 'path.aligned': 'holds'})
        self.assertEqual(self.client.condition('source.stable')['statistics']['verifications'], 1)


class ExampleCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def test_the_example_conditions_are_maintained_on_the_demonstration_devices(self):
        import json
        from pathlib import Path
        self.client.configure_platform(json.loads((Path(__file__).resolve().parents[2] / 'examples/iop/conditions.json').read_text()))
        # At 30 degrees a quarter of the signal is left: the Runtime turns the stage until the meter reads it all.
        self.client.invoke('stage', 'move_to', {'angle_deg': 30})
        report = self.client.maintain('demo-bench')
        self.assertEqual([(e['condition'], e['result']) for e in report['report']],
                         [('stage.in_range', 'verified'), ('meter.signal', 'corrected')])
        self.assertEqual(report['report'][1]['by'], 'tune stage.angle_deg')
        self.assertGreaterEqual(self.client.condition('meter.signal')['reference']['power'], 0.98)
        # Outside its range the stage is brought back first; the signal is then verified without tuning.
        self.client.invoke('stage', 'move_to', {'angle_deg': 60})
        report = self.client.maintain('demo-bench', force=True)
        self.assertEqual([(e['condition'], e['result']) for e in report['report']],
                         [('stage.in_range', 'corrected'), ('meter.signal', 'verified')])


class DefinitionCase(unittest.TestCase):
    def test_definitions_are_checked(self):
        for change, message in (
                ({'after': ['missing']}, 'after names other conditions'),
                ({'accept': {'value': ['signal']}}, 'set minimum, maximum or equals'),
                ({'verify': {'operation': ''}}, 'verify is one operation'),
                ({'kind': ''}, 'kind'),
                ({'valid_for': 0}, 'valid_for is a duration'),
                ({'interval': 5}, 'unknown fields interval')):
            with self.assertRaisesRegex(ValueError, message):
                check([SOURCE, {**PATH, **change}])
        self.assertEqual(check([{**SOURCE, 'kind': 'sample_integrity'}])[0]['kind'], 'sample_integrity')
        with self.assertRaisesRegex(ValueError, 'cycle'):
            check([{**SOURCE, 'after': ['path.aligned']}, PATH])
        with self.assertRaisesRegex(ValueError, 'already defined on another platform'):
            check([SOURCE], taken={'source.stable'})
        knob = {'device': 'coupler', 'operation': 'set_x', 'argument': 'position', 'step': 0.1, 'minimum': -1, 'maximum': 1,
                'read': {'device': 'coupler', 'operation': 'where', 'value': ['x']}}
        tuned = check([SOURCE, {**PATH, 'correct': {'tune': knob}}])[1]['correct'][0]
        self.assertEqual((tuned['goal'], tuned['evaluations'], tuned['until'], tuned['tune'][0]['resolution']),
                         ('maximize', 40, 'optimum', 0.00625))
        for change, message in (({'minimum': 2}, 'minimum < maximum'), ({'step': 0}, 'step > 0'), ({'read': {}}, 'read is the operation')):
            with self.assertRaisesRegex(ValueError, message):
                check([SOURCE, {**PATH, 'correct': {'tune': {**knob, **change}}}])
        with self.assertRaisesRegex(ValueError, 'numeric tolerance'):
            check([{**SOURCE, 'accept': {'value': ['level'], 'equals': 1}, 'correct': {'tune': knob}}])

    def test_a_result_is_classified_in_three_ways(self):
        condition = check([SOURCE, PATH])[1]
        self.assertEqual(outcome(condition, {'signal': 0.9, 'dark': 0.01}), ('holds', 0.9, {'dark': 0.01}))
        self.assertEqual(outcome(condition, {'signal': 0.4, 'dark': 0.01})[0], 'drifted')
        self.assertEqual(outcome(condition, {'signal': 0.01, 'dark': 0.01})[0], 'unusable')
        self.assertEqual(outcome(condition, {'error': 'no reading'}), ('unusable', None, {}))

    def test_records_give_the_drift_and_when_the_tolerance_will_be_reached(self):
        condition = check([SOURCE])[0]
        rows = [{'type': 'verified', 'time': f'2026-01-01T0{hour}:00:00+00:00', 'outcome': 'holds', 'value': 1.0 + 0.01 * hour}
                for hour in range(4)]
        trend = summary(condition, rows)['trend']
        self.assertAlmostEqual(trend['per_hour'], 0.01)
        self.assertEqual((trend['points'], trend['expected_until']), (4, '2026-01-01T05:00:00+00:00'))
        rows.append({'type': 'verified', 'time': '2026-01-01T06:00:00+00:00', 'outcome': 'drifted', 'value': 1.06})
        self.assertEqual(summary(condition, rows)['held'], {'count': 1, 'median_s': 21600.0, 'shortest_s': 21600.0})


if __name__ == '__main__':
    unittest.main()

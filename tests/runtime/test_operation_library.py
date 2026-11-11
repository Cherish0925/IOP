"""Standard and Workflow Operations: saved, used as one step, expanded into Basic Operations and recorded."""
import tempfile
import time
import unittest
from pathlib import Path
from ai4instruments.iop import IOPError
from ai4instruments.iop.config import initialize
from ai4instruments.iop.drivers import Driver
from ai4instruments.iop.engine import Engine
from ai4instruments.iop.library import check, expand
from ai4instruments.iop.process_driver import executable
from . import test_service as fixture

try:
    executable()
    executor = True
except ValueError:
    executor = False

POSITION = {
    'id': 'axis.position', 'layer': 'standard',
    'description': 'Move one axis and read where it arrived',
    'conditions': 'The axis is free to rotate',
    'parameters': {'axis': {'type': 'string', 'enum': ['stage', 'aux']},
                   'angle': {'type': 'number', 'minimum': -90, 'maximum': 90, 'unit': 'deg'}},
    'steps': [{'id': 'move', 'device': '${axis}', 'operation': 'move_to', 'arguments': {'angle_deg': '${angle}', 'duration': 0.15}},
              {'id': 'read', 'device': '${axis}', 'operation': 'position'}],
    'result': {'angle': {'step': 'read', 'path': ['angle_actual']}},
}
CAPTURE = {
    'id': 'meter.capture', 'layer': 'standard', 'description': 'Set the exposure and measure the power',
    'parameters': {'exposure': {'type': 'number', 'minimum': 0.001, 'maximum': 1, 'default': 0.01, 'unit': 's'}},
    'steps': [{'id': 'configure', 'device': 'meter', 'operation': 'configure', 'arguments': {'exposure_s': '${exposure}'}},
              {'id': 'measure', 'device': 'meter', 'operation': 'measure'}],
    'result': {'power': {'step': 'measure', 'path': ['power']}, 'exposure': {'step': 'configure', 'path': ['exposure_s']}},
}
MEASURE_AT = {
    'id': 'bench.measure_at', 'layer': 'workflow',
    'description': 'Position both axes at once, then measure',
    'parameters': {'a': {'type': 'number', 'minimum': -90, 'maximum': 90}, 'b': {'type': 'number', 'minimum': -90, 'maximum': 90}},
    'steps': [{'id': 'first', 'operation': 'axis.position', 'arguments': {'axis': 'stage', 'angle': '${a}'}, 'after': []},
              {'id': 'second', 'operation': 'axis.position', 'arguments': {'axis': 'aux', 'angle': '${b}'}, 'after': []},
              {'id': 'capture', 'operation': 'meter.capture', 'after': ['first', 'second']}],
    'result': {'power': {'step': 'capture', 'path': ['power']}, 'first': {'step': 'first', 'path': ['angle']}},
}


class Faulty(Driver):
    def describe(self):
        return {'operations': {'fail': {'parameters': {}}}}

    def invoke(self, operation, arguments, cancelled):
        raise RuntimeError('actuator fault')


class LibraryCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def save_all(self):
        for definition in (POSITION, CAPTURE, MEASURE_AT):
            self.client.save_operation(definition)

    def test_operations_are_saved_listed_revised_and_shown_to_agents(self):
        self.save_all()
        listed = {item['id']: item for item in self.client.saved_operations()}
        self.assertEqual((listed['axis.position']['layer'], listed['axis.position']['parameters'], listed['bench.measure_at']['layer']),
                         ('standard', ['axis', 'angle'], 'workflow'))
        self.assertEqual(self.client.save_operation(POSITION)['revision'], 1)
        revised = self.client.save_operation({**POSITION, 'conditions': 'The axis is free and homed'})
        self.assertEqual((revised['revision'], revised['steps'], revised['uses']['calls']), (2, POSITION['steps'], 0))
        with self.assertRaisesRegex(IOPError, 'current revision is 2'):
            self.client.save_operation({**POSITION, 'description': 'Changed'}, expected_revision=1)
        self.assertEqual(len(self.client.request('/v1/library/axis.position/history')), 2)
        operations = self.client.context()['operations']
        self.assertEqual((operations['standard'], operations['workflow']), (['axis.position', 'meter.capture'], ['bench.measure_at']))
        self.assertGreater(operations['basic'], 0)
        self.assertEqual(self.client.schema('operation')['properties']['layer']['enum'], ['standard', 'workflow'])

    def test_a_workflow_operation_runs_as_one_step_and_records_its_use(self):
        self.save_all()
        run = self.client.submit([
            {'id': 'm', 'operation': 'bench.measure_at', 'arguments': {'a': 10, 'b': 20}},
            {'id': 'return', 'operation': 'axis.position', 'arguments': {'axis': 'aux'},
             'bindings': {'angle': {'step': 'm', 'path': ['first']}}, 'after': ['m']}])
        result = run.result(10)
        self.assertEqual((result['m']['first'], result['return']['angle']), (10, 10))
        self.assertIsInstance(result['m']['power'], float)
        self.assertEqual(result['m.second.read']['angle_actual'], 20)
        records = {op['step']: op for op in run.info()['operation_records']}
        # Both axes moved at the same time.
        first, second = records['m.first.move'], records['m.second.move']
        self.assertTrue(first['started'] < second['ended'] and second['started'] < first['ended'])
        self.assertEqual(records['m.first.move']['calls'], ['m', 'm.first'])
        self.assertEqual(records['m.capture.configure']['after'], ['m.first.read', 'm.second.read'])
        self.assertEqual(run.info()['calls']['m']['operation'], 'bench.measure_at')
        uses = {item['id']: item['uses'] for item in self.client.saved_operations()}
        self.assertEqual((uses['bench.measure_at']['calls'], uses['bench.measure_at']['completed']), (1, 1))
        self.assertEqual((uses['axis.position']['calls'], uses['meter.capture']['calls']), (3, 1))
        self.assertEqual(uses['bench.measure_at']['last']['run_id'], run.id)
        ended = [event for event in self.client.events(run_id=run.id)['events'] if event['kind'] == 'call.completed']
        self.assertEqual(len(ended), 5)

    def test_saved_operations_are_used_in_a_continuous_experiment(self):
        self.save_all()
        experiment = self.client.start_experiment(request_id='adaptive', resources=['stage', 'aux', 'meter'])
        batch = self.client.request(f'/v1/runs/{experiment.id}/batches?wait=10', {'request_id': 'point-1', 'steps': [
            {'id': 'place', 'operation': 'axis.position', 'arguments': {'axis': 'stage', 'angle': 30}},
            {'id': 'capture', 'operation': 'meter.capture', 'arguments': {'exposure': 0.02}}]})
        self.assertTrue(batch['complete'])
        self.assertEqual((batch['results']['place'], batch['results']['capture']['exposure']), ({'angle': 30}, 0.02))
        # Written in order: the capture waited for the placement.
        steps = {op['step']: op for op in batch['operations']}
        self.assertEqual(steps['capture.configure']['after'], ['place.read'])
        again = self.client.request(f'/v1/runs/{experiment.id}/batches?wait=10', {'request_id': 'point-2', 'steps': [
            {'id': 'place', 'operation': 'axis.position', 'arguments': {'axis': 'stage', 'angle': 40}}]})
        self.assertEqual(again['results']['place'], {'angle': 40})
        experiment.finish()
        self.assertEqual(self.client.saved_operation('axis.position')['uses']['completed'], 2)

    def test_a_failed_step_is_recorded_against_the_operation(self):
        self.client.register({'id': 'actuator', 'driver': __name__ + ':Faulty'})
        self.client.save_operation(POSITION)
        self.client.save_operation({'id': 'bench.prepare', 'layer': 'standard', 'description': 'Place the axis and actuate',
                                    'steps': [{'id': 'place', 'operation': 'axis.position', 'arguments': {'axis': 'stage', 'angle': 5}},
                                              {'id': 'actuate', 'device': 'actuator', 'operation': 'fail'},
                                              {'id': 'back', 'operation': 'axis.position', 'arguments': {'axis': 'stage', 'angle': 0}}]})
        run = self.client.submit([{'id': 'p', 'operation': 'bench.prepare'}])
        self.assertEqual(run.wait(10)['state'], 'failed')
        uses = self.client.saved_operation('bench.prepare')['uses']
        self.assertEqual((uses['calls'], uses['completed'], uses['failed'], uses['last']['state']), (1, 0, 1, 'failed'))
        placement = self.client.saved_operation('axis.position')['uses']
        self.assertEqual((placement['calls'], placement['completed']), (2, 1))

    def test_arguments_are_checked_before_anything_runs(self):
        self.save_all()
        for steps, message in (
                ([{'id': 'm', 'operation': 'bench.measure_at', 'arguments': {'a': 10, 'b': 200}}], 'm.*outside'),
                ([{'id': 'm', 'operation': 'bench.measure_at', 'arguments': {'a': 10}}], 'Missing parameter: b'),
                ([{'id': 'm', 'operation': 'bench.unknown'}], 'not a saved operation'),
                ([{'id': 'c', 'operation': 'meter.capture'},
                  {'id': 'x', 'device': 'stage', 'operation': 'move_to', 'bindings': {'angle_deg': {'step': 'c', 'path': ['image']}}}],
                 'returns power, exposure')):
            with self.assertRaisesRegex(IOPError, message):
                self.client.submit(steps)
        self.assertEqual(self.client.request('/v1/runs'), [])

    def test_the_example_operations_run_on_the_demonstration_devices(self):
        import json
        from pathlib import Path
        for definition in json.loads((Path(__file__).resolve().parents[2] / 'examples/iop/operations.json').read_text()):
            self.client.save_operation(definition)
        result = self.client.submit([{'id': 'm', 'operation': 'bench.measure_at', 'arguments': {'a': 10, 'b': 20}}]).result(10)
        self.assertEqual(set(result['m']), {'power'})

    def test_platform_procedures_use_saved_operations(self):
        self.save_all()
        self.client.configure_platform({'schema_version': 1, 'id': 'bench', 'devices': ['stage', 'aux', 'meter'], 'procedures': {
            'initialize': {'steps': [{'id': 'home', 'operation': 'axis.position', 'arguments': {'axis': 'stage', 'angle': 0}}]}}})
        self.assertEqual(self.client.procedure('bench').result(10)['home'], {'angle': 0})


SCAN = """import json, os
from ai4instruments.iop import Client
arguments = json.loads(os.environ['IOP_PARAMETERS_JSON'])
run = Client().current_run()
best, angle = None, arguments['start']
# Decide from each result: stop at the first angle whose power reaches the threshold.
while angle <= arguments['stop']:
    run.call('axis.position', {'axis': 'stage', 'angle': angle})
    power = run.call('meter.capture')['power']
    if best is None or power > best['power']:
        best = {'angle': angle, 'power': power}
    if power >= arguments['threshold']:
        break
    angle += arguments['step']
run.save_result({'best': best, 'reached': best['power'] >= arguments['threshold']})
"""
PROGRAM = {'id': 'stage.find_power', 'layer': 'workflow',
           'description': 'Step the stage until the measured power reaches a threshold',
           'parameters': {'start': {'type': 'number', 'minimum': -90, 'maximum': 90},
                          'stop': {'type': 'number', 'minimum': -90, 'maximum': 90},
                          'step': {'type': 'number', 'minimum': 1, 'maximum': 90, 'default': 10},
                          'threshold': {'type': 'number', 'minimum': 0}}}


@unittest.skipUnless(executor, 'Build iop-execd to run programs')
class ProgramCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def test_a_workflow_that_decides_from_results_is_saved_used_and_read(self):
        for definition in (POSITION, CAPTURE):
            self.client.save_operation(definition)
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'experiment.py').write_text(SCAN)
            saved = self.client.save_operation(PROGRAM, program=folder)
        self.assertEqual((saved['revision'], saved['program']['command'], [f['name'] for f in saved['program']['files']]),
                         (1, ['{python}', 'experiment.py'], ['experiment.py']))
        self.assertIn('run.call', self.client.program_file('stage.find_power', 'experiment.py')['text'])
        self.assertTrue(next(o for o in self.client.saved_operations() if o['id'] == 'stage.find_power')['program'])
        run = self.client.submit([{'id': 'scan', 'operation': 'stage.find_power',
                                   'arguments': {'start': -60, 'stop': 60, 'threshold': 0.85}}], resources=['stage', 'meter'])
        result = run.result(30)['scan']
        # The simulated power is cos(2 angle)^2: from -60 in steps of 10 it first reaches 0.85 at -10 (0.88).
        self.assertEqual((result['best']['angle'], result['reached']), (-10, True))
        self.assertEqual(run.info()['parameters']['step'], 10)
        uses = {o['id']: o['uses'] for o in self.client.saved_operations()}
        self.assertEqual((uses['stage.find_power']['completed'], uses['axis.position']['calls'], uses['meter.capture']['calls']), (1, 6, 6))
        for steps, message in (([{'id': 'scan', 'operation': 'stage.find_power', 'arguments': {'start': 0, 'stop': 10}}], 'Missing parameter: threshold'),
                               ([{'id': 'scan', 'operation': 'stage.find_power', 'arguments': {'start': 0, 'stop': 10, 'threshold': 1}},
                                 {'id': 'p', 'device': 'stage', 'operation': 'position'}], 'only step')):
            with self.assertRaisesRegex(IOPError, message):
                self.client.submit(steps)
        with self.assertRaisesRegex(IOPError, 'programs run as their own experiment'):
            self.client.save_operation({'id': 'bench.twice', 'layer': 'workflow', 'description': 'Scan twice',
                                        'steps': [{'id': 'a', 'operation': 'stage.find_power'}]})
        with self.assertRaisesRegex(IOPError, 'without steps'):
            with tempfile.TemporaryDirectory() as folder:
                Path(folder, 'experiment.py').write_text(SCAN)
                self.client.save_operation({**PROGRAM, 'layer': 'standard'}, program=folder)


class DefinitionCase(unittest.TestCase):
    def test_definitions_are_checked_when_saved(self):
        library = {'axis.position': {**POSITION, 'revision': 1}, 'bench.measure_at': {**MEASURE_AT, 'revision': 1}}
        for change, message in (
                ({'layer': 'basic'}, 'layer is standard'),
                ({'description': ''}, 'Describe what the operation achieves'),
                ({'steps': [{'id': 'a.b', 'device': 'stage', 'operation': 'position'}]}, 'without dots'),
                ({'steps': [{'id': 'move', 'device': 'stage', 'operation': 'move_to', 'arguments': {'angle_deg': '${speed}'}}]}, 'describe parameter speed'),
                ({'steps': [{'id': 'm', 'operation': 'bench.measure_at'}]}, 'built from Basic and Standard'),
                ({'steps': [{'id': 'm', 'operation': 'axis.position'}]}, 'cannot use itself'),
                ({'steps': [{'id': 'a', 'device': 'stage', 'operation': 'position', 'after': ['b']},
                            {'id': 'b', 'device': 'stage', 'operation': 'position', 'after': ['a']}]}, 'cycle'),
                ({'result': {'angle': {'step': 'absent'}}}, 'Result angle')):
            with self.assertRaisesRegex(ValueError, message):
                check({**POSITION, **change}, library.get)

    def test_steps_with_devices_pass_through_untouched(self):
        steps = [{'id': 'a', 'device': 'stage', 'operation': 'position'}]
        self.assertIs(expand(steps, {}.get)[0], steps)

    def test_saved_operations_and_their_use_records_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as home:
            config = initialize(home, True, 0); config['discovery_interval'] = 0
            engine = Engine(home, config); engine.start()
            try:
                engine.save_operation(POSITION)
                run = engine.submit({'steps': [{'id': 'p', 'operation': 'axis.position', 'arguments': {'axis': 'stage', 'angle': 1}}]})
                deadline = time.monotonic() + 5
                while engine.store.get('runs', run['id'])['state'] != 'completed' and time.monotonic() < deadline:
                    time.sleep(0.02)
            finally:
                engine.close()
            engine = Engine(home, config)
            try:
                saved = engine.saved_operation('axis.position')
                self.assertEqual((saved['revision'], saved['uses']['completed']), (1, 1))
            finally:
                engine.close()


if __name__ == '__main__':
    unittest.main()

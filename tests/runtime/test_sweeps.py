"""Sweeps: a step run at each value of its parameters, what it returns gathered into arrays."""
import array
import asyncio
import importlib.util
import json
import math
import unittest
from ai4instruments.iop import Driver, IOPError, arrays, sweeps
from . import test_service as fixture

MEASURE = {'id': 'axis.measure', 'layer': 'standard', 'description': 'Turn an axis and measure the transmitted power',
           'parameters': {'axis': {'type': 'string', 'enum': ['stage', 'aux']},
                          'angle': {'type': 'number', 'minimum': -90, 'maximum': 90, 'unit': 'deg'}},
           'steps': [{'id': 'turn', 'device': '${axis}', 'operation': 'move_to', 'arguments': {'angle_deg': '${angle}'}},
                     {'id': 'read', 'device': 'meter', 'operation': 'measure'}],
           'result': {'power': {'step': 'read', 'path': ['power']}, 'angle': {'step': 'turn', 'path': ['angle_actual']}}}

# Find the angle of most power over a coarse sweep and go there.
PEAK = {'id': 'axis.peak', 'layer': 'standard', 'description': 'Turn the stage to the angle of most power',
        'parameters': {'points': {'type': 'integer', 'minimum': 2, 'maximum': 181}},
        'steps': [{'id': 'scan', 'operation': 'axis.measure', 'arguments': {'axis': 'stage'},
                   'sweep': {'angle': {'start': -60, 'stop': 30, 'points': '${points}'}}},
                  {'id': 'go', 'device': 'stage', 'operation': 'move_to',
                   'bindings': {'angle_deg': {'step': 'scan', 'view': {'field': 'power'}, 'path': ['summary', 'max_at']}}}],
        'result': {'angle': {'step': 'go', 'path': ['angle_actual']},
                   'power': {'step': 'scan', 'view': {'field': 'power'}, 'path': ['summary', 'max']}}}


def power(angle):
    return math.cos(math.radians(2 * angle)) ** 2


class Spectrometer(Driver):
    """A line at 500 nm plus 10 nm per volt of bias, recorded over 2000 wavelengths and kept only in a file."""
    def describe(self):
        return {'operations': {'spectrum': {'parameters': {'volts': {'type': 'number', 'unit': 'V', 'default': 0}},
                                            'effect': 'observe'}}}

    def invoke(self, operation, arguments, context):
        line = 500 + 10 * arguments['volts']
        counts = array.array('d', [100 / (1 + ((400 + 0.1 * i - line) / 2) ** 2) for i in range(2000)])
        axis = {'name': 'wavelength', 'unit': 'nm', 'start': 400, 'step': 0.1}
        spec = arrays.check_result({'dtype': 'float64', 'axes': [axis], 'unit': 'counts', 'inline_limit': 0})
        return {'line': line, 'spectrum': arrays.result(counts.tobytes(), spec, context, 'spectrum')}


class GatherCase(unittest.TestCase):
    """What a sweep returns, from what its points returned."""

    def test_values_and_axes_of_a_sweep(self):
        points, shape, axes = sweeps.grid({'axis': ['x', 'y'], 'volts': {'start': 0, 'stop': 1, 'points': 3}})
        self.assertEqual((shape, len(points), points[:2]), ([2, 3], 6, [{'axis': 'x', 'volts': 0.0}, {'axis': 'x', 'volts': 0.5}]))
        self.assertEqual(axes, [{'name': 'axis', 'labels': ['x', 'y']}, {'name': 'volts', 'start': 0, 'stop': 1}])
        self.assertEqual(sweeps.taken('n', {'start': 10, 'stop': 0, 'points': 6})[0], [10, 8, 6, 4, 2, 0])
        self.assertEqual(sweeps.taken('f', [1, 2, 5, 10])[1], {'name': 'f', 'coordinates': [1.0, 2.0, 5.0, 10.0]})
        self.assertEqual(sweeps.taken('f', [1, 2, 3])[1], {'name': 'f', 'start': 1.0, 'step': 1.0})
        for spec, message in (([], 'list the numbers'), ([1, 'a'], 'list the numbers'), ({'start': 0, 'stop': 1}, 'start, stop'),
                              ({'start': 0, 'stop': 1, 'points': 0}, 'start, stop')):
            with self.assertRaisesRegex(ValueError, message):
                sweeps.grid({'v': spec})
        with self.assertRaisesRegex(ValueError, 'at most 10000 points'):
            sweeps.grid({'a': list(range(101)), 'b': list(range(100))})

    def test_numbers_become_arrays_and_other_values_stay_when_the_same(self):
        axes = [{'name': 'volts', 'unit': 'V', 'start': 0, 'stop': 1}]
        found = sweeps.gather([{'value': 0.1 * i, 'unit': 'A', 'time': str(i), 'trace': None} for i in range(3)] + [None],
                              [4], axes)
        self.assertEqual(found['value'], {'data': {'dtype': 'float64', 'shape': [4], 'count': 4, 'axes': axes, 'unit': 'A'},
                                          'values': [0.0, 0.1, 0.2, None]})
        self.assertEqual((found['unit'], 'time' in found, found['trace']), ('A', False, None))
        # Numbers become arrays even when they do not change; a unit beside two numbers is not given to either.
        found = sweeps.gather([{'x': i, 'y': 10, 'unit': 'V'} for i in range(3)], [3], axes)
        self.assertEqual((found['y']['values'], 'unit' in found['x']['data'], found['unit']), ([10, 10, 10], False, 'V'))
        # Arrays held in the record combine; those kept only in a file stay with each point.
        trace = {'data': {'dtype': 'float64', 'shape': [2], 'axes': [{'name': 'f', 'start': 5, 'step': 1}], 'unit': 'dB'}}
        found = sweeps.gather([{'trace': {**trace, 'values': [i, -i]}} for i in range(3)], [3], axes)
        self.assertEqual(found['trace']['data'], {'dtype': 'float64', 'shape': [3, 2], 'count': 6, 'unit': 'dB',
                                                  'axes': axes + trace['data']['axes']})
        self.assertEqual(found['trace']['values'], [[0, 0], [1, -1], [2, -2]])
        # Arrays the points saved in files combine when every point saved one, read from those files in order.
        saved = [{'trace': {'data': {**trace['data'], 'file': f'operations/op{i}/trace.bin'}}} for i in range(3)]
        self.assertEqual(sweeps.gather(saved, [3], axes)['trace']['data']['files'],
                         ['operations/op0/trace.bin', 'operations/op1/trace.bin', 'operations/op2/trace.bin'])
        self.assertEqual(sweeps.gather(saved[:2] + [None], [3], axes), {})


class SweepCase(unittest.TestCase):
    """Swept steps in step graphs, saved operations and continuous experiments."""
    tearDown = fixture.ServiceCase.tearDown
    submit = fixture.ServiceCase.submit

    def setUp(self):
        fixture.ServiceCase.setUp(self)
        self.client.save_operation(MEASURE)

    def test_a_device_operation_swept_returns_its_numbers_over_the_sweep(self):
        run = self.submit([{'id': 'turn', 'device': 'stage', 'operation': 'move_to', 'arguments': {'duration': 0},
                            'sweep': {'angle_deg': [0, 10, 25]}}])
        result = run.result(10)
        self.assertEqual(set(result), {'turn'})
        self.assertEqual(result['turn']['angle_actual']['values'], [0, 10, 25])
        self.assertEqual(result['turn']['angle_actual']['data']['axes'],
                         [{'name': 'angle_deg', 'coordinates': [0.0, 10.0, 25.0], 'unit': 'deg'}])
        self.assertEqual((result['turn']['angle_actual']['data']['unit'], result['turn']['source']), ('deg', 'simulation'))
        operations = run.info()['operation_records']
        self.assertEqual([(op['step'], op['arguments']['angle_deg']) for op in operations],
                         [('turn.0', 0), ('turn.1', 10), ('turn.2', 25)])
        self.assertLessEqual(operations[0]['ended'], operations[1]['started'])

    def test_a_saved_operation_swept_and_its_peak_used_by_a_later_step(self):
        run = self.submit([
            {'id': 'scan', 'operation': 'axis.measure', 'arguments': {'axis': 'stage'},
             'sweep': {'angle': {'start': -40, 'stop': 40, 'points': 9}}},
            {'id': 'best', 'device': 'stage', 'operation': 'move_to', 'after': ['scan'],
             'bindings': {'angle_deg': {'step': 'scan', 'view': {'field': 'power'}, 'path': ['summary', 'max_at']}}}])
        result = run.result(10)
        self.assertEqual(set(result), {'scan', 'best'})
        powers = result['scan']['power']
        self.assertEqual(powers['data']['axes'], [{'name': 'angle', 'start': -40, 'stop': 40, 'unit': 'deg'}])
        for got, angle in zip(powers['values'], range(-40, 41, 10)):
            self.assertAlmostEqual(got, power(angle))
        self.assertEqual(result['scan']['angle']['values'], list(range(-40, 41, 10)))
        self.assertEqual(result['best']['angle_actual'], 0)
        self.assertEqual(self.client.saved_operation('axis.measure')['uses']['completed'], 9)

    def test_two_parameters_give_a_map_and_a_sweep_inside_a_saved_operation_combines(self):
        run = self.submit([{'id': 'map', 'operation': 'axis.measure',
                            'sweep': {'axis': ['stage', 'aux'], 'angle': [0, 45]}}])
        found = run.result(10)['map']['power']
        self.assertEqual((found['data']['shape'], [axis['name'] for axis in found['data']['axes']]), ([2, 2], ['axis', 'angle']))
        self.assertAlmostEqual(found['values'][0][1], 0)
        # Turning aux leaves the power where the stage left it.
        self.assertAlmostEqual(found['values'][1][0], 0)
        self.client.save_operation({'id': 'axis.row', 'layer': 'standard', 'description': 'Measure along one axis',
                                    'parameters': {'axis': MEASURE['parameters']['axis']},
                                    'steps': [{'id': 'scan', 'operation': 'axis.measure', 'arguments': {'axis': '${axis}'},
                                               'sweep': {'angle': [-45, 0, 45]}}],
                                    'result': {'power': {'step': 'scan', 'path': ['power']}}})
        rows = self.submit([{'id': 'rows', 'operation': 'axis.row', 'sweep': {'axis': ['stage', 'aux']}}]).result(10)
        self.assertEqual(rows['rows']['power']['data']['shape'], [2, 3])
        self.assertEqual([axis['name'] for axis in rows['rows']['power']['data']['axes']], ['axis', 'angle'])
        self.assertAlmostEqual(rows['rows']['power']['values'][0][1], 1)
        # A sweep inside a saved operation is gathered under its step, beside the named result of the use.
        run = self.submit([{'id': 'row', 'operation': 'axis.row', 'arguments': {'axis': 'stage'}}])
        self.assertEqual(set(run.result(10)), {'row', 'row.scan'})
        seen = run.read_array('row.scan.power', points=8)
        self.assertEqual((seen['along'], seen['values'][1]), (
            {'axis': 'angle', 'start': 0, 'stop': 3, 'low': -45.0, 'high': 45.0, 'step': 45.0, 'unit': 'deg'}, 1))
        with self.assertRaisesRegex(IOPError, "this result holds: 'row.power', 'row.scan.power', 'row.scan.angle'"):
            run.read_array('row.scan')

    def test_spectra_saved_in_files_combine_into_a_map_read_without_copying(self):
        self.client.register({'id': 'spectrometer', 'driver': Spectrometer.__module__ + ':Spectrometer'})
        run = self.submit([
            {'id': 'map', 'device': 'spectrometer', 'operation': 'spectrum', 'sweep': {'volts': [0, 1, 2, 3]}},
            {'id': 'tuned', 'device': 'spectrometer', 'operation': 'spectrum', 'after': ['map'], 'bindings': {
                'volts': {'step': 'map', 'view': {'field': 'spectrum', 'along': 'volts', 'at': {'wavelength': 1100}},
                          'path': ['summary', 'max_at']}}}])
        result = run.result(20)
        found = result['map']['spectrum']['data']
        self.assertEqual((found['shape'], found['unit'], len(found['files']), 'values' in result['map']['spectrum']),
                         ([4, 2000], 'counts', 4, False))
        self.assertEqual(result['map']['line']['values'], [500, 510, 520, 530])
        # The line moves 10 nm per volt; along wavelength at each bias its peak lies there.
        for volts in range(4):
            seen = run.read_array('map.spectrum', along='wavelength', at={'volts': volts}, points=50)
            self.assertAlmostEqual(seen['summary']['max_at'], 500 + 10 * volts)
        self.assertEqual(run.read_array('map.spectrum', along='volts', at={'wavelength': 1000})['values'][0], 100)
        # The later step took the bias that puts the line at 510 nm.
        self.assertEqual(result['tuned']['line'], 510)

    def test_a_saved_operation_with_a_sweep_in_a_continuous_experiment(self):
        self.client.save_operation(PEAK)
        with self.client.start_experiment(resources=['stage', 'meter']) as run:
            found = run.call('axis.peak', {'points': 10})
            self.assertEqual((found['angle'], found['power']), (0, 1))
            gathered = run.call('axis.measure', {'axis': 'stage'}, sweep={'angle': [10, 20]}, request_id='two-angles')
            self.assertEqual(gathered['angle']['values'], [10, 20])
            self.assertEqual(gathered['angle']['data']['axes'], [{'name': 'angle', 'start': 10.0, 'step': 10.0, 'unit': 'deg'}])
            seen = run.read_array('call.power', request_id='two-angles')
            self.assertEqual((seen['request_id'], seen['summary']['min_at']), ('two-angles', 20.0))

    @unittest.skipUnless(importlib.util.find_spec('mcp'), 'Install the agent extra to exercise MCP')
    def test_agents_sweep_in_a_batch_and_read_what_it_gathered(self):
        from ai4instruments.iop import mcp
        server = mcp.create_server(self.client)

        def call(name, arguments):
            found = asyncio.run(server.call_tool(name, arguments))
            content = found[0] if isinstance(found, tuple) else getattr(found, 'content', found)
            return json.loads(content[0].text)
        with self.client.start_experiment(resources=['stage', 'meter']) as run:
            submission = {'run_id': run.id, 'request_id': 'scan', 'wait': 25, 'steps': [
                {'id': 'scan', 'operation': 'axis.measure', 'arguments': {'axis': 'stage'},
                 'sweep': {'angle': {'start': -45, 'stop': 45, 'points': 91}}}]}
            reply = call('submit_operations', submission)
            for _ in range(10):
                # The same request returns the batch already accepted, waiting for it as an agent would.
                if reply['complete']:
                    break
                reply = call('submit_operations', submission)
            self.assertEqual((reply['complete'], reply['operations'], reply['sweeps']),
                             (True, [], {'scan': {'points': {'completed': 182}}}))
            self.assertEqual(reply['results']['scan']['power']['values_omitted'], 91)
            seen = call('read_array', {'run_id': run.id, 'request_id': 'scan', 'field': 'scan.power', 'points': 4})
            self.assertEqual((seen['summary']['max_at'], len(seen['envelope']['max'])), (0.0, 4))

    def test_sweeps_are_checked_before_anything_runs(self):
        for step, message in (
                ({'id': 's', 'device': 'stage', 'operation': 'move_to', 'arguments': {'angle_deg': 1},
                  'sweep': {'angle_deg': [1, 2]}}, 'not also given'),
                ({'id': 's', 'device': 'stage', 'operation': 'move_to', 'sweep': {'angle_deg': []}}, 'list the numbers'),
                ({'id': 's', 'operation': 'axis.measure', 'arguments': {'axis': 'stage'}, 'sweep': {'angle': [0, 120]}},
                 'outside'),
                ({'id': 's', 'device': 'stage', 'operation': 'move_to', 'sweep': {'angle_deg': '${a}'}}, 'give the values')):
            with self.assertRaisesRegex(IOPError, message):
                self.submit([step])
        with self.assertRaisesRegex(IOPError, 'describe parameter points'):
            self.client.save_operation({**PEAK, 'parameters': {}})
        with self.assertRaisesRegex(IOPError, 'start, stop'):
            self.client.save_operation({**PEAK, 'parameters': {}, 'steps': [
                {**PEAK['steps'][0], 'sweep': {'angle': {'start': 0, 'stop': 1}}}, PEAK['steps'][1]]})

"""Sweeps: a step run at each value of its parameters, what it returns gathered into arrays."""
import array
import asyncio
import importlib.util
import json
import math
import statistics
import struct
import time
import unittest
from datetime import datetime
from unittest.mock import patch
from ai4instruments.iop import Driver, IOPError, arrays, library, sweeps
from . import test_service as fixture
from .test_array_views import load

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


class Resonance(Driver):
    """A line 3 MHz wide at 1.0613 GHz, seen one frequency at a time."""
    def describe(self):
        return {'operations': {'probe': {'parameters': {'hz': {'type': 'number', 'unit': 'Hz'}}, 'effect': 'observe'}}}

    def invoke(self, operation, arguments, context):
        return {'power': 1 / (1 + ((arguments['hz'] - 1.0613e9) / 1.5e6) ** 2), 'unit': 'W'}


class Diode(Driver):
    """A source across a diode, which keeps the voltages it was given."""
    given = []

    def describe(self):
        return {'operations': {'source': {'parameters': {'volts': {'type': 'number', 'unit': 'V'}}}}}

    def invoke(self, operation, arguments, context):
        Diode.given.append(arguments['volts'])
        return {'current': 1e-12 * math.expm1(arguments['volts'] / 0.02585), 'unit': 'A'}


class Counter(Driver):
    """A reading that grows by one each time it is taken."""
    count = 0

    def describe(self):
        return {'operations': {'read': {'parameters': {}, 'effect': 'observe'}}}

    def invoke(self, operation, arguments, context):
        Counter.count += 1
        return {'count': Counter.count}


class Noisy(Driver):
    """A power of 1 mW, and a line 3 MHz wide at 1.0613 GHz, read with noise spread evenly over ±50 µW and ±0.02
    from a fixed sequence."""
    seed = 12345

    def describe(self):
        return {'operations': {'read': {'parameters': {}, 'effect': 'observe'},
                               'probe': {'parameters': {'hz': {'type': 'number', 'unit': 'Hz'}}, 'effect': 'observe'}}}

    def invoke(self, operation, arguments, context):
        return self.read(operation, arguments)

    @staticmethod
    def read(operation, arguments):
        Noisy.seed = (Noisy.seed * 6364136223846793005 + 1442695040888963407) % 2 ** 64
        noise = (Noisy.seed >> 11) / 2 ** 53 - 0.5
        if operation == 'read':
            return {'power': 1e-3 + 1e-4 * noise, 'unit': 'W'}
        return {'power': 1 / (1 + ((arguments['hz'] - 1.0613e9) / 1.5e6) ** 2) + 0.04 * noise, 'unit': 'W'}


class Flaky(Driver):
    """A meter whose readings get no reply when failing says so of their number; by default every other one."""
    calls = 0

    @staticmethod
    def failing(call):
        return call % 2

    def describe(self):
        return {'operations': {'read': {'parameters': {'x': {'type': 'number', 'default': 0}}, 'effect': 'observe'}}}

    def invoke(self, operation, arguments, context):
        Flaky.calls += 1
        if Flaky.failing(Flaky.calls):
            context.file('partial.txt').write_text('cut off')
            raise TimeoutError('no reply within 5 s')
        return {'value': arguments['x']}


class GatherCase(unittest.TestCase):
    """What a sweep returns, from what its points returned."""

    def test_a_step_repeats_at_each_point_over_a_last_axis(self):
        self.assertEqual(sweeps.grid({}, 3), ([{}, {}, {}], [3], [{'name': 'repeat', 'start': 0, 'step': 1}]))
        points, shape, axes = sweeps.grid({'axis': ['x', 'y']}, 2)
        self.assertEqual((points, shape, [axis['name'] for axis in axes]),
                         ([{'axis': 'x'}, {'axis': 'x'}, {'axis': 'y'}, {'axis': 'y'}], [2, 2], ['axis', 'repeat']))
        for sweep, repeat, message in (({}, 0, 'from 1 to 10000'), ({}, True, 'from 1 to 10000'),
                                       ({'repeat': [1, 2]}, 2, 'named repeat'),
                                       ({'x': {'start': 0, 'stop': 8, 'points': 5, 'adapt': ['power']}}, 2, 'does not repeat'),
                                       ({'x': list(range(101))}, 100, 'at most 10000 points')):
            with self.subTest(sweep=sweep, repeat=repeat), self.assertRaisesRegex(ValueError, message):
                sweeps.grid(sweep, repeat)

    def test_until_stops_a_sweep_beyond_its_limits_or_without_a_number(self):
        until = sweeps.limits({'path': ['current'], 'above': 1e-3, 'below': -1e-3})
        self.assertEqual([sweeps.beyond(until, value) for value in (0, 1e-3, 2e-3, -2e-3, None, math.nan)],
                         [False, False, True, True, True, True])
        for until, message in (({'path': ['current']}, 'above, below, or both'), ({'path': 'current', 'above': 1}, 'path'),
                               ({'path': ['current'], 'above': '1'}, 'number'), ({'path': ['i'], 'above': 1, 'over': 2}, 'path'),
                               ({'path': ['current'], 'above': 1, 'below': 1}, 'below is less than above'),
                               ({'path': ['fit', 'center'], 'below': 1, 'view': {'fit': 'voigt'}}, 'fit is line'),
                               ({'path': ['current'], 'above': 1, 'points': 0}, 'points is how many')):
            with self.subTest(until=until), self.assertRaisesRegex(ValueError, message):
                sweeps.limits(until)
        # A view of what the sweep gathered is read before each point, of at most 1000.
        step = {'id': 'mean', 'operation': 'read', 'repeat': 1001,
                'until': {'view': {'field': 'power'}, 'path': ['summary', 'error'], 'below': 1e-6, 'points': 10}}
        with self.assertRaisesRegex(ValueError, 'at most 1000 points'):
            library.swept(step)
        self.assertEqual(library.swept({**step, 'repeat': 1000})[1], [1000])

    def test_an_adaptive_range_places_each_point_from_the_readings_before_it(self):
        spec = {'start': 0, 'stop': 8, 'points': 5, 'adapt': ['power']}
        self.assertEqual(sweeps.taken('x', spec), ([{'range': spec, 'index': i} for i in range(5)], {'name': 'x', 'range': spec}))
        # The ends first, then the middle of the interval whose ends differ most; integer ends give integers.
        self.assertEqual(sweeps.adapted(spec, []), [0])
        self.assertEqual(sweeps.adapted(spec, [0.0, 0.0]), [0, 8, 4])
        self.assertEqual(sweeps.adapted(spec, [0.0, 0.0, 1.0, 1.0]), [0, 8, 4, 2, 6])
        # A difference counts by how far it exceeds twice the median one: a step that stands out draws the next
        # point; differences like the others, as from noise, do not, and the widest interval, here the first, does.
        finer = {**spec, 'stop': 8.0, 'points': 9}
        self.assertEqual(sweeps.adapted(finer, [0.0, 3.0, 0.0, 0.0, 0.0], [0, 8, 4, 2, 6])[5], 7.0)
        self.assertEqual(sweeps.adapted(finer, [0.0, 0.0, 0.0, 0.1, 0.1], [0, 8, 4, 2, 6])[5], 1.0)
        # Values already found are kept; a reading that is not a number counts only by its spacing.
        self.assertEqual(sweeps.adapted(spec, [0.0, None, 1.0], [0, 8, 4]), [0, 8, 4, 2])
        for sweep, message in (({'x': {**spec, 'adapt': 'power'}}, 'path to the number'),
                               ({'x': {**spec, 'points': 1}}, '2 to 1000 points'),
                               ({'x': {**spec, 'points': 10}}, 'do not fit between 0 and 8'),
                               ({'x': {**spec, 'stop': 0}}, 'ends of an adaptive range differ'),
                               ({'x': spec, 'y': [1, 2]}, 'varies one parameter')):
            with self.subTest(sweep=sweep), self.assertRaisesRegex(ValueError, message):
                sweeps.grid(sweep)

    def test_an_adaptive_range_finds_a_narrow_line_through_noise(self):
        # A line 3% of the range wide under noise of 2% of its height: 40 adaptive points put some on it from each
        # of ten noise sequences, then gather there, as spacing alone would not.
        spec = {'start': 1.0e9, 'stop': 1.1e9, 'points': 40, 'adapt': ['power']}
        for seed in range(1, 11):
            Noisy.seed, readings, values = seed, [], []
            for index in range(40):
                values = sweeps.adapted(spec, readings, values)
                readings.append(Noisy.read('probe', {'hz': values[index]})['power'])
            with self.subTest(seed=seed):
                self.assertGreaterEqual(sum(abs(f - 1.0613e9) < 1.5e6 for f in values), 4)

    def test_values_and_axes_of_a_sweep(self):
        points, shape, axes = sweeps.grid({'axis': ['x', 'y'], 'volts': {'start': 0, 'stop': 1, 'points': 3}})
        self.assertEqual((shape, len(points), points[:2]), ([2, 3], 6, [{'axis': 'x', 'volts': 0.0}, {'axis': 'x', 'volts': 0.5}]))
        self.assertEqual(axes, [{'name': 'axis', 'labels': ['x', 'y']}, {'name': 'volts', 'start': 0, 'stop': 1}])
        self.assertEqual(sweeps.taken('n', {'start': 10, 'stop': 0, 'points': 6})[0], [10, 8, 6, 4, 2, 0])
        self.assertEqual(sweeps.taken('f', [1, 2, 5, 10])[1], {'name': 'f', 'coordinates': [1.0, 2.0, 5.0, 10.0]})
        self.assertEqual(sweeps.taken('f', [1, 2, 3])[1], {'name': 'f', 'start': 1.0, 'step': 1.0})
        self.assertEqual(sweeps.taken('f', {'center': 10, 'span': 4, 'points': 5}),
                         ([8, 9, 10, 11, 12], {'name': 'f', 'start': 8, 'stop': 12}))
        # Ends read from earlier results leave each point what to read; read, they give its value.
        read = {'center': {'step': 'coarse', 'path': ['peak']}, 'span': 1, 'points': 3}
        self.assertEqual(sweeps.taken('f', read), ([{'range': read, 'index': i} for i in range(3)], {'name': 'f', 'range': read}))
        self.assertEqual(sweeps.value({**read, 'center': 2.0}, 2), 2.5)
        for spec, message in (([], 'list the numbers'), ([1, 'a'], 'list the numbers'), ({'start': 0, 'stop': 1}, 'start, stop'),
                              ({'start': 0, 'stop': 1, 'points': 0}, 'start, stop')):
            with self.assertRaisesRegex(ValueError, message):
                sweeps.grid({'v': spec})
        with self.assertRaisesRegex(ValueError, 'at most 10000 points'):
            sweeps.grid({'a': list(range(101)), 'b': list(range(100))})

    def test_oversized_ranges_are_rejected_before_generating_values(self):
        for ends in ({'start': 0, 'stop': 1}, {'center': {'step': 'prior', 'path': ['center']}, 'span': 1}):
            with self.subTest(ends=ends), patch.object(sweeps, '_value') as value:
                with self.assertRaisesRegex(ValueError, 'at most 10000 points'):
                    sweeps.taken('x', {**ends, 'points': 10 ** 9})
                value.assert_not_called()
        with patch.object(sweeps, 'taken') as taken:
            with self.assertRaisesRegex(ValueError, 'at most 10000 points'):
                sweeps.grid({'x': {'start': 0, 'stop': 1, 'points': 101}, 'y': list(range(100))})
            taken.assert_not_called()

    def test_nested_expansion_is_bounded_before_building_all_operations(self):
        definition = {'id': 'many', 'revision': 1, 'layer': 'standard', 'steps': [
            {'id': f'read{i}', 'device': 'meter', 'operation': 'measure'} for i in range(1001)]}
        with patch.object(library, 'derived') as derive:
            with self.assertRaisesRegex(ValueError, 'at most 10000 steps'):
                library.expand([{'id': 'outer', 'operation': 'many', 'sweep': {'unused': list(range(10000))}}],
                               lambda name: {**definition, 'parameters': {'unused': {'type': 'number'}}})
            derive.assert_not_called()

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

    def test_nested_file_arrays_keep_each_segments_size(self):
        first = {'data': {'dtype': 'float64', 'shape': [2], 'files': ['a', 'b'],
                           'file_sizes': [8, 8], 'bytes': 16}}
        second = {'data': {'dtype': 'float64', 'shape': [2], 'file': 'c'}}
        combined = sweeps.gather([first, second], [2], [{'name': 'row'}])
        self.assertEqual((combined['data']['shape'], combined['data']['files'], combined['data']['file_sizes']),
                         ([2, 2], ['a', 'b', 'c'], [8, 8, 16]))


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

    def test_a_running_sweep_shows_what_it_gathered_so_far(self):
        run = self.submit([{'id': 'turn', 'device': 'stage', 'operation': 'move_to', 'arguments': {'duration': 0.5},
                            'sweep': {'angle_deg': [0, 10, 20, 30, 40]}}])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            # Until a point completes, the sweep has gathered nothing.
            partial = (self.client.request(f'/v1/runs/{run.id}/result') or {}).get('turn')
            if partial and partial['angle_actual']['values'][1] is not None:
                break
            time.sleep(0.05)
        self.assertEqual(run.info(details=False)['state'], 'running')
        self.assertIsNone(partial['angle_actual']['values'][-1])
        seen = run.read_array('turn.angle_actual')
        self.assertEqual((seen['summary']['count'], seen['summary']['min']), (5, 0))
        self.assertGreater(seen['summary']['nonfinite'], 0)
        self.assertEqual(run.result(10)['turn']['angle_actual']['values'], [0, 10, 20, 30, 40])

    def test_independent_steps_and_sweeps_can_share_a_name_prefix(self):
        run = self.submit([
            {'id': 'scan', 'device': 'stage', 'operation': 'move_to', 'arguments': {'duration': 0},
             'sweep': {'angle_deg': [0, 10]}},
            {'id': 'scan.reference', 'device': 'meter', 'operation': 'measure', 'after': ['scan']},
            {'id': 'scan.0.reference', 'operation': 'axis.measure', 'arguments': {'axis': 'aux', 'angle': 0},
             'after': ['scan.reference']},
            {'id': 'scan.other', 'device': 'aux', 'operation': 'move_to', 'arguments': {'duration': 0},
             'sweep': {'angle_deg': [20, 30]}, 'after': ['scan.0.reference']}])
        result = run.result(10)
        self.assertTrue({'scan', 'scan.reference', 'scan.0.reference', 'scan.other'} <= set(result))
        self.assertIn('power', result['scan.reference'])
        self.assertEqual(result['scan.other']['angle_actual']['values'], [20, 30])
        self.assertFalse({'scan.0', 'scan.1', 'scan.other.0', 'scan.other.1'} & set(result))

    def test_a_nested_sweep_keeps_and_downloads_file_arrays(self):
        self.client.register({'id': 'spectrometer', 'driver': Spectrometer.__module__ + ':Spectrometer'})
        self.client.save_operation({
            'id': 'spectrometer.row', 'layer': 'standard', 'description': 'Record two neighboring spectra',
            'parameters': {'center': {'type': 'number'}},
            'steps': [{'id': 'scan', 'device': 'spectrometer', 'operation': 'spectrum',
                       'sweep': {'volts': {'center': '${center}', 'span': 1, 'points': 2}}}],
            'result': {'spectra': {'step': 'scan', 'path': ['spectrum']}}})
        run = self.submit([{'id': 'rows', 'operation': 'spectrometer.row', 'sweep': {'center': [0, 2]}}])
        data = run.result(10)['rows']['spectra']['data']
        self.assertEqual((data['shape'], data['file_sizes']), ([2, 2, 2000], [16000] * 4))
        seen = run.read_array('rows.spectra', along=2, at={'center': 1, 'volts': 0})
        self.assertEqual(seen['summary']['max_at'], 515)
        header, payload = load(run.array_file('rows.spectra'))
        self.assertEqual((header['shape'], len(payload)), ((2, 2, 2000), 64000))

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

    def test_a_fine_sweep_centered_where_a_coarse_sweep_found_most_power(self):
        # The coarse points miss the peak at 0 deg by 3 deg; around the best of them, the fine sweep finds it.
        peak = {'view': {'field': 'power'}, 'path': ['summary', 'max_at']}
        run = self.submit([
            {'id': 'coarse', 'operation': 'axis.measure', 'arguments': {'axis': 'stage'},
             'sweep': {'angle': {'start': -37, 'stop': 43, 'points': 9}}},
            {'id': 'fine', 'operation': 'axis.measure', 'arguments': {'axis': 'stage'},
             'sweep': {'angle': {'center': {'step': 'coarse', **peak}, 'span': 10, 'points': 11}}},
            {'id': 'settle', 'device': 'stage', 'operation': 'move_to', 'arguments': {'duration': 0},
             'sweep': {'angle_deg': {'center': {'step': 'fine', **peak}, 'span': 0.5, 'points': 3}}}])
        result = run.result(20)
        self.assertEqual(result['coarse']['angle']['values'][4], 3)
        self.assertEqual(result['fine']['power']['data']['axes'], [{'name': 'angle', 'start': -2, 'stop': 8, 'unit': 'deg'}])
        self.assertEqual(result['fine']['angle']['values'], list(range(-2, 9)))
        self.assertEqual(run.read_array('fine.power')['summary']['max_at'], 0)
        # The line through the coarse points has its center between them, by the true peak.
        self.assertAlmostEqual(run.read_array('coarse.power', peak='max')['peak']['center'], 0, delta=0.2)
        self.assertEqual(result['settle']['angle_actual']['values'], [-0.25, 0, 0.25])
        # Each point records the range it read and the value it took.
        turned = {op['step']: op for op in run.info()['operation_records']}['fine.4.turn']
        self.assertEqual((turned['arguments']['angle_deg'], turned['bindings']['angle_deg']['index']), (2, 4))
        self.assertEqual(turned['bindings']['angle_deg']['range']['span'], 10)

    def test_saved_operations_sweep_around_what_they_found_and_what_they_are_given(self):
        found = {'step': 'coarse', 'view': {'field': 'power'}, 'path': ['summary', 'max_at']}
        self.client.save_operation({
            'id': 'axis.find', 'layer': 'standard', 'description': 'Find the angle of most power, coarse then fine',
            'steps': [{'id': 'coarse', 'operation': 'axis.measure', 'arguments': {'axis': 'stage'},
                       'sweep': {'angle': {'start': -37, 'stop': 43, 'points': 9}}},
                      {'id': 'fine', 'operation': 'axis.measure', 'arguments': {'axis': 'stage'},
                       'sweep': {'angle': {'center': found, 'span': 10, 'points': 11}}}],
            'result': {'angle': {**found, 'step': 'fine'}}})
        self.client.save_operation({
            'id': 'axis.around', 'layer': 'standard', 'description': 'Measure around an angle',
            'parameters': {'center': {'type': 'number', 'unit': 'deg'}},
            'steps': [{'id': 'scan', 'operation': 'axis.measure', 'arguments': {'axis': 'stage'},
                       'sweep': {'angle': {'center': '${center}', 'span': 4, 'points': 5}}}],
            'result': {'power': {'step': 'scan', 'path': ['power']}}})
        run = self.submit([{'id': 'find', 'operation': 'axis.find'},
                           {'id': 'around', 'operation': 'axis.around', 'bindings': {'center': {'step': 'find', 'path': ['angle']}}}])
        result = run.result(20)
        self.assertEqual(result['find']['angle'], 0)
        self.assertEqual(result['around']['power']['data']['axes'], [{'name': 'angle', 'start': -2, 'stop': 2, 'unit': 'deg'}])
        self.assertAlmostEqual(result['around']['power']['values'][2], 1)
        # Given as an argument, the same parameter is a number before anything runs.
        given = self.submit([{'id': 'around', 'operation': 'axis.around', 'arguments': {'center': 10}}]).result(10)
        self.assertEqual(given['around.scan']['angle']['values'], [8, 9, 10, 11, 12])

    def test_an_adaptive_sweep_puts_its_points_on_the_line(self):
        self.client.register({'id': 'analyzer', 'driver': Resonance.__module__ + ':Resonance'})
        line = {'view': {'field': 'power', 'peak': 'max'}, 'path': ['peak', 'center']}
        run = self.submit([
            {'id': 'adaptive', 'device': 'analyzer', 'operation': 'probe',
             'sweep': {'hz': {'start': 1.0e9, 'stop': 1.1e9, 'points': 40, 'adapt': ['power']}}},
            {'id': 'even', 'device': 'analyzer', 'operation': 'probe', 'sweep': {'hz': {'start': 1.0e9, 'stop': 1.1e9, 'points': 40}}},
            # A coarse look, then 20 points placed around the line it shows.
            {'id': 'coarse', 'device': 'analyzer', 'operation': 'probe', 'sweep': {'hz': {'start': 1.0e9, 'stop': 1.1e9, 'points': 11}}},
            {'id': 'fine', 'device': 'analyzer', 'operation': 'probe', 'sweep': {'hz': {
                'center': {'step': 'coarse', **line}, 'span': 20e6, 'points': 20, 'adapt': ['power']}}}])
        result = run.result(20)
        hz = result['adaptive']['power']['data']['axes'][0]
        self.assertEqual((hz['name'], hz['unit'], len(hz['coordinates'])), ('hz', 'Hz', 40))
        self.assertEqual(hz['coordinates'], sorted(hz['coordinates']))
        # 16 of the 40 points lie within a line width of the center; spaced evenly, three do.
        near = [f for f in hz['coordinates'] if abs(f - 1.0613e9) < 3e6]
        self.assertEqual((len(near) >= 15, sum(abs(1e9 + 1e8 * i / 39 - 1.0613e9) < 3e6 for i in range(40))), (True, 3))
        adaptive, even = (run.read_array(f'{name}.power', peak='max')['peak'] for name in ('adaptive', 'even'))
        self.assertAlmostEqual(adaptive['center'], 1.0613e9, delta=2e4)
        self.assertAlmostEqual(adaptive['width'], 3e6, delta=6e4)
        self.assertGreater(abs(even['width'] - 3e6), 4 * abs(adaptive['width'] - 3e6))
        self.assertAlmostEqual(run.read_array('fine.power', peak='max')['peak']['center'], 1.0613e9, delta=5e4)
        # Each point records the frequency it was given; the first two are the ends.
        given = {op['step']: op['arguments']['hz'] for op in run.info()['operation_records']}
        self.assertEqual((given['adaptive.0'], given['adaptive.1'], given['adaptive.2']), (1.0e9, 1.1e9, 1.05e9))
        self.assertEqual(sorted(given[f'adaptive.{i}'] for i in range(40)), hz['coordinates'])

    def test_a_running_adaptive_sweep_holds_the_points_finished_so_far_in_order(self):
        run = self.submit([{'id': 'turn', 'device': 'stage', 'operation': 'move_to', 'arguments': {'duration': 0.3},
                            'sweep': {'angle_deg': {'start': -40, 'stop': 40, 'points': 6, 'adapt': ['angle_actual']}}}])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            partial = (self.client.request(f'/v1/runs/{run.id}/result') or {}).get('turn')
            if partial and 3 <= len(partial['angle_actual']['values']) < 6:
                break
            time.sleep(0.05)
        values, axis = partial['angle_actual']['values'], partial['angle_actual']['data']['axes'][0]
        placed = axis.get('coordinates') or [axis['start'] + i * axis['step'] for i in range(len(values))]
        self.assertEqual((values, placed), (sorted(values), sorted(values)))
        self.assertEqual(run.result(10)['turn']['angle_actual']['values'], [-40, -30, -20, 0, 20, 40])

    def test_an_adaptive_sweep_of_a_saved_operation_reads_its_named_result(self):
        run = self.submit([{'id': 'scan', 'operation': 'axis.measure', 'arguments': {'axis': 'stage'},
                            'sweep': {'angle': {'start': -45, 'stop': 45, 'points': 9, 'adapt': ['power']}}}])
        found = run.result(10)['scan']
        angles = found['power']['data']['axes'][0]['coordinates']
        self.assertEqual((angles[0], angles[-1], angles, found['angle']['values']), (-45, 45, sorted(angles), angles))
        for got, angle in zip(found['power']['values'], angles):
            self.assertAlmostEqual(got, power(angle))

    def test_a_ramp_stops_at_compliance_and_the_steps_after_it_run(self):
        self.client.register({'id': 'smu', 'driver': Diode.__module__ + ':Diode'})
        Diode.given = []
        run = self.submit([
            {'id': 'ramp', 'device': 'smu', 'operation': 'source', 'sweep': {'volts': {'start': 0, 'stop': 1, 'points': 101}},
             'until': {'path': ['current'], 'above': 1e-3}},
            {'id': 'zero', 'device': 'smu', 'operation': 'source', 'arguments': {'volts': 0}, 'after': ['ramp']}])
        current = run.result(20)['ramp']['current']
        # The current first exceeds 1 mA at 0.54 V: the source is given no higher voltage, then returns to zero.
        self.assertEqual((run.info(details=False)['state'], max(Diode.given), Diode.given[-1], len(Diode.given)),
                         ('completed', 0.54, 0, 56))
        self.assertEqual(run.info(details=False)['operation_states'], {'completed': 56, 'skipped': 46})
        self.assertEqual((current['data']['shape'], current['data']['unit'], current['values'][55:]), ([101], 'A', [None] * 46))
        self.assertAlmostEqual(current['values'][54], 1.18e-3, delta=1e-5)
        ops = {op['step']: op for op in run.info()['operation_records']}
        self.assertEqual([ops[f'ramp.{i}']['state'] for i in (54, 55, 100)], ['completed', 'skipped', 'skipped'])
        self.assertNotIn('started', ops['ramp.55'])
        stopped = {'point': 54, 'reading': current['values'][54]}
        self.assertEqual(ops['ramp.100']['gathers']['ramp']['stopped'], stopped)
        events = self.client.request(f'/v1/events?run_id={run.id}&limit=1000')['events']
        self.assertEqual([{key: e[key] for key in ('sweep', 'point', 'reading', 'skipped')} for e in events
                          if e['kind'] == 'sweep.stopped'], [{'sweep': 'ramp', 'skipped': 46, **stopped}])
        # Between the last two points, the current reaches 1 mA at 0.5352 V; on the diode's curve, at 0.5357 V.
        crossing = run.read_array('ramp.current', level=1e-3)['crossings']
        self.assertEqual((len(crossing), crossing[0]['rising'], crossing[0]['index']), (1, True, 53))
        self.assertAlmostEqual(crossing[0]['at'], 0.5352, delta=1e-4)
        # A reading that is not a number does not show that the limit holds: the sweep stops after its first point.
        run = self.submit([{'id': 'ramp', 'device': 'smu', 'operation': 'source', 'sweep': {'volts': [0, 0.1, 0.2]},
                            'until': {'path': ['curent'], 'above': 1e-3}}])
        self.assertEqual(run.result(10)['ramp']['current']['values'], [0, None, None])

    def test_saved_operations_stop_their_sweeps_at_the_limits_they_are_given(self):
        self.client.register({'id': 'smu', 'driver': Diode.__module__ + ':Diode'})
        self.client.save_operation({
            'id': 'diode.iv', 'layer': 'standard', 'description': 'Ramp the forward bias up to a compliance, then to zero',
            'parameters': {'compliance': {'type': 'number', 'unit': 'A'}},
            'steps': [{'id': 'ramp', 'device': 'smu', 'operation': 'source',
                       'sweep': {'volts': {'start': 0, 'stop': 1, 'points': 101}},
                       'until': {'path': ['current'], 'above': '${compliance}'}},
                      {'id': 'zero', 'device': 'smu', 'operation': 'source', 'arguments': {'volts': 0}}],
            'result': {'current': {'step': 'ramp', 'path': ['current']}}})
        # Each use stops at its own compliance and returns to zero; the next use then runs.
        found = self.submit([{'id': 'rows', 'operation': 'diode.iv', 'sweep': {'compliance': [1e-4, 1e-3]}}]).result(20)
        current = found['rows']['current']
        self.assertEqual((current['data']['shape'], [row.index(None) for row in current['values']]), ([2, 101], [49, 55]))
        self.assertEqual(self.client.saved_operation('diode.iv')['uses']['completed'], 2)
        # A use swept in a continuous experiment stops on its named result; uses that never ran are not counted.
        self.client.save_operation({
            'id': 'diode.point', 'layer': 'standard', 'description': 'Source a forward bias and read the current',
            'parameters': {'volts': {'type': 'number', 'unit': 'V'}},
            'steps': [{'id': 'source', 'device': 'smu', 'operation': 'source', 'arguments': {'volts': '${volts}'}}],
            'result': {'current': {'step': 'source', 'path': ['current']}}})
        with self.client.start_experiment(resources=['smu']) as run:
            found = run.call('diode.point', sweep={'volts': {'start': 0, 'stop': 1, 'points': 101}},
                             until={'path': ['current'], 'above': 1e-3})
        self.assertEqual(found['current']['values'].index(None), 55)
        self.assertEqual(self.client.saved_operation('diode.point')['uses']['calls'], 55)

    def test_a_graph_is_previewed_and_a_running_one_tells_when_it_ends(self):
        steps = [{'id': 'turn', 'device': 'stage', 'operation': 'move_to', 'arguments': {'duration': 0.2},
                  'sweep': {'angle_deg': [0, 10, 20, 30, 40]}}]
        # Nothing has run on the stage yet: the preview counts the moves, and no time is estimated.
        self.assertEqual(self.client.preview(steps), {'preview': True, 'operations': 5, 'estimated_s': None,
                                                      'by_operation': {'stage.move_to': {'operations': 5}}, 'requires': {}})
        run = self.submit(steps)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and run.info(details=False)['operation_states'].get('completed', 0) < 2:
            time.sleep(0.02)
        # Two moves done, three to go at the same pace.
        ends = datetime.fromisoformat(run.info(details=False)['estimated_end']).timestamp() - time.time()
        self.assertTrue(0 < ends < 3, ends)
        run.result(10)
        self.assertNotIn('estimated_end', run.info(details=False))
        # Now each move is known to take about 0.2 s.
        preview = self.client.preview(steps)
        self.assertEqual(preview['by_operation']['stage.move_to']['from'], 5)
        self.assertAlmostEqual(preview['estimated_s'], 1.0, delta=0.5)

    def test_a_step_tries_again_an_instrument_call_that_failed(self):
        self.client.register({'id': 'flaky', 'driver': Flaky.__module__ + ':Flaky'})
        Flaky.calls = 0
        # Every other reading gets no reply: each point is read again 0.05 s later, and the sweep completes.
        run = self.submit([{'id': 'scan', 'device': 'flaky', 'operation': 'read', 'sweep': {'x': [1, 2, 3]},
                            'retry': {'times': 1, 'after_s': 0.05}}])
        self.assertEqual(run.result(10)['scan']['value']['values'], [1, 2, 3])
        operations = run.info()['operation_records']
        self.assertEqual([len(op['attempts']) for op in operations], [1, 1, 1])
        self.assertEqual(operations[0]['attempts'][0]['error'], 'TimeoutError: no reply within 5 s')
        self.assertNotIn('files', operations[0])
        self.assertNotIn('retry_at', operations[0])
        waited = [datetime.fromisoformat(op['started']) - datetime.fromisoformat(op['attempts'][0]['ended']) for op in operations]
        self.assertTrue(all(w.total_seconds() >= 0.05 for w in waited))
        events = self.client.request(f'/v1/events?run_id={run.id}&limit=1000')['events']
        self.assertEqual(sum(e['kind'] == 'operation.retrying' for e in events), 3)
        # Without retry the first missing reply ends the experiment.
        Flaky.calls = 0
        self.assertEqual(self.submit([{'id': 'scan', 'device': 'flaky', 'operation': 'read', 'sweep': {'x': [1, 2]}}])
                         .wait(10, details=False)['state'], 'failed')
        with self.assertRaisesRegex(IOPError, 'retry is'):
            self.submit([{'id': 'scan', 'device': 'flaky', 'operation': 'read', 'retry': {'times': 0}}])

    def test_a_step_graph_continues_where_it_stopped(self):
        self.client.register({'id': 'flaky', 'driver': Flaky.__module__ + ':Flaky'})
        Flaky.calls = 0
        steps = [{'id': 'scan', 'device': 'flaky', 'operation': 'read', 'sweep': {'x': [1, 2, 3, 4]}},
                 {'id': 'last', 'device': 'flaky', 'operation': 'read', 'arguments': {'x': 5}, 'after': ['scan']}]
        # The meter stops answering at the third point, and the experiment fails there.
        with patch.object(Flaky, 'failing', lambda call: call >= 3):
            run = self.submit(steps)
            self.assertEqual(run.wait(10, details=False)['state'], 'failed')
        info = run.info(details=False)
        self.assertTrue(info['can_resume'])
        self.assertEqual(info['operation_states'], {'completed': 2, 'cancelled': 2, 'failed': 1})
        self.assertIn('partial.txt', [f['name'].rsplit('/', 1)[-1] for f in run.files()])
        # Answering again, it continues: the two points read are kept, the rest are read, and the sweep gathers all.
        with patch.object(Flaky, 'failing', lambda call: False):
            self.assertEqual(run.resume('continue-1')['attempt'], 2)
            self.assertEqual(run.result(10)['scan']['value']['values'], [1, 2, 3, 4])
        self.assertEqual(Flaky.calls, 6)
        operations = run.info()['operation_records']
        self.assertEqual([[a['state'] for a in op.get('attempts', [])] for op in operations], [[], [], ['failed'], [], []])
        self.assertEqual(operations[2]['attempts'][0]['error'], 'TimeoutError: no reply within 5 s')
        self.assertNotIn('partial.txt', [f['name'].rsplit('/', 1)[-1] for f in run.files()])
        self.assertFalse(run.info(details=False)['can_resume'])
        self.assertEqual(run.resume('continue-1')['attempt'], 2)
        with self.assertRaisesRegex(IOPError, 'Continue an interrupted, failed or cancelled experiment'):
            run.resume('continue-2')

    def test_a_device_record_limits_what_its_setup_tolerates(self):
        from .test_conditions import Part
        limits = {'source': {'volts': {'maximum': 0.6, 'note': 'The diode overheats above 0.6 V'}}}
        self.client.register({'id': 'smu', 'driver': Diode.__module__ + ':Diode', 'limits': limits})
        volts = next(d for d in self.client.devices() if d['id'] == 'smu')['operations']['source']['parameters']['volts']
        self.assertEqual((volts['maximum'], volts['limit']), (0.6, 'The diode overheats above 0.6 V'))
        # A ramp that would go beyond is refused before anything runs, with the reason; within, it runs.
        Diode.given = []
        with self.assertRaises(IOPError) as refused:
            self.submit([{'id': 'ramp', 'device': 'smu', 'operation': 'source',
                          'sweep': {'volts': {'start': 0, 'stop': 1, 'points': 101}}}])
        self.assertEqual(refused.exception.code, 'invalid_arguments')
        self.assertIn('volts is 0.61 V, above 0.6 V: The diode overheats above 0.6 V', str(refused.exception))
        self.assertEqual(Diode.given, [])
        ramp = self.submit([{'id': 'ramp', 'device': 'smu', 'operation': 'source', 'sweep': {'volts': [0, 0.3, 0.6]}}])
        self.assertEqual(ramp.result(10)['ramp']['current']['data']['shape'], [3])
        # A limit stays within what the instrument accepts, on a numeric parameter of one of its operations.
        for limits, message in (({'set_x': {'position': {'maximum': 2}}}, 'takes nothing outside -1 to 1'),
                                ({'set_x': {'position': {'minimum': 0.5, 'maximum': 0.2}}}, 'minimum is above maximum'),
                                ({'set_x': {'speed': {'maximum': 1}}}, 'numeric parameter'), ({'jump': {'x': {}}}, 'name an operation')):
            with self.subTest(limits=limits), self.assertRaisesRegex(IOPError, message):
                self.client.register({'id': 'coupler', 'driver': Part.__module__ + ':Part', 'config': {'part': 'coupler'},
                                      'limits': limits})

    def test_a_step_repeats_for_the_spread_of_a_reading_or_until_a_reading(self):
        # Twenty readings in turn; at each of two angles, three readings, a row per angle.
        result = self.submit([{'id': 'read', 'device': 'meter', 'operation': 'measure', 'repeat': 20},
                              {'id': 'rows', 'operation': 'axis.measure', 'arguments': {'axis': 'stage'},
                               'sweep': {'angle': [0, 45]}, 'repeat': 3, 'after': ['read']}]).result(20)
        read = result['read']['power']['data']
        self.assertEqual((read['shape'], read['axes']), ([20], [{'name': 'repeat', 'start': 0, 'step': 1}]))
        rows = result['rows']['power']
        self.assertEqual((rows['data']['shape'], [axis['name'] for axis in rows['data']['axes']]), ([2, 3], ['angle', 'repeat']))
        self.assertEqual([[round(value, 9) for value in row] for row in rows['values']], [[1, 1, 1], [0, 0, 0]])
        # A saved operation takes how many times from a parameter.
        self.client.save_operation({'id': 'meter.spread', 'layer': 'standard', 'description': 'Read the meter several times',
                                    'parameters': {'n': {'type': 'integer', 'minimum': 1}},
                                    'steps': [{'id': 'read', 'device': 'meter', 'operation': 'measure', 'repeat': '${n}'}],
                                    'result': {'power': {'step': 'read', 'path': ['power']}}})
        spread = self.submit([{'id': 'spread', 'operation': 'meter.spread', 'arguments': {'n': 4}}]).result(10)
        self.assertEqual(spread['spread']['power']['data']['shape'], [4])
        # Repeated until a reading passes a limit, a step waits for it: here the fifth reading, above 4.
        self.client.register({'id': 'counter', 'driver': Counter.__module__ + ':Counter'})
        Counter.count = 0
        run = self.submit([{'id': 'poll', 'device': 'counter', 'operation': 'read', 'repeat': 100,
                            'until': {'path': ['count'], 'above': 4}}])
        self.assertEqual(run.result(10)['poll']['count']['values'][:6], [1, 2, 3, 4, 5, None])
        self.assertEqual(run.info(details=False)['operation_states'], {'completed': 5, 'skipped': 95})

    def test_a_sweep_stops_once_what_it_gathered_is_known_well_enough(self):
        self.client.register({'id': 'noisy', 'driver': Noisy.__module__ + ':Noisy'})
        Noisy.seed = 12345
        # A power read with a spread of 29 µW, until its mean is known to 5 µW, from five readings on.
        run = self.submit([{'id': 'mean', 'device': 'noisy', 'operation': 'read', 'repeat': 1000,
                            'until': {'view': {'field': 'power'}, 'path': ['summary', 'error'], 'below': 5e-6, 'points': 5}}])
        values = [value for value in run.result(20)['mean']['power']['values'] if value is not None]
        # It stops at the first reading whose error of the mean is below 5 µW, here the 37th of the 1000 allowed.
        error = [statistics.stdev(values[:n]) / math.sqrt(n) for n in (len(values) - 1, len(values))]
        self.assertTrue(error[1] < 5e-6 <= error[0] and len(values) < 100)
        summary = run.read_array('mean.power')['summary']
        self.assertAlmostEqual(summary['error'], error[1], delta=1e-12)
        self.assertAlmostEqual(summary['mean'], 1e-3, delta=3 * summary['error'])
        stopped = next(op['gathers']['mean']['stopped'] for op in run.info()['operation_records'] if 'gathers' in op)
        self.assertEqual(stopped['point'], len(values) - 1)
        self.assertAlmostEqual(stopped['reading'], error[1], delta=1e-12)
        # An adaptive sweep over 100 MHz, until a Lorentzian through what it gathered gives the line's center to
        # 20 kHz: here after 18 of the 200 points it may take.
        run = self.submit([{'id': 'find', 'device': 'noisy', 'operation': 'probe',
                            'sweep': {'hz': {'start': 1.0e9, 'stop': 1.1e9, 'points': 200, 'adapt': ['power']}},
                            'until': {'view': {'field': 'power', 'fit': 'lorentzian'}, 'path': ['fit', 'errors', 'center'],
                                      'below': 20e3, 'points': 10}}])
        run.result(60)
        fit = run.read_array('find.power', fit='lorentzian')['fit']
        self.assertLess(run.info(details=False)['operation_states']['completed'], 50)
        self.assertLess(fit['errors']['center'], 20e3)
        self.assertAlmostEqual(fit['center'], 1.0613e9, delta=3 * fit['errors']['center'])
        self.assertAlmostEqual(fit['width'], 3e6, delta=3 * fit['errors']['width'])

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
        # Read at every bias at once, from the four files: the line's center against the bias.
        rows = run.read_array('map.spectrum', along='wavelength', peak='max', points=1)['rows']
        self.assertEqual((rows['axis'], rows['coordinates'], [round(line['center'], 6) for line in rows['peak']]),
                         ('volts', [0, 1, 2, 3], [500, 510, 520, 530]))
        self.assertEqual(run.read_array('map.spectrum', along='volts', at={'wavelength': 1000})['values'][0], 100)
        # As a map: each bias a row, wavelengths 505 to 535 nm in columns of 2 nm.
        seen = run.read_array('map.spectrum', along=['volts', 'wavelength'], low=[None, 505], high=[None, 535],
                              points=[4, 15])
        self.assertEqual([(axis['axis'], axis['low'], axis['high']) for axis in seen['along']],
                         [('volts', 0.0, 3.0), ('wavelength', 505.0, 535.0)])
        self.assertEqual((len(seen['envelope']['max']), len(seen['envelope']['max'][0]), seen['summary']['max_at']),
                         (4, 15, [1.0, 510.0]))
        self.assertEqual([row.index(max(row)) for row in seen['envelope']['max']], [0, 2, 7, 12])
        # The later step took the bias that puts the line at 510 nm.
        self.assertEqual(result['tuned']['line'], 510)
        # The whole map, from the four files, as one NumPy array; and the spectrum of one point.
        header, data = load(run.array_file('map.spectrum'))
        self.assertEqual((header['descr'], header['shape'], len(data)), ('<f8', (4, 2000), 4 * 2000 * 8))
        self.assertEqual(struct.unpack_from('<d', data, (3 * 2000 + 1300) * 8)[0], 100)
        tuned = next(op for op in run.info()['operation_records'] if op['step'] == 'tuned')
        self.assertEqual(load(self.client.array_file(tuned['id'], 'spectrum'))[1], data[2000 * 8:2 * 2000 * 8])

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
            # A search that stops once the power exceeds 0.9: at -9 degrees, where cos²(18°) is 0.905.
            reply = call('submit_operations', {**submission, 'request_id': 'search', 'steps': [
                {**submission['steps'][0], 'until': {'path': ['power'], 'above': 0.9}}]})
            self.assertEqual(reply['sweeps']['scan']['points'], {'completed': 74, 'skipped': 108})
            self.assertEqual(reply['sweeps']['scan']['stopped']['point'], 36)
            self.assertAlmostEqual(reply['sweeps']['scan']['stopped']['reading'], power(-9))

    def test_sweeps_are_checked_before_anything_runs(self):
        for step, message in (
                ({'id': 's', 'device': 'stage', 'operation': 'move_to', 'arguments': {'angle_deg': 1},
                  'sweep': {'angle_deg': [1, 2]}}, 'not also given'),
                ({'id': 's', 'device': 'stage', 'operation': 'move_to', 'sweep': {'angle_deg': []}}, 'list the numbers'),
                ({'id': 's', 'operation': 'axis.measure', 'arguments': {'axis': 'stage'}, 'sweep': {'angle': [0, 120]}},
                 'outside'),
                ({'id': 's', 'device': 'stage', 'operation': 'move_to', 'sweep': {'angle_deg': '${a}'}}, 'give the values'),
                ({'id': 's', 'device': 'stage', 'operation': 'move_to',
                  'sweep': {'angle_deg': {'center': 0, 'span': 1, 'points': {'step': 'x'}}}}, 'positive integer'),
                ({'id': 's', 'device': 'stage', 'operation': 'move_to',
                  'sweep': {'angle_deg': {'center': {'step': 'nowhere'}, 'span': 1, 'points': 3}}}, 'unknown step'),
                ({'id': 's', 'device': 'stage', 'operation': 'move_to', 'until': {'path': ['angle_actual'], 'above': 1}},
                 'until and retake belong to a sweep'),
                ({'id': 's', 'device': 'stage', 'operation': 'move_to', 'repeat': 0}, 'from 1 to 10000'),
                ({'id': 's', 'device': 'stage', 'operation': 'move_to', 'sweep': {'angle_deg': [1, 2]},
                  'until': {'path': ['angle_actual'], 'above': 2, 'below': 3}}, 'below is less than above'),
                ({'id': 's', 'device': 'stage', 'operation': 'move_to', 'sweep': {'angle_deg': [1, 2]},
                  'until': {'path': ['angle_actual'], 'above': '${limit}'}}, 'values of sweep, repeat and until')):
            with self.assertRaisesRegex(IOPError, message):
                self.submit([step])
        # An end read from an earlier result is a number only once it is read: the first point fails if it is not.
        run = self.submit([{'id': 'read', 'device': 'meter', 'operation': 'measure'},
                           {'id': 'turn', 'device': 'stage', 'operation': 'move_to',
                            'sweep': {'angle_deg': {'start': 0, 'stop': {'step': 'read', 'path': ['unit']}, 'points': 3}}}])
        self.assertEqual(run.wait(10)['state'], 'failed')
        states = {op['step']: (op['state'], op.get('error', '')) for op in run.info()['operation_records']}
        self.assertEqual((states['turn.0'][0], states['turn.2'][0]), ('failed', 'cancelled'))
        self.assertIn("stop is 'W'", states['turn.0'][1])
        with self.assertRaisesRegex(IOPError, 'describe parameter points'):
            self.client.save_operation({**PEAK, 'parameters': {}})
        with self.assertRaisesRegex(IOPError, 'start, stop'):
            self.client.save_operation({**PEAK, 'parameters': {}, 'steps': [
                {**PEAK['steps'][0], 'sweep': {'angle': {'start': 0, 'stop': 1}}}, PEAK['steps'][1]]})
        with self.assertRaisesRegex(IOPError, 'describe parameter limit'):
            self.client.save_operation({**PEAK, 'steps': [
                {**PEAK['steps'][0], 'until': {'path': ['power'], 'above': '${limit}'}}, PEAK['steps'][1]]})
        with self.assertRaisesRegex(IOPError, 'until and retake belong to a sweep'):
            self.client.save_operation({**PEAK, 'steps': [
                PEAK['steps'][0], {**PEAK['steps'][1], 'until': {'path': ['angle_actual'], 'above': 1}}]})

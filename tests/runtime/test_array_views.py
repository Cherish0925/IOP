"""Views of saved arrays: what a trace, spectrum or image says without moving all of its values."""
import array
import ast
import importlib.util
import io
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ai4instruments.iop import Driver, IOPError, arrays, views
from . import test_service as fixture
from .test_arrays import start

# A resonance dip over a frequency sweep: 1001 points, 1.0 to 1.1 GHz.
DIP = [1.0 - 0.8 / (1 + ((i - 600) / 20) ** 2) for i in range(1001)]
# Three modes on the same sweep, 0.77, 0.5 and 0.3 deep, on a ripple 0.005 high.
MODES = [1.0 - sum(depth / (1 + ((1.0e9 + 1e5 * i - center) / half) ** 2)
                   for center, half, depth in ((1.0213e9, 1.05e6, 0.77), (1.0587e9, 0.8e6, 0.5), (1.0841e9, 1.5e6, 0.3)))
         + 0.005 * math.sin(i / 7) for i in range(1001)]
FREQUENCY = {'name': 'frequency', 'unit': 'Hz', 'start': 1.0e9, 'step': 1.0e5}


def saved(folder, values, description):
    """Write values as an operation would save them and return the array's holder and file."""
    payload = _payload(values, description)
    holder = arrays.result(payload, arrays.check_result(description), None)
    path = Path(folder) / f'{len(os.listdir(folder))}.bin'
    path.write_bytes(payload)
    return holder, path


def load(payload):
    """The header and the data of a .npy file, read as NumPy reads them; with NumPy present, also by NumPy."""
    length = struct.unpack('<H', payload[8:10])[0]
    assert payload[:8] == b'\x93NUMPY\x01\x00' and (10 + length) % 64 == 0
    header = ast.literal_eval(payload[10:10 + length].decode('latin-1'))
    if importlib.util.find_spec('numpy'):
        import numpy
        loaded = numpy.load(io.BytesIO(payload))
        assert (loaded.dtype.str, loaded.shape) == (header['descr'], header['shape'])
    return header, payload[10 + length:]


def _payload(values, description):
    dtype = description.get('dtype', 'float64')
    flat = array.array(arrays._typecode(dtype), values)
    if description.get('byte_order', 'little') != sys.byteorder and flat.itemsize > 1:
        flat.byteswap()
    return flat.tobytes()


class Sweeps(Driver):
    """A network analyzer model whose sweeps are saved as arrays."""
    def describe(self):
        return {'operations': {'sweep': {'parameters': {}, 'effect': 'observe'},
                               'pair': {'parameters': {}, 'effect': 'observe'}}}

    def invoke(self, operation, arguments, context):
        trace = arrays.result(_payload(DIP, {}), arrays.check_result({'dtype': 'float64', 'axes': [FREQUENCY]}),
                              context, 'trace')
        if operation == 'sweep':
            return trace
        reference = arrays.result(_payload([1.0] * 1001, {}), arrays.check_result({'dtype': 'float64'}), context,
                                  'reference')
        return {'items': {'trace': trace, 'reference': reference}}


class ViewCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def view(self, values, description, use_native=True, **options):
        holder, path = saved(self.folder.name, values, description)
        return views.view(holder, path, use_native=use_native, **options)

    def test_a_trace_reads_as_a_summary_an_envelope_and_a_window(self):
        trace = {'dtype': 'float64', 'axes': [FREQUENCY]}
        seen = self.view(DIP, trace, points=10)
        self.assertEqual(seen['along'], {'axis': 'frequency', 'start': 0, 'stop': 1001, 'low': 1.0e9, 'high': 1.1e9,
                                         'step': 1.0e5, 'unit': 'Hz'})
        self.assertEqual((seen['summary']['count'], seen['summary']['min_index']), (1001, [600]))
        self.assertAlmostEqual(seen['summary']['min'], 0.2)
        self.assertAlmostEqual(seen['summary']['min_at'], 1.06e9)
        envelope = seen['envelope']
        self.assertEqual(len(envelope['position']), 10)
        self.assertEqual(envelope['position'][:3], [0, 101, 201])
        self.assertEqual(envelope['coordinate'][1], 1.0101e9)
        # The bucket that holds the dip shows it; the mean of a bucket is not lower than its minimum.
        deepest = min(range(10), key=lambda b: envelope['min'][b])
        self.assertEqual((deepest, envelope['min'][deepest]), (5, 0.2))
        self.assertTrue(all(lo <= m <= hi for lo, m, hi in zip(envelope['min'], envelope['mean'], envelope['max'])))
        # Zooming in by coordinates or positions returns the exact values.
        window = self.view(DIP, trace, low=1.0595e9, high=1.0605e9)
        self.assertEqual((window['along']['start'], window['along']['stop']), (595, 606))
        self.assertEqual(window['values'], DIP[595:606])
        self.assertEqual(self.view(DIP, trace, start=595, stop=606)['values'], DIP[595:606])
        for options, message in (({'low': 2e9}, 'No position'), ({'start': 5, 'stop': 5}, 'start before stop'),
                                 ({'points': 0}, 'points'), ({'low': 1e9, 'start': 3}, 'not both'),
                                 ({'along': 'time'}, 'the axes are frequency')):
            with self.assertRaisesRegex(ValueError, message):
                self.view(DIP, trace, **options)
        with self.assertRaisesRegex(ValueError, 'no coordinates'):
            self.view(DIP, {'dtype': 'float64'}, low=1)

    def test_complex_values_read_as_modulus_argument_or_one_component(self):
        z = [complex(math.cos(k / 50), math.sin(k / 50)) * (1 + k / 100) for k in range(200)]
        pairs = [x for c in z for x in (c.real, c.imag)]
        trace = {'dtype': 'float32', 'shape': [-1, 2], 'unit': 'V',
                 'axes': [{'name': 'point'}, {'name': 'component', 'labels': ['re', 'im']}]}
        modulus = self.view(pairs, trace, start=0, stop=5)
        self.assertEqual((modulus['at'], modulus['unit']), ({'component': 'abs'}, 'V'))
        self.assertNotIn('summarized', modulus)
        for got, c in zip(modulus['values'], z):
            self.assertAlmostEqual(got, abs(c), places=5)
        argument = self.view(pairs, trace, at={'component': 'arg'}, start=0, stop=5)
        self.assertEqual(argument['unit'], 'rad')
        for got, c in zip(argument['values'], z):
            self.assertAlmostEqual(got, math.atan2(c.imag, c.real), places=5)
        imaginary = self.view(pairs, trace, at={'component': 'im'}, start=0, stop=5)
        self.assertEqual(imaginary['at'], {'component': 'im'})
        for got, c in zip(imaginary['values'], z):
            self.assertAlmostEqual(got, c.imag, places=5)
        power = self.view(pairs, trace, at={'component': 'power'}, start=0, stop=5)
        self.assertEqual(power['unit'], 'V²')
        for got, c in zip(power['values'], z):
            self.assertAlmostEqual(got, abs(c) ** 2, places=5)
        with self.assertRaisesRegex(ValueError, 'labels are re, im; abs, arg and power'):
            self.view(pairs, trace, at={'component': 'phase'})
        # A notch resonance at 1.06 GHz with loaded Q 200: its dip in power is as wide as f0 / Q.
        s21 = [1 - 0.8 / (1 + 2j * 200 * (1e5 * i - 6e7) / 1.06e9) for i in range(1001)]
        notch = {'dtype': 'float64', 'shape': [-1, 2], 'axes': [FREQUENCY, {'name': 'component', 'labels': ['re', 'im']}]}
        line = self.view([x for c in s21 for x in (c.real, c.imag)], notch, at={'component': 'power'}, peak='min')['peak']
        self.assertEqual(line['center'], 1.06e9)
        self.assertAlmostEqual(line['center'] / line['width'], 200, delta=2)

    def test_other_axes_are_fixed_or_summarized(self):
        # A 4 x 5 image of big-endian integers: value = 10 * row + column.
        image = {'dtype': 'int16', 'byte_order': 'big', 'shape': [4, 5],
                 'axes': [{'name': 'row'}, {'name': 'column'}]}
        values = [10 * r + c for r in range(4) for c in range(5)]
        rows = self.view(values, image)
        self.assertEqual(rows['summarized'], ['column'])
        self.assertEqual(rows['envelope']['min'], [0, 10, 20, 30])
        self.assertEqual(rows['envelope']['mean'], [2, 12, 22, 32])
        self.assertEqual(rows['summary']['max_index'], [3, 4])
        self.assertEqual(self.view(values, image, along='column', at={'row': 2})['values'], [20, 21, 22, 23, 24])
        columns = self.view(values, image, along=1)
        self.assertEqual((columns['envelope']['min'], columns['envelope']['max']), ([0, 1, 2, 3, 4], [30, 31, 32, 33, 34]))
        with self.assertRaisesRegex(ValueError, 'other than those the view follows'):
            self.view(values, image, along='column', at={'column': 1})

    def test_two_axes_give_a_map(self):
        # A 4 x 5 image of big-endian integers on a 10 nm grid: value = 10 * row + column.
        image = {'dtype': 'int16', 'byte_order': 'big', 'shape': [4, 5], 'unit': 'counts',
                 'axes': [{'name': 'row', 'unit': 'nm', 'start': 0, 'step': 10}, {'name': 'column'}]}
        values = [10 * r + c for r in range(4) for c in range(5)]
        whole = self.view(values, image, along=['row', 'column'])
        self.assertEqual(whole['values'], [[10 * r + c for c in range(5)] for r in range(4)])
        self.assertEqual([item['axis'] for item in whole['along']], ['row', 'column'])
        self.assertEqual((whole['along'][0]['high'], whole['summary']['max_index'], whole['summary']['max_at']),
                         (30.0, [3, 4], [30.0, None]))
        coarse = self.view(values, image, along=['row', 'column'], points=[2, 2])
        self.assertEqual(coarse['envelope']['position'], [[0, 2], [0, 3]])
        self.assertEqual(coarse['envelope']['coordinate'], [[0.0, 20.0], None])
        self.assertEqual(coarse['envelope']['mean'], [[6, 8.5], [26, 28.5]])
        self.assertEqual(coarse['envelope']['min'], [[0, 3], [20, 23]])
        # Windows by coordinates on one axis and by positions on the other; columns first gives the transpose.
        window = self.view(values, image, along=['column', 'row'], start=[3, None], low=[None, 15], high=[None, 30])
        self.assertEqual(window['values'], [[23, 33], [24, 34]])
        for options, message in (({'along': ['row', 'row']}, 'different axes'), ({'along': ['row', 'column'], 'start': 1},
                                 'one value for each axis'), ({'along': ['row', 'column'], 'points': [2]}, 'one for each axis'),
                                 ({'along': ['row', 'column', 'row']}, 'a list of two')):
            with self.assertRaisesRegex(ValueError, message):
                self.view(values, image, **options)
        with self.assertRaisesRegex(ValueError, 'at most 250000 buckets'):
            self.view([0.0] * 1_000_000, {'dtype': 'float32', 'shape': [1000, 1000]}, along=[0, 1], points=600)

    def test_whole_arrays_are_written_as_numpy_files(self):
        image = {'dtype': 'int16', 'byte_order': 'big', 'shape': [4, 5], 'axes': [{'name': 'row'}, {'name': 'column'}]}
        values = [10 * r + c for r in range(4) for c in range(5)]
        holder, path = saved(self.folder.name, values, image)
        length, pieces = views.npy(holder, [[str(path), path.stat().st_size]])
        payload = b''.join(pieces)
        header, data = load(payload)
        self.assertEqual((len(payload), header, data), (length, {'descr': '>i2', 'fortran_order': False, 'shape': (4, 5)},
                                                        path.read_bytes()))
        # re/im pairs become complex values; values held in the record, missing ones not a number.
        pairs = {'dtype': 'float32', 'shape': [-1, 2], 'axes': [{'name': 'k'}, {'name': 'component', 'labels': ['re', 'im']}]}
        holder, path = saved(self.folder.name, [1, 2, 3, 4, 5, 6], pairs)
        self.assertEqual(load(b''.join(views.npy(holder, [[str(path), 24]])[1]))[0]['shape'], (3,))
        self.assertEqual(load(b''.join(views.npy(holder, [[str(path), 24]])[1]))[0]['descr'], '<c8')
        gathered = {'data': {'dtype': 'float64', 'shape': [2, 2]}, 'values': [[1.5, None], [3, 4]]}
        header, data = load(b''.join(views.npy(gathered)[1]))
        self.assertEqual(header['shape'], (2, 2))
        self.assertTrue(math.isnan(struct.unpack('<4d', data)[1]))
        with self.assertRaisesRegex(ValueError, 'fewer bytes'):
            views.npy(holder, [[str(path), 8]])

    def test_values_that_are_not_finite_are_counted_and_left_out(self):
        seen = self.view([1.0, math.nan, 3.0, math.inf, -2.0], {'dtype': 'float64'})
        self.assertEqual({k: seen['summary'][k] for k in ('count', 'nonfinite', 'min', 'max')},
                         {'count': 5, 'nonfinite': 2, 'min': -2.0, 'max': 3.0})
        self.assertAlmostEqual(seen['summary']['mean'], 2 / 3)
        self.assertEqual(seen['values'], [1.0, None, 3.0, None, -2.0])
        self.assertEqual(json.loads(json.dumps(seen, allow_nan=False)), seen)

    def test_points_give_their_axis_coordinates(self):
        self.assertEqual(arrays.coordinates([2.0, 2.5, 3.0, 3.5]), {'start': 2.0, 'step': 0.5})
        self.assertEqual(arrays.coordinates([1.0e9 + 1e5 * i for i in range(1001)]), {'start': 1.0e9, 'step': 1.0e5})
        for values in ([1.0, 2.0, 4.0], [3.0], [1.0, 1.0]):
            self.assertEqual(arrays.coordinates(values), {'coordinates': values})
        for values in ([0.0, math.nan], [], [[1.0, 2.0]], [0.0] * (arrays.COORDINATES_LIMIT + 1)):
            self.assertEqual(arrays.coordinates(values), {})

    def test_listed_coordinates_place_positions_that_are_not_evenly_spaced(self):
        # A logarithmic sweep from 10 Hz to 100 kHz with the response falling above 1 kHz.
        hz = [10 * 10 ** (k / 10) for k in range(41)]
        response = [1 / math.sqrt(1 + (f / 1e3) ** 2) for f in hz]
        trace = {'dtype': 'float64', 'unit': 'V', 'axes': [{'name': 'frequency', 'unit': 'Hz', 'coordinates': hz}]}
        seen = self.view(response, trace, points=8)
        self.assertEqual(seen['along'], {'axis': 'frequency', 'start': 0, 'stop': 41, 'low': 10.0, 'high': hz[-1],
                                         'unit': 'Hz'})
        self.assertEqual((seen['summary']['max_at'], seen['summary']['min_at']), (10.0, hz[-1]))
        self.assertEqual(seen['envelope']['coordinate'][:2], [10.0, float(f'{hz[6]:.7g}')])
        window = self.view(response, trace, low=900, high=1200)
        self.assertEqual((window['along']['start'], window['along']['coordinates']), (20, hz[20:21]))
        self.assertAlmostEqual(window['values'][0], math.sqrt(0.5))
        # Coordinates that fall are selected the same way; those that go back and forth only by position.
        falling = {**trace, 'axes': [{**trace['axes'][0], 'coordinates': hz[::-1]}]}
        self.assertEqual(self.view(response[::-1], falling, low=900, high=1200)['along']['start'], 20)
        tangled = {**trace, 'axes': [{**trace['axes'][0], 'coordinates': [1.0, 3.0, 2.0] + hz[3:]}]}
        with self.assertRaisesRegex(ValueError, 'increase or decrease'):
            self.view(response, tangled, low=1, high=2)
        self.assertEqual(self.view(response, tangled, start=1, stop=3)['along']['coordinates'], [3.0, 2.0])
        for axis, message in (({'coordinates': hz, 'start': 10}, 'not both'), ({'coordinates': []}, 'list of numbers'),
                              ({'coordinates': hz[:5]}, 'each of its 41 positions')):
            with self.assertRaisesRegex(ValueError, message):
                arrays.check_result({'dtype': 'float64', 'shape': [41], 'axes': [{'name': 'frequency', **axis}]})
        with self.assertRaisesRegex(ValueError, 'lists 5 coordinates for its 41 positions'):
            self.view(response, {'dtype': 'float64', 'axes': [{'name': 'frequency', 'coordinates': hz[:5]}]})

    def test_a_line_reads_as_its_center_width_and_depth(self):
        trace = {'dtype': 'float64', 'axes': [FREQUENCY]}
        line = self.view(DIP, trace, peak='min')['peak']
        # Sampled at its center, the dip lies at 1.06 GHz; its base is the highest value, at the far end.
        self.assertEqual((line['center'], line['value'], line['base'], line['index']), (1.06e9, DIP[600], DIP[0], 600))
        self.assertAlmostEqual(line['width'], 4e6, delta=1e4)
        self.assertAlmostEqual(line['high'] - 1.06e9, 1.06e9 - line['low'])
        # Between samples, the parabola through the highest three places the line.
        lorentz = [1 / (1 + ((i - 41.3) / 3.2) ** 2) for i in range(100)]
        delay = {'dtype': 'float64', 'axes': [{'name': 'delay', 'unit': 's', 'start': 0, 'step': 0.5}]}
        line = self.view(lorentz, delay, peak='max')['peak']
        self.assertAlmostEqual(line['center'], 20.65, delta=0.02)
        self.assertAlmostEqual(line['width'], 3.2, delta=0.05)
        # Fit the parabola in physical coordinates, including uneven spacing.
        listed = {'dtype': 'float64', 'axes': [{'name': 'f', 'coordinates': [1, 2, 4, 8, 16]}]}
        line = self.view([0, 2, 3, 1, 0], listed, peak='max')['peak']
        self.assertAlmostEqual(line['center'], 4.5)
        self.assertEqual(set(line), {'center', 'width', 'low', 'high', 'value', 'base', 'index'})
        image = {'dtype': 'float64', 'shape': [2, 3], 'axes': [{'name': 'row'}, {'name': 'column'}]}
        self.assertEqual(self.view([0, 5, 0, 1, 2, 3], image, along='column', at={'row': 0}, peak='max')['peak']['index'], 1)
        # An axis of one position, such as the only channel of a stream, is read at it.
        self.assertEqual(self.view([0, 5, 0], {'dtype': 'float64', 'shape': [3, 1]}, peak='max')['peak']['index'], 1)
        cube = {'dtype': 'float64', 'shape': [2, 3, 2], 'axes': [{'name': 'row'}, {'name': 'column'}, {'name': 'depth'}]}
        for description, options, message in ((cube, {'peak': 'max'}, 'at most one other; fix the others with at: column, depth'),
                                              (trace, {'peak': 'top'}, 'max, or min for a dip')):
            with self.assertRaisesRegex(ValueError, message):
                self.view(DIP, description, **options) if description is trace else self.view([0] * 12, cube, **options)

    def test_peaks_fit_actual_coordinates_in_both_directions(self):
        for coordinates, values, peak in (([0, 1, 3], [0, 2, 0], 'max'),
                                           ([3, 1, 0], [0, -2, 0], 'min')):
            trace = {'dtype': 'float64', 'axes': [{'name': 'x', 'coordinates': coordinates}]}
            line = self.view(values, trace, peak=peak)['peak']
            self.assertEqual(line['center'], 1.5)
            self.assertEqual(line['value'], 2.25 if peak == 'max' else -2.25)
            self.assertAlmostEqual(line['width'], 1.3125)
        with self.assertRaisesRegex(ValueError, 'strictly increase or decrease'):
            self.view([0, 2, 0], {'dtype': 'float64', 'axes': [{'name': 'x', 'coordinates': [0, 1, 0]}]}, peak='max')

    def test_the_lines_of_a_spectrum_stand_out_by_their_prominence(self):
        trace = {'dtype': 'float64', 'axes': [FREQUENCY]}
        modes = self.view(MODES, trace, peak='min', prominence=0.1)['peaks']
        self.assertEqual(len(modes), 3)
        for line, center, width in zip(modes, (1.0213e9, 1.0587e9, 1.0841e9), (2.1e6, 1.6e6, 3e6)):
            self.assertAlmostEqual(line['center'], center, delta=3e4)
            self.assertAlmostEqual(line['width'], width, delta=0.03 * width)
        self.assertEqual([round(line['prominence'], 1) for line in modes], [0.8, 0.5, 0.3])
        # A larger prominence leaves the two deeper modes; a small one adds the ripple's own dips.
        self.assertEqual(len(self.view(MODES, trace, peak='min', prominence=0.4)['peaks']), 2)
        self.assertGreater(len(self.view(MODES, trace, peak='min', prominence=0.001)['peaks']), 3)
        for options, message in (({'prominence': 0.1}, 'read with a peak or a dip'),
                                 ({'peak': 'min', 'prominence': 0}, 'positive number')):
            with self.assertRaisesRegex(ValueError, message):
                self.view(MODES, trace, **options)

    def test_where_a_trace_crosses_a_level(self):
        # The current of a diode reaches 1 mA between 0.53 and 0.54 V; on the straight line between them, at 0.5352.
        volts = {'name': 'volts', 'unit': 'V', 'start': 0, 'step': 0.01}
        current = [1e-12 * math.expm1(0.01 * i / 0.02585) for i in range(60)]
        found = self.view(current, {'dtype': 'float64', 'axes': [volts]}, level=1e-3)['crossings']
        self.assertEqual((len(found), found[0]['rising'], found[0]['index']), (1, True, 53))
        self.assertAlmostEqual(found[0]['at'], 0.5352, delta=1e-4)
        # A dip crosses a level on its way down and back up, placed at the coordinates of its axis.
        self.assertEqual(self.view(DIP, {'dtype': 'float64', 'axes': [FREQUENCY]}, level=0.6)['crossings'],
                         [{'at': 1.058e9, 'rising': False, 'index': 580}, {'at': 1.062e9, 'rising': True, 'index': 619}])
        # A magnetization loop swept up and back over listed fields crosses zero at the coercive field each way.
        fields = [round(-1 + 0.1 * k, 10) for k in range(21)] + [round(1 - 0.1 * k, 10) for k in range(1, 21)]
        moments = [math.tanh((h - 0.3 if k <= 20 else h + 0.3) / 0.1) for k, h in enumerate(fields)]
        loop = self.view(moments, {'dtype': 'float64', 'axes': [{'name': 'field', 'coordinates': fields}]}, level=0)
        self.assertEqual([(round(c['at'], 6), c['rising']) for c in loop['crossings']], [(0.3, True), (-0.3, False)])
        for level in (math.inf, 'high'):
            with self.assertRaisesRegex(ValueError, 'level is a finite number'):
                self.view(DIP, {'dtype': 'float64'}, level=level)
        with self.assertRaisesRegex(ValueError, 'A level is read along one axis, at each position of at most one other'):
            self.view([0] * 12, {'dtype': 'float64', 'shape': [2, 3, 2]}, level=0)

    def test_a_straight_line_through_the_straight_part_of_a_trace(self):
        # A laser gives 0.4 W/A above its threshold at 25 mA and a little spontaneous light throughout. The line
        # through the light above 30 mA reaches zero at 24.94 mA: the threshold by its usual definition.
        current = {'name': 'current', 'unit': 'A', 'start': 0, 'step': 1e-3}
        light = [1e-3 * i * 1e-3 + 0.4 * max(0.0, i * 1e-3 - 0.025) for i in range(61)]
        fit = self.view(light, {'dtype': 'float64', 'axes': [current], 'unit': 'W'}, low=0.03, fit='line')['fit']
        for key, expected in (('slope', 0.401), ('intercept', -0.01), ('zero', 0.01 / 0.401), ('residual', 0)):
            self.assertAlmostEqual(fit[key], expected, delta=1e-12)
        self.assertLess(max(fit['errors'].values()), 1e-12)
        self.assertEqual(fit['count'], 31)
        tiny = self.view([1e-170 * x for x in range(10)], {'dtype': 'float64'}, fit='line')['fit']
        self.assertAlmostEqual(tiny['slope'] / 1e-170, 1.0)
        self.assertEqual(tiny['errors']['zero'], 0.0)
        with self.assertRaisesRegex(ValueError, 'fit is line'):
            self.view(light, {'dtype': 'float64'}, fit='curve')
        with self.assertRaisesRegex(ValueError, 'A fit is read along one axis, at each position of at most one other'):
            self.view([0] * 12, {'dtype': 'float64', 'shape': [2, 3, 2]}, fit='line')

    def test_lines_of_lorentzian_and_gaussian_shape_through_noise(self):
        # A resonance at 1.06 GHz, 0.5 MHz wide (Q 2120), under noise of up to 0.02 from a fixed sequence: the
        # Lorentzian through its 201 points gives Q within 0.5%, where the width at half height is 2% off.
        seed = [12345]

        def noise():
            seed[0] = (seed[0] * 6364136223846793005 + 1442695040888963407) % 2 ** 64
            return ((seed[0] >> 11) / 2 ** 53 - 0.5) * 0.04
        frequency = {'name': 'frequency', 'unit': 'Hz', 'start': 1.0575e9, 'step': 25e3}
        dip = [1 - 0.8 / (1 + ((1.0575e9 + 25e3 * i - 1.06e9) / 0.25e6) ** 2) + noise() for i in range(201)]
        seen = self.view(dip, {'dtype': 'float64', 'axes': [frequency]}, peak='min', fit='lorentzian')
        fit, line = seen['fit'], seen['peak']
        self.assertAlmostEqual(fit['center'] / fit['width'], 2120, delta=0.005 * 2120)
        self.assertGreater(abs(line['center'] / line['width'] - 2120), 0.01 * 2120)
        self.assertEqual((round(fit['height'], 1), round(fit['base'], 2), fit['count']), (-0.8, 1.0, 201))
        # The true center and width lie within three standard errors, which come with the fit.
        self.assertLess(abs(fit['center'] - 1.06e9), 3 * fit['errors']['center'])
        self.assertLess(abs(fit['width'] - 0.5e6), 3 * fit['errors']['width'])
        self.assertLess(fit['errors']['width'], 0.01 * 0.5e6)
        # One high value on the flat noise beside the dip is no line its samples resolve: its fit gives no errors.
        spike = [1 + noise() for _ in range(120)]
        spike[60] = 2
        for shape in ('lorentzian', 'gaussian'):
            fit = self.view(spike, {'dtype': 'float64'}, fit=shape)['fit']
            self.assertEqual((fit['width'] < 1, fit['errors']), (True, None))
        # A Gaussian beam profile 10 um wide at half height, sampled every 0.5 um.
        x = {'name': 'x', 'unit': 'um', 'start': 0, 'step': 0.5}
        profile = [0.1 + 2 * math.exp(-math.log(2) * ((0.5 * i - 64.5) / 5) ** 2) for i in range(200)]
        beam = self.view(profile, {'dtype': 'float64', 'axes': [x]}, fit='gaussian')['fit']
        self.assertEqual((round(beam['center'], 9), round(beam['width'], 9), round(beam['height'], 9)), (64.5, 10, 2))
        with self.assertRaisesRegex(ValueError, 'fit is line, lorentzian or gaussian'):
            self.view(profile, {'dtype': 'float64'}, fit='voigt')
        with self.assertRaisesRegex(ValueError, 'fitted to at most 1000000 values'):
            views.plan({'dtype': 'float64', 'shape': [1000, 2000]}, along=1, fit='gaussian')

    def test_lines_crossings_and_fits_at_each_position_of_another_axis(self):
        # A line that moves 10 nm per volt, read along wavelength at each bias: its center tunes with the bias.
        volts = {'name': 'volts', 'unit': 'V', 'start': 0, 'step': 1}
        wavelength = {'name': 'wavelength', 'unit': 'nm', 'start': 400, 'step': 0.1}
        spectra = [100 / (1 + ((400 + 0.1 * i - 500 - 10 * v) / 2) ** 2) for v in range(4) for i in range(2000)]
        rows = self.view(spectra, {'dtype': 'float64', 'shape': [4, 2000], 'axes': [volts, wavelength]},
                         along='wavelength', peak='max', level=50, fit='line')['rows']
        self.assertEqual((rows['axis'], rows['unit'], rows['coordinates']), ('volts', 'V', [0, 1, 2, 3]))
        self.assertEqual([round(line['center'], 6) for line in rows['peak']], [500, 510, 520, 530])
        self.assertEqual([[round(c['at'], 3) for c in row] for row in rows['crossings']],
                         [[498, 502], [508, 512], [518, 522], [528, 532]])
        self.assertEqual([fit['count'] for fit in rows['fit']], [2000] * 4)
        # A labelled axis gives its labels; prominence reads one trace.
        labelled = self.view([0, 1, 0, 2, 3, 2], {'dtype': 'float64', 'shape': [2, 3],
                                                 'axes': [{'name': 'channel', 'labels': ['x', 'y']}, {'name': 'k'}]},
                             along='k', peak='max')['rows']
        self.assertEqual((labelled['labels'], [line['index'] for line in labelled['peak']]), (['x', 'y'], [1, 1]))
        with self.assertRaisesRegex(ValueError, 'prominence reads the lines of one trace'):
            self.view(spectra, {'dtype': 'float64', 'shape': [4, 2000]}, along=1, peak='max', prominence=1)

    def test_inline_values_are_viewed_without_a_file(self):
        holder = arrays.result(_payload(DIP[:100], {}), arrays.check_result({'dtype': 'float64'}), None)
        self.assertEqual(views.view(holder, points=4)['envelope']['position'], [0, 25, 50, 75])

    def test_iop_execd_and_python_give_the_same_view(self):
        folder = self.folder.name
        nan_trace = [math.sin(i / 37.0) * 1e9 + 3e9 if i % 97 else math.nan for i in range(20011)]
        cube = [((i * 7919) % 1000) / 7.0 for i in range(6 * 7 * 50)]
        z = [x for k in range(3001) for x in (math.cos(k / 9), math.sin(k / 9) * (k % 5))]
        cases = [
            (nan_trace, {'dtype': 'float64'}, {'points': 37}),
            (nan_trace, {'dtype': 'float32', 'byte_order': 'big'}, {'start': 101, 'stop': 4093, 'points': 1000}),
            (cube, {'dtype': 'float64', 'shape': [6, 7, 50], 'axes': [{'name': 'a'}, {'name': 'b'}, {'name': 'c'}]},
             {'along': 'c', 'points': 9}),
            (cube, {'dtype': 'float64', 'shape': [6, 7, 50], 'axes': [{'name': 'a'}, {'name': 'b'}, {'name': 'c'}]},
             {'along': 'b', 'at': {'a': 4}, 'points': 3}),
            (z, {'dtype': 'float64', 'shape': [-1, 2], 'axes': [{'name': 'k'}, {'name': 'component', 'labels': ['re', 'im']}]},
             {'at': {'component': 'arg'}, 'points': 50}),
            (cube, {'dtype': 'float64', 'shape': [6, 7, 50], 'axes': [{'name': 'a'}, {'name': 'b'}, {'name': 'c'}]},
             {'along': ['c', 'a'], 'start': [3, None], 'stop': [47, 5], 'points': [9, 4]}),
            (nan_trace[:20000], {'dtype': 'float32', 'shape': [100, 200]}, {'along': [0, 1], 'points': [7, 30]}),
            (nan_trace, {'dtype': 'float64'}, {'points': 37, 'peak': 'max'}),
            (nan_trace, {'dtype': 'float32', 'byte_order': 'big'}, {'start': 101, 'stop': 4093, 'peak': 'min'}),
            (DIP, {'dtype': 'float64', 'axes': [FREQUENCY]}, {'low': 1.05e9, 'high': 1.07e9, 'peak': 'min'}),
            (MODES, {'dtype': 'float64', 'axes': [FREQUENCY]}, {'peak': 'min', 'prominence': 0.1}),
            (nan_trace, {'dtype': 'float32', 'byte_order': 'big'}, {'start': 101, 'stop': 9000, 'peak': 'max', 'prominence': 1e8}),
            ([0, 2, 0], {'dtype': 'float64', 'axes': [{'name': 'x', 'coordinates': [0, 1, 3]}]}, {'peak': 'max'}),
            ([0, -2, 0], {'dtype': 'float64', 'axes': [{'name': 'x', 'coordinates': [3, 1, 0]}]}, {'peak': 'min'}),
            (z, {'dtype': 'float64', 'shape': [-1, 2], 'axes': [{'name': 'k'}, {'name': 'component', 'labels': ['re', 'im']}]},
             {'at': {'component': 'power'}, 'points': 40, 'peak': 'max'}),
            (cube, {'dtype': 'float64', 'shape': [6, 7, 50], 'axes': [{'name': 'a'}, {'name': 'b'}, {'name': 'c'}]},
             {'along': 'c', 'at': {'a': 4, 'b': 2}, 'peak': 'max'}),
            (nan_trace, {'dtype': 'float64'}, {'points': 37, 'level': 3e9}),
            (nan_trace, {'dtype': 'float32', 'byte_order': 'big'}, {'start': 101, 'stop': 9000, 'level': 3.5e9, 'peak': 'max'}),
            ([-1, 1, -1, 1], {'dtype': 'float64', 'axes': [{'name': 'x', 'coordinates': [0, 2, 1, 3]}]}, {'level': 0}),
            (nan_trace, {'dtype': 'float64'}, {'points': 37, 'fit': 'line'}),
            (DIP, {'dtype': 'float64', 'axes': [FREQUENCY]}, {'low': 1.05e9, 'high': 1.07e9, 'fit': 'line', 'level': 0.5}),
            ([1, 3, 2, 5], {'dtype': 'float64', 'axes': [{'name': 'x', 'coordinates': [0, 2, 1, 3]}]}, {'fit': 'line'}),
            ([1e-170 * x for x in range(10)], {'dtype': 'float64'}, {'fit': 'line'}),
            (cube, {'dtype': 'float64', 'shape': [6, 7, 50], 'axes': [{'name': 'a'}, {'name': 'b'}, {'name': 'c'}]},
             {'along': 'c', 'at': {'a': 4}, 'peak': 'max', 'level': 70, 'fit': 'line'}),
            (nan_trace[:20000], {'dtype': 'float32', 'byte_order': 'big', 'shape': [100, 200]},
             {'along': 0, 'start': 3, 'stop': 97, 'peak': 'min', 'level': 3.2e9}),
            (DIP, {'dtype': 'float64', 'axes': [FREQUENCY]}, {'low': 1.05e9, 'high': 1.07e9, 'fit': 'lorentzian'}),
            (nan_trace, {'dtype': 'float32', 'byte_order': 'big'}, {'start': 101, 'stop': 400, 'fit': 'gaussian'}),
            ([0, 1, 3, 1, 0, 0.5], {'dtype': 'float64', 'axes': [{'name': 'x', 'coordinates': [0, 1, 2, 3, 5, 8]}]},
             {'fit': 'lorentzian'}),
            (cube, {'dtype': 'float64', 'shape': [6, 7, 50], 'axes': [{'name': 'a'}, {'name': 'b'}, {'name': 'c'}]},
             {'along': 'c', 'at': {'a': 4}, 'fit': 'gaussian'}),
            ([1, 1.01, 0.99, 1, 2, 1.01, 1, 0.99, 1], {'dtype': 'float64'}, {'fit': 'lorentzian'}),
        ]
        compared = 0
        for values, description, options in cases:
            holder, path = saved(folder, values, description)
            request, _ = views.plan(holder['data'], **options)
            request['segments'] = [[str(path), path.stat().st_size]]
            native = views.native(request)
            if native is None:
                continue
            python = views.python(request)
            compared += 1
            with self.subTest(description=description, options=options):
                for key in ('count', 'finite', 'min', 'max', 'min_index', 'max_index'):
                    self.assertEqual(native[key], python[key], key)
                for key in ('mean', 'std'):
                    self.assertAlmostEqual(native[key], python[key], delta=1e-9 * max(1.0, abs(python[key])))
                for key in ('start', 'count', 'finite', 'min', 'max'):
                    self.assertEqual(native['buckets'][key], python['buckets'][key], key)
                for a, b in zip(native['buckets']['mean'], python['buckets']['mean']):
                    self.assertTrue(a is b is None or abs(a - b) <= 1e-9 * max(1.0, abs(b)))
                self.assertEqual(native.get('peak'), python.get('peak'))
                self.assertEqual(native.get('peaks'), python.get('peaks'))
                self.assertEqual(native.get('crossings'), python.get('crossings'))
                self.assertEqual(native.get('fit'), python.get('fit'))
                self.assertEqual(native.get('rows'), python.get('rows'))
        # The chunks of a stream: the same values split across files at arbitrary byte boundaries.
        holder, path = saved(folder, nan_trace, {'dtype': 'float64'})
        payload, parts = path.read_bytes(), []
        for number, (begin, end) in enumerate(((0, 1001), (1001, 80013), (80013, len(path.read_bytes())))):
            part = Path(folder) / f'chunk-{number}.bin'
            part.write_bytes(payload[begin:end])
            parts.append([str(part), end - begin])
        request, _ = views.plan(holder['data'], start=9000, stop=15000, points=13)
        request['segments'] = parts
        native = views.native(request)
        if native is not None:
            compared += 1
            whole = dict(request, segments=[[str(path), path.stat().st_size]])
            self.assertEqual(native, views.native(whole))
            self.assertEqual((native['count'], native['min_index']), (views.python(request)['count'],
                                                                      views.python(request)['min_index']))
        if compared == 0:
            self.skipTest('iop-execd with array views is not installed')

    def test_without_iop_execd_python_computes_views_up_to_a_limit(self):
        holder, path = saved(self.folder.name, DIP, {'dtype': 'float64'})
        # An executor that predates views is not used; Python computes the same view.
        with patch.dict(os.environ, {'IOP_EXECD': sys.executable}):
            self.assertIsNone(views.native({'segments': [[str(path), path.stat().st_size]]}))
            self.assertEqual(views.view(holder, path, points=5)['summary']['min_index'], [600])
        with patch.object(views, 'PYTHON_LIMIT', 1000), self.assertRaisesRegex(ValueError, 'iop-execd'):
            views.view(holder, path, use_native=False)


class ServiceViewCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def test_an_operation_array_is_read_through_the_service(self):
        self.client.register({'id': 'vna', 'driver': Sweeps.__module__ + ':Sweeps'})
        with self.client.start_experiment(resources=['vna']) as run:
            sweep = run.invoke_record('vna', 'sweep')
            pair = run.invoke_record('vna', 'pair')
        seen = self.client.read_array(sweep['id'], points=8)
        self.assertEqual((seen['operation_id'], seen['field'], seen['summary']['min_index']), (sweep['id'], '', [600]))
        self.assertAlmostEqual(seen['summary']['min_at'], 1.06e9)
        self.assertEqual(len(seen['envelope']['min']), 8)
        zoomed = self.client.read_array(sweep['id'], low=1.0598e9, high=1.0602e9)
        self.assertEqual(zoomed['values'], DIP[598:603])
        self.assertEqual(self.client.read_array(sweep['id'], peak='min', points=2)['peak']['center'], 1.06e9)
        self.assertEqual([line['index'] for line in self.client.read_array(sweep['id'], peak='min', prominence=0.5)['peaks']],
                         [600])
        self.assertEqual([(c['at'], c['rising']) for c in self.client.read_array(sweep['id'], level=0.6)['crossings']],
                         [(1.058e9, False), (1.062e9, True)])
        fit = self.client.read_array(sweep['id'], fit='lorentzian')['fit']
        self.assertAlmostEqual(fit['center'], 1.06e9, delta=1)
        self.assertAlmostEqual(fit['width'], 4e6, delta=1)
        printed = subprocess.check_output([sys.executable, '-m', 'ai4instruments.iop', '--endpoint', self.client.endpoint,
                                           'array', sweep['id'], '--low', '1.0598e9', '--high', '1.0602e9', '--peak', 'min',
                                           '--level', '0.201', '--fit', 'line'],
                                          env=dict(os.environ, IOP_TOKEN=self.config['token']), text=True)
        printed = json.loads(printed)
        self.assertEqual((printed['values'], printed['peak']['center'], [c['rising'] for c in printed['crossings']]),
                         (DIP[598:603], 1.06e9, [False, True]))
        # The window is symmetric about the dip: the line through it is flat.
        self.assertEqual((printed['fit']['slope'], printed['fit']['zero'], printed['fit']['count']), (0.0, None, 5))
        with self.assertRaisesRegex(IOPError, "several arrays.*'items.trace', 'items.reference'"):
            self.client.read_array(pair['id'])
        self.assertEqual(self.client.read_array(pair['id'], 'items.reference', points=2)['envelope']['max'], [1.0, 1.0])
        with self.assertRaisesRegex(IOPError, 'Unknown view options: colour'):
            self.client.request(f"/v1/operations/{sweep['id']}/array?colour=red")
        with self.assertRaisesRegex(IOPError, 'at is a JSON object'):
            self.client.request(f"/v1/operations/{sweep['id']}/array?at=row")

    @unittest.skipUnless(importlib.util.find_spec('mcp'), 'Install the agent extra to exercise MCP')
    def test_agents_read_arrays_through_one_tool(self):
        import asyncio
        from ai4instruments.iop import mcp
        tools = {tool.name: tool for tool in asyncio.run(mcp.create_server(self.client).list_tools())}
        self.assertIn('read_array', mcp.TOOLSETS['experiments'])
        # The schema field is inputSchema in MCP SDK 1.x and input_schema in 2.x.
        schema = getattr(tools['read_array'], 'input_schema', None) or tools['read_array'].inputSchema
        self.assertTrue({'operation_id', 'field', 'along', 'at', 'start', 'stop', 'low', 'high', 'points', 'peak',
                         'prominence', 'level', 'fit'} <= set(schema['properties']))
        self.assertIn('read_array', tools['operation_result'].description)

    def test_scpi_arrays_carry_coordinates_read_from_the_instrument(self):
        points = [complex(1, 0) - 0.9 / (1 + 1j * (k - 30) / 3) for k in range(61)]
        payload = struct.pack('<%dd' % (2 * len(points)), *[x for c in points for x in (c.real, c.imag)])
        server = start({'CALC1:DATA:SDAT?': arrays.block(payload) + b'\n', 'SENS1:FREQ:STAR?': b'+1.000000000E+09\n',
                        'SENS1:FREQ:STOP?': b'+1.006000000E+09\n'})
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        trace = {'query': 'CALC1:DATA:SDAT?', 'effect': 'observe', 'parameters': {},
                 'result': {'type': 'array', 'dtype': 'float64', 'shape': [-1, 2],
                            'axes': [{'name': 'frequency', 'unit': 'Hz', 'start': {'query': 'SENS1:FREQ:STAR?'},
                                      'stop': {'query': 'SENS1:FREQ:STOP?'}},
                                     {'name': 'component', 'labels': ['re', 'im']}]}}
        self.client.register({'id': 'analyzer', 'driver': 'scpi', 'config': {
            'host': '127.0.0.1', 'port': server.server_address[1], 'operations': {'read_trace': trace}}})
        with self.client.start_experiment(resources=['analyzer']) as run:
            op = run.invoke_record('analyzer', 'read_trace')
        self.assertEqual(op['result']['data']['axes'][0], {'name': 'frequency', 'unit': 'Hz', 'start': 1.0e9,
                                                           'stop': 1.006e9})
        self.assertEqual(server.commands, ['CALC1:DATA:SDAT?', 'SENS1:FREQ:STAR?', 'SENS1:FREQ:STOP?'])
        seen = self.client.read_array(op['id'], points=200)
        self.assertEqual((seen['at'], seen['along']['step'], len(seen['values'])), ({'component': 'abs'}, 1.0e5, 61))
        self.assertAlmostEqual(seen['summary']['min_at'], 1.003e9)
        self.assertAlmostEqual(seen['summary']['min'], 0.1)
        for axis, message in (({'name': 'f', 'start': 1, 'step': 2, 'stop': 3}, 'either step or stop'),
                              ({'name': 'f', 'step': 2}, 'start with either'),
                              ({'name': 'f', 'start': {'command': 'X?'}, 'step': 1}, 'number or')):
            bad = {**trace, 'result': {**trace['result'], 'axes': [axis, trace['result']['axes'][1]]}}
            with self.assertRaisesRegex(IOPError, message):
                self.client.register({'id': 'other', 'driver': 'scpi', 'config': {
                    'host': '127.0.0.1', 'port': 1, 'operations': {'read_trace': bad}}})
        with self.assertRaisesRegex(ValueError, 'start is a number$'):
            arrays.check_result({'dtype': 'float64', 'axes': [{'name': 'f', 'start': {'query': 'X?'}, 'step': 1}]})



class Resonator(Driver):
    """A resonance at 1.0 GHz plus 10 MHz per volt of bias, seen by a sweep from 1.0 to 1.1 GHz."""
    def __init__(self, *args):
        super().__init__(*args)
        self.volts = 6.0

    def describe(self):
        return {'operations': {
            'set_bias': {'parameters': {'volts': {'type': 'number', 'minimum': 0, 'maximum': 10, 'unit': 'V'}}},
            'bias': {'parameters': {}, 'effect': 'observe'}, 'sweep': {'parameters': {}, 'effect': 'observe'},
            'probe': {'parameters': {'hz': {'type': 'number', 'unit': 'Hz'}}}}}

    def invoke(self, operation, arguments, context):
        if operation == 'set_bias':
            self.volts = arguments['volts']
        if operation in ('set_bias', 'bias'):
            return {'value': self.volts, 'unit': 'V'}
        if operation == 'probe':
            return {'value': arguments['hz'], 'unit': 'Hz'}
        trace = [1.0 - 0.8 / (1 + ((i - 100 * self.volts) / 20) ** 2) for i in range(1001)]
        return arrays.result(_payload(trace, {}), arrays.check_result({'dtype': 'float64', 'axes': [FREQUENCY]}),
                             context, 'trace')


LOCATE = {'id': 'resonance.locate', 'layer': 'standard', 'description': 'Sweep and locate the resonance dip',
          'steps': [{'id': 'sweep', 'device': 'resonator', 'operation': 'sweep'}],
          'result': {'f0': {'step': 'sweep', 'view': {'peak': 'min'}, 'path': ['peak', 'center']},
                     'width': {'step': 'sweep', 'view': {'peak': 'min'}, 'path': ['peak', 'width']},
                     'depth': {'step': 'sweep', 'view': {}, 'path': ['summary', 'min']}}}


class StreamViewCase(unittest.TestCase):
    """Numeric data streams read as arrays of samples by channels."""
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def test_stream_formats_describe_their_array(self):
        from ai4instruments.iop.streams import array
        two = {'format': 'int16-be', 'channels': [{'name': 'x', 'unit': 'V'}, {'name': 'y', 'unit': 'mV'}],
               'sample_rate_hz': 1000}
        self.assertEqual(array(two, 4 * 5 + 3), {
            'dtype': 'int16', 'byte_order': 'big', 'shape': [5, 2], 'count': 10, 'bytes': 20,
            'axes': [{'name': 'time', 'unit': 's', 'start': 0.0, 'step': 0.001},
                     {'name': 'channel', 'labels': ['x', 'y'], 'units': ['V', 'mV']}]})
        self.assertEqual(array({'format': 'uint8'}, 7)['axes'], [{'name': 'sample'}])
        for definition in ({'format': 'npy'}, {'format': 'float32'}, {'format': 'float32-le', 'channels': [{}, {}]}):
            with self.assertRaisesRegex(ValueError, 'numeric type|no complete sample'):
                array(definition, 4)

    def test_a_streamed_acquisition_reads_as_one_array(self):
        samples = [(math.sin(i / 50), 1000 * math.cos(i / 50) if i != 777 else math.nan) for i in range(3000)]
        payload = struct.pack('<6000f', *[v for pair in samples for v in pair])
        with self.client.start_experiment() as run:
            stream = run.stream('voltage', {'format': 'float32-le', 'sample_rate_hz': 1000,
                                            'channels': [{'name': 'x', 'unit': 'V'}, {'name': 'y', 'unit': 'mV'}]})
            # Chunks need not hold whole samples.
            for sequence, (begin, end) in enumerate(((0, 9001), (9001, 20000), (20000, len(payload)))):
                stream.append(payload[begin:end], sequence)
            seen = stream.read_array(at={'channel': 'y'}, points=6)
            self.assertEqual((seen['unit'], seen['shape'], seen['along']['unit']), ('mV', [3000, 2], 's'))
            self.assertEqual((seen['summary']['count'], seen['summary']['nonfinite']), (3000, 1))
            self.assertAlmostEqual(seen['summary']['max'], 1000, delta=0.1)
            self.assertEqual(len(seen['envelope']['min']), 6)
            window = stream.read_array(at={'channel': 'x'}, low=1.0, high=1.004)
            self.assertEqual((window['unit'], window['along']['start'], len(window['values'])), ('V', 1000, 5))
            self.assertAlmostEqual(window['values'][0], math.sin(20), places=6)
            run.stream('frames', {'format': 'npy'})
            with self.assertRaisesRegex(IOPError, 'numeric type'):
                run.stream('frames').read_array()
            self.assertEqual([(s['name'], s['chunks'], s['bytes']) for s in run.streams()],
                             [('frames', 0, 0), ('voltage', 3, len(payload))])
            # The whole samples, by channels: a sample that has not arrived in full is left out.
            stream.append(b'\x00\x00', 3)
            header, data = load(stream.array_file())
            self.assertEqual((header['descr'], header['shape'], data), ('<f4', (3000, 2), payload))


class DerivedValueCase(unittest.TestCase):
    """Saved operations, bindings and operating conditions take values from arrays through views."""
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def register(self):
        self.client.register({'id': 'resonator', 'driver': Resonator.__module__ + ':Resonator'})
        self.client.save_operation(LOCATE)

    def test_named_results_and_bindings_read_values_from_arrays(self):
        self.register()
        with self.client.start_experiment(resources=['resonator']) as run:
            found = run.call('resonance.locate')
        self.assertAlmostEqual(found['f0'], 1.06e9)
        self.assertAlmostEqual(found['width'], 4e6, delta=1e4)
        self.assertAlmostEqual(found['depth'], 0.2)
        # A later step receives the value; the sweep keeps what was derived from it.
        follow = self.client.submit([
            {'id': 'find', 'operation': 'resonance.locate'},
            {'id': 'hold', 'device': 'resonator', 'operation': 'probe', 'bindings': {'hz': {'step': 'find', 'path': ['f0']}}},
            {'id': 'band', 'device': 'resonator', 'operation': 'probe', 'after': ['find.sweep'], 'bindings': {'hz': {
                'step': 'find.sweep', 'view': {'low': 1.0e9, 'high': 1.05e9}, 'path': ['summary', 'max_at']}}}])
        result = follow.result(10)
        self.assertAlmostEqual(result['hold']['value'], 1.06e9)
        self.assertAlmostEqual(result['find']['f0'], 1.06e9)
        self.assertEqual(result['band']['value'], 1.0e9)
        sweep = next(op for op in follow.operations()['operations'] if op['step'] == 'find.sweep')
        self.assertEqual(len(sweep['derived']), 4)
        # A view that cannot be read fails the step that needs it, with the reason.
        failing = self.client.submit([
            {'id': 'sweep', 'device': 'resonator', 'operation': 'sweep'},
            {'id': 'probe', 'device': 'resonator', 'operation': 'probe',
             'bindings': {'hz': {'step': 'sweep', 'view': {'field': 'items.trace'}, 'path': ['summary', 'min_at']}}}])
        self.assertEqual(failing.wait(10)['state'], 'failed')
        probe = next(op for op in failing.operations()['operations'] if op['step'] == 'probe')
        self.assertIn("No array at 'items.trace'", probe['error'])
        for steps, message in (
                ([{'id': 'find', 'operation': 'resonance.locate'},
                  {'id': 'p', 'device': 'resonator', 'operation': 'probe',
                   'bindings': {'hz': {'step': 'find', 'view': {}, 'path': ['f0']}}}], 'a view reads an array'),
                ([{'id': 's', 'device': 'resonator', 'operation': 'sweep'},
                  {'id': 'p', 'device': 'resonator', 'operation': 'probe',
                   'bindings': {'hz': {'step': 's', 'view': {'colour': 'red'}}}}], 'view takes field, along')):
            with self.assertRaisesRegex(IOPError, message):
                self.client.submit(steps)

    def test_a_condition_tunes_a_bias_until_the_resonance_is_on_target(self):
        self.register()
        self.client.configure_platform({'schema_version': 1, 'id': 'tunable', 'devices': ['resonator'], 'conditions': [{
            'id': 'resonance.on_target', 'description': 'The resonance lies within 1 MHz of 1.05 GHz',
            'verify': {'operation': 'resonance.locate'}, 'accept': {'value': ['f0'], 'minimum': 1.049e9, 'maximum': 1.051e9},
            'correct': {'tune': {'device': 'resonator', 'operation': 'set_bias', 'argument': 'volts', 'step': 0.5,
                                 'minimum': 0, 'maximum': 10, 'read': {'device': 'resonator', 'operation': 'bias'}}}}]})
        report = self.client.maintain('tunable')
        self.assertEqual((report['holds'], report['report'][-1]['result']), (True, 'corrected'))
        state = self.client.condition('resonance.on_target')
        self.assertAlmostEqual(state['value'], 1.05e9, delta=1e6)
        self.assertAlmostEqual(self.engine.devices['resonator'].volts, 5.0, delta=0.1)


if __name__ == '__main__':
    unittest.main()

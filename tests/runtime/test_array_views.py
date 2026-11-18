"""Views of saved arrays: what a trace, spectrum or image says without moving all of its values."""
import array
import importlib.util
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
FREQUENCY = {'name': 'frequency', 'unit': 'Hz', 'start': 1.0e9, 'step': 1.0e5}


def saved(folder, values, description):
    """Write values as an operation would save them and return the array's holder and file."""
    payload = _payload(values, description)
    holder = arrays.result(payload, arrays.check_result(description), None)
    path = Path(folder) / f'{len(os.listdir(folder))}.bin'
    path.write_bytes(payload)
    return holder, path


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
        with self.assertRaisesRegex(ValueError, 'labels are re, im; abs and arg'):
            self.view(pairs, trace, at={'component': 'phase'})

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
        with self.assertRaisesRegex(ValueError, 'other than the one'):
            self.view(values, image, along='column', at={'column': 1})

    def test_values_that_are_not_finite_are_counted_and_left_out(self):
        seen = self.view([1.0, math.nan, 3.0, math.inf, -2.0], {'dtype': 'float64'})
        self.assertEqual({k: seen['summary'][k] for k in ('count', 'nonfinite', 'min', 'max')},
                         {'count': 5, 'nonfinite': 2, 'min': -2.0, 'max': 3.0})
        self.assertAlmostEqual(seen['summary']['mean'], 2 / 3)
        self.assertEqual(seen['values'], [1.0, None, 3.0, None, -2.0])
        self.assertEqual(json.loads(json.dumps(seen, allow_nan=False)), seen)

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
        ]
        compared = 0
        for values, description, options in cases:
            holder, path = saved(folder, values, description)
            request, _ = views.plan(holder['data'], **options)
            request['file'] = str(path)
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
        if compared == 0:
            self.skipTest('iop-execd with array views is not installed')

    def test_without_iop_execd_python_computes_views_up_to_a_limit(self):
        holder, path = saved(self.folder.name, DIP, {'dtype': 'float64'})
        # An executor that predates views is not used; Python computes the same view.
        with patch.dict(os.environ, {'IOP_EXECD': sys.executable}):
            self.assertIsNone(views.native({'file': str(path)}))
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
        printed = subprocess.check_output([sys.executable, '-m', 'ai4instruments.iop', '--endpoint', self.client.endpoint,
                                           'array', sweep['id'], '--low', '1.0598e9', '--high', '1.0602e9'],
                                          env=dict(os.environ, IOP_TOKEN=self.config['token']), text=True)
        self.assertEqual(json.loads(printed)['values'], DIP[598:603])
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
        self.assertTrue({'operation_id', 'field', 'along', 'at', 'start', 'stop', 'low', 'high', 'points'}
                        <= set(schema['properties']))
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


if __name__ == '__main__':
    unittest.main()

"""Agent-facing views stay small; programs keep full results."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ai4instruments.iop import arrays
from ai4instruments.iop.execution import Context
from ai4instruments.iop.mcp import AGENT_VALUES, compact
from . import test_service as fixture


class CompactCase(unittest.TestCase):
    def test_long_arrays_become_a_count_and_short_ones_remain(self):
        trace = {'data': {'dtype': 'float64', 'count': 802, 'shape': [401, 2]}, 'values': [[0.0, 1.0]] * 401}
        axis = {'data': {'dtype': 'float64', 'count': AGENT_VALUES}, 'values': list(range(AGENT_VALUES))}
        page = {'operations': [{'id': 'op-1', 'result': {**trace, 'setpoints': [axis]}}], 'cursor': 3}
        reduced = compact(page)
        result = reduced['operations'][0]['result']
        self.assertEqual((result['values_omitted'], 'values' in result), (802, False))
        self.assertEqual(result['setpoints'][0]['values'], list(range(AGENT_VALUES)))
        self.assertEqual(compact(page, values=True), page)
        self.assertEqual(compact({'value': 3, 'values': [1, 2]}), {'value': 3, 'values': [1, 2]})


class ArrayResultCase(unittest.TestCase):
    def test_saved_large_arrays_are_not_decoded(self):
        spec = arrays.check_result({'dtype': 'float32', 'inline_limit': 4})
        payload = arrays.encode([float(i) for i in range(1000)], 'float32')
        with tempfile.TemporaryDirectory() as folder, patch.object(arrays, 'decode', side_effect=AssertionError('decoded')):
            fields = arrays.result(payload, spec, Context('run', 'op', Path(folder), lambda _: None), 'trace')
        self.assertEqual((fields['data']['count'], 'values' in fields), (1000, False))
        with self.assertRaisesRegex(ValueError, 'whole float32 elements'):
            arrays.result(payload[:-1], spec)


class OverviewCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def test_long_operation_lists_end_with_a_count(self):
        operations = {f'read_channel_{n:02d}': {'path': f'/ch/{n}', 'parameters': {}} for n in range(30)}
        self.client.register({'id': 'logger', 'driver': 'http', 'config': {'base_url': 'http://192.0.2.5', 'operations': operations}})
        summary = next(d for d in self.client.context()['devices'] if d['id'] == 'logger')
        self.assertEqual((len(summary['operations']), summary['operations_omitted']), (24, 6))
        stage = next(d for d in self.client.context()['devices'] if d['id'] == 'stage')
        self.assertNotIn('operations_omitted', stage)
        self.assertEqual(len(self.client.request('/v1/devices/logger')['operations']), 30)


if __name__ == '__main__':
    unittest.main()

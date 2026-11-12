"""NaN and infinite readings are recorded; a monitor treats them as a failed read."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from ai4instruments.iop.drivers import Driver
from ai4instruments.iop.store import finite
from . import test_service as fixture


class Overranged(Driver):
    """Reports values a power meter or sensor returns when out of range or without a signal."""
    def describe(self):
        return {'operations': {'read': {'effect': 'observe', 'parameters': {}}}}

    def invoke(self, operation, arguments, cancelled):
        return {'value': float('nan'), 'trace': [1.5, float('inf'), float('-inf')]}


class FiniteCase(unittest.TestCase):
    def test_strings_replace_values_json_cannot_carry(self):
        self.assertEqual(finite({'a': [float('nan'), (2.0, float('inf'))], 'b': 'x'}),
                         {'a': ['NaN', [2.0, 'Infinity']], 'b': 'x'})


class ReadingCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def test_operations_record_non_finite_readings(self):
        self.client.register({'id': 'sensor', 'driver': __name__ + ':Overranged'})
        result = self.client.submit([{'id': 'read', 'device': 'sensor', 'operation': 'read'}]).result(10)
        self.assertEqual(result['read'], {'value': 'NaN', 'trace': [1.5, 'Infinity', '-Infinity']})

    def test_monitors_treat_non_finite_values_as_failed_reads(self):
        self.client.register({'id': 'sensor', 'driver': __name__ + ':Overranged', 'monitors': [
            {'id': 'power', 'operation': 'read', 'interval_s': 0.1, 'maximum': 1}]})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            monitor = self.client.request('/v1/devices/sensor')['monitors'][0]
            if monitor['state'] != 'pending':
                break
            time.sleep(0.05)
        self.assertEqual(monitor['state'], 'error')
        self.assertIn('not a finite number', monitor['error'])


class WorkerCase(unittest.TestCase):
    def test_process_workers_send_non_finite_readings(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'overranged_sensor.py').write_text(
                'from ai4instruments.iop.drivers import Driver\n'
                'class Sensor(Driver):\n'
                '    def describe(self): return {"operations": {"read": {"effect": "observe", "parameters": {}}}}\n'
                '    def invoke(self, operation, arguments, cancelled): return {"value": float("nan")}\n')
            source = str(Path(__file__).resolve().parents[2] / 'src')
            worker = subprocess.Popen([sys.executable, '-m', 'ai4instruments.iop.worker'], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, text=True,
                                      env=dict(os.environ, PYTHONPATH=os.pathsep.join([folder, source])))
            try:
                replies = []
                for message in ({'id': 1, 'method': 'configure', 'params': {'devices': [
                                    {'id': 'sensor', 'driver': 'overranged_sensor:Sensor'}]}},
                                {'id': 2, 'method': 'invoke', 'params': {'device': 'sensor', 'operation': 'read'}}):
                    worker.stdin.write(json.dumps(message) + '\n'); worker.stdin.flush()
                    replies.append(json.loads(worker.stdout.readline()))
                self.assertEqual(replies[1], {'id': 2, 'result': {'value': 'NaN'}})
            finally:
                worker.stdin.close(); worker.wait(10); worker.stdout.close()


if __name__ == '__main__':
    unittest.main()

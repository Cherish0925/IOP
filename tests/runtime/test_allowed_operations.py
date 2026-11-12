"""allowed_operations limits what experiments and agents can call on an instrument."""
import unittest
from ai4instruments.iop import IOPError
from . import test_service as fixture


class AllowedOperationsCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def supply(self, patterns):
        return {'id': 'supply', 'driver': 'scpi', 'allowed_operations': patterns,
                'config': {'host': '127.0.0.1', 'port': 5025, 'operations': {
                    'read_voltage': {'parameters': {}, 'query': 'MEAS:VOLT?', 'effect': 'observe'},
                    'set_voltage': {'parameters': {'volts': {'type': 'number', 'minimum': 0, 'maximum': 5}},
                                    'write': 'VOLT {volts}'}}}}

    def test_raw_commands_are_hidden_and_refused(self):
        description = self.client.register(self.supply(['read_*', 'set_voltage']))
        self.assertEqual(sorted(description['operations']), ['identify', 'read_voltage', 'set_voltage'])
        with self.assertRaisesRegex(IOPError, 'Unknown operation: supply.write'):
            self.client.submit([{'id': 'raw', 'device': 'supply', 'operation': 'write',
                                 'arguments': {'command': 'VOLT 30'}}])

    def test_patterns_are_validated(self):
        with self.assertRaisesRegex(IOPError, 'list of operation name patterns'):
            self.client.register(self.supply('read_*'))
        with self.assertRaisesRegex(IOPError, 'matches none'):
            self.client.register(self.supply(['nothing']))

    def test_configuration_change_and_reconnect_keep_the_limit(self):
        record = {'id': 'slide', 'driver': 'simulated', 'config': {'kind': 'stage'}, 'allowed_operations': ['position']}
        self.assertEqual(sorted(self.client.register(record)['operations']), ['position'])
        current = self.client.device_history('slide')[0]
        record['allowed_operations'] = ['position', 'wait']
        self.client.configure_device('slide', record, current['revision'])
        self.assertEqual(sorted(self.engine.descriptions['slide']['operations']), ['position', 'wait'])
        self.client.reconnect('slide')
        self.assertEqual(sorted(self.engine.descriptions['slide']['operations']), ['position', 'wait'])
        with self.assertRaisesRegex(IOPError, 'Unknown operation: slide.move_to'):
            self.client.submit([{'id': 'move', 'device': 'slide', 'operation': 'move_to', 'arguments': {'angle_deg': 5}}])


if __name__ == '__main__':
    unittest.main()

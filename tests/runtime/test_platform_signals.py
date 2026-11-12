"""Trigger, clock and reference connections in platform descriptions."""
import unittest
from ai4instruments.iop import IOPError
from . import test_service as fixture


def link(source, target, **signal):
    return {'kind': 'electrical', 'from': {'device': source[0], 'port': source[1]},
            'to': {'device': target[0], 'port': target[1]}, 'signal': signal}


def bench(*connections):
    return {'schema_version': 1, 'id': 'timing-bench', 'devices': ['stage', 'meter', 'aux'], 'connections': list(connections)}


class SignalCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def test_signals_are_listed_with_an_arming_order(self):
        self.client.configure_platform(bench(
            link(('stage', 'in_position'), ('meter', 'trigger_in'), type='trigger', edge='rising', delay_s=2e-6, level_v=3.3),
            link(('stage', 'ref_out'), ('aux', 'ref_in'), type='reference', frequency_hz=10e6, impedance_ohm=50),
            link(('meter', 'loop_out'), ('meter', 'loop_in'), type='sync')))
        timing = self.client.platform('timing-bench')['timing']
        self.assertEqual(timing['arm_order'], ['aux', 'meter', 'stage'])
        self.assertEqual(timing['signals'][0], {'from': 'stage.in_position', 'to': 'meter.trigger_in', 'type': 'trigger',
                                                'edge': 'rising', 'delay_s': 2e-6, 'level_v': 3.3})

    def test_feedback_wiring_is_preserved_without_inventing_an_arming_order(self):
        definition = bench(
            link(('stage', 'ref_out'), ('meter', 'ref_in'), type='reference', frequency_hz=10e6),
            link(('meter', 'ready'), ('stage', 'enable'), type='gate'))
        self.client.configure_platform(definition)
        platform = self.client.platform('timing-bench')
        self.assertIsNone(platform['timing']['arm_order'])
        self.assertEqual(platform['definition']['connections'], definition['connections'])
        self.assertEqual(len(platform['timing']['signals']), 2)

    def test_invalid_signal_values_are_refused(self):
        for connections, message in (
                ([link(('stage', 'out'), ('meter', 'in'), type='pulse')], 'signal.type'),
                ([link(('stage', 'out'), ('meter', 'in'), type='gate', width_s=0)], 'width_s must be a positive'),
                ([link(('stage', 'out'), ('meter', 'in'), type='trigger', edge='up')], 'rising, falling or both')):
            with self.assertRaisesRegex(IOPError, message):
                self.client.configure_platform(bench(*connections))

    def test_platforms_without_signals_have_no_timing(self):
        self.client.configure_platform(bench())
        self.assertNotIn('timing', self.client.platform('timing-bench'))


if __name__ == '__main__':
    unittest.main()

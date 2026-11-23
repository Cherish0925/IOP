"""A status observation cannot certify data when the status itself is unreliable."""
import copy
import unittest

from ai4instruments.iop.drivers import Driver
from . import test_service as fixture


class StatusInstrument(Driver):
    def describe(self):
        return {'operations': {
            'read': {'effect': 'observe', 'parameters': {}, 'result': {'unit': 'V', 'maximum': 10},
                     'quality': {'operation': 'status', 'equals': 0}},
            'status': {'effect': 'observe', 'parameters': {}}}}

    def invoke(self, operation, arguments, cancelled):
        return copy.deepcopy(self.config['reading' if operation == 'read' else 'status'])


class QualityDispatchCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def read(self, reading, status):
        self.client.register({'id': 'voltmeter', 'driver': __name__ + ':StatusInstrument',
                              'config': {'reading': reading, 'status': status}})
        run = self.client.submit([{'id': 'sample', 'device': 'voltmeter', 'operation': 'read'}])
        return run.result(5)['sample'], run.info()['operation_records'][0]

    def test_a_matching_but_unreliable_status_does_not_certify_the_reading(self):
        result, record = self.read({'value': 1.25}, {'value': 0, 'quality': {
            'level': 'uncertain', 'source': 'controller', 'detail': 'cached status after connection loss'}})
        self.assertEqual(result['value'], 1.25)
        self.assertEqual(result['quality']['level'], 'uncertain')
        self.assertIn('cached status', result['quality']['detail'])
        self.assertIn('quality', record['in_doubt'])

    def test_a_second_status_does_not_erase_the_original_instrument_failure(self):
        result, record = self.read({'value': 1.25, 'quality': {
            'level': 'bad', 'source': 'detector', 'code': 42, 'detail': 'sensor disconnected'}}, {'value': 1})
        self.assertEqual(result['quality']['level'], 'bad')
        self.assertEqual(result['quality']['source'], 'detector')
        self.assertEqual(result['quality']['code'], 42)
        self.assertIn('sensor disconnected', result['quality']['detail'])
        self.assertIn('status returned 1', result['quality']['detail'])
        self.assertIn('sensor disconnected', record['in_doubt']['quality']['reason'])

    def test_limits_append_their_reason_without_reclassifying_invalid_data(self):
        result, _ = self.read({'value': 12.0, 'quality': {
            'level': 'invalid', 'source': 'adc', 'detail': 'conversion failed'}}, {'value': 0})
        self.assertEqual(result['quality']['level'], 'invalid')
        self.assertIn('conversion failed', result['quality']['detail'])
        self.assertIn('outside the measuring interval', result['quality']['detail'])


if __name__ == '__main__':
    unittest.main()

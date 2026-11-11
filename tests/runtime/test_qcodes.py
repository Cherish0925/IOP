"""QCoDeS drivers become IOP instruments with operations described from parameter metadata."""
import importlib.util
import tempfile
import threading
import unittest
from pathlib import Path
from ai4instruments.iop import IOPError
from ai4instruments.iop.drivers import make_driver
from ai4instruments.iop.execution import Context
from . import test_service as fixture

available = importlib.util.find_spec('qcodes') is not None
simulated = available and importlib.util.find_spec('pyvisa_sim') is not None
MOCK = 'qcodes.instrument_drivers.mock_instruments:DummyChannelInstrument'


def channel_box(device_id='box', **config):
    return {'id': device_id, 'driver': 'qcodes',
            'config': {'instrument': MOCK, 'parameters': ['A.*'], 'exclude': ['*multi*'], **config}}


@unittest.skipUnless(available, 'Install qcodes to use QCoDeS drivers')
class DescriptionCase(unittest.TestCase):
    def setUp(self):
        self.box = make_driver(channel_box(), {})
        self.addCleanup(self.box.close)

    def test_operations_carry_units_ranges_and_effects(self):
        operations = self.box.describe()['operations']
        self.assertEqual(operations['set.A.temperature']['parameters']['value'],
                         {'unit': 'K', 'type': 'number', 'minimum': 0.0, 'maximum': 300.0})
        self.assertEqual(operations['get.A.temperature']['effect'], 'observe')
        self.assertEqual(operations['get.A.temperature']['result'], {'unit': 'K', 'type': 'number'})
        self.assertEqual(operations['set.A.dummy_text']['parameters']['value']['type'], 'string')
        self.assertEqual(operations['get.A.dummy_parameter_with_setpoints']['result']['type'], 'array')
        self.assertNotIn('set.A.dummy_parameter_with_setpoints', operations)
        self.assertFalse([name for name in operations if 'multi' in name or name.startswith(('get.B.', 'get.IDN'))])

    def test_values_arrays_and_setpoints(self):
        with tempfile.TemporaryDirectory() as folder:
            context = Context('run', 'op', Path(folder), lambda _: None)
            self.box.invoke('set.A.temperature', {'value': 12.5}, context)
            self.assertEqual(self.box.invoke('get.A.temperature', {}, context)['value'], 12.5)
            self.assertEqual(self.box.invoke('get.A.dummy_complex', {}, context)['value'], {'re': 1.0, 'im': 1.0})
            trace = self.box.invoke('get.A.dummy_parameter_with_setpoints', {}, context)
            self.assertEqual(trace['data']['axes'], [{'name': 'dummy_sp_axis', 'unit': 'some unit'}])
            self.assertEqual(trace['data']['shape'], trace['setpoints'][0]['data']['shape'])
            self.assertEqual((Path(folder) / trace['data']['file']).stat().st_size, trace['data']['bytes'])
            complex_trace = self.box.invoke('get.A.dummy_complex_array_parameter', {}, context)
            self.assertEqual(complex_trace['data']['shape'], [5, 2])
            self.assertEqual(complex_trace['data']['axes'][-1], {'name': 'component', 'labels': ['re', 'im']})

    def test_configuration_errors_name_the_problem(self):
        with self.assertRaisesRegex(ValueError, 'module:Class'):
            make_driver({'id': 'x', 'driver': 'qcodes', 'config': {'instrument': 'qcodes.Instrument'}}, {})
        with self.assertRaisesRegex(ValueError, 'not a QCoDeS instrument class'):
            make_driver({'id': 'x', 'driver': 'qcodes', 'config': {'instrument': 'pathlib:Path'}}, {})
        with self.assertRaisesRegex(ValueError, 'No QCoDeS parameters remain'):
            make_driver(channel_box('y', parameters=['nothing.*']), {})


@unittest.skipUnless(simulated, 'Install qcodes and pyvisa-sim to run a QCoDeS VISA driver')
class VisaDriverCase(unittest.TestCase):
    def test_vendor_driver_reports_identity_and_sets_its_source(self):
        source = make_driver({'id': 'gs200', 'driver': 'qcodes', 'config': {
            'instrument': 'qcodes.instrument_drivers.yokogawa:YokogawaGS200', 'address': 'GPIB::1::INSTR',
            'arguments': {'pyvisa_sim_file': 'Yokogawa_GS200.yaml'}}}, {})
        try:
            description = source.describe()
            self.assertEqual(description['identity']['model'], 'GS200')
            self.assertEqual(description['operations']['set.source_mode']['parameters']['value']['enum'], ['CURR', 'VOLT'])
            # The simulated GS200 starts in current mode; its output switch behaves alike in every QCoDeS release.
            source.invoke('set.output', {'value': 'on'}, threading.Event())
            self.assertEqual(source.invoke('get.output', {}, threading.Event())['value'], 'on')
        finally:
            source.close()


@unittest.skipUnless(available, 'Install qcodes to use QCoDeS drivers')
class RuntimeCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def test_runtime_checks_driver_ranges_and_records_array_files(self):
        self.client.register(channel_box())
        with self.assertRaises(IOPError) as raised:
            self.client.submit([{'id': 'hot', 'device': 'box', 'operation': 'set.A.temperature', 'arguments': {'value': 400}}])
        self.assertIn('outside', str(raised.exception))
        run = self.client.submit([
            {'id': 'set', 'device': 'box', 'operation': 'set.A.temperature', 'arguments': {'value': 77}},
            {'id': 'read', 'device': 'box', 'operation': 'get.A.temperature', 'after': ['set']},
            {'id': 'trace', 'device': 'box', 'operation': 'get.A.dummy_parameter_with_setpoints'}])
        result = run.result(10)
        self.assertEqual(result['read']['value'], 77)
        self.assertEqual(result['trace']['data']['count'], 101)
        names = [f['name'] for f in run.files()]
        self.assertTrue(any(n.endswith('get-A-dummy_parameter_with_setpoints.bin') for n in names))


if __name__ == '__main__':
    unittest.main()

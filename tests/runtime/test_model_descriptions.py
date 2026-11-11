"""Model descriptions for Modbus and HTTP instruments, applied by naming them at registration."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ai4instruments.iop import IOPError
from ai4instruments.iop.identification import validate_profile
from . import test_service as fixture

CONTROLLER = {
    'id': 'example-tc1', 'driver': 'modbus', 'match': {'manufacturer': 'Example Controls', 'model': 'TC-1'},
    'documentation': ['https://example.com/tc1-modbus.pdf'],
    'config': {'name': 'TC-1 temperature controller', 'operations': {
        'read_temperature': {'function': 'read_holding_registers', 'arguments': {'address': 0, 'count': 1},
                             'scale': 0.1, 'unit': 'degC', 'signed': True, 'effect': 'observe', 'parameters': {}},
        'set_setpoint': {'function': 'write_register', 'arguments': {'address': 1}, 'scale': 0.1, 'value_parameter': 'celsius',
                         'parameters': {'celsius': {'type': 'number', 'minimum': -50, 'maximum': 200, 'unit': 'degC'}}}}}}
SENSOR = {
    'id': 'example-env2', 'driver': 'http', 'match': {'manufacturer': 'Example Sensors', 'model': 'ENV-2'},
    'config': {'operations': {
        'read_humidity': {'method': 'GET', 'path': '/channels/{channel}', 'result_path': ['humidity'], 'unit': '%',
                          'effect': 'observe', 'parameters': {'channel': {'type': 'integer', 'minimum': 1, 'maximum': 4}}}}}}


class ValidationCase(unittest.TestCase):
    def test_driver_specific_rules(self):
        validate_profile(json.loads(json.dumps(CONTROLLER)))
        validate_profile(json.loads(json.dumps(SENSOR)))
        cases = (
            ({**CONTROLLER, 'driver': 'example_vendor:Camera'}, 'scpi, modbus or http'),
            ({**CONTROLLER, 'config': {'operations': {'read': {'arguments': {}}}}}, 'function must be one of'),
            ({**CONTROLLER, 'config': {'operations': {'write_register': CONTROLLER['config']['operations']['set_setpoint']}}},
             'provided by the driver'),
            ({**CONTROLLER, 'config': {'operations': {'set': {'function': 'write_register', 'value_parameter': 'celsius'}}}},
             'describe value parameter'),
            ({**SENSOR, 'config': {'operations': {'read': {'path': '/channels/{channel}'}}}}, 'Describe command parameter: channel'),
            ({**SENSOR, 'config': {'operations': {'read': {'path': '/x', 'method': 'TRACE'}}}}, 'method must be'),
            ({**SENSOR, 'config': {'operations': {'read': {'method': 'GET'}}}}, 'provide its path'))
        for profile, message in cases:
            with self.assertRaisesRegex(ValueError, message):
                validate_profile(json.loads(json.dumps(profile)))


class RegistrationCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def test_modbus_description_supplies_operations_and_documentation(self):
        self.client.save_instrument_profile(CONTROLLER)
        listed = self.client.instrument_catalog(model='TC-1')['profiles']
        self.assertEqual([p['id'] for p in listed], ['example-tc1'])
        description = self.client.register({'id': 'bath', 'instrument_profile': 'example-tc1',
                                            'config': {'host': '192.0.2.10', 'device_id': 3, 'operations': {
                                                'read_temperature': {'function': 'read_input_registers',
                                                                     'arguments': {'address': 10, 'count': 1}, 'parameters': {}}}}})
        self.assertEqual(description['driver'], 'modbus')
        self.assertEqual(description['name'], 'TC-1 temperature controller')
        self.assertEqual(description['documentation'], ['https://example.com/tc1-modbus.pdf'])
        self.assertEqual(description['operations']['read_temperature']['function'], 'read_input_registers')
        self.assertEqual(description['operations']['set_setpoint']['parameters']['celsius']['maximum'], 200)

    def test_unknown_or_mismatched_descriptions_are_refused(self):
        self.client.save_instrument_profile(CONTROLLER)
        with self.assertRaisesRegex(IOPError, 'Unknown model description: missing'):
            self.client.register({'id': 'a', 'driver': 'modbus', 'instrument_profile': 'missing', 'config': {'host': '192.0.2.10'}})
        with self.assertRaisesRegex(IOPError, 'for the modbus driver'):
            self.client.register({'id': 'b', 'driver': 'http', 'instrument_profile': 'example-tc1',
                                  'config': {'base_url': 'http://192.0.2.11'}})

    def test_http_description_runs_against_a_device(self):
        class Sensor(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                body = json.dumps({'channel': int(self.path.rsplit('/', 1)[1]), 'humidity': 41.5}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Sensor)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.client.save_instrument_profile(SENSOR)
        self.client.register({'id': 'room', 'instrument_profile': 'example-env2',
                              'config': {'base_url': f'http://127.0.0.1:{server.server_port}'}})
        result = self.client.submit([{'id': 'rh', 'device': 'room', 'operation': 'read_humidity',
                                      'arguments': {'channel': 2}}]).result(5)
        self.assertEqual((result['rh']['value'], result['rh']['unit']), (41.5, '%'))


if __name__ == '__main__':
    unittest.main()

"""The validity of a reading: the instrument's own status, and the measuring interval its description declares."""
import socketserver
import threading
import unittest
from ai4instruments.iop import IOPError
from ai4instruments.iop.drivers import Driver
from . import test_service as fixture


# A voltmeter whose STATus:QUEStionable condition register sets bit 0 while its input is overloaded.
class Voltmeter(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            command, server = line.decode().strip(), self.server
            with server.lock:
                if command == '*IDN?':
                    reply = 'ACME,VM1,001,1.0'
                elif command == 'MEAS:VOLT?':
                    server.reads += 1
                    reply = repr(server.values[min(server.reads, len(server.values)) - 1])
                    server.flag = 1 if server.reads in server.overloaded else 0
                elif command == 'STAT:QUES:COND?':
                    reply = str(server.flag)
                else:
                    continue
            self.wfile.write((reply + '\n').encode())
            self.wfile.flush()


# A camera of any make: its pixels clip at 4095 counts.
class Camera(Driver):
    def describe(self):
        return {'operations': {'peak': {'effect': 'observe', 'parameters': {}, 'result': {'unit': 'counts', 'saturation': 4095}}}}

    def invoke(self, operation, arguments, cancelled):
        return {'value': self.config['peak']}


# Any instrument whose SDK or registers report a status beside the reading: a channel flags an overrange.
class Digitizer(Driver):
    def describe(self):
        channel = {'type': 'integer', 'enum': [1, 2]}
        return {'operations': {
            'read': {'effect': 'observe', 'parameters': {'channel': channel}, 'result': {'unit': 'V'},
                     'quality': {'operation': 'status', 'arguments': {'channel': '{channel}'}, 'value': ['overrange'],
                                 'equals': False}},
            'status': {'effect': 'observe', 'parameters': {'channel': channel}}}}

    def invoke(self, operation, arguments, cancelled):
        if operation == 'status':
            return {'overrange': arguments['channel'] in self.config['overrange']}
        return {'value': 0.5}


class Broken(Digitizer):
    def describe(self):
        description = super().describe()
        description['operations']['read']['quality']['operation'] = 'missing'
        return description


class QualityCase(unittest.TestCase):
    def setUp(self):
        fixture.ServiceCase.setUp(self)
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Voltmeter)
        server.daemon_threads, server.lock, server.reads, server.flag = True, threading.Lock(), 0, 0
        server.values, server.overloaded = [1.25], set()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.meter = server
        self.client.register({'id': 'dmm', 'driver': 'scpi', 'config': {
            'transport': 'tcp', 'host': '127.0.0.1', 'port': server.server_address[1], 'timeout': 1, 'operations': {
                'read_voltage': {'query': 'MEAS:VOLT?', 'effect': 'observe', 'parameters': {},
                                 'result': {'type': 'number', 'unit': 'V', 'minimum': -10, 'maximum': 10},
                                 'quality': {'query': 'STAT:QUES:COND?', 'bits': 1}}}}})

    tearDown = fixture.ServiceCase.tearDown

    def read(self, **step):
        run = self.client.submit([{'id': 'v', 'device': 'dmm', 'operation': 'read_voltage', **step}])
        return run, run.result(10)['v']

    def test_a_valid_reading_carries_no_doubt(self):
        run, reading = self.read()
        self.assertEqual(reading['value'], 1.25)
        self.assertNotIn('quality', reading)
        self.assertNotIn('in_doubt', run.info()['operation_records'][0])

    def test_the_instrument_status_puts_a_reading_in_doubt(self):
        self.meter.overloaded = {1}
        run, reading = self.read()
        self.assertEqual(reading['quality'], {'level': 'uncertain', 'source': 'instrument', 'detail': 'STAT:QUES:COND? returned 1'})
        record = run.info()['operation_records'][0]
        self.assertEqual((record['in_doubt']['quality']['outcome'], record['in_doubt']['quality']['reason']),
                         ('uncertain', 'STAT:QUES:COND? returned 1'))
        self.assertEqual([op['id'] for op in run.operations(in_doubt=True)['operations']], [record['id']])

    def test_a_reading_outside_the_measuring_interval_is_in_doubt(self):
        self.meter.values = [12.5]
        run, reading = self.read()
        self.assertEqual(reading['quality']['detail'], '12.5 is outside the measuring interval -10 to 10')

    def test_a_point_in_doubt_is_taken_again(self):
        self.meter.values, self.meter.overloaded = [1.0, 9.9, 2.0, 3.0], {2}
        run = self.client.submit([{'id': 'v', 'device': 'dmm', 'operation': 'read_voltage', 'repeat': 3, 'retake': True}])
        run.result(10)
        records = run.info()['operation_records']
        doubtful = [r for r in records if r.get('in_doubt')]
        self.assertEqual(len(doubtful), 1)
        self.assertEqual(doubtful[0]['result']['value'], 9.9)
        retake = next(r for r in records if r['id'] == doubtful[0]['retaken_by'])
        self.assertEqual((retake['result']['value'], retake.get('in_doubt')), (2.0, None))
        # What the repetition gathers holds the retake in place of the reading in doubt.
        self.assertEqual(run.result(10)['v']['value']['values'], [1.0, 2.0, 3.0])

    def test_a_verification_with_an_uncertain_reading_is_unusable(self):
        self.client.configure_platform({'schema_version': 1, 'id': 'bench', 'devices': ['dmm'], 'conditions': [
            {'id': 'supply.on', 'kind': 'operating_point', 'description': 'The supply gives about 1 V',
             'verify': {'device': 'dmm', 'operation': 'read_voltage'}, 'accept': {'minimum': 0.9, 'maximum': 1.6}}]})
        self.meter.values, self.meter.overloaded = [1.25, 1.25], {1}
        entry = self.client.maintain('bench')['report'][0]
        self.assertEqual((entry['result'], entry['found']), ('lost', 'unusable'))
        self.assertTrue(self.client.maintain('bench')['holds'])

    def test_saturation_declared_by_any_driver(self):
        self.client.register({'id': 'cam', 'driver': __name__ + ':Camera', 'config': {'peak': 4095}})
        run = self.client.submit([{'id': 'p', 'device': 'cam', 'operation': 'peak'}])
        self.assertEqual(run.result(5)['p']['quality']['detail'], '4095 reaches saturation at 4095')

    def test_any_instrument_qualifies_a_reading_with_another_of_its_operations(self):
        self.client.register({'id': 'adc', 'driver': __name__ + ':Digitizer', 'config': {'overrange': [2]}})
        run = self.client.submit([{'id': 'one', 'device': 'adc', 'operation': 'read', 'arguments': {'channel': 1}},
                                  {'id': 'two', 'device': 'adc', 'operation': 'read', 'arguments': {'channel': 2}}])
        result = run.result(5)
        self.assertNotIn('quality', result['one'])
        self.assertEqual(result['two']['quality']['detail'], 'status {"channel": 2} returned True')
        with self.assertRaisesRegex(IOPError, 'Operation read quality: missing must be an operation of this instrument'):
            self.client.register({'id': 'adc2', 'driver': __name__ + ':Broken', 'config': {}})

    def test_a_quality_belongs_to_a_query(self):
        with self.assertRaisesRegex(IOPError, 'quality qualifies a reading; it belongs to a query'):
            self.client.register({'id': 'bad', 'driver': 'scpi', 'config': {
                'transport': 'tcp', 'host': '127.0.0.1', 'port': 9, 'timeout': 0.2, 'operations': {
                    'arm': {'write': 'INIT', 'parameters': {}, 'quality': {'query': 'STAT:QUES:COND?', 'bits': 1}}}}})
        with self.assertRaisesRegex(IOPError, 'quality is'):
            self.client.register({'id': 'bad', 'driver': 'scpi', 'config': {
                'transport': 'tcp', 'host': '127.0.0.1', 'port': 9, 'timeout': 0.2, 'operations': {
                    'v': {'query': 'MEAS:VOLT?', 'parameters': {}, 'quality': {'query': 'STAT:QUES:COND?'}}}}})


if __name__ == '__main__':
    unittest.main()

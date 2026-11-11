"""Text command protocols that are not SCPI: terminators, echo, acknowledgments, error replies, patterns."""
import socketserver
import threading
import time
import unittest
from ai4instruments.iop import IOPError
from ai4instruments.iop.drivers import make_driver
from ai4instruments.iop.identification import validate_profile
from ai4instruments.iop.text_protocol import reader
from . import test_service as fixture


class Controller(socketserver.StreamRequestHandler):
    """A motion and laser controller in the style of vendor ASCII protocols.

    Commands end with CR and replies with CR LF. Queries answer with the command as a prefix,
    settings with OK or ERR <code>, and a move takes 0.3 s while the state reads 28 instead of 33.
    """
    def handle(self):
        server = self.server
        while True:
            line = bytearray()
            while not line.endswith(b'\r'):
                byte = self.rfile.read(1)
                if not byte:
                    return
                line += byte
            server.gaps.append(time.monotonic())
            command = line.decode().rstrip('\r')
            if server.echo:
                self.send(command)
            if command.startswith('1PA'):
                server.target, server.arrival = float(command[3:]), time.monotonic() + 0.3
            elif command == '1TP':
                self.send('1TP' + format(server.target if time.monotonic() >= server.arrival else 0.0, '.4f'))
            elif command == '1TS':
                self.send('1TS0000' + ('33' if time.monotonic() >= server.arrival else '28'))
            elif command == 'l?':
                self.send('ON' if server.laser else 'OFF')
            elif command in ('l1', 'l0'):
                server.laser = command == 'l1'
                self.send('OK')
            elif command.startswith('p '):
                power = float(command[2:])
                if power > 100:
                    self.send('ERR 3')
                else:
                    server.counts = round(power * 40)
                    self.send('OK')
            elif command == 'p?':
                self.send(str(server.counts))
            else:
                self.send('ERR 1')

    def send(self, text):
        self.wfile.write((text + '\r\n').encode())
        self.wfile.flush()


PROTOCOL = {'write_termination': '\r', 'read_termination': '\r\n', 'identify': False, 'error_reply': '^ERR'}
OPERATIONS = {
    'move': {'write': '1PA{mm}', 'parameters': {'mm': {'type': 'number', 'minimum': 0, 'maximum': 25, 'unit': 'mm'}},
             'completion': {'query': '1TS', 'match': '(32|33|34)$', 'interval': 0.02, 'timeout': 5}},
    'position': {'query': '1TP', 'effect': 'observe', 'result': {'type': 'number', 'match': '^1TP(.+)$', 'unit': 'mm'}},
    'laser_on': {'query': 'l?', 'effect': 'observe', 'result': {'map': {'ON': True, 'OFF': False}}},
    'enable_laser': {'write': 'l1', 'reply': {'match': '^OK$'}},
    'set_power': {'write': 'p {mw}', 'parameters': {'mw': {'type': 'number', 'minimum': 0, 'unit': 'mW'}}, 'reply': True},
    'power': {'query': 'p?', 'effect': 'observe', 'result': {'type': 'integer', 'scale': 0.025, 'unit': 'mW'}},
}


class TextProtocolCase(unittest.TestCase):
    setUp_service = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def setUp(self):
        self.instrument = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Controller)
        self.instrument.daemon_threads = True
        self.instrument.target, self.instrument.arrival, self.instrument.laser = 0.0, 0.0, False
        self.instrument.counts, self.instrument.echo, self.instrument.gaps = 0, False, []
        threading.Thread(target=self.instrument.serve_forever, daemon=True).start()
        self.addCleanup(self.instrument.server_close)
        self.addCleanup(self.instrument.shutdown)
        self.setUp_service()

    def register(self, protocol=PROTOCOL, **config):
        return self.client.register({'id': 'bench', 'driver': 'scpi', 'config': {
            'transport': 'tcp', 'host': '127.0.0.1', 'port': self.instrument.server_address[1], 'timeout': 2,
            'protocol': protocol, 'operations': OPERATIONS, **config}})

    def run_steps(self, *steps):
        return self.client.submit([{'id': name, 'device': 'bench', 'operation': operation, 'arguments': arguments}
                                   for name, operation, arguments in steps])

    def test_replies_are_read_with_patterns_scales_and_maps(self):
        description = self.register()
        self.assertNotIn('identify', description['operations'])
        with self.assertRaisesRegex(IOPError, 'no identification query'):
            self.client.request('/v1/devices/bench/identify', {})
        started = time.monotonic()
        result = self.run_steps(('move', 'move', {'mm': 12.5}), ('where', 'position', {}),
                                ('enable', 'enable_laser', {}), ('state', 'laser_on', {}),
                                ('set', 'set_power', {'mw': 50}), ('read', 'power', {})).result(10)
        self.assertGreaterEqual(time.monotonic() - started, 0.3)
        self.assertEqual(result['move']['completion_response'], '1TS000033')
        self.assertEqual((result['where']['value'], result['where']['unit']), (12.5, 'mm'))
        self.assertEqual(result['enable']['reply'], 'OK')
        self.assertIs(result['state']['value'], True)
        self.assertEqual((result['set']['reply'], result['read']['value']), ('OK', 50.0))

    def test_an_error_reply_fails_the_operation_without_holding_the_device(self):
        self.register(unconfirmed_change='hold')
        run = self.run_steps(('set', 'set_power', {'mw': 500}))
        self.assertEqual(run.wait(10)['state'], 'failed')
        operation = run.info()['operation_records'][0]
        self.assertIn("answered 'p 500' with 'ERR 3'", operation['error'])
        self.assertNotIn('outcome_unknown', operation)
        self.assertFalse(self.engine.resource_holds)
        # The refused command consumed its reply; the next query reads its own.
        self.assertEqual(self.client.invoke('bench', 'power')['value'], 0.0)
        refused = self.run_steps(('raw', 'query', {'command': 'bogus'}))
        self.assertEqual(refused.wait(10)['state'], 'failed')

    def test_echoed_commands_and_command_pacing(self):
        self.instrument.echo = True
        self.register({**PROTOCOL, 'echo': True, 'min_interval': 0.05})
        result = self.run_steps(('a', 'position', {}), ('b', 'position', {}), ('c', 'laser_on', {})).result(10)
        self.assertEqual((result['a']['value'], result['c']['value']), (0.0, False))
        gaps = [later - earlier for earlier, later in zip(self.instrument.gaps, self.instrument.gaps[1:])]
        self.assertGreaterEqual(min(gaps), 0.045)

    def test_acknowledged_writes_keep_replies_in_step(self):
        self.register({**PROTOCOL, 'write_reply': True}, operations={
            'laser_on': OPERATIONS['laser_on'], 'power': OPERATIONS['power'],
            'set_power': {'write': 'p {mw}', 'parameters': {'mw': {'type': 'number'}}}})
        result = self.run_steps(('raw', 'write', {'command': 'l1'}), ('state', 'laser_on', {}),
                                ('set', 'set_power', {'mw': 20}), ('read', 'power', {})).result(10)
        self.assertEqual((result['raw']['reply'], result['state']['value'], result['read']['value']), ('OK', True, 20.0))

    def test_a_missing_echo_is_reported(self):
        self.register({**PROTOCOL, 'echo': True})
        run = self.run_steps(('a', 'position', {}))
        self.assertEqual(run.wait(10)['state'], 'failed')
        self.assertIn('Expected the echo', run.info()['operation_records'][0]['error'])


class DescriptionCase(unittest.TestCase):
    def test_reply_descriptions_are_checked_when_saved(self):
        self.assertEqual(reader({'type': 'number', 'match': 'T=([-0-9.]+)', 'scale': 0.1, 'offset': -273.15})('T=2931.5K'),
                         2931.5 * 0.1 - 273.15)
        self.assertIs(reader({'type': 'boolean'})(' off '), False)
        with self.assertRaisesRegex(ValueError, 'is not a number'):
            reader({'type': 'number'})('ERR')
        base = {'driver': 'scpi', 'config': {'transport': 'tcp', 'host': '192.0.2.1'}}
        for change, message in (({'protocol': {'error_reply': '('}}, 'error_reply'),
                                ({'protocol': {'terminator': '\r'}}, 'Unknown protocol fields'),
                                ({'operations': {'x': {'query': 'x?', 'result': {'match': '['}}}}, 'Operation x: result.match'),
                                ({'operations': {'x': {'query': 'x?', 'reply': True}}}, 'reply belongs to a write'),
                                ({'operations': {'x': {'query': 'x?', 'result': {'type': 'string', 'scale': 2}}}}, 'scale')):
            with self.assertRaisesRegex(ValueError, message):
                make_driver({**base, 'id': 'x', 'config': {**base['config'], **change}}, {})

    def test_model_descriptions_carry_protocol_traits(self):
        profile = {'id': 'vendor-stage', 'match': {'manufacturer': 'Vendor', 'model': 'Stage'}, 'driver': 'scpi',
                   'config': {'protocol': PROTOCOL, 'operations': OPERATIONS}}
        validate_profile(profile)
        from ai4instruments.iop.identification import apply_profile
        record = apply_profile({'id': 's', 'instrument_profile': 'vendor-stage', 'config': {
            'transport': 'tcp', 'host': '192.0.2.1', 'protocol': {'min_interval': 0.1}}}, [profile])
        self.assertEqual(record['config']['protocol'], {**PROTOCOL, 'min_interval': 0.1})
        with self.assertRaisesRegex(ValueError, 'scpi driver'):
            validate_profile({**profile, 'driver': 'http', 'config': {'protocol': PROTOCOL,
                             'operations': {'read': {'path': '/read'}}}})


if __name__ == '__main__':
    unittest.main()

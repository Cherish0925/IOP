"""A sent SCPI change command whose completion is not confirmed can keep its device held."""
import socket
import socketserver
import threading
import time
import unittest
from ai4instruments.iop.drivers import SCPI, UnconfirmedChange
from . import test_service as fixture


def instrument(answer_completion):
    commands = []
    class Instrument(socketserver.StreamRequestHandler):
        def handle(self):
            while True:
                line = self.rfile.readline()
                if not line:
                    return
                command = line.decode().strip(); commands.append(command)
                if command == '*OPC?' and answer_completion:
                    self.wfile.write(b'1\n')
                elif command == 'VOLT?':
                    self.wfile.write(b'3.000\n')
    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Instrument); server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, commands


def supply(port, mode=None):
    config = {'host': '127.0.0.1', 'port': port, 'timeout': 0.3, 'operations': {
        'set_voltage': {'parameters': {'volts': {'type': 'number', 'minimum': 0, 'maximum': 30}},
                        'write': 'VOLT {volts}', 'completion': {'query': '*OPC?', 'timeout': 0.3}},
        'read_voltage': {'parameters': {}, 'query': 'VOLT?', 'effect': 'observe',
                         'result': {'type': 'number', 'unit': 'V'}},
        'read_slow': {'parameters': {}, 'query': 'MEAS?', 'effect': 'observe'}}}
    if mode:
        config['unconfirmed_change'] = mode
    return config


class DriverCase(unittest.TestCase):
    def test_sent_change_without_completion_is_unconfirmed_only_when_requested(self):
        server, commands = instrument(answer_completion=False)
        held = SCPI('psu', supply(server.server_address[1], 'hold'), {})
        failed = SCPI('psu2', supply(server.server_address[1]), {})
        try:
            with self.assertRaises(UnconfirmedChange) as raised:
                held.invoke('set_voltage', {'volts': 3}, threading.Event())
            self.assertIn("'VOLT 3' was sent", str(raised.exception))
            with self.assertRaises(TimeoutError):
                failed.invoke('set_voltage', {'volts': 3}, threading.Event())
            self.assertEqual(commands.count('VOLT 3'), 2)
        finally:
            held.close(); failed.close(); server.shutdown(); server.server_close()

    def test_observation_timeout_and_unsent_command_remain_ordinary_failures(self):
        server, _ = instrument(answer_completion=True)
        driver = SCPI('psu', supply(server.server_address[1], 'hold'), {})
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); closed = probe.getsockname()[1]
        unreachable = SCPI('psu', supply(closed, 'hold'), {})
        try:
            with self.assertRaises(TimeoutError):
                driver.invoke('read_slow', {}, threading.Event())
            self.assertEqual(driver.invoke('set_voltage', {'volts': 2}, threading.Event())['completion_response'], '1')
            with self.assertRaises(OSError) as raised:
                unreachable.invoke('set_voltage', {'volts': 2}, threading.Event())
            self.assertNotIsInstance(raised.exception, UnconfirmedChange)
        finally:
            driver.close(); unreachable.close(); server.shutdown(); server.server_close()

    def test_mode_is_validated_and_described(self):
        with self.assertRaises(ValueError):
            SCPI('psu', supply(5025, 'retry'), {})
        self.assertEqual(SCPI('psu', supply(5025, 'hold'), {}).describe()['connection']['unconfirmed_change'], 'hold')


class RuntimeCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def finished(self, run_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            run = self.engine.store.get('runs', run_id)
            if run['state'] in ('completed', 'failed', 'cancelled', 'interrupted'):
                return run
            time.sleep(0.02)
        self.fail('Run did not finish')

    def test_unconfirmed_change_holds_the_device_until_an_observation_is_recorded(self):
        server, commands = instrument(answer_completion=False)
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.engine.register({'id': 'psu', 'driver': 'scpi', 'config': supply(server.server_address[1], 'hold')})
        change = self.engine.submit({'steps': [{'id': 'set', 'device': 'psu', 'operation': 'set_voltage',
                                                'arguments': {'volts': 3}}]})
        self.assertEqual(self.finished(change['id'])['state'], 'failed')
        op = self.engine.store.get('operations', self.engine.store.get('runs', change['id'])['operations'][0])
        self.assertTrue(op['outcome_unknown'])
        self.assertIn('UnconfirmedChange', op['error'])
        self.assertIn('device:psu', self.engine.resource_holds)
        self.assertNotIn('psu', self.engine.offline)

        blocked = self.engine.submit({'steps': [{'id': 'again', 'device': 'psu', 'operation': 'set_voltage',
                                                 'arguments': {'volts': 3}}]})
        self.assertEqual(self.finished(blocked['id'])['state'], 'interrupted')
        self.assertEqual(commands.count('VOLT 3'), 1)

        read = self.engine.submit({'steps': [{'id': 'read', 'device': 'psu', 'operation': 'read_voltage'}]})
        self.assertEqual(self.finished(read['id'])['result']['read']['value'], 3.0)
        self.engine.resolve_resources(['device:psu'], 'Read back 3.000 V')
        self.assertFalse(self.engine.resource_holds)

    def test_default_mode_keeps_the_existing_failure(self):
        server, _ = instrument(answer_completion=False)
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.engine.register({'id': 'psu', 'driver': 'scpi', 'config': supply(server.server_address[1])})
        run = self.engine.submit({'steps': [{'id': 'set', 'device': 'psu', 'operation': 'set_voltage',
                                             'arguments': {'volts': 3}}]})
        self.assertEqual(self.finished(run['id'])['state'], 'failed')
        op = self.engine.store.get('operations', self.engine.store.get('runs', run['id'])['operations'][0])
        self.assertNotIn('outcome_unknown', op)
        self.assertFalse(self.engine.resource_holds)


if __name__ == '__main__':
    unittest.main()

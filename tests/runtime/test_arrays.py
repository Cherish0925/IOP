"""Binary blocks and numerical arrays: SCPI reads and writes, shared description and saved files."""
import hashlib
import importlib.util
import socketserver
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from ai4instruments.iop import arrays
from ai4instruments.iop.drivers import SCPI
from . import test_service as fixture

# A float32 whose little-endian bytes contain the newline character used as terminator.
NEWLINE_FLOAT = struct.unpack('<f', b'\n\n\x80\x3f')[0]
TRACE = [NEWLINE_FLOAT, -2.0, 3.25, 0.5, -0.125, 7.0]


class Instrument(socketserver.BaseRequestHandler):
    """Reads commands byte by byte so binary blocks containing newlines arrive intact."""
    def handle(self):
        server, buffer = self.server, bytearray()
        while True:
            chunk = self.request.recv(65536)
            if not chunk:
                return
            buffer.extend(chunk)
            while True:
                newline, mark = buffer.find(b'\n'), buffer.find(b'#')
                if mark >= 0 and (newline < 0 or mark < newline):
                    parsed = arrays.parse_block(buffer[mark:])
                    if parsed is None or len(buffer) < mark + parsed[1] + 1:
                        break
                    server.writes.append((bytes(buffer[:mark]).decode(), parsed[0]))
                    del buffer[:mark + parsed[1] + 1]
                    continue
                if newline < 0:
                    break
                command = bytes(buffer[:newline]).decode().strip(); del buffer[:newline + 1]
                server.commands.append(command)
                reply = server.replies.get(command)
                if reply is not None:
                    self.request.sendall(reply)


def start(replies):
    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Instrument); server.daemon_threads = True
    server.replies, server.commands, server.writes = replies, [], []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def replies(points=TRACE):
    payload = struct.pack('<%df' % len(points), *points)
    return {'TRAC:DATA?': arrays.block(payload) + b'\n', 'TRAC:ASC?': b'1.5, 2.5,-3\n', '*OPC?': b'1\n',
            'TRAC:BIG?': b'  ' + arrays.block(struct.pack('<2048f', *range(2048))) + b'\n',
            'TRAC:ERR?': b'-113,"Undefined header"\n', 'TRAC:OPEN?': b'#0\x01\x02\n'}


def operations():
    return {
        'trace': {'parameters': {}, 'query': 'TRAC:DATA?', 'effect': 'observe',
                  'result': {'type': 'array', 'dtype': 'float32', 'byte_order': 'little', 'shape': [-1, 2], 'unit': 'V',
                             'axes': [{'name': 'point'}, {'name': 'component', 'labels': ['re', 'im']}]}},
        'ascii': {'parameters': {}, 'query': 'TRAC:ASC?', 'result': {'type': 'array', 'encoding': 'ascii', 'unit': 'dB'}},
        'big': {'parameters': {}, 'query': 'TRAC:BIG?', 'effect': 'observe',
                'result': {'type': 'array', 'dtype': 'float32', 'file': 'big/trace.f32'}},
        'error': {'parameters': {}, 'query': 'TRAC:ERR?', 'result': {'type': 'array', 'dtype': 'uint8'}},
        'open': {'parameters': {}, 'query': 'TRAC:OPEN?', 'result': {'type': 'array', 'dtype': 'uint8'}},
        'load': {'parameters': {'name': {'type': 'string'}, 'samples': {'type': 'array', 'items': {'type': 'number'}}},
                 'write': 'DATA:ARB {name},', 'data': {'parameter': 'samples', 'dtype': 'int16', 'byte_order': 'big'},
                 'completion': {'query': '*OPC?'}}}


def scpi(port, **extra):
    return SCPI('scope', {'host': '127.0.0.1', 'port': port, 'timeout': 1, 'operations': operations(), **extra}, {})


class FormatCase(unittest.TestCase):
    def test_block_round_trip_and_partial_input(self):
        payload = bytes(range(256)) * 50
        framed = arrays.block(payload)
        self.assertEqual(framed[:7], b'#512800')
        self.assertIsNone(arrays.parse_block(framed[:-1]))
        self.assertEqual(arrays.parse_block(b' \r\n' + framed + b'\n'), (payload, len(framed) + 3))
        self.assertEqual(arrays.decode(arrays.encode([1, -2, 300], 'int16', 'big'), 'int16', 'big'), [1, -2, 300])
        self.assertEqual(arrays.encode([1], 'uint16', 'big'), b'\x00\x01')

    def test_descriptions_are_checked(self):
        with self.assertRaisesRegex(ValueError, 'dtype'):
            arrays.check_result({'type': 'array'})
        with self.assertRaisesRegex(ValueError, 'one axis for each'):
            arrays.check_result({'dtype': 'float32', 'shape': [-1, 2], 'axes': [{'name': 'point'}]})
        with self.assertRaisesRegex(ValueError, 'at most one -1'):
            arrays.check_result({'dtype': 'float32', 'shape': [-1, -1]})
        with self.assertRaisesRegex(ValueError, 'outside the int16 range'):
            arrays.encode([40000], 'int16')
        with self.assertRaisesRegex(ValueError, 'finite integers'):
            arrays.encode([1.5], 'int16')
        with self.assertRaisesRegex(ValueError, 'do not fit shape'):
            arrays.resolve_shape(5, [-1, 2])

    def test_driver_rejects_inconsistent_operations_at_registration(self):
        bad = operations(); bad['load']['data']['parameter'] = 'name'
        with self.assertRaisesRegex(ValueError, 'Operation load: data.parameter'):
            SCPI('scope', {'host': 'h', 'operations': bad}, {})
        bad = operations(); bad['trace']['result'].pop('dtype')
        with self.assertRaisesRegex(ValueError, 'Operation trace: dtype'):
            SCPI('scope', {'host': 'h', 'operations': bad}, {})


class TransportMixin:
    def driver(self, server):
        raise NotImplementedError

    def setUp(self):
        self.server = start(replies())
        self.scope = self.driver(self.server)

    def tearDown(self):
        self.scope.close(); self.server.shutdown(); self.server.server_close()

    def test_block_query_keeps_binary_bytes_and_describes_the_array(self):
        result = self.scope.invoke('trace', {}, threading.Event())
        self.assertEqual(result['data']['shape'], [3, 2])
        self.assertEqual(result['data']['unit'], 'V')
        self.assertEqual(result['data']['axes'][1]['labels'], ['re', 'im'])
        self.assertEqual(result['values'], [[NEWLINE_FLOAT, -2.0], [3.25, 0.5], [-0.125, 7.0]])
        # The connection stays aligned: the next text query reads its own reply.
        self.assertEqual(self.scope.invoke('ascii', {}, threading.Event())['values'], [1.5, 2.5, -3.0])

    def test_binary_write_sends_one_block_after_the_command(self):
        result = self.scope.invoke('load', {'name': 'ramp', 'samples': [0, 1, -1, 10]}, threading.Event())
        self.assertEqual(result['data_bytes'], 8)
        self.assertEqual(result['completion_response'], '1')
        self.assertEqual(self.server.writes, [('DATA:ARB ramp,', struct.pack('>4h', 0, 1, -1, 10))])

    def test_instrument_errors_are_reported(self):
        with self.assertRaisesRegex(ValueError, 'Undefined header'):
            self.scope.invoke('error', {}, threading.Event())
        self.assertEqual(self.scope.invoke('ascii', {}, threading.Event())['values'], [1.5, 2.5, -3.0])


class TCPCase(TransportMixin, unittest.TestCase):
    def driver(self, server):
        return scpi(server.server_address[1])

    def test_indefinite_blocks_are_refused_with_the_reason(self):
        with self.assertRaisesRegex(ValueError, 'definite-length'):
            self.scope.invoke('open', {}, threading.Event())

    def test_saved_file_and_inline_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            from ai4instruments.iop.execution import Context
            context = Context('run', 'op', Path(folder), lambda _: None)
            big = self.scope.invoke('big', {}, context)
            small = self.scope.invoke('trace', {}, context)
            payload = (Path(folder) / 'big' / 'trace.f32').read_bytes()
            self.assertEqual(big['data']['sha256'], hashlib.sha256(payload).hexdigest())
            self.assertEqual((big['data']['count'], big['data']['file']), (2048, 'big/trace.f32'))
            self.assertNotIn('values', big)
            self.assertEqual((Path(folder) / 'trace.bin').stat().st_size, 24)
            self.assertIn('values', small)


@unittest.skipUnless(importlib.util.find_spec('pyvisa_py'), 'Install the devices extra for pyvisa-py')
class VISACase(TransportMixin, unittest.TestCase):
    def driver(self, server):
        return SCPI('scope', {'transport': 'visa', 'backend': '@py', 'timeout': 2, 'operations': operations(),
                              'resource': 'TCPIP::127.0.0.1::%d::SOCKET' % server.server_address[1]}, {})


class RuntimeCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def test_operation_files_are_available_before_the_experiment_finishes(self):
        server = start(replies())
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.client.register({'id': 'scope', 'driver': 'scpi',
                              'config': {'host': '127.0.0.1', 'port': server.server_address[1], 'operations': operations()}})
        with self.client.start_experiment(resources=['scope']) as run:
            op = run.invoke_record('scope', 'big')
            self.assertEqual([f['name'] for f in op['files']], ['output/operations/%s/big/trace.f32' % op['id']])
            listed = {f['id']: f for f in run.files()}
            self.assertEqual(listed[op['files'][0]['id']]['operation_ids'], [op['id']])
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / 'trace.f32'
                run.download(op['files'][0]['id'], target)
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), op['result']['data']['sha256'])
            self.assertNotIn('values', op['result'])
        self.assertEqual(len([f for f in run.files() if f['name'].endswith('trace.f32')]), 1)


if __name__ == '__main__':
    unittest.main()

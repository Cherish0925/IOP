"""OPC UA servers: operations, ranges and units come from the address space."""
import importlib.util
import socket
import unittest
from unittest.mock import patch
from . import test_service as fixture

available = importlib.util.find_spec('asyncua') is not None


def opcua_server():
    """A server with a reactor (typed variables, a method, an array) and a device with identification."""
    from asyncua import ua
    from asyncua.sync import Server, ThreadLoop
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    loop = ThreadLoop()
    loop.daemon = True
    loop.start()
    server = Server(tloop=loop)
    server.set_endpoint(f'opc.tcp://127.0.0.1:{port}/iop/')
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    index = server.register_namespace('urn:iop:case')
    reactor = server.nodes.objects.add_object(index, 'Reactor')
    reactor.add_variable(index, 'Temperature', 21.5).write_attribute(
        ua.AttributeIds.Description, ua.DataValue(ua.Variant(ua.LocalizedText('Jacket temperature'))))
    setpoint = reactor.add_variable(index, 'Setpoint', ua.Variant(25.0, ua.VariantType.Float))
    setpoint.set_writable()
    setpoint.add_property(0, 'EURange', ua.Range(Low=0, High=150))
    setpoint.add_property(0, 'EngineeringUnits', ua.EUInformation(
        NamespaceUri='http://www.opcfoundation.org/UA/units/un/cefact', UnitId=4408652, DisplayName=ua.LocalizedText('°C')))
    mode = reactor.add_variable(index, 'Mode', ua.Variant(0, ua.VariantType.Int32))
    mode.set_writable()
    mode.add_property(0, 'EnumStrings', [ua.LocalizedText('Idle'), ua.LocalizedText('Heating')])
    reactor.add_variable(index, 'Spectrum', [float(n) for n in range(300)])
    reactor.add_method(index, 'Purge', lambda parent, seconds: [ua.Variant(seconds.Value > 0, ua.VariantType.Boolean)],
        [ua.Argument(Name='seconds', DataType=ua.NodeId(ua.ObjectIds.Double), ValueRank=-1,
                     Description=ua.LocalizedText('Purge time'))],
        [ua.Argument(Name='ok', DataType=ua.NodeId(ua.ObjectIds.Boolean), ValueRank=-1)])
    analyzer = server.nodes.objects.add_object(index, 'Analyzer')
    analyzer.add_property(index, 'Manufacturer', ua.LocalizedText('Example Instruments'))
    analyzer.add_property(index, 'Model', ua.LocalizedText('LC-100'))
    analyzer.add_property(index, 'SerialNumber', 'A-17')
    analyzer.add_variable(index, 'Pressure', 1.2)
    server.start()

    def stop():
        server.stop()
        loop.stop()
    return f'opc.tcp://127.0.0.1:{port}/iop/', {'reactor': reactor, 'setpoint': setpoint, 'analyzer': analyzer}, stop


@unittest.skipUnless(available, 'Install the opcua extra for OPC UA servers')
class OPCUACase(unittest.TestCase):
    setUp_service = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def setUp(self):
        self.endpoint, self.nodes, stop = opcua_server()
        self.addCleanup(stop)
        self.setUp_service()

    def register(self, node='reactor', **config):
        return self.client.register({'id': node, 'driver': 'opcua', 'config': {
            'endpoint': self.endpoint, 'nodes': [self.nodes[node].nodeid.to_string()], **config}})

    def test_variables_and_methods_become_described_operations(self):
        operations = self.register()['operations']
        self.assertEqual(operations['get.Reactor.Temperature'],
                         {'description': 'Jacket temperature', 'effect': 'observe', 'parameters': {}})
        self.assertNotIn('set.Reactor.Temperature', operations)
        self.assertEqual(operations['set.Reactor.Setpoint']['parameters']['value'],
                         {'type': 'number', 'minimum': 0, 'maximum': 150, 'unit': '°C'})
        self.assertEqual(operations['set.Reactor.Mode']['parameters']['value'],
                         {'type': 'integer', 'minimum': -2**31, 'maximum': 2**31 - 1, 'enum': [0, 1],
                          'description': '0: Idle, 1: Heating'})
        self.assertEqual(operations['call.Reactor.Purge']['parameters'],
                         {'seconds': {'type': 'number', 'description': 'Purge time'}})
        self.assertEqual(operations['snapshot']['effect'], 'observe')
        analyzer = self.register('analyzer')
        self.assertEqual(analyzer['identity'], {'manufacturer': 'Example Instruments', 'model': 'LC-100',
                                                'serial_number': 'A-17'})

    def test_operations_read_write_and_call_the_server(self):
        self.register()
        run = self.client.submit([
            {'id': 'set', 'device': 'reactor', 'operation': 'set.Reactor.Setpoint', 'arguments': {'value': 30}},
            {'id': 'read', 'device': 'reactor', 'operation': 'get.Reactor.Setpoint'},
            {'id': 'trace', 'device': 'reactor', 'operation': 'get.Reactor.Spectrum'},
            {'id': 'purge', 'device': 'reactor', 'operation': 'call.Reactor.Purge', 'arguments': {'seconds': 2}},
            {'id': 'all', 'device': 'reactor', 'operation': 'snapshot'}])
        result = run.result(10)
        self.assertEqual((result['read']['value'], result['read']['unit']), (30.0, '°C'))
        self.assertEqual(self.nodes['setpoint'].read_value(), 30.0)
        self.assertEqual((result['trace']['data']['count'], result['trace']['data']['dtype']), (300, 'float64'))
        self.assertIn('get-Reactor-Spectrum.bin', [f['name'].rsplit('/', 1)[-1] for f in run.files()])
        self.assertEqual(result['purge']['outputs'], {'ok': True})
        self.assertEqual(result['all']['variables']['Reactor.Temperature'], 21.5)
        self.assertNotIn('Reactor.Spectrum', result['all']['variables'])
        with self.assertRaisesRegex(Exception, 'outside'):
            self.client.submit([{'id': 'hot', 'device': 'reactor', 'operation': 'set.Reactor.Setpoint',
                                 'arguments': {'value': 200}}])
        self.assertEqual(self.nodes['setpoint'].read_value(), 30.0)

    def test_a_write_without_a_reply_holds_the_reactor(self):
        from asyncua.sync import SyncNode
        self.register(unconfirmed_change='hold')
        with patch.object(SyncNode, 'write_value', side_effect=TimeoutError('no reply')):
            run = self.client.submit([{'id': 'set', 'device': 'reactor', 'operation': 'set.Reactor.Setpoint',
                                       'arguments': {'value': 40}}])
            self.assertEqual(run.wait(10)['state'], 'failed')
        self.assertTrue(run.info()['operation_records'][0]['outcome_unknown'])
        self.assertIn('device:reactor', self.engine.resource_holds)
        self.client.resolve_resources(['device:reactor'], 'Setpoint reads 25 on the controller panel')
        self.assertEqual(self.client.invoke('reactor', 'get.Reactor.Setpoint')['value'], 25.0)

    def test_configuration_is_checked_before_connecting(self):
        from ai4instruments.iop.drivers import make_driver
        for config, message in (({}, 'endpoint'), ({'endpoint': self.endpoint, 'password': 'x'}, 'password_env'),
                                ({'endpoint': self.endpoint, 'depth': 0}, 'depth')):
            with self.assertRaisesRegex(ValueError, message):
                make_driver({'id': 'x', 'driver': 'opcua', 'config': config}, {})


if __name__ == '__main__':
    unittest.main()

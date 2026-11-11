"""EPICS Channel Access: PV metadata becomes operations and puts wait for completion."""
import importlib.util
import logging
import os
import socket
import threading
import time
import unittest
from unittest.mock import patch
from . import test_service as fixture

available = importlib.util.find_spec('caproto') is not None


def setUpModule():
    if not available:
        return
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    os.environ.update(EPICS_CA_SERVER_PORT=str(port), EPICS_CA_ADDR_LIST='127.0.0.1', EPICS_CA_AUTO_ADDR_LIST='NO',
                      EPICS_CAS_INTF_ADDR_LIST='127.0.0.1', EPICS_CAS_BEACON_ADDR_LIST='127.0.0.1',
                      EPICS_CAS_AUTO_BEACON_ADDR_LIST='NO')
    logging.getLogger('caproto').setLevel(logging.CRITICAL)
    import asyncio
    from caproto import ChannelType
    from caproto.server import PVGroup, pvproperty, run

    class Bench(PVGroup):
        position = pvproperty(value=0.0, name='BL:MOT:VAL', units='mm', lower_ctrl_limit=-10.0, upper_ctrl_limit=10.0)
        readback = pvproperty(value=0.0, name='BL:MOT:RBV', units='mm', read_only=True)
        shutter = pvproperty(value='Closed', name='BL:SHUTTER', enum_strings=['Closed', 'Open'], dtype=ChannelType.ENUM)
        spectrum = pvproperty(value=[float(n) for n in range(500)], name='BL:DET:SPECTRUM', read_only=True)
        label = pvproperty(value='sample A', name='BL:DET:LABEL', dtype=ChannelType.STRING)

        @position.putter
        async def position(self, instance, value):
            await asyncio.sleep(0.3)
            await self.readback.write(value)
            return value
    threading.Thread(target=run, args=(Bench(prefix='').pvdb,), daemon=True,
                     kwargs={'interfaces': ['127.0.0.1'], 'log_pv_names': False}).start()
    time.sleep(0.3)


@unittest.skipUnless(available, 'Install the epics extra for Channel Access')
class EPICSCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def register(self, **config):
        return self.client.register({'id': 'beamline', 'driver': 'epics', 'config': {
            'prefix': 'BL:', 'pvs': ['MOT:VAL', 'MOT:RBV', 'SHUTTER', 'DET:SPECTRUM', 'DET:LABEL'], **config}})

    def test_pv_metadata_becomes_described_operations(self):
        operations = self.register()['operations']
        self.assertEqual(operations['set.MOT:VAL']['parameters']['value'],
                         {'type': 'number', 'minimum': -10.0, 'maximum': 10.0, 'unit': 'mm'})
        self.assertNotIn('set.MOT:RBV', operations)
        self.assertEqual(operations['set.SHUTTER']['parameters']['value'],
                         {'type': 'integer', 'enum': [0, 1], 'description': '0: Closed, 1: Open'})
        self.assertEqual(operations['get.MOT:RBV'], {'effect': 'observe', 'parameters': {}, 'result': {'unit': 'mm'}})

    def test_puts_wait_for_completion_and_reads_carry_metadata(self):
        self.register()
        started = time.monotonic()
        run = self.client.submit([
            {'id': 'move', 'device': 'beamline', 'operation': 'set.MOT:VAL', 'arguments': {'value': 2.5}},
            {'id': 'read', 'device': 'beamline', 'operation': 'get.MOT:RBV'},
            {'id': 'open', 'device': 'beamline', 'operation': 'set.SHUTTER', 'arguments': {'value': 1}},
            {'id': 'state', 'device': 'beamline', 'operation': 'get.SHUTTER'},
            {'id': 'trace', 'device': 'beamline', 'operation': 'get.DET:SPECTRUM'},
            {'id': 'all', 'device': 'beamline', 'operation': 'snapshot'}])
        result = run.result(10)
        self.assertGreaterEqual(time.monotonic() - started, 0.3)
        self.assertEqual((result['read']['value'], result['read']['unit']), (2.5, 'mm'))
        self.assertEqual((result['state']['value'], result['state']['label']), (1, 'Open'))
        self.assertEqual((result['trace']['data']['count'], result['trace']['data']['dtype']), (500, 'float64'))
        self.assertEqual(result['all']['variables']['DET:LABEL'], 'sample A')
        with self.assertRaisesRegex(Exception, 'outside'):
            self.client.submit([{'id': 'far', 'device': 'beamline', 'operation': 'set.MOT:VAL', 'arguments': {'value': 50}}])

    def test_a_put_without_completion_holds_the_device(self):
        self.register(unconfirmed_change='hold', put_timeout=0.1)
        run = self.client.submit([{'id': 'move', 'device': 'beamline', 'operation': 'set.MOT:VAL', 'arguments': {'value': 4}}])
        self.assertEqual(run.wait(10)['state'], 'failed')
        self.assertTrue(run.info()['operation_records'][0]['outcome_unknown'])
        self.assertIn('device:beamline', self.engine.resource_holds)

    def test_missing_pvs_are_named(self):
        with self.assertRaisesRegex(Exception, 'No IOC answered for BL:NOPE'):
            self.client.register({'id': 'gone', 'driver': 'epics', 'config': {'pvs': ['BL:NOPE'], 'timeout': 0.5}})


if __name__ == '__main__':
    unittest.main()

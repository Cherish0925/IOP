"""Python library objects: operations bound to methods and attributes by a description."""
import collections
import enum
import importlib.util
import threading
import time
import unittest
from ai4instruments.iop.drivers import make_driver
from . import test_service as fixture

numpy_available = importlib.util.find_spec('numpy') is not None
created = []
Status = collections.namedtuple('Status', 'state temperature')


class State(enum.Enum):
    READY = 1


class Channel:
    voltage = 0.0


class Bench:
    """Stands in for a vendor SDK object: a stage that moves in the background, a camera and outputs."""
    def __init__(self, serial, scale=1.0):
        self.serial, self.scale, self.target, self.arrival = serial, scale, 0.0, 0.0
        self.exposure, self.stopped, self.closed = 0.01, False, False
        self.channels = [Channel(), Channel()]
        self.threads = {threading.get_ident()}
        created.append(self)

    def move_to(self, position, velocity=1.0, *, relative=False):
        self.threads.add(threading.get_ident())
        self.target, self.velocity, self.relative = position * self.scale, velocity, relative
        self.arrival = time.monotonic() + 0.2

    def is_moving(self):
        self.threads.add(threading.get_ident())
        return time.monotonic() < self.arrival

    def get_position(self):
        return self.target if not self.is_moving() else 0.0

    def stop(self):
        self.stopped, self.arrival = True, 0.0

    def status(self):
        return Status(State.READY, 21.5)

    def snap(self):
        import numpy
        return numpy.arange(480 * 640, dtype=numpy.uint16).reshape(480, 640)

    def spectrum(self):
        import numpy
        return numpy.linspace(400, 800, 512), numpy.ones(512, dtype=numpy.float32)

    def fault(self):
        raise RuntimeError('stage fault')

    def reject(self, value):
        raise ValueError('value not accepted')

    def close(self):
        self.closed = True


OPERATIONS = {
    'move': {'call': 'move_to', 'parameters': {'mm': {'type': 'number', 'minimum': 0, 'maximum': 25, 'unit': 'mm'},
                                               'velocity': {'type': 'number', 'default': 2.0}},
             'positional': ['mm'], 'fixed': {'relative': False},
             'completion': {'call': 'is_moving', 'equals': False, 'interval': 0.02, 'timeout': 5},
             'on_cancel': {'call': 'stop'}},
    'slow_move': {'call': 'move_to', 'parameters': {'position': {'type': 'number'}},
                  'completion': {'call': 'is_moving', 'equals': False, 'interval': 0.02, 'timeout': 0.05}},
    'position': {'call': 'get_position', 'effect': 'observe', 'result': {'unit': 'mm'}},
    'exposure': {'get': 'exposure', 'result': {'unit': 's'}},
    'set_exposure': {'set': 'exposure', 'parameters': {'seconds': {'type': 'number', 'minimum': 0.001, 'maximum': 10}}},
    'set_voltage': {'set': 'channels.1.voltage', 'parameters': {'volts': {'type': 'number'}}},
    'voltage': {'get': 'channels.1.voltage'},
    'status': {'call': 'status', 'effect': 'observe'},
    'image': {'call': 'snap', 'effect': 'observe', 'result': {'unit': 'counts', 'axes': [{'name': 'row'}, {'name': 'column'}]}},
    'spectrum': {'call': 'spectrum', 'effect': 'observe', 'result': {'items': {
        'wavelength': {'path': [0], 'unit': 'nm'}, 'intensity': {'path': [1], 'unit': 'counts'}}}},
    'fault': {'call': 'fault'},
    'reject': {'call': 'reject', 'parameters': {'value': {'type': 'number'}}},
}


def record(**config):
    return {'id': 'bench', 'driver': 'python', 'config': {'object': __name__ + ':Bench', 'arguments': ['SN-7'],
                                                           'keywords': {'scale': 2.0}, 'operations': OPERATIONS, **config}}


class PythonObjectCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def steps(self, *steps):
        return self.client.submit([{'id': name, 'device': 'bench', 'operation': operation, 'arguments': arguments}
                                   for name, operation, arguments in steps])

    def test_operations_call_methods_and_attributes_on_one_thread(self):
        description = self.client.register(record())
        self.assertEqual(description['operations']['exposure']['effect'], 'observe')
        started = time.monotonic()
        result = self.steps(('move', 'move', {'mm': 5}), ('where', 'position', {}), ('set', 'set_exposure', {'seconds': 0.5}),
                            ('read', 'exposure', {}), ('volts', 'set_voltage', {'volts': 1.5}), ('out', 'voltage', {}),
                            ('status', 'status', {})).result(10)
        self.assertGreaterEqual(time.monotonic() - started, 0.2)
        bench = created[-1]
        self.assertEqual((bench.serial, bench.velocity, bench.relative), ('SN-7', 2.0, False))
        self.assertEqual((result['where']['value'], result['where']['unit']), (10.0, 'mm'))
        self.assertEqual((result['read']['value'], result['read']['unit'], result['out']['value']), (0.5, 's', 1.5))
        self.assertEqual(result['status']['value'], {'state': 'READY', 'temperature': 21.5})
        self.assertEqual(len(bench.threads), 1)
        self.assertNotIn(threading.get_ident(), bench.threads)
        with self.assertRaisesRegex(Exception, 'outside'):
            self.steps(('far', 'move', {'mm': 100}))

    @unittest.skipUnless(numpy_available, 'Install NumPy for array results')
    def test_arrays_are_described_and_saved(self):
        self.client.register(record())
        run = self.steps(('image', 'image', {}), ('spectrum', 'spectrum', {}))
        result = run.result(10)
        image = result['image']['data']
        self.assertEqual((image['shape'], image['dtype'], image['unit']), ([480, 640], 'uint16', 'counts'))
        self.assertNotIn('values', result['image'])
        items = result['spectrum']['items']
        self.assertEqual((items['wavelength']['data']['unit'], items['intensity']['data']['dtype']), ('nm', 'float32'))
        self.assertEqual({f['name'].rsplit('/', 1)[-1] for f in run.files()} - {'experiment.json'},
                         {'image.bin', 'spectrum-wavelength.bin', 'spectrum-intensity.bin'})

    def test_cancellation_stops_the_motion_and_failures_follow_the_configuration(self):
        self.client.register(record(unconfirmed_change='hold'))
        bench = created[-1]
        bench.arrival = 0
        run = self.steps(('move', 'move', {'mm': 5}))
        deadline = time.monotonic() + 3
        while not bench.is_moving() and time.monotonic() < deadline:
            time.sleep(0.005)
        bench.arrival = time.monotonic() + 30
        run.cancel()
        self.assertEqual(run.wait(10)['state'], 'cancelled')
        self.assertTrue(bench.stopped)
        rejected = self.steps(('reject', 'reject', {'value': 1}))
        self.assertEqual(rejected.wait(10)['state'], 'failed')
        self.assertNotIn('outcome_unknown', rejected.info()['operation_records'][0])
        fault = self.steps(('fault', 'fault', {}))
        self.assertEqual(fault.wait(10)['state'], 'failed')
        self.assertTrue(fault.info()['operation_records'][0]['outcome_unknown'])
        self.assertIn('device:bench', self.engine.resource_holds)

    @unittest.skipUnless(importlib.util.find_spec('pymeasure'), 'Install PyMeasure for its instrument classes')
    def test_a_pymeasure_instrument_is_used_without_a_driver_class(self):
        self.client.register({'id': 'bench', 'driver': 'python', 'config': {
            'object': 'pymeasure.instruments.fakes:SwissArmyFake', 'close': 'shutdown', 'operations': {
                'set_output': {'set': 'output_voltage', 'parameters': {'volts': {'type': 'number', 'minimum': 0, 'maximum': 10, 'unit': 'V'}}},
                'output': {'get': 'output_voltage', 'result': {'unit': 'V'}},
                'frame': {'get': 'frame', 'result': {'unit': 'counts'}}}}})
        result = self.steps(('set', 'set_output', {'volts': 3.5}), ('read', 'output', {}), ('frame', 'frame', {})).result(10)
        self.assertEqual((result['read']['value'], result['read']['unit']), (3.5, 'V'))
        self.assertEqual((result['frame']['data']['shape'], result['frame']['data']['dtype']), ([1080, 1920], 'uint8'))

    def test_the_object_is_closed_with_the_device(self):
        driver = make_driver(record(), {})
        bench = created[-1]
        driver.close()
        self.assertTrue(bench.closed)
        with self.assertRaisesRegex(ConnectionError, 'closed'):
            driver.invoke('position', {}, threading.Event())

    def test_descriptions_are_checked(self):
        for change, message in (({'object': 'Bench'}, 'module:Class'),
                                ({'operations': {'x': {'call': 'a', 'get': 'b'}}}, 'exactly one of call, get or set'),
                                ({'operations': {'x': {'get': '_secret'}}}, 'without private parts'),
                                ({'operations': {'x': {'set': 'a', 'parameters': {}}}}, 'set takes exactly one'),
                                ({'operations': {'x': {'call': 'a', 'positional': ['n']}}}, 'positional'),
                                ({'operations': {'x': {'call': 'a', 'completion': {'equals': 1}}}}, 'completion')):
            with self.assertRaisesRegex(ValueError, message):
                make_driver(record(**change), {})


if __name__ == '__main__':
    unittest.main()

"""An instrument's own conditions: in its record or its model description, named <device>.<id>, used by platforms."""
import copy
import json
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from ai4instruments.iop import Client, IOPError
from ai4instruments.iop.conditions import instrument
from ai4instruments.iop.discovery import normalize
from ai4instruments.iop.drivers import Driver
from ai4instruments.iop.engine import Engine
from ai4instruments.iop.identification import validate_profile
from ai4instruments.iop.server import Server
from . import test_service as fixture

LASER = {}


class Laser(Driver):
    """A laser that has to warm up before its output power is stable, and a detector after it."""
    def describe(self):
        if self.config.get('part') == 'detector':
            return {'operations': {'read': {'effect': 'observe', 'parameters': {}}}}
        return {'operations': {
            'temperature': {'effect': 'observe', 'parameters': {}},
            'power': {'effect': 'observe', 'parameters': {}},
            'wait_warm': {'parameters': {'minutes': {'type': 'number', 'minimum': 0, 'maximum': 60}}},
            'set_current': {'parameters': {'ma': {'type': 'number', 'minimum': 0, 'maximum': 200}}}}}

    def invoke(self, operation, arguments, cancelled):
        LASER['log'].append(operation)
        if operation == 'wait_warm':
            LASER['temperature'] = 25.0
        elif operation == 'set_current':
            LASER['current'] = arguments['ma']
        elif operation == 'temperature':
            return {'value': LASER['temperature']}
        elif operation == 'power':
            return {'value': LASER['current'] * 0.01 if abs(LASER['temperature'] - 25.0) < 0.5 else None}
        elif operation == 'read':
            return {'value': LASER['current'] * 0.005}
        return {}


WARM = {'id': 'warm', 'kind': 'equilibrium', 'description': 'The diode has reached its set temperature',
        'verify': {'operation': 'temperature'}, 'accept': {'minimum': 24.5, 'maximum': 25.5},
        'correct': {'operation': 'wait_warm', 'arguments': {'minutes': 30}}}
POWER = {'id': 'power_ok', 'kind': 'operating_point', 'description': 'The output power is at its working value', 'after': ['warm'],
         'verify': {'operation': 'power'}, 'accept': {'minimum': 0.9, 'maximum': 1.1},
         'usable': {'minimum': 0}, 'correct': {'operation': 'set_current', 'arguments': {'ma': 100}}}
LINK = {'id': 'link.ok', 'kind': 'signal', 'description': 'The detector sees the laser', 'after': ['laser.power_ok'],
        'verify': {'device': 'detector', 'operation': 'read'}, 'accept': {'minimum': 0.4}}


def laser(conditions=(WARM, POWER), **fields):
    return {'id': 'laser', 'driver': __name__ + ':Laser', 'config': {}, 'conditions': copy.deepcopy(list(conditions)), **fields}


class InstrumentCase(unittest.TestCase):
    def setUp(self):
        fixture.ServiceCase.setUp(self)
        LASER.update(temperature=20.0, current=0.0, log=[])

    tearDown = fixture.ServiceCase.tearDown

    def test_experiment_upkeep_covers_multiple_reserved_instruments(self):
        self.engine.config['upkeep_interval_s'] = 3600
        for name in ('laser1', 'laser2'):
            self.client.register(laser((WARM,), id=name))
        self.client.configure_platform({'schema_version': 1, 'id': 'pair', 'devices': ['laser1', 'laser2']})
        for platform in (None, 'pair'):
            with self.subTest(platform=platform):
                LASER['temperature'] = 20.0
                self.client.invalidate(['laser1.warm', 'laser2.warm'], 'cooled down')
                run = self.client.start_experiment(resources=['laser1', 'laser2'], platform=platform,
                                                   requires=['laser1.warm', 'laser2.warm'], upkeep=True)
                self.assertEqual(run.invoke('laser1', 'temperature'), {'value': 25.0})
                self.assertEqual([self.client.condition(c)['state'] for c in ('laser1.warm', 'laser2.warm')],
                                 ['holds', 'holds'])
                run.finish()

    def test_package_upkeep_starts_after_its_reservation_is_acquired(self):
        self.engine.config['upkeep_interval_s'] = 3600
        self.client.register(laser((WARM,)))
        blocker = self.client.start_experiment(resources=['laser'])
        blocker.invoke('laser', 'temperature')
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'experiment.py').write_text(
                "from ai4instruments.iop import Client\n"
                "run = Client().current_run()\n"
                "run.save_result(run.invoke('laser', 'temperature'))\n", encoding='utf-8')
            run = self.client.submit_package(folder, resources=['laser'], requires=['laser.warm'], upkeep=True)
        with self.engine.lock:
            self.assertEqual(self.engine.runs[run.id]['state'], 'queued')
            self.assertFalse(self.engine.conditions.keep(self.engine.runs[run.id], ['laser.warm']))
            self.assertNotIn(run.id, self.engine.conditions.attempts)
        blocker.finish()
        self.assertEqual(run.result(12), {'value': 25.0})
        self.assertEqual(self.client.condition('laser.warm')['state'], 'holds')

    def test_new_requirements_wait_for_the_current_maintenance_before_acceptance(self):
        self.engine.config['upkeep_interval_s'] = 3600
        self.client.register(laser())
        self.client.save_operation({'id': 'read.power', 'layer': 'standard', 'description': 'Read stable laser power',
                                    'requires': ['laser.power_ok'],
                                    'steps': [{'id': 'read', 'device': 'laser', 'operation': 'power'}],
                                    'result': {'power': {'step': 'read', 'path': ['value']}}})
        started, release = threading.Event(), threading.Event()
        work = self.engine.conditions._work

        def paused(job):
            started.set()
            release.wait(5)
            work(job)

        steps = [{'id': 'power', 'operation': 'read.power'}]
        with patch.object(self.engine.conditions, '_work', paused):
            run = self.client.start_experiment(resources=['laser'], requires=['laser.warm'], upkeep=True)
            try:
                self.assertTrue(started.wait(3))
                with self.assertRaises(IOPError) as refused:
                    run.batch(steps, request_id='additional-condition')
                self.assertEqual(refused.exception.code, 'maintenance_active')
                self.assertEqual(run.info()['requires'], ['laser.warm'])
            finally:
                release.set()
        job = next(job for job in self.engine.conditions.jobs.values() if job['run_id'] == run.id)
        self.engine.conditions.job(job['id'], wait=5)
        # The rejected request ID remains available, and upkeep now covers the newly required condition.
        self.assertEqual(run.batch(steps, request_id='additional-condition')[-1]['state'], 'completed')
        self.assertEqual(self.client.condition('laser.power_ok')['state'], 'holds')
        run.finish()

    def test_an_instrument_brings_its_conditions_and_is_maintained_alone(self):
        self.client.register(laser())
        rows = self.client.conditions(device='laser')
        self.assertEqual([(r['id'], r['device'], r['after'], r['state']) for r in rows],
                         [('laser.warm', 'laser', [], 'unknown'), ('laser.power_ok', 'laser', ['laser.warm'], 'unknown')])
        self.assertEqual([r['id'] for r in self.client.request('/v1/devices/laser')['conditions']], ['laser.warm', 'laser.power_ok'])
        context = self.client.context()
        self.assertIn({'action': 'maintain_conditions', 'device': 'laser', 'conditions': ['laser.power_ok', 'laser.warm'],
                       'submit': '/v1/conditions/maintain'}, context['next_steps'])
        report = self.client.maintain(device='laser')
        self.assertTrue(report['holds'])
        self.assertEqual((report['device'], report['platform']), ('laser', None))
        self.assertEqual([(e['condition'], e['result']) for e in report['report']],
                         [('laser.warm', 'corrected'), ('laser.power_ok', 'corrected')])
        # The power reading meant nothing until the diode was warm: the warm-up came first.
        self.assertEqual(LASER['log'], ['temperature', 'wait_warm', 'temperature', 'power', 'set_current', 'power'])
        self.assertEqual(self.client.run(report['run_id']).info()['name'], 'Maintain laser')
        self.assertNotIn('conditions', self.client.context()['setup'])

    def test_platform_conditions_stand_on_those_of_its_instruments(self):
        self.client.register(laser())
        self.client.register({'id': 'detector', 'driver': __name__ + ':Laser', 'config': {'part': 'detector'}})
        bench = {'schema_version': 1, 'id': 'bench', 'devices': ['laser', 'detector'], 'conditions': [LINK]}
        self.client.configure_platform(bench)
        self.assertEqual([r['id'] for r in self.client.conditions('bench')], ['laser.warm', 'laser.power_ok', 'link.ok'])
        # One step for the platform covers its instruments.
        steps = [s for s in self.client.context()['next_steps'] if s['action'] == 'maintain_conditions']
        self.assertEqual(steps, [{'action': 'maintain_conditions', 'platform': 'bench',
                                  'conditions': ['laser.power_ok', 'laser.warm', 'link.ok'], 'submit': '/v1/conditions/maintain'}])
        read = [{'id': 'read', 'device': 'detector', 'operation': 'read'}]
        with self.assertRaisesRegex(IOPError, 'laser.warm is unknown'):
            self.client.submit(read, requires=['link.ok'])
        report = self.client.maintain('bench')
        self.assertEqual([e['condition'] for e in report['report']], ['laser.warm', 'laser.power_ok', 'link.ok'])
        run = self.client.submit(read, requires=['link.ok'])
        run.result(10)
        self.assertEqual(sorted(run.info()['conditions']), ['laser.power_ok', 'laser.warm', 'link.ok'])
        # The diode lost its temperature: everything standing on it is due.
        self.client.invalidate(['laser.warm'], 'the laser was switched off')
        states = {r['id']: r['state'] for r in self.client.conditions('bench')}
        self.assertEqual(states, {'laser.warm': 'due', 'laser.power_ok': 'due', 'link.ok': 'due'})
        with self.assertRaisesRegex(IOPError, 'maintenance_active|platform and its instruments'):
            self.client.request('/v1/conditions/maintain', {'conditions': ['laser.warm', 'link.ok'], 'platform': 'other'})
        # Conditions of the platform's instruments can be maintained as part of the platform.
        part = self.client.maintain('bench', ['laser.warm'])
        self.assertEqual((part['platform'], part['holds']), ('bench', True))

    def test_definitions_are_checked_against_the_instrument(self):
        cases = ((laser([{**WARM, 'verify': {'operation': 'humidity'}}]), 'laser has no operation humidity'),
                 (laser([{**WARM, 'correct': {'operation': 'wait_warm', 'arguments': {'minutes': 90}}}]), 'wait_warm'),
                 (laser([{**WARM, 'verify': {'device': 'detector', 'operation': 'read'}}]), 'uses only that instrument'),
                 (laser([{**WARM, 'id': 'a.b'}]), 'the device name is added'),
                 (laser([POWER]), 'after names other conditions'))
        for record, message in cases:
            with self.assertRaisesRegex(IOPError, message):
                self.client.register(record)
        self.client.register(laser())
        self.client.register({'id': 'detector', 'driver': __name__ + ':Laser', 'config': {'part': 'detector'}})
        taken = {'schema_version': 1, 'id': 'bench', 'devices': ['laser', 'detector'], 'conditions': [{**LINK, 'id': 'laser.warm', 'after': []}]}
        with self.assertRaisesRegex(IOPError, 'already defined on another platform or instrument'):
            self.client.configure_platform(taken)
        self.client.configure_platform({'schema_version': 1, 'id': 'bench', 'devices': ['laser', 'detector'], 'conditions': [LINK]})
        current = self.client.device_history('laser')[0]
        with self.assertRaisesRegex(IOPError, 'link.ok depends on laser.power_ok; revise platform bench first'):
            self.client.configure_device('laser', {**current['definition'], 'conditions': [WARM]}, current['revision'])

    def test_conditions_and_their_states_survive_a_restart(self):
        self.client.register(laser())
        self.client.maintain(device='laser')
        self.server.shutdown(); self.server.server_close(); self.engine.close()
        self.engine = Engine(self.temp.name, json.loads((self.engine.home / 'config.json').read_text()))
        self.server = Server(('127.0.0.1', 0), self.engine)
        self.engine.start()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = Client(self.engine.endpoint, token=self.config['token'])
        self.assertEqual({r['id']: r['state'] for r in self.client.conditions()}, {'laser.warm': 'holds', 'laser.power_ok': 'holds'})


# A lock-in amplifier speaking the SR830 command set: its LIA status bits stay set until they are read.
class LockIn(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            command = line.decode().strip()
            server = self.server
            if command == '*IDN?':
                reply = 'Stanford_Research_Systems,SR830,s/n86000,ver1.07'
            elif command.startswith('LIAS? '):
                bit = int(command.split()[1])
                with server.lock:
                    reply = str(server.status >> bit & 1)
                    server.status &= ~(1 << bit)
            elif command == 'OUTP? 1':
                reply = '1.25E-6'
            else:
                continue
            self.wfile.write((reply + '\n').encode())
            self.wfile.flush()


class ModelDescriptionCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def lock_in(self):
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), LockIn)
        server.daemon_threads, server.status, server.lock = True, 0, threading.Lock()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, {'transport': 'tcp', 'host': '127.0.0.1', 'port': server.server_address[1], 'timeout': 1}

    def test_the_model_description_brings_the_conditions_of_the_instrument(self):
        server, connection = self.lock_in()
        self.client.register({'id': 'lockin', 'instrument_profile': 'srs-sr830', 'config': connection})
        self.assertEqual([r['id'] for r in self.client.conditions(device='lockin')],
                         ['lockin.reference_locked', 'lockin.input_in_range', 'lockin.filter_in_range', 'lockin.output_in_range'])
        self.assertTrue(self.client.maintain(device='lockin')['holds'])
        run = self.client.submit([{'id': 'x', 'device': 'lockin', 'operation': 'read_x'}], requires=['lockin.reference_locked'])
        self.assertEqual(run.result(10)['x']['value'], 1.25e-6)
        # The reference was lost and came back between two observations: the bit records it.
        with server.lock:
            server.status |= 1 << 3
        report = self.client.maintain(device='lockin', force=True)
        found = {e['condition']: e for e in report['report']}
        self.assertEqual((found['lockin.reference_locked']['result'], found['lockin.reference_locked']['value']), ('lost', 1))
        self.assertEqual(run.info()['in_doubt']['lockin.reference_locked']['outcome'], 'drifted')
        # Reading the bit cleared it; the next observation shows the lock has returned.
        self.assertTrue(self.client.maintain(conditions=['lockin.reference_locked'])['holds'])

    def test_an_identified_instrument_gets_the_conditions_of_its_model(self):
        server, connection = self.lock_in()
        row = {'transport': 'tcp', 'address': '127.0.0.1:' + str(connection['port']), 'provider': 'lxi-network',
               'protocol': 'scpi', 'connection': connection}
        with patch('ai4instruments.iop.discovery.discover', return_value=normalize({
                'connections': [copy.deepcopy(row)], 'providers': [{'name': 'lxi-network', 'available': True}]})):
            result = self.engine.scan()
        device = next(r for r in result['instruments'] if r.get('identity', {}).get('model'))['device_id']
        self.assertIn(device + '.reference_locked', [r['id'] for r in self.client.conditions(device=device)])

    def test_the_record_decides_what_the_description_adds(self):
        server, connection = self.lock_in()
        own = {'id': 'reference_locked', 'description': 'Locked, as this laboratory checks it',
               'verify': {'operation': 'read_frequency'}, 'accept': {'minimum': 999, 'maximum': 1001}}
        self.client.register({'id': 'lockin', 'instrument_profile': 'srs-sr830', 'config': connection, 'conditions': [own],
                              'allowed_operations': ['read_*', 'reference_unlock_detected', 'input_overload_detected']})
        rows = {r['id']: r for r in self.client.conditions(device='lockin')}
        # The laboratory's own definition wins; conditions whose operations are not allowed are left out.
        self.assertEqual(sorted(rows), ['lockin.input_in_range', 'lockin.reference_locked'])
        self.assertEqual(rows['lockin.reference_locked']['description'], 'Locked, as this laboratory checks it')

    def test_model_descriptions_check_their_conditions(self):
        profile = json.loads((Path(__file__).resolve().parents[2] / 'iop/instruments/srs-sr830.json').read_text())
        validate_profile(copy.deepcopy(profile))
        broken = copy.deepcopy(profile)
        broken['conditions'][0]['verify'] = {'operation': 'self_test'}
        with self.assertRaisesRegex(ValueError, 'device has no operation self_test'):
            validate_profile(broken)
        broken['conditions'][0]['verify'] = {'device': 'other', 'operation': 'read_x'}
        with self.assertRaisesRegex(ValueError, 'uses only that instrument'):
            validate_profile(broken)
        self.assertEqual(self.engine.instrument_catalog.errors, [])
        listed = self.client.instrument_catalog(model='SR830')['profiles'][0]
        self.assertEqual(listed['conditions'], ['reference_locked', 'input_in_range', 'filter_in_range', 'output_in_range'])
        self.assertEqual([c['id'] for c in instrument('lockin', profile['conditions'])][0], 'lockin.reference_locked')


if __name__ == '__main__':
    unittest.main()

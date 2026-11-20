import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from . import test_service as fixture
from ai4instruments.iop.drivers import make_driver
from ai4instruments.iop.process_driver import DriverUnavailable, executable

try:
    executable()
    available = True
except ValueError:
    available = False


@unittest.skipUnless(available, 'Build iop-execd to run native process cases')
class NativeProtocolCase(unittest.TestCase):
    def test_nonreading_sdk_cannot_block_the_operation_deadline(self):
        process = subprocess.Popen([executable(), '--', sys.executable, '-c', 'import time;time.sleep(2)'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        replies = queue.Queue()
        reader = threading.Thread(target=lambda: replies.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            # This exceeds the pipe capacity. Forwarding it must not suspend the
            # native select loop while the SDK ignores its stdin.
            process.stdin.write(json.dumps({'id': 'blocked-write', 'method': 'invoke',
                'timeout_s': 0.1, 'params': {'payload': 'x' * 1_048_576}}) + '\n')
            process.stdin.flush()
            reply = json.loads(replies.get(timeout=5))
            self.assertEqual(reply['id'], 'blocked-write')
            self.assertTrue(reply['outcome_unknown'])
            self.assertIn('timed out', reply['error'])
        finally:
            process.stdin.close()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
            reader.join(2)
            process.stdout.close(); process.stderr.close()

    def test_invalid_job_deadlines_do_not_start_or_truncate_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, errors, started = root/'stdout', root/'stderr', root/'started'
            output.write_text('previous output')
            for timeout in (-1, True, '3', 1e300):
                with self.subTest(timeout=timeout):
                    request = {'id': 'invalid-timeout', 'params': {
                        'command': [sys.executable, '-c', 'from pathlib import Path;Path("started").touch()'],
                        'cwd': str(root), 'stdout': str(output), 'stderr': str(errors), 'timeout': timeout}}
                    result = subprocess.run([executable(), '--job'], input=json.dumps(request)+'\n',
                        capture_output=True, text=True, timeout=5, check=True)
                    reply = json.loads(result.stdout)
                    self.assertEqual(reply['id'], request['id'])
                    self.assertIn('timeout', reply['error'])
                    self.assertFalse(started.exists())
                    self.assertEqual(output.read_text(), 'previous output')
                    self.assertFalse(errors.exists())


@unittest.skipUnless(available, 'Build iop-execd to run native process cases')
class ProcessCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown
    submit = fixture.ServiceCase.submit
    step = fixture.ServiceCase.step
    wait_running = fixture.ServiceCase.wait_running

    def record(self):
        return {'id':'external','driver':'process','config':{'worker':'external-sdk','timeout':3,
            'configuration':{'devices':[{'id':'external','driver':'simulated','config':{'kind':'stage'}}]}}}

    def test_native_worker_drives_instrument_and_cooperates_with_cancellation(self):
        self.client.register(self.record())
        self.assertEqual(self.client.invoke('external','move_to',{'angle_deg':12})['angle_actual'],12)
        run=self.submit([self.step('wait','external',2)])
        self.wait_running(run);run.cancel()
        self.assertEqual(run.wait(5)['state'],'cancelled')
        self.assertEqual(self.client.invoke('external','position')['angle_actual'],12)

    def test_sdk_exit_retains_affected_resources_and_other_platform_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            worker=Path(directory,'crash.py')
            worker.write_text('''import json,sys,os
for line in sys.stdin:
 m=json.loads(line)
 if m['method']=='configure':r={}
 elif m['method']=='describe':r={'operations':{'crash':{'parameters':{}}}}
 else:os._exit(7)
 print(json.dumps({'id':m['id'],'result':r}),flush=True)
''')
            self.client.register({'id':'crash','driver':'process','config':{'command':[sys.executable,str(worker)],'timeout':2}})
            self.client.configure_platform({'schema_version':1,'id':'bench','devices':['crash','stage'],
                'resources':[{'id':'sample','devices':['crash','stage']}]})
            run=self.submit([{'id':'crash','device':'crash','operation':'crash'}],platform='bench')
            self.assertEqual(run.wait(5)['state'],'failed')
            holds=self.client.request('/v1/status')['resource_holds']
            self.assertIn('shared:sample',[h['resource'] for h in holds])
            self.assertTrue(run.info()['operation_records'][0]['outcome_unknown'])
            blocked=self.submit([self.step('blocked','stage',0)])
            self.assertEqual(blocked.wait(5)['state'],'interrupted')
            self.submit([self.step('independent','aux',0)]).result(5)
            self.assertEqual(self.engine.store.resource_holds().keys(),self.engine.resource_holds.keys())

    def test_timeout_finishes_worker_without_reissuing_operation(self):
        record=self.record();record['config']['timeout']=2
        driver=make_driver(record,{})
        try:
            with self.assertRaises(DriverUnavailable):
                driver.invoke('wait',{'duration':5},__import__('threading').Event())
            self.assertTrue(driver.worker.failed)
            with self.assertRaises(DriverUnavailable):
                driver.invoke('position',{},__import__('threading').Event())
        finally:driver.close()

    def test_startup_and_cancellation_have_separate_native_deadlines(self):
        import subprocess
        from ai4instruments.iop.config import VERSION
        if subprocess.check_output([executable(), '--version'], text=True).strip() != 'iop-execd ' + VERSION:
            self.skipTest('Rebuild iop-execd ' + VERSION + ' for per-request deadline coverage')
        worker = Path(self.temp.name, 'unresponsive.py')
        worker.write_text('''import json,sys,time
for line in sys.stdin:
 m=json.loads(line)
 if m['method']=='configure': time.sleep(0.2); r={}
 elif m['method']=='describe': r={'operations':{'wait':{'parameters':{}}}}
 else: time.sleep(30); r={}
 print(json.dumps({'id':m['id'],'result':r}),flush=True)
''')
        self.client.register({'id':'stuck','driver':'process','config':{
            'command':[sys.executable,str(worker)],'timeout':10,'startup_timeout':5,'cancel_timeout':0.2}})
        run = self.submit([{'id':'wait','device':'stuck','operation':'wait'}])
        self.wait_running(run)
        started = time.monotonic()
        run.cancel()
        record = run.wait(5)
        self.assertLess(time.monotonic()-started, 3)
        self.assertTrue(record['operation_records'][0]['outcome_unknown'])
        self.assertIn('stuck', self.engine.offline)
        self.client.invoke('aux', 'position', timeout=2)

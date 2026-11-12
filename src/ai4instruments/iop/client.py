"""IOP Python client. Experiments and devices live in the service."""
from __future__ import annotations
import base64
import io
import json
import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler, getproxies, proxy_bypass
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse, urljoin
from .config import home_path
from .engine import TERMINAL


def archive(path):
    """ZIP bytes of a program directory, leaving out environments and caches; a ZIP file is sent as it is."""
    path = Path(path).expanduser()
    if not path.is_dir():
        return path.read_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as package:
        for file in sorted(path.rglob('*')):
            rel = file.relative_to(path)
            if file.is_file() and not file.is_symlink() and not any(p in {'.git','.venv','venv','__pycache__'} for p in rel.parts):
                package.write(file, rel.as_posix())
    return buffer.getvalue()


class IOPError(RuntimeError):
    def __init__(self, message, status=None, details=None):
        super().__init__(message)
        self.status, self.details = status, details or {}
        self.code = self.details.get('code')


def origin(url):
    url = urlparse(url)
    return (url.scheme.lower(), url.hostname, url.port if url.port is not None else (443 if url.scheme == 'https' else 80))


class SameServiceRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if origin(req.full_url) != origin(newurl):
            raise IOPError('The configured service redirected to another address.', code)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, endpoint=None, token=None, home=None, timeout=30):
        config = {}
        path = home_path(home) / 'config.json'
        if path.exists():
            config = json.loads(path.read_text(encoding='utf-8'))
        host = config.get('host','127.0.0.1')
        local = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{config.get('port',8765)}"
        self.endpoint = (endpoint or os.environ.get('IOP_ENDPOINT') or local).rstrip('/')
        saved = config.get('token', '') if origin(self.endpoint) == origin(local) else ''
        self.token = token if token is not None else os.environ.get('IOP_TOKEN', saved)
        self.timeout = timeout
        # Loopback instrument services communicate directly, independent of OS proxy settings.
        handlers = [SameServiceRedirect()]
        if urlparse(self.endpoint).hostname in {'127.0.0.1','localhost','::1'}:
            handlers.append(ProxyHandler({}))
        self._open = build_opener(*handlers).open
        from .transport import Connections
        self._connections = Connections()

    def close(self):
        self._connections.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @classmethod
    def connect(cls, endpoint=None, **kwargs):
        return cls(endpoint, **kwargs)

    def request(self, path, body=None, binary=False):
        data = json.dumps(body, allow_nan=False).encode() if body is not None else None
        request = Request(self.endpoint + path, data=data, headers={'Authorization':'Bearer '+self.token,
                          'Content-Type':'application/json'})
        target = urlparse(self.endpoint)
        direct = target.hostname in {'127.0.0.1', 'localhost', '::1'} or proxy_bypass(target.hostname or '') or not getproxies().get(target.scheme)
        if direct:
            url = request.full_url
            for _ in range(5):
                status, payload, location = self._connections.request(url, data, dict(request.header_items()), self.timeout)
                if status in (301, 302, 303, 307, 308) and location:
                    redirected = urljoin(url, location)
                    if origin(redirected) != origin(self.endpoint):
                        raise IOPError('The configured service redirected to another address.', status)
                    if data is not None:
                        raise IOPError('Use the service endpoint directly for instrument submissions.', status)
                    url = redirected
                    continue
                if status >= 400:
                    try:
                        details = json.loads(payload)
                    except ValueError:
                        details = {'error': payload.decode('utf-8', errors='replace')}
                    raise IOPError(details.get('error', str(status)), status, details)
                return payload if binary else json.loads(payload)
            raise IOPError('Too many service redirects.')
        try:
            with self._open(request, timeout=self.timeout) as response:
                data = response.read()
                return data if binary else json.loads(data)
        except HTTPError as error:
            try:
                details = json.loads(error.read())
            except Exception:
                details = {'error': str(error)}
            raise IOPError(details.get('error', str(error)), error.code, details) from None

    def devices(self):
        return self.request('/v1/devices')

    def context(self, refresh=False, detail='summary'):
        return self.request('/v1/context?' + urlencode({'refresh': str(refresh).lower(), 'detail': detail}))

    def changes(self, after=0, limit=100, wait=0):
        return self.request('/v1/context/changes?' + urlencode({'after': after, 'limit': limit, 'wait': wait}))

    def device_page(self, offset=0, limit=100):
        return self.request('/v1/context/devices?' + urlencode({'offset': offset, 'limit': limit}))

    def device_history(self, device):
        from urllib.parse import quote
        return self.request('/v1/devices/' + quote(device, safe='') + '/history')

    def configure_device(self, device, definition, expected_revision):
        from urllib.parse import quote
        return self.request('/v1/devices/' + quote(device, safe='') + '/configuration',
                            {'definition': definition, 'expected_revision': expected_revision})

    def schema(self, name):
        from urllib.parse import quote
        return self.request('/v1/schemas/' + quote(name, safe=''))

    def discover(self):
        return self.request('/v1/discovery')

    def start_discovery(self):
        return self.request('/v1/discovery', {})

    def inventory(self):
        return self.request('/v1/discovery?cached=true')

    def instrument_profiles(self):
        return self.request('/v1/instrument-profiles')

    def instrument_catalog(self, *, manufacturer=None, model=None, serial_number=None, firmware=None,
                           id=None, offset=0, limit=50, details=False):
        from urllib.parse import urlencode
        fields = {k: v for k, v in locals().items() if k not in ('self', 'urlencode') and v is not None}
        fields['details'] = 'true' if details else 'false'
        return self.request('/v1/instrument-catalog?' + urlencode(fields))

    def save_instrument_profile(self, profile):
        return self.request('/v1/instrument-profiles', profile)

    def identify(self, device, request_id=None):
        from urllib.parse import quote
        return Run(self, self.request('/v1/devices/' + quote(device, safe='') + '/identify',
                                     {'request_id':request_id or str(uuid.uuid4())})['id'])

    def retry_discovery(self, key=None):
        return self.request('/v1/discovery/retry', {'key':key})

    def register(self, record):
        return self.request('/v1/devices', record)

    def register_many(self, records):
        return self.request('/v1/devices/batch', {'devices': records})

    def reconnect(self, device):
        return self.request('/v1/devices/' + device + '/reconnect', {})

    def release(self, device):
        return self.request('/v1/devices/' + device + '/release', {})

    def resolve_resources(self, resources, observation):
        """Return held resources to use after recording the observed state of the equipment."""
        return self.request('/v1/resources/resolve', {'resources': list(resources), 'observation': observation})

    def manual_tasks(self):
        """Steps waiting for a person, with their instructions and requested readings."""
        return self.request('/v1/manual-tasks')

    def report_manual(self, operation_id, status, readings=None, note='', by=''):
        """Report a manual step done (with its readings), failed or unsure."""
        return self.request('/v1/manual-tasks/' + quote(operation_id, safe=''),
                            {'status': status, 'readings': readings or {}, 'note': note, 'by': by})

    def adapters(self, name=None):
        """Installed adapters, or with a name the fields and an example of that adapter's device record."""
        return self.request('/v1/adapters' + ('?' + urlencode({'name': name}) if name else ''))

    def resources(self):
        return self.request('/v1/resources')

    def configure_resources(self, capacities):
        return self.request('/v1/resources', {'capacities': capacities})

    def saved_operations(self):
        """Standard and Workflow Operations saved in the laboratory, one line each."""
        return self.request('/v1/library')

    def saved_operation(self, operation_id):
        """One saved operation with its parameters, steps, result and use record."""
        return self.request('/v1/library/' + quote(operation_id, safe=''))

    def save_operation(self, definition, expected_revision=None, program=None):
        """Save a Standard or Workflow Operation as a new revision.

        ``program`` is a directory or ZIP holding a Workflow Operation that decides from results; it
        starts like a submitted program, reads its arguments from IOP_PARAMETERS_JSON and saves its result.
        """
        body = {'definition': definition, 'expected_revision': expected_revision}
        if program is not None:
            body['archive'] = base64.b64encode(archive(program)).decode()
        return self.request('/v1/library', body)

    def program_file(self, operation_id, name, revision=None):
        """The text of one file of a saved program."""
        return self.request('/v1/library/' + quote(operation_id, safe='') + '/files/' + quote(name, safe='/') +
                            ('?' + urlencode({'revision': revision}) if revision else ''))

    def conditions(self, platform=None):
        """What must hold for experiments, with what is known now: holds, due, lost or unknown."""
        return self.request('/v1/conditions' + ('?' + urlencode({'platform': platform}) if platform else ''))

    def condition(self, condition_id):
        """One condition with its definition, state, statistics and latest records."""
        return self.request('/v1/conditions/' + quote(condition_id, safe=''))

    def maintain(self, platform=None, conditions=None, *, run_id=None, request_id=None, force=False, timeout=None):
        """Verify the conditions that are due and correct those that drifted, in dependency order.

        Returns the report when the pass has ended; ``holds`` says whether every named condition holds.
        Inside an experiment pass its ``run_id``; ``force`` verifies conditions whose state still holds.
        """
        request_id = request_id or uuid.uuid4().hex
        deadline = time.monotonic() + timeout if timeout is not None else None
        wait = min(25, max(0, self.timeout - 1))
        job = self.request('/v1/conditions/maintain?' + urlencode({'wait': wait}), {
            'platform': platform, 'conditions': conditions, 'run_id': run_id, 'request_id': request_id, 'force': force})
        while not job['complete']:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('Maintenance is still running: ' + job['id'])
            job = self.request('/v1/maintenance/' + job['id'] + '?' + urlencode({'wait': wait}))
        return job

    def invalidate(self, conditions, reason):
        """Record that something disturbed the system; these conditions are verified again before use."""
        return self.request('/v1/conditions/invalidate', {'conditions': conditions, 'reason': reason})

    def platforms(self):
        return self.request('/v1/platforms')

    def platform(self, platform_id):
        return self.request('/v1/platforms/' + platform_id)

    def configure_platform(self, definition, expected_revision=0):
        return self.request('/v1/platforms', {'definition': definition, 'expected_revision': expected_revision})

    def procedure(self, platform_id, procedure='initialize', request_id=None):
        record = self.request('/v1/platforms/' + platform_id + '/procedures/' + procedure,
                              {'request_id': request_id or str(uuid.uuid4())})
        return Run(self, record['id'])

    def submit(self, steps, *, name='Experiment', project=None, experiment=None, resources=(), request_id=None, parameters=None, platform=None, requirements=None, requires=()):
        """Submit a graph of steps. ``requires`` names platform conditions that must hold for it to be accepted."""
        return Run(self, self.request('/v1/runs', {'name':name, 'project':project, 'experiment':experiment,
                   'steps':steps, 'resources':list(resources), 'parameters':parameters or {}, 'platform': platform,
                   'requirements':requirements or {}, 'request_id':request_id or str(uuid.uuid4()),
                   **({'requires': list(requires)} if requires else {})})['id'])

    def start_experiment(self, *, name='Experiment', project=None, experiment=None, resources=(),
                         request_id=None, parameters=None, platform=None, requirements=None, requires=()):
        """Start one externally driven run; retain its ID and call finish after saving results.

        ``requires`` names platform conditions the experiment relies on: its operations are accepted
        while they hold, and ``run.maintain()`` restores them when one is due.
        """
        return Run(self, self.request('/v1/runs', {'kind':'external', 'name':name, 'project':project,
                   'experiment':experiment, 'resources':list(resources), 'parameters':parameters or {},
                   'platform':platform, 'requirements':requirements or {},
                   'request_id':request_id or str(uuid.uuid4()),
                   **({'requires': list(requires)} if requires else {})})['id'])

    def submit_package(self, path, *, name=None, project=None, experiment=None, resources=(), parameters=None, request_id=None, platform=None, requirements=None, requires=()):
        path = Path(path)
        data = archive(path)
        run = self.request('/v1/packages', {'archive':base64.b64encode(data).decode(), 'name':name or path.stem,
                           'project':project, 'experiment':experiment, 'resources':list(resources), 'platform':platform,
                           'parameters':parameters or {}, 'requirements':requirements or {}, 'request_id':request_id or str(uuid.uuid4()),
                           **({'requires': list(requires)} if requires else {})})
        return Run(self, run['id'])

    def run(self, run_id):
        return Run(self, run_id)

    def current_run(self):
        return Run(self, os.environ['IOP_RUN_ID'])

    def invoke(self, device, operation, arguments=None, timeout=None):
        run = self.submit([{'id':'call', 'device':device, 'operation':operation, 'arguments':arguments or {}}])
        return run.result(timeout)['call']

    def events(self, after=0, run_id=None, wait=0):
        return self.request('/v1/events?' + urlencode({k:v for k,v in {'after':after,'run_id':run_id,'wait':wait}.items() if v is not None}))


class Run:
    def __init__(self, client, run_id):
        self.client, self.id = client, run_id

    def info(self, details=True):
        return self.client.request('/v1/runs/'+self.id + ('' if details else '?details=false'))

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        if kind is None:
            self.finish()
        else:
            try:
                self.cancel()
            except Exception:
                pass  # Preserve the experiment's original exception.

    def wait(self, timeout=None, interval=0.1, details=True):
        deadline = time.monotonic() + timeout if timeout is not None else None
        cursor = 0
        while True:
            info = self.info(details=False)
            if info['state'] in TERMINAL:
                return self.info() if details else info
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('Experiment is still running: '+self.id)
            remaining = max(0, deadline - time.monotonic()) if deadline is not None else 25
            page = self.client.events(cursor, self.id, wait=min(25, max(0, self.client.timeout - 1), remaining))
            cursor = page['cursor']
            if self.client.timeout <= 1 and not page['events']:
                time.sleep(min(interval, remaining))

    def result(self, timeout=None):
        info = self.wait(timeout, details=False)
        if info['state'] != 'completed':
            raise IOPError(f"{self.id}: {info['state']}: {info.get('error','')}")
        return self.client.request('/v1/runs/' + self.id + '/result')

    def cancel(self):
        return self.client.request('/v1/runs/'+self.id+'/cancel', {})

    def finish(self):
        """Finish an external experiment after all accepted operations have ended."""
        return self.client.request('/v1/runs/'+self.id+'/finish', {})

    def checkpoints(self):
        return self.client.request('/v1/runs/' + self.id + '/checkpoints')

    def checkpoint(self, state, name='main'):
        return self.client.request('/v1/runs/' + self.id + '/checkpoints', {'name': name, 'state': state})

    def resume(self, request_id=None):
        return self.client.request('/v1/runs/' + self.id + '/resume', {'request_id': request_id or str(uuid.uuid4())})

    def stream(self, name, definition=None):
        stream = DataStream(self, name)
        if definition is not None:
            self.client.request(stream.path, {'definition': definition})
        return stream

    def invoke(self, device, operation, arguments=None, timeout=None, request_id=None):
        return self.invoke_record(device, operation, arguments, timeout, request_id)['result']

    def invoke_record(self, device, operation, arguments=None, timeout=None, request_id=None):
        """Return the completed operation, including its ID, configuration revision and result."""
        wait = min(25, max(0, self.client.timeout - 1), timeout if timeout is not None else 25)
        deadline = time.monotonic() + timeout if timeout is not None else None
        op = self.client.request('/v1/runs/'+self.id+'/operations?' + urlencode({'wait': wait}), {'device':device, 'operation':operation,
                                 'arguments':arguments or {}, 'request_id':request_id or str(uuid.uuid4())})
        while op['state'] not in TERMINAL:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('Operation is still running: '+op['id'])
            remaining = max(0, deadline - time.monotonic()) if deadline is not None else 25
            wait = min(25, max(0, self.client.timeout - 1), remaining)
            op = self.client.request('/v1/operations/' + op['id'] + '?' + urlencode({'wait': wait}))
        if op['state'] != 'completed':
            raise IOPError(op.get('error', op['state']))
        return op

    def maintain(self, conditions=None, *, force=False, timeout=None):
        """Inside this experiment, verify what it relies on and correct what drifted; returns the report."""
        return self.client.maintain(conditions=conditions, run_id=self.id, force=force, timeout=timeout)

    def batch(self, steps, *, request_id=None, timeout=None, maintain=False):
        """Run a sequence or dependency graph inside this experiment; return operation records.

        With ``maintain``, when a condition the work relies on is due or lost, it is maintained
        first and the batch is then submitted.
        """
        request_id = request_id or uuid.uuid4().hex
        deadline = time.monotonic() + timeout if timeout is not None else None
        wait = min(25, max(0, self.client.timeout - 1), timeout if timeout is not None else 25)
        body = {'steps':steps, 'request_id':request_id}
        try:
            result = self.client.request('/v1/runs/'+self.id+'/batches?' + urlencode({'wait':wait}), body)
        except IOPError as error:
            if not maintain or error.code != 'conditions_not_held':
                raise
            report = self.maintain(list(error.details['conditions']))
            if not report.get('holds'):
                raise IOPError('Conditions could not be restored', details=report) from None
            result = self.client.request('/v1/runs/'+self.id+'/batches?' + urlencode({'wait':wait}), body)
        while not result['complete']:
            remaining = deadline - time.monotonic() if deadline is not None else 25
            if remaining <= 0:
                raise TimeoutError('Batch is still running: ' + request_id)
            result = self.client.request('/v1/runs/'+self.id+'/batches?' +
                urlencode({'request_id':request_id, 'wait':min(wait, remaining)}))
        if any(op['state'] != 'completed' for op in result['operations']):
            raise IOPError('Operation batch did not complete', details=result)
        return result['operations']

    def call(self, operation, arguments=None, *, request_id=None, timeout=None, maintain=False):
        """Use a saved Standard or Workflow Operation in this experiment and return its named result."""
        request_id = request_id or uuid.uuid4().hex
        self.batch([{'id': 'call', 'operation': operation, 'arguments': arguments or {}}], request_id=request_id,
                   timeout=timeout, maintain=maintain)
        return self.client.request('/v1/runs/' + self.id + '/batches?' + urlencode({'request_id': request_id}))['results']['call']

    def begin_episode(self, episode_id, *, task, observation, policy=None, seed=None, info=None):
        """Save initial conditions. Device preparation is performed explicitly before this call."""
        record = self.client.request('/v1/runs/' + self.id + '/episodes', {
            'id': episode_id, 'task': task, 'observation': observation, 'policy': policy,
            'seed': seed, 'info': info or {}})
        return Episode(self, record['id'])

    def episode(self, episode_id):
        return Episode(self, episode_id)

    def episodes(self, offset=0, limit=100):
        return self.client.request('/v1/runs/' + self.id + '/episodes?' + urlencode({'offset': offset, 'limit': limit}))

    def save_acquisition(self, name, operation_ids, *, conditions=None, analysis=None):
        """Save already acquired results, their device configurations and analysis together."""
        return self.client.request('/v1/runs/'+self.id+'/acquisitions', {'name':name,
            'operation_ids':list(operation_ids), 'conditions':conditions or {}, 'analysis':analysis or {}})

    def save_result(self, result):
        return self.client.request('/v1/runs/'+self.id+'/result', {'result':result})

    def save_file(self, name, content, *, operation_ids=()):
        data = content.encode() if isinstance(content, str) else content
        return self.client.request('/v1/runs/'+self.id+'/artifacts', {'name':name, 'data':base64.b64encode(data).decode(),
                                                                  'operation_ids':list(operation_ids)})

    def files(self):
        return self.client.request('/v1/runs/'+self.id+'/artifacts')

    def file_page(self, after=0, limit=100):
        return self.client.request('/v1/runs/'+self.id+'/file-page?' + urlencode({'after':after, 'limit':limit}))

    def updates(self, after=0, limit=100, wait=0):
        """Read new events and changed operation states without measurement arrays."""
        return self.client.request('/v1/runs/'+self.id+'/updates?' + urlencode({'after':after, 'limit':limit, 'wait':wait}))

    def operations(self, after=0, limit=200):
        return self.client.request('/v1/runs/' + self.id + '/operations?' + urlencode({'after':after,'limit':limit}))

    def upload(self, source, name=None, *, operation_ids=()):
        source = Path(source)
        with source.open('rb') as file:
            data = iter(lambda: file.read(256 * 1024), b'')
            request = Request(self.client.endpoint + '/v1/runs/' + self.id + '/files?' + urlencode({
                'name':name or source.name, 'operation_ids':json.dumps(list(operation_ids))}),
                data=data, headers={'Authorization':'Bearer ' + self.client.token,
                                    'Content-Type':'application/octet-stream', 'Content-Length':str(os.fstat(file.fileno()).st_size)})
            with self.client._open(request, timeout=self.client.timeout) as response:
                return json.loads(response.read())

    def download(self, artifact_id, destination):
        request = Request(self.client.endpoint+'/v1/artifacts/'+artifact_id, headers={'Authorization':'Bearer '+self.client.token})
        with self.client._open(request, timeout=self.client.timeout) as response, Path(destination).open('wb') as file:
            shutil.copyfileobj(response,file,256*1024)

    def watch(self, after=0, interval=0.2):
        terminal_observed = False
        while True:
            page = self.client.events(after, self.id, wait=min(25, max(0, self.client.timeout - 1)))
            for event in page['events']:
                yield event
            after = page['cursor']
            # Drain queued events before observing completion.
            if not page['events'] and self.info(details=False)['state'] in TERMINAL:
                if terminal_observed:
                    return
                terminal_observed = True
                continue
            if not page['events']:
                time.sleep(interval)


IOP = Client


class DataStream:
    def __init__(self, run, name):
        from urllib.parse import quote
        self.client = run.client
        self.path = '/v1/runs/' + quote(run.id, safe='') + '/streams/' + quote(name, safe='')

    def info(self):
        return self.client.request(self.path)

    def chunks(self, after=-1, limit=100):
        return self.client.request(self.path + '/chunks?' + urlencode({'after': after, 'limit': limit}))

    def append(self, data, sequence):
        request = Request(self.client.endpoint + self.path + '/chunks?' + urlencode({'sequence': sequence}),
                          data=data, headers={'Authorization': 'Bearer ' + self.client.token,
                                              'Content-Type': 'application/octet-stream'})
        try:
            with self.client._open(request, timeout=self.client.timeout) as response:
                return json.loads(response.read())
        except HTTPError as error:
            details = json.loads(error.read())
            raise IOPError(details.get('error', str(error)), error.code, details) from None


class Episode:
    """A durable sequence of observations and decisions within an existing experiment."""
    def __init__(self, run, episode_id):
        from urllib.parse import quote
        self.run, self.id = run, episode_id
        self.path = '/v1/runs/' + run.id + '/episodes/' + quote(episode_id, safe='')

    def info(self):
        return self.run.client.request(self.path)

    def record(self, sequence, *, action, observation, operation_ids, reward=None,
               terminated=False, truncated=False, info=None):
        return self.run.client.request(self.path + '/transitions', {
            'sequence': sequence, 'action': action, 'observation': observation,
            'operation_ids': list(operation_ids), 'reward': reward, 'terminated': terminated,
            'truncated': truncated, 'info': info or {}})

    def transitions(self, after=-1, limit=100):
        return self.run.client.request(self.path + '/transitions?' + urlencode({'after': after, 'limit': limit}))

    def export(self, destination):
        """Stream a snapshot as JSON Lines without loading the full trajectory into memory."""
        header = self.info()
        stop = header['next_sequence']
        with Path(destination).open('w', encoding='utf-8') as file:
            file.write(json.dumps({'type': 'episode', **header}, ensure_ascii=False, allow_nan=False) + '\n')
            cursor = -1
            while cursor + 1 < stop:
                page = self.transitions(cursor)
                for record in page['transitions']:
                    if record['sequence'] >= stop:
                        break
                    file.write(json.dumps({'type': 'transition', **record}, ensure_ascii=False, allow_nan=False) + '\n')
                if page['cursor'] <= cursor:
                    raise IOPError('Episode changed while exporting')
                cursor = page['cursor']
        return Path(destination)

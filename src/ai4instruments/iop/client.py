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
from .engine import SATISFIED, TERMINAL


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

    def guide(self, topic='overview'):
        """How to work with this laboratory, as text: the working method, or one part of it with examples
        (steps, sweeps, arrays, measurements, conditions, priors, experiments, research, programs, setup, episodes)."""
        return self.request('/v1/guides/' + quote(topic, safe=''))['text']

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

    def conditions(self, platform=None, device=None):
        """What must hold for experiments, with what is known now: holds, due, lost or unknown.

        ``platform`` gives a platform's conditions with those of its instruments; ``device`` one instrument's.
        """
        query = {k: v for k, v in (('platform', platform), ('device', device)) if v}
        return self.request('/v1/conditions' + ('?' + urlencode(query) if query else ''))

    def condition(self, condition_id):
        """One condition with its definition, state, statistics and latest records."""
        return self.request('/v1/conditions/' + quote(condition_id, safe=''))

    def read_array(self, operation_id, field=None, *, along=None, at=None, start=None, stop=None, low=None,
                   high=None, points=None, peak=None, prominence=None, level=None, fit=None):
        """What an array an operation returned says, without moving all its values.

        Returns the count, extremes with their index (and coordinate), mean and standard deviation,
        then exact values when each position holds one element and there are at most ``points``
        positions, otherwise the minimum, maximum and mean of ``points`` buckets. ``along`` names the
        axis followed, or a list of two for a map, with start, stop, low and high then given per axis;
        ``at`` fixes others by index or label, and ``{"component": "arg"}`` reads complex values as their
        argument, ``"power"`` as their squared modulus (abs by default). ``start``/``stop`` select positions and ``low``/``high`` coordinates.
        ``peak`` ("max", or "min" for a dip) adds the line there: its center between samples, its width
        where it is halfway to the opposite extreme, and its value; with ``prominence``, ``peaks`` lists every
        line that stands out by that much, each with its width at half its prominence. ``level`` adds
        ``crossings``: each place the trace crosses that value, between samples, rising or falling. ``fit``
        "line" adds the straight line through the trace: its slope, intercept, where it reaches zero and the
        residual; "lorentzian" or "gaussian" a line of that shape: its center, width, height, base and residual. ``field`` names the array when a result holds several.
        """
        return self.request('/v1/operations/' + quote(operation_id, safe='') + '/array' +
                            view_query(field=field, along=along, at=at, start=start, stop=stop, low=low, high=high,
                                       points=points, peak=peak, prominence=prominence, level=level, fit=fit))

    def array_file(self, operation_id, field=None):
        """The whole array an operation returned as the bytes of a NumPy .npy file, which numpy.load reads; a last
        axis of re/im pairs becomes complex values."""
        return self.request('/v1/operations/' + quote(operation_id, safe='') + '/array.npy' + view_query(field=field),
                            binary=True)

    def maintain(self, platform=None, conditions=None, *, device=None, run_id=None, request_id=None, force=False, timeout=None):
        """Verify the conditions that are due and correct those that drifted, in dependency order.

        Returns the report when the pass has ended; ``holds`` says whether every named condition holds.
        A platform's pass includes the conditions of its instruments; ``device`` maintains one instrument's.
        Inside an experiment pass its ``run_id``; ``force`` verifies conditions whose state still holds.
        """
        request_id = request_id or uuid.uuid4().hex
        deadline = time.monotonic() + timeout if timeout is not None else None
        wait = min(25, max(0, self.timeout - 1))
        job = self.request('/v1/conditions/maintain?' + urlencode({'wait': wait}), {
            'platform': platform, 'conditions': conditions, 'run_id': run_id, 'request_id': request_id, 'force': force,
            **({'device': device} if device else {})})
        while not job['complete']:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('Maintenance is still running: ' + job['id'])
            job = self.request('/v1/maintenance/' + job['id'] + '?' + urlencode({'wait': wait}))
        return job

    def upkeep(self, platform=None):
        """Platforms whose conditions the Runtime keeps: settings, pending reasons, the next foreseen pass and the last one."""
        return self.request('/v1/upkeep' + ('?' + urlencode({'platform': platform}) if platform else ''))

    def invalidate(self, conditions=(), reason=None, *, platform=None, disturbance=None):
        """Record that something disturbed the system; these conditions are verified again before use.

        ``disturbance`` with ``platform`` names an event the platform lists, such as an opened enclosure:
        the conditions it acts on are verified again, and its description is the reason when none is given.
        """
        body = {'conditions': list(conditions), 'reason': reason, 'platform': platform, 'disturbance': disturbance}
        return self.request('/v1/conditions/invalidate', {k: v for k, v in body.items() if v is not None})

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

    def submit(self, steps, *, name='Experiment', project=None, experiment=None, resources=(), request_id=None, parameters=None, platform=None, requirements=None, requires=(), upkeep=None):
        """Submit a graph of steps. ``requires`` names conditions that must hold for it to be accepted. With
        ``upkeep`` (True, or ``{ahead_s, retry_s, correct}``) the Runtime keeps them while it runs: before an
        operation starts, it maintains what is due or about to expire, inside this experiment."""
        return Run(self, self.request('/v1/runs', {'name':name, 'project':project, 'experiment':experiment,
                   'steps':steps, 'resources':list(resources), 'parameters':parameters or {}, 'platform': platform,
                   'requirements':requirements or {}, 'request_id':request_id or str(uuid.uuid4()),
                   **({'requires': list(requires)} if requires else {}), **({'upkeep': upkeep} if upkeep else {})})['id'])

    def preview(self, steps, *, platform=None, resources=(), requirements=None, requires=(), upkeep=None, parameters=None):
        """What a graph of steps would do, without running it: how many operations of each instrument, the conditions
        it relies on and their state, and estimated_s, how long its operations would take one after another at the
        median their kinds took recently. Refused as the submission itself would be."""
        return self.request('/v1/runs', {'steps': steps, 'preview': True, 'platform': platform, 'resources': list(resources),
                                         'requirements': requirements or {}, 'parameters': parameters or {},
                                         **({'requires': list(requires)} if requires else {}),
                                         **({'upkeep': upkeep} if upkeep else {})})

    def start_experiment(self, *, name='Experiment', project=None, experiment=None, resources=(),
                         request_id=None, parameters=None, platform=None, requirements=None, requires=(), upkeep=None):
        """Start one externally driven run; retain its ID and call finish after saving results.

        ``requires`` names the conditions the experiment relies on: its operations are accepted
        while they hold, and ``run.maintain()`` restores them when one is due. With ``upkeep`` (True, or
        ``{ahead_s, retry_s, correct}``) the Runtime keeps them: before an operation and between
        operations it maintains what is due or about to expire, inside this experiment. Reserve the
        devices that maintenance uses.
        """
        return Run(self, self.request('/v1/runs', {'kind':'external', 'name':name, 'project':project,
                   'experiment':experiment, 'resources':list(resources), 'parameters':parameters or {},
                   'platform':platform, 'requirements':requirements or {},
                   'request_id':request_id or str(uuid.uuid4()),
                   **({'requires': list(requires)} if requires else {}),
                   **({'upkeep': upkeep} if upkeep else {})})['id'])

    def submit_package(self, path, *, name=None, project=None, experiment=None, resources=(), parameters=None, request_id=None, platform=None, requirements=None, requires=(), upkeep=None):
        path = Path(path)
        data = archive(path)
        run = self.request('/v1/packages', {'archive':base64.b64encode(data).decode(), 'name':name or path.stem,
                           'project':project, 'experiment':experiment, 'resources':list(resources), 'platform':platform,
                           'parameters':parameters or {}, 'requirements':requirements or {}, 'request_id':request_id or str(uuid.uuid4()),
                           **({'requires': list(requires)} if requires else {}), **({'upkeep': upkeep} if upkeep else {})})
        return Run(self, run['id'])

    def run(self, run_id):
        return Run(self, run_id)

    def runs(self, search=None, *, state=None, limit=20, before=None):
        """Find experiments, newest first, as summaries. ``search`` is text the name, project, experiment label,
        request ID, ID or an instrument used contains; ``state`` is queued, running, completed, failed, cancelled
        or interrupted; ``before`` is the ID of an experiment, to list older ones than."""
        chosen = {'search': search, 'state': state, 'before': before}
        return self.request('/v1/runs?' + urlencode({'details': 'false', 'limit': limit,
                                                    **{key: value for key, value in chosen.items() if value}}))

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
        info = self.info(details=False)
        while info['state'] not in TERMINAL:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('Experiment is still running: '+self.id)
            remaining = max(0, deadline - time.monotonic()) if deadline is not None else 25
            # The Runtime answers when the experiment's state changes, however many operations it runs meanwhile.
            seconds = min(25, max(0, self.client.timeout - 1), remaining)
            info = self.client.request(f'/v1/runs/{self.id}?' + urlencode({'details': 'false', 'wait': seconds, 'state': info['state']}))
            if not seconds:
                time.sleep(min(interval, remaining))
        return self.info() if details else info

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

    def read_array(self, field=None, *, request_id=None, along=None, at=None, start=None, stop=None, low=None,
                   high=None, points=None, peak=None, prominence=None, level=None, fit=None):
        """What an array this experiment's result holds says, such as what a sweep gathered; with
        ``request_id``, an array in the results of that batch. The options are those of Client.read_array."""
        query = view_query(field=field, along=along, at=at, start=start, stop=stop, low=low, high=high, points=points,
                           peak=peak, prominence=prominence, level=level, fit=fit)
        if request_id is not None:
            query += ('&' if query else '?') + urlencode({'request_id': request_id})
        return self.client.request('/v1/runs/' + self.id + '/array' + query)

    def array_file(self, field=None, *, request_id=None):
        """A whole array this experiment's result holds, such as a map a sweep gathered from its points' files, as
        the bytes of a NumPy .npy file; with ``request_id``, one in the results of that batch."""
        query = view_query(field=field, request_id=request_id)
        return self.client.request('/v1/runs/' + self.id + '/array.npy' + query, binary=True)

    def streams(self):
        """The data streams of this experiment with their descriptions, chunk counts and sizes."""
        return self.client.request('/v1/runs/' + self.id + '/streams')

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
        if any(op['state'] not in SATISFIED for op in result['operations']):
            raise IOPError('Operation batch did not complete', details=result)
        return result['operations']

    def call(self, operation, arguments=None, *, sweep=None, repeat=None, until=None, seek=None, retry=None,
             request_id=None, timeout=None, maintain=False):
        """Use a saved Standard or Workflow Operation in this experiment and return its named result. The options
        of a step apply. With ``sweep``, such as {"angle": {"start": -10, "stop": 10, "points": 21}}, it is used
        at each point and its named results return gathered into arrays over the sweep; ``repeat`` uses it that
        many times at each point; with ``until``, such as {"path": ["current"], "above": 0.001}, the sweep stops
        after a point whose named result there is beyond that limit. With ``seek``, such as {"maximize": ["power"],
        "over": {"angle": {"minimum": -60, "maximum": 30, "step": 4}}}, it is used where a search places it, and
        what returns holds the readings, the places under positions and the best. ``retry``, such as {"times": 2},
        makes a failed instrument call of its steps again."""
        request_id = request_id or uuid.uuid4().hex
        options = {'sweep': sweep, 'repeat': repeat, 'until': until, 'seek': seek, 'retry': retry}
        step = {'id': 'call', 'operation': operation, 'arguments': arguments or {},
                **{key: value for key, value in options.items() if value is not None}}
        self.batch([step], request_id=request_id, timeout=timeout, maintain=maintain)
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

    def operations(self, after=0, limit=200, in_doubt=False):
        """A page of this experiment's operations; with ``in_doubt``, only those a condition may have spoiled."""
        return self.client.request('/v1/runs/' + self.id + '/operations?' + urlencode(
            {'after': after, 'limit': limit, **({'in_doubt': 'true'} if in_doubt else {})}))

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

    def archive(self, destination=None):
        """Save the whole experiment as one ZIP: its records, its files and an RO-Crate description."""
        destination = Path(destination or self.id + '.zip')
        request = Request(self.client.endpoint + '/v1/runs/' + self.id + '/archive',
                          headers={'Authorization': 'Bearer ' + self.client.token})
        with self.client._open(request, timeout=self.client.timeout) as response, destination.open('wb') as file:
            shutil.copyfileobj(response, file, 256 * 1024)
        return destination

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


def view_query(**options):
    """The URL query of an array view; at, and a list with one value per axis along, are sent as JSON."""
    query = {key: json.dumps(value) if key == 'at' or isinstance(value, list) else value
             for key, value in options.items() if value is not None}
    return '?' + urlencode(query) if query else ''


class DataStream:
    def __init__(self, run, name):
        from urllib.parse import quote
        self.client = run.client
        self.path = '/v1/runs/' + quote(run.id, safe='') + '/streams/' + quote(name, safe='')

    def info(self):
        return self.client.request(self.path)

    def chunks(self, after=-1, limit=100):
        return self.client.request(self.path + '/chunks?' + urlencode({'after': after, 'limit': limit}))

    def read_array(self, *, along=None, at=None, start=None, stop=None, low=None, high=None, points=None, peak=None,
                   prominence=None, level=None, fit=None):
        """What a numeric stream (format such as float32-le) says, its chunks read as one array of samples
        by channels; the options are those of Client.read_array, and at={"channel": name} picks a channel."""
        return self.client.request(self.path + '/array' + view_query(along=along, at=at, start=start, stop=stop,
                                                                     low=low, high=high, points=points, peak=peak,
                                                                     prominence=prominence, level=level, fit=fit))

    def array_file(self):
        """The whole samples of a numeric stream, by channels, as the bytes of a NumPy .npy file."""
        return self.client.request(self.path + '/array.npy', binary=True)

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

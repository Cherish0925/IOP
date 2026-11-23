"""One scheduler for experiment dependencies, device resources and worker processes."""
from __future__ import annotations
import base64
import copy
import io
import json
import math
import mimetypes
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
import zipfile
import queue
import hashlib
import re
import shutil
from collections import OrderedDict, deque
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, CancelledError
from pathlib import Path, PurePosixPath
from .calibration import operation_fields
from .config import save_json
from .device_configuration import METADATA, allowed
from .drivers import make_driver, operation_parameters, parameters
from .store import Store, dumps, finite, now
from . import library, views
from . import platforms as platform_model
from .resources import Allocator, QueueFull, capacities, quantities
from .execution import Context
from .monitors import Monitors, check as check_monitors

TERMINAL = {"completed", "skipped", "failed", "cancelled", "interrupted"}
# An operation in these states lets the steps after it run: it completed, or its sweep stopped before it.
SATISFIED = {"completed", "skipped"}


def identifier(prefix):
    return prefix + "_" + uuid.uuid4().hex[:20]


def stamp(text):
    """Seconds since the epoch of a recorded time."""
    return datetime.fromisoformat(text).timestamp()


def relative_path(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise ValueError("Expected a relative file path")
    return Path(*path.parts)


def week_ago():
    return datetime.fromtimestamp(time.time() - 7 * 86400, timezone.utc).isoformat(timespec='microseconds')


class Engine:
    def __init__(self, home, config):
        self.home, self.config = Path(home), config
        self.home.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.registration_lock = threading.RLock()
        self.wake = threading.Event()
        self.stopping = False
        self.endpoint = ""
        self.devices, self.descriptions, self.device_records = {}, {}, {}
        self.reconnecting, self.offline = set(), {}
        self.released = set(config.get('released_devices', []))
        self.communication = {}
        self.runs, self.operations, self.futures, self.processes = {}, {}, {}, {}
        # Operations waiting on their own thread (manual steps) do not count against the worker pool.
        self.waiting_steps = set()
        self.allocation = Allocator()
        self.monitors = Monitors(self)
        self.capacities = capacities(config.get('resource_pools', {}))
        self.allocation.limits.update({'pool:' + k: v for k, v in self.capacities.items()})
        self.ready, self.dependents, self.remaining_dependencies, self.remaining = {}, {}, {}, {}
        self.step_ids, self.minimum_capacities = {}, {}
        # The ends of each range swept, read once for all of its points, by experiment.
        self.ranges = {}
        # Points of sweeps that retake them which a lost condition put in doubt, and their retakes in progress.
        self.doubted, self.retaking = {}, {}
        # Operations refused before their instrument was called, and how many calls of each were made again.
        self.refused, self.tries = set(), {}
        self.completed = queue.SimpleQueue()
        self.progress_lock = threading.Lock()
        self.progress_updates = {}
        self.progress_times = {}
        self.last_scheduled_run = None
        self.cancellations = {}
        self.errors = []
        self.discovery = {"connections":[], "providers":[]}
        self.discovery_stop = threading.Event()
        self.discovery_thread = None
        self.scan_lock = threading.Lock()
        self.scan_request_lock = threading.Lock()
        self.scan_request = None
        self.store = Store(self.home / "records.sqlite3")
        from .catalog import InstrumentCatalog
        self.instrument_catalog = InstrumentCatalog()
        from .identification import Enrollment
        self.enrollment = Enrollment(self)
        self.discovery['instruments'] = [dict(r, present=None) for r in self.enrollment.records.values()]
        self.platforms = {p['id']: p for p in self.store.platforms()}
        # Standard and Workflow Operations saved in this laboratory, and how often each was used.
        self.library = {record['id']: record for record in self.store.saved_operations()}
        self.uses = self.store.operation_uses()
        self.call_progress = {}
        self.platform_resources = platform_model.resource_index(self.platforms)
        # What must hold on each platform for its experiments, and what is known about it now.
        from .conditions import Conditions
        self.conditions = Conditions(self)
        self.resource_holds = self.store.resource_holds()
        self.worker_count = max(1, int(config.get("workers", 8)))
        self.package_count = max(1, int(config.get("package_workers", 4)))
        self.pool = ThreadPoolExecutor(max_workers=self.worker_count, thread_name_prefix="iop-device")
        for device in config.get("devices", []):
            try:
                self.register(device, persist=False)
            except Exception as error:
                self.errors.append({"device":device.get("id"), "error":str(error)})
                self.offline[device.get('id', 'unknown')] = str(error)
        for run in self.store.recover():
            self.runs[run["id"]] = run
            self.operations.update((op['id'], op) for op in self.store.operations(run['id']))
            self._index_run(run)
        self.resource_holds = self.store.resource_holds()
        # Experiments a stop of the service or an unresolved instrument left interrupted in the last week, until
        # they are continued: the laboratory overview names them as something to do.
        self.interrupted = self.store.interrupted(week_ago())
        self.thread = threading.Thread(target=self._loop, name="iop-scheduler", daemon=True)

    def start(self):
        self.thread.start()
        self.monitors.start()
        self.conditions.start()
        interval = float(self.config.get("discovery_interval", 30))
        if interval > 0:
            def inventory_loop():
                while not self.discovery_stop.is_set():
                    started = time.monotonic()
                    try:
                        self.scan()
                    except Exception as error:
                        with self.lock:
                            self.discovery['error'] = str(error)
                    self.discovery_stop.wait(max(0.1, interval - (time.monotonic() - started)))
            self.discovery_thread = threading.Thread(target=inventory_loop, name="iop-discovery", daemon=True)
            self.discovery_thread.start()

    def scan(self):
        if not self.scan_lock.acquire(blocking=False):
            with self.scan_lock:
                return self.inventory()
        try:
            return self._scan()
        finally:
            self.scan_lock.release()

    def inventory(self):
        with self.lock:
            return {**json.loads(dumps(self.discovery)), 'scanning': self.scan_lock.locked()}

    def request_scan(self):
        with self.scan_request_lock:
            if not self.stopping and not self.scan_lock.locked() and not (self.scan_request and self.scan_request.is_alive()):
                def scan():
                    try:
                        self.scan()
                    except Exception as error:
                        with self.lock:
                            self.discovery['error'] = str(error)
                self.scan_request = threading.Thread(target=scan, name='iop-requested-discovery', daemon=True)
                self.scan_request.start()
            result = self.inventory()
            result['scanning'] = bool(self.scan_request and self.scan_request.is_alive()) or result['scanning']
            return result

    def _scan(self):
        started = time.monotonic()
        from .discovery import discover, match_profiles
        with self.lock:
            previous = json.loads(dumps(self.discovery))
            self.discovery.pop('error', None)
            self.discovery['phase'] = 'discovering'
        def publish(partial):
            from .discovery import normalize
            with self.lock:
                finished = {p['name'] for p in partial['providers'] if p.get('available')}
                failed = {p['name'] for p in partial['providers'] if not p.get('available')}
                current = json.loads(dumps(partial))
                current['connections'] += [dict(c, present=None) if c.get('provider') in failed else c
                    for c in previous.get('connections', []) if c.get('provider') not in finished]
                # A failed provider leaves the last observation visible, with unknown presence.
                keys = {(c.get('transport'), c.get('address')) for c in partial['connections']}
                current['connections'] = current['connections'][:len(partial['connections'])] + [c
                    for c in current['connections'][len(partial['connections']):]
                    if (c.get('transport'), c.get('address')) not in keys]
                current.update(phase='discovering', instruments=previous.get('instruments', []), updated=now())
                self.discovery = normalize(current)
        inventory = discover(self.config.get('software_profiles', []), on_update=publish)
        if self.stopping:
            return inventory
        for record in match_profiles(inventory, self.config.get("profiles", [])):
            if record.get("id") not in self.devices:
                try:
                    self.register(record)
                except Exception as error:
                    inventory.setdefault("profile_errors", []).append({"device":record.get("id"), "error":str(error)})
        with self.lock:
            for record in self.device_records.values():
                c = record.get("config", {})
                if record['driver'] in ('scpi', 'modbus') and c.get('transport') in ('serial', 'visa'):
                    address = c.get('resource') if c['transport'] == 'visa' else c.get('port')
                    for candidate in inventory['connections']:
                        if candidate.get('transport') == c['transport'] and candidate.get('address') == address:
                            ids = sorted(set(candidate.get('device_ids', [])) | {record['id']})
                            candidate.update(status='registered', device_ids=ids)
                            candidate.pop('id', None)
                            if len(ids) == 1:
                                candidate['id'] = ids[0]
                if record["driver"] == "scpi" and c.get("transport", "tcp") == "tcp":
                    inventory["connections"].append({"transport":"tcp", "address":str(c.get("host"))+":"+str(c.get("port",5025)), "id":record["id"], "status":"registered", "provider":"configured", "present":None})
            for record in self.device_records.values():
                c = record.get("config", {})
                if record["driver"] == "modbus" and c.get("transport","tcp") == "tcp":
                    inventory["connections"].append({"transport":"tcp","protocol":"modbus","address":str(c.get("host"))+":"+str(c.get("port",502)),"id":record["id"],"status":"registered", "provider":"configured", "present":None})
                elif record["driver"] == "http":
                    inventory["connections"].append({"transport":"http","address":c.get("base_url"),"id":record["id"],"status":"registered", "provider":"configured", "present":None})
            inventory["updated"] = now()
            from .discovery import normalize
            normalize(inventory)
            inventory['phase'] = 'identifying'
            self.discovery = json.loads(dumps(inventory))
        def identified(row):
            with self.lock:
                from .discovery import connection_key, normalize
                for candidate in self.discovery['connections']:
                    if connection_key(candidate) == row['key']:
                        candidate['identification'] = copy.deepcopy(row)
                        if row.get('device_id'):
                            candidate.update(id=row['device_id'], status='registered')
                records = {r['key']: r for r in self.discovery.get('instruments', [])}
                records[row['key']] = copy.deepcopy(row)
                self.discovery['instruments'] = list(records.values())
                normalize(self.discovery)
        self.enrollment.scan(inventory, on_update=identified)
        with self.lock:
            normalize(inventory)
            inventory['phase'] = 'complete'
            inventory['duration_s'] = round(time.monotonic() - started, 3)
            self.discovery = inventory
            old = {r['key']: r for r in previous.get('instruments', [])}
            with self.store.transaction():
                for row in inventory.get('instruments', []):
                    fields = ('present', 'status', 'device_id', 'identity', 'required')
                    if any(row.get(k) != old.get(row['key'], {}).get(k) for k in fields):
                        self.store.event('instrument.changed', connection=row['key'],
                            device_id=row.get('device_id'), state={k: row.get(k) for k in fields})
        return inventory

    def device_states(self):
        """Discovery presence, communication and resource use are independent observations."""
        from .discovery import device_connection
        with self.lock:
            seen = {r['key']: r for r in self.discovery.get('instruments', [])}
            return {device: {'present': seen.get(device_connection(record), {}).get('present'),
                'last_seen': seen.get(device_connection(record), {}).get('last_seen'),
                'communication': dict(self.communication.get(device, {})),
                'state': 'reconnecting' if device in self.reconnecting else
                         'released' if device in self.released else
                         'offline' if device in self.offline else
                         'busy' if self.allocation.used['device:' + device] else 'ready',
                'error': self.offline.get(device)} for device, record in self.device_records.items()}

    def _resources(self, values, operation=None):
        if not isinstance(values, (list, tuple)):
            raise ValueError("Resources must be a list")
        resources = set()
        for value in values:
            if not isinstance(value, str) or not value:
                raise ValueError("Resources must be nonempty names")
            if value in self.devices:
                if value in self.released:
                    from .errors import RequestError
                    raise RequestError('device_released', 'Reconnect this instrument before reserving it.', device_id=value)
                if value in self.reconnecting or value in self.offline:
                    raise ValueError('Device needs reconnection: ' + value)
                resources.add("device:" + value)
                record = self.device_records[value]
                resources.update("shared:" + r for r in record.get("resources", []))
                resources.update("shared:" + r for r in self.descriptions[value].get("resources", []))
                resources.update(platform_model.operation_resources(self.platforms, value, operation)
                                 if operation else self.platform_resources.get(value, ()))
            else:
                resources.add("shared:" + value)
        return sorted(resources)

    def list_platforms(self):
        with self.lock:
            return [self.platform_info(p) for p in sorted(self.platforms)]

    def resolve_resources(self, resources, observation):
        with self.lock:
            platform_model.strings(resources, 'Resources')
            if not isinstance(observation,str) or not observation.strip():
                raise ValueError('Describe the observed device state before returning resources to use')
            if set(resources) - self.resource_holds.keys():
                raise ValueError('Resource is not awaiting an observed state')
            if any(self.allocation.used[r] for r in resources):
                raise ValueError('Affected operations are still running')
            self.store.resolve_resources(resources, observation)
            self.resource_holds = self.store.resource_holds()
            self.wake.set()
            return {'released':resources}

    def platform_info(self, platform_id):
        with self.lock:
            record = json.loads(dumps(self.platforms[platform_id]))
            record['active_runs'] = [r['id'] for r in self.runs.values() if r.get('platform', {}).get('id') == platform_id]
            record['missing_devices'] = sorted(set(record['definition']['devices']) - self.devices.keys())
            signals = platform_model.timing(record['definition'])
            if signals:
                record['timing'] = signals
            return record

    def configure_resources(self, value):
        value = capacities(value)
        with self.lock:
            for name in self.capacities.keys() | value.keys():
                key = 'pool:' + name
                needed = max([self.allocation.used[key]] +
                    [self._amounts(r, reserve=True).get(key, 0) for r in self.runs.values()] +
                    [self._amounts(o).get(key, 0) for o in self.operations.values() if o['state'] not in TERMINAL])
                if needed > value.get(name, 0):
                    raise ValueError('Capacity is needed by accepted work: ' + name)
            self.allocation.limits = {k: v for k, v in self.allocation.limits.items() if not k.startswith('pool:')}
            self.allocation.limits.update({'pool:' + k: v for k, v in value.items()})
            self.capacities = value
            self.config['resource_pools'] = value
            save_json(self.home / 'config.json', self.config)
            self.wake.set()
            return self.resource_info()

    def resource_info(self):
        with self.lock:
            return {'capacities': dict(self.capacities), 'allocation': self.allocation.snapshot()}

    def _amounts(self, item, reserve=False):
        amounts = {r: self.allocation.limit(r) if reserve else 1 for r in item['resources']}
        amounts.update(item.get('requirements', {}))
        return amounts

    def left_interrupted(self):
        """The experiments left interrupted in the last week and not continued since, oldest first."""
        since = week_ago()
        with self.lock:
            # A service that stays up for months forgets those it named for a week.
            for run_id in [run_id for run_id, run in self.interrupted.items() if run.get('ended', since) < since]:
                del self.interrupted[run_id]
            return list(self.interrupted.values())

    def running(self, run_id):
        """Whether an operation of this experiment is in progress."""
        return any(self.operations[oid]['run_id'] == run_id for oid in self.futures)

    def startable(self, run_id):
        """Whether an operation of this experiment could start now but for the conditions it relies on: it is
        ready, its retry delay and any retakes have cleared, and its instruments are free."""
        return any(not self.operations[oid].get('maintenance')
                   and not self._deferred(self.operations[oid])
                   and self.allocation.can_claim(run_id, self._amounts(self.operations[oid]))
                   for oid in self.ready.get(run_id, ()))

    def _deferred(self, op):
        """A retry delay or pending retakes prevents this operation from starting."""
        return (bool(self.retaking.get(op['run_id'])) and not (op.get('retakes') or op.get('maintenance'))
                or 'retry_at' in op and stamp(op['retry_at']) > time.time())

    def _status(self, op):
        """The status operation that qualifies this reading, when its description names one, with its arguments:
        "{parameter}" passes an argument of the reading."""
        operations = self.descriptions.get(op['device'], {}).get('operations', {})
        quality = operations.get(op['operation'], {}).get('quality')
        if not isinstance(quality, dict) or 'operation' not in quality:
            return None
        arguments = {key: op['arguments'].get(value[1:-1]) if isinstance(value, str) and value.startswith('{')
                     and value.endswith('}') else value for key, value in quality.get('arguments', {}).items()}
        try:
            arguments = parameters(operations[quality['operation']], arguments)
        except (KeyError, ValueError):
            pass  # The status call then reports what is wrong, and the reading is kept as uncertain.
        return {**quality, 'arguments': arguments}

    def _qualify(self, op):
        """A reading the instrument reports as uncertain, or outside the measuring interval its description declares,
        is in doubt with the reason; a sweep that retakes its points takes it again."""
        result = op.get('result')
        if not isinstance(result, dict):
            return
        reasons = []
        quality = result.get('quality')
        if isinstance(quality, dict) and quality.get('level', 'good') != 'good':
            reasons.append(str(quality.get('detail') or quality['level']))
        spec = self.descriptions.get(op['device'], {}).get('operations', {}).get(op['operation'], {}).get('result')
        value = result.get('value')
        if isinstance(spec, dict) and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            low, high, clip = (spec.get(k) for k in ('minimum', 'maximum', 'saturation'))
            if isinstance(clip, (int, float)) and value >= clip:
                reasons.append(f'{value} reaches saturation at {clip}')
            elif isinstance(low, (int, float)) and value < low or isinstance(high, (int, float)) and value > high:
                reasons.append(f'{value} is outside the measuring interval {low} to {high}')
        if reasons:
            detail = '; '.join(dict.fromkeys(reasons))
            prior = quality if isinstance(quality, dict) else {}
            level = prior.get('level', 'good')
            result['quality'] = {**prior, 'level': level if level != 'good' else 'uncertain', 'detail': detail}
            entry = {'since': op.get('started') or now(), 'found': now(), 'outcome': 'uncertain', 'reason': detail}
            op.setdefault('in_doubt', {})['quality'] = entry
            run = self.runs.get(op['run_id'])
            if run is not None and 'quality' not in run.setdefault('in_doubt', {}):
                run['in_doubt']['quality'] = entry
                self.store.put('runs', run)

    def doubt(self, run_id, condition_id, after, entry):
        """Its data may have been taken while a condition did not hold: mark each operation of this experiment
        that relied on the condition and was in progress after that moment, in its record and in memory."""
        for oid, found in self.store.doubt(run_id, condition_id, after, entry).items():
            if oid in self.operations:
                self.operations[oid].setdefault('in_doubt', {})[condition_id] = found
                self._doubted(self.operations[oid])

    def _doubted(self, op):
        """A point of a sweep that retakes its points, ended and now in doubt, is taken again."""
        if any(entry.get('source_operations') for entry in op.get('in_doubt', {}).values()):
            # The arguments came from doubtful data. Repeating the same physical action cannot repair them.
            self.doubted.get(op['run_id'], {}).pop(op['id'], None)
        elif op.get('retake') and op['state'] == 'completed' and op.get('in_doubt') and 'retaken_by' not in op:
            self.doubted.setdefault(op['run_id'], {})[op['id']] = None

    def _retake(self, run, originals):
        """Take again points a lost condition put in doubt, before the experiment's other operations: the same
        operation with the arguments it was given; what the sweep gathers then holds what the retakes read."""
        ops = []
        for oid in originals:
            original = self.operations[oid]
            op = self._operation(run['id'], {'id': original['step'] + '~1', 'device': original['device'],
                                             'operation': original['operation'], 'arguments': original['arguments']})
            op.update(after_ids=[], swept_by=original['swept_by'], retakes=oid,
                      **({'input_operations': original['input_operations']} if original.get('input_operations') else {}),
                      **({'requires': original['requires']} if original.get('requires') else {}))
            ops.append((op, original))
        for op, original in ops:
            original['retaken_by'] = op['id']
        run['operations'].extend(op['id'] for op, _ in ops)
        run['operation_count'] = run.get('operation_count', 0) + len(ops)
        with self.store.transaction():
            self.store.put('runs', run)
            for op, original in ops:
                self.store.put('operations', original)
                self.store.put('operations', op)
                self.store.event('operation.queued', run['id'], operation_id=op['id'], device=op['device'],
                                 operation=op['operation'], retakes=original['id'])
        for op, _ in reversed(ops):
            self.operations[op['id']] = op
            self.ready[run['id']][op['id']] = None
            self.ready[run['id']].move_to_end(op['id'], last=False)
            self.remaining[run['id']] += 1
            self.retaking.setdefault(run['id'], set()).add(op['id'])
            for key, count in self._amounts(op).items():
                self.minimum_capacities[run['id']][key] = max(self.minimum_capacities[run['id']].get(key, 0), count)

    def _release(self, op_id):
        """What an operation held while its instrument was called, given back once the call returned."""
        self.allocation.release(op_id)
        del self.futures[op_id]
        self.waiting_steps.discard(op_id)
        self.cancellations.pop(op_id, None)
        self.progress_times.pop(op_id, None)
        self.refused.discard(op_id)

    def _again(self, op, error):
        """An instrument call that failed cleanly is queued again, as its step allows, after the wait it gives; the
        failed attempt stays in the record, and what it wrote is discarded."""
        attempt = {'state': 'failed', 'error': f'{type(error).__name__}: {error}', 'started': op['started'], 'ended': now()}
        op.setdefault('attempts', []).append(attempt)
        self.tries[op['id']] = self.tries.get(op['id'], 0) + 1
        op.update(state='queued', retry_at=datetime.fromtimestamp(time.time() + op['retry'].get('after_s', 1),
                                                                  timezone.utc).isoformat(timespec='microseconds'))
        for key in ('started', 'progress'):
            op.pop(key, None)
        shutil.rmtree(self.home / 'runs' / op['run_id'] / 'output' / 'operations' / op['id'], ignore_errors=True)
        self.communication[op['device']] = {'time': attempt['ended'], 'operation_id': op['id'], 'state': 'failed',
                                            'error': attempt['error']}
        self._release(op['id'])
        self.ready[op['run_id']][op['id']] = None
        with self.store.transaction():
            self.store.put('operations', op)
            self.store.event('operation.retrying', op['run_id'], operation_id=op['id'], attempt=len(op['attempts']),
                             error=attempt['error'])

    def _retaken(self, op):
        """A point taken again: what its sweep gathers holds what it read in place of the point in doubt."""
        sweep = op['swept_by'][-1]
        for oid in self.runs[op['run_id']]['operations']:
            holder = self.operations.get(oid)
            if holder and sweep in holder.get('gathers', {}):
                for point in holder['gathers'][sweep]['points']:
                    if point.get('step') == op['retakes']:
                        point['step'] = op['id']
                self.store.put('operations', holder)
                return

    def _index_run(self, run):
        self.ready[run['id']] = OrderedDict()
        run['involved_resources'] = sorted(self._amounts(run, reserve=True).keys() |
            set().union(*(self._amounts(self.operations[o]).keys() for o in run['operations'])))
        ops = [self.operations[oid] for oid in run['operations']]
        by_step = {o['step']: o for o in ops if o.get('step') is not None and not o.get('maintenance')}
        self.step_ids[run['id']] = {name: op['id'] for name, op in by_step.items()}
        minimum = self._amounts(run, reserve=True)
        for op in ops:
            for key, count in self._amounts(op).items():
                minimum[key] = max(minimum.get(key, 0), count)
        self.minimum_capacities[run['id']] = minimum
        self.remaining[run['id']] = sum(o['state'] not in TERMINAL for o in ops)
        for op in ops:
            self._follow_calls(op, run.get('calls', {}))
        for op in ops:
            if op['state'] != 'queued':
                continue
            parents = ([self.operations[oid] for oid in op['after_ids'] if self.operations[oid]['state'] not in SATISFIED]
                       if 'after_ids' in op else [by_step[step] for step in op['after'] if by_step[step]['state'] not in SATISFIED])
            self.remaining_dependencies[op['id']] = len(parents)
            for parent in parents:
                self.dependents.setdefault(parent['id'], []).append(op['id'])
            if not parents:
                self.ready[run['id']][op['id']] = None

    def _follow_calls(self, op, calls):
        """Count the steps still open in each use of a saved operation that this step belongs to."""
        for call in op.get('calls', ()):
            if call not in calls:
                continue
            progress = self.call_progress.setdefault(op['run_id'], {}).setdefault((op.get('batch'), call), {
                'remaining': 0, 'state': 'skipped', 'operation': calls[call]['operation'], 'revision': calls[call]['revision']})
            if op['state'] not in TERMINAL:
                progress['remaining'] += 1
            else:
                self._use_step_ended(progress, op['state'])

    @staticmethod
    def _use_step_ended(progress, state):
        """A use ends failed when a step failed, else cancelled when one stopped, else completed when one completed;
        one whose steps were all skipped, as a point after its sweep stopped, was not made."""
        order = ('skipped', 'completed', 'cancelled', 'failed')
        progress['state'] = max(progress['state'], state if state in order else 'cancelled', key=order.index)

    def _calls_ended(self, op):
        """Record a use of a saved operation when its last step ends: this is its evidence."""
        for call in op.get('calls', ()):
            progress = self.call_progress.get(op['run_id'], {}).get((op.get('batch'), call))
            if progress is None:
                continue
            self._use_step_ended(progress, op['state'])
            progress['remaining'] -= 1
            if progress['remaining'] > 0:
                continue
            del self.call_progress[op['run_id']][(op.get('batch'), call)]
            if progress['state'] != 'skipped':
                self._record_use(progress['operation'], progress['revision'], op['run_id'], call, progress['state'])

    def _record_use(self, operation, revision, run_id, call, state):
        uses = self.uses.setdefault(operation, {'calls': 0, 'completed': 0, 'failed': 0})
        uses['calls'] += 1
        if state in ('completed', 'failed'):
            uses[state] += 1
        uses['last'] = {'run_id': run_id, 'call': call, 'state': state, 'time': now(), 'revision': revision}
        self.store.record_use(operation, uses, run_id, call, state)

    def _operation_ended(self, op):
        self._calls_ended(op)
        self.tries.pop(op['id'], None)
        if op.get('retakes'):
            self.retaking.get(op['run_id'], set()).discard(op['id'])
            if op['state'] == 'completed':
                self._retaken(op)
        else:
            self._doubted(op)
        self.ready[op['run_id']].pop(op['id'], None)
        self.remaining_dependencies.pop(op['id'], None)
        self.remaining[op['run_id']] -= 1
        children = self.dependents.pop(op['id'], [])
        if op['state'] in SATISFIED:
            for child in children:
                if self.operations[child]['state'] != 'queued':
                    continue
                self.remaining_dependencies[child] -= 1
                if self.remaining_dependencies[child] == 0 and self.operations[child]['state'] == 'queued':
                    self.ready[op['run_id']][child] = None

    def prepare_platform(self, definition):
        definition = platform_model.description(definition, self.devices)
        for procedure in definition['procedures'].values():
            for step in library.expand(procedure['steps'], self.library.get)[0]:
                if step['device'] not in definition['devices']:
                    raise ValueError('Procedure device must belong to the platform')
                self._operation('prepare', step)
        conditions = self.conditions.prepare(definition)
        if conditions:
            definition['conditions'] = conditions
        else:
            definition.pop('conditions', None)
        return definition

    def save_operation(self, definition, expected_revision=None, archive=None):
        """Save a Standard or Workflow Operation as a new revision; ``archive`` is the ZIP of a program."""
        with self.lock:
            definition = json.loads(dumps(definition))
            if archive is not None:
                definition['program'] = self._program(archive)
            elif 'program' in definition:
                raise ValueError('Send the program files with the definition')
            definition = library.check(definition, self.library.get)
            if 'program' not in definition:
                self._constituents(definition)
            old = self.library.get(definition['id'])
            revision = old['revision'] if old else 0
            if expected_revision is not None and expected_revision != revision:
                raise ValueError('Operation revision changed; current revision is ' + str(revision))
            if old and all(old.get(key) == value for key, value in definition.items()) and set(definition) | {'revision', 'updated'} == set(old):
                return self.saved_operation(definition['id'])
            if any(job['state'] == 'running' and definition['id'] in job.get('operation_revisions', {})
                   for job in self.conditions.jobs.values()):
                raise ValueError('This saved operation is in use by active maintenance; revise it after that pass ends')
            record = {**definition, 'revision': revision + 1, 'updated': now()}
            if archive is not None:
                path = self.home / 'library' / record['id'] / f"{record['revision']}.zip"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive)
            self.store.save_operation(record)
            self.library[record['id']] = record
            self.conditions.index()
            return self.saved_operation(record['id'])

    @staticmethod
    def _program(archive):
        """Describe the files of a program and the command that starts it."""
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as package:
                members = [m for m in package.infolist() if not m.is_dir()]
                if sum(m.file_size for m in members) > 128 * 1024 * 1024:
                    raise ValueError('Expanded program exceeds 128 MiB')
                for member in members:
                    relative_path(member.filename)
                names = {m.filename for m in members}
                manifest = json.loads(package.read('package.json')) if 'package.json' in names else {}
        except zipfile.BadZipFile:
            raise ValueError('The program must be a ZIP archive or a directory') from None
        command = manifest.get('command', ['{python}', 'experiment.py'])
        if not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError('package.json command must be a nonempty string array')
        if 'package.json' not in names and 'experiment.py' not in names:
            raise ValueError('A program needs package.json with its command, or experiment.py')
        return {'command': command, 'files': [{'name': m.filename, 'size': m.file_size} for m in sorted(members, key=lambda m: m.filename)],
                'sha256': hashlib.sha256(archive).hexdigest(), 'bytes': len(archive)}

    def program_file(self, operation_id, name, revision=None):
        """The text of one file of a saved program, so the method can be read before it is used or revised."""
        with self.lock:
            record = self.library[operation_id]
            if 'program' not in record:
                raise KeyError(name)
            revision = int(revision or record['revision'])
        path = self.home / 'library' / operation_id / f'{revision}.zip'
        with zipfile.ZipFile(path) as package:
            try:
                data = package.read(name)
            except KeyError:
                raise KeyError(name) from None
        try:
            return {'operation': operation_id, 'revision': revision, 'name': name, 'text': data.decode('utf-8')}
        except UnicodeDecodeError:
            return {'operation': operation_id, 'revision': revision, 'name': name, 'bytes': len(data), 'text': None}

    def _constituents(self, definition):
        """Check the steps against registered devices and saved operations, and that no operation uses itself."""
        for step in definition['steps']:
            supplied = step.get('arguments', {}).keys() | step.get('bindings', {}).keys() | step.get('sweep', {}).keys()
            if 'device' not in step:
                inner = self.library[step['operation']]
                if supplied - inner.get('parameters', {}).keys():
                    raise ValueError(f"Step {step['id']}: {inner['id']} has no parameter " +
                                     ', '.join(sorted(supplied - inner.get('parameters', {}).keys())))
            elif step['device'] in self.descriptions:
                # A device named by a parameter or not yet registered is checked when the operation is used.
                operations = self.descriptions[step['device']]['operations']
                if step['operation'] not in operations:
                    raise ValueError(f"Step {step['id']}: unknown operation {step['device']}.{step['operation']}")
                if supplied - operations[step['operation']].get('parameters', {}).keys():
                    raise ValueError(f"Step {step['id']}: {step['device']}.{step['operation']} has no parameter " +
                                     ', '.join(sorted(supplied - operations[step['operation']].get('parameters', {}).keys())))
        pending, seen = [step['operation'] for step in definition['steps'] if 'device' not in step], set()
        while pending:
            current = pending.pop()
            if current == definition['id']:
                raise ValueError('An operation cannot use itself, directly or through another operation')
            if current not in seen:
                seen.add(current)
                pending.extend(step['operation'] for step in self.library[current]['steps'] if 'device' not in step)

    def _unavailable(self, operation_id, found=None):
        """What a saved operation names that the laboratory no longer has: devices, their operations
        or parameters. A device given by a parameter, and the devices a program uses, are known only
        when it runs."""
        found = {} if found is None else found
        if operation_id in found:
            return found[operation_id]
        found[operation_id] = problems = []
        problems += [f'{c}: no such condition' for c in self.library[operation_id].get('requires', [])
                     if c not in self.conditions.definitions]
        for step in self.library[operation_id].get('steps', []):
            if 'device' not in step:
                problems += [f"{step['operation']}: {p}" for p in self._unavailable(step['operation'], found)]
                continue
            if library.placeholders(step['device']):
                continue
            if step['device'] not in self.descriptions:
                problems.append(f"{step['device']}: not registered")
                continue
            operation = self.descriptions[step['device']]['operations'].get(step['operation'])
            if operation is None:
                problems.append(f"{step['device']}.{step['operation']}: no such operation")
                continue
            extra = (step.get('arguments', {}).keys() | step.get('bindings', {}).keys() | step.get('sweep', {}).keys()) - \
                operation.get('parameters', {}).keys()
            if extra:
                problems.append(f"{step['device']}.{step['operation']}: no parameter " + ', '.join(sorted(extra)))
        found[operation_id] = list(dict.fromkeys(problems))
        return found[operation_id]

    def saved_operation(self, operation_id):
        with self.lock:
            record = self.library[operation_id]
            unavailable = self._unavailable(operation_id)
            return {**record, 'uses': self.uses.get(operation_id, {'calls': 0, 'completed': 0, 'failed': 0}),
                    **({'unavailable': unavailable} if unavailable else {})}

    def saved_operations(self):
        """One line per saved operation: enough to choose one; saved_operation gives its steps."""
        with self.lock:
            found, rows = {}, []
            for record in sorted(self.library.values(), key=lambda r: (r['layer'], r['id'])):
                unavailable = self._unavailable(record['id'], found)
                rows.append({'id': record['id'], 'layer': record['layer'], 'description': record['description'],
                             'parameters': list(record.get('parameters', {})), 'revision': record['revision'],
                             **({'program': True} if 'program' in record else {}),
                             'uses': self.uses.get(record['id'], {'calls': 0, 'completed': 0, 'failed': 0}),
                             **({'unavailable': unavailable} if unavailable else {})})
            return rows

    def apply_platform(self, definition, expected_revision=0):
        with self.lock:
            if self.stopping:
                raise ValueError('Service is stopping')
            definition = self.prepare_platform(definition)
            old = self.platforms.get(definition['id'])
            revision = old['revision'] if old else 0
            if type(expected_revision) is not int or expected_revision != revision:
                raise ValueError('Platform revision changed; current revision is ' + str(revision))
            if old and old['definition'] == definition:
                return self.platform_info(definition['id'])
            affected = set(definition['devices']) | set(old['definition']['devices'] if old else [])
            resource_ids = {r['id'] for r in definition['resources'] + (old['definition']['resources'] if old else [])}
            for p in self.platforms.values():
                for resource in p['definition']['resources']:
                    if resource['id'] in resource_ids:
                        affected.update(resource['devices'])
            for run in self.runs.values():
                used = {self.operations[o]['device'] for o in run['operations']}
                used.update(r[7:] for r in run['resources'] if r.startswith('device:'))
                if run['kind'] == 'package' and not run['resources']:
                    used.update(run.get('platform', {}).get('definition', {}).get('devices', self.devices))
                elif run['kind'] == 'external':
                    used.update(run['devices'])
                if affected & used or {'shared:' + r for r in resource_ids} & set(run['resources']):
                    raise ValueError('Wait for or cancel affected experiment before changing platform: ' + run['id'])
            record = {'id': definition['id'], 'revision': revision + 1, 'updated': now(), 'definition': definition}
            self.store.save_platform(record)
            self.platforms[record['id']] = record
            self.platform_resources = platform_model.resource_index(self.platforms)
            self.conditions.index()
            return self.platform_info(record['id'])

    def run_procedure(self, platform_id, procedure_id, request_id=None):
        with self.lock:
            record = self.platforms[platform_id]
            procedure = record['definition']['procedures'][procedure_id]
            return self.submit({'name': record['definition']['name'] + ': ' + procedure_id,
                'platform': platform_id, 'platform_revision': record['revision'],
                'steps': procedure['steps'], 'resources': procedure.get('resources', record['definition']['devices']),
                'requirements': procedure.get('requirements', {}),
                'request_id': request_id}, procedure=procedure_id)

    def register(self, record, persist=True, enrollment_key=None):
        # SDK startup can take time; it must not hold the experiment scheduler lock.
        with self.registration_lock:
            from .identification import same_registration
            record = json.loads(dumps(record))
            if persist and not any((not record.get('id') or old['id'] == record['id']) and same_registration(old, record)
                                   for old in self.config.get('devices', [])):
                operation_parameters(record.get('config', {}).get('operations', {}),
                                     'Instrument ' + record.get('id', '(new)') + ' operations')
            if 'calibration' in record:
                from .calibration import check
                check(record['calibration'])
            if record.get('instrument_profile'):
                from .identification import apply_profile
                record = apply_profile(record, self.instrument_profiles())
            device_id = record.get("id", "")
            if not device_id:
                config = record.get('config', {})
                identity = {k: config[k] for k in ('transport', 'host', 'port', 'resource', 'base_url', 'command') if k in config}
                identity['driver'] = record.get('driver', '')
                if len(identity) == 1:
                    identity['config'] = config
                suffix = hashlib.sha256(dumps(identity).encode()).hexdigest()[:10]
                stem = re.sub(r'[^a-z0-9]+', '-', str(config.get('name') or record.get('driver', 'instrument')).lower()).strip('-')[:50] or 'instrument'
                # Identity comes from the connection; display names may be localized.
                prior = next((d for d in self.config.get('devices', []) if same_registration(d, record)), None)
                device_id = prior['id'] if prior else stem + '-' + suffix
                record['id'] = device_id
            platform_model.name(device_id, 'Device ID')
            from .discovery import device_connection
            endpoint = device_connection(record)
            with self.enrollment.lock:
                if endpoint in self.enrollment.probing and endpoint != enrollment_key:
                    raise ValueError('Instrument identification is in progress for this connection. Retry when it finishes.')
            with self.lock:
                if self.stopping:
                    raise ValueError("Service is stopping")
                if device_id in self.devices:
                    if same_registration(self.device_records[device_id], record):
                        return self.descriptions[device_id]
                    raise ValueError("Device is already registered: " + device_id)
                if any(device_id == r['id'] for p in self.platforms.values() for r in p['definition']['resources']):
                    raise ValueError('Device ID already names a shared platform resource: ' + device_id)
                from .discovery import connection_conflict
                for old in self.device_records.values():
                    if connection_conflict(record, old):
                        raise ValueError("This instrument address already belongs to " + old["id"])
            saved_release = self.config.get('released_devices', {}).get(device_id)
            if saved_release:
                from .drivers import Released
                driver = Released(saved_release['description'])
            else:
                driver = make_driver(record, self.devices)
            try:
                description = allowed(record, driver.describe())
                if not isinstance(description.get("operations"), dict) or not description["operations"]:
                    raise ValueError("Driver must describe its operations")
                if any(not isinstance(spec, dict) for spec in description["operations"].values()):
                    raise ValueError("Invalid operation description")
                concurrency = description.get('concurrency', 1)
                if type(concurrency) is not int or concurrency < 1:
                    raise ValueError('Driver concurrency must be a positive integer')
                monitors = check_monitors(record, description)
                with self.lock:
                    if self.stopping:
                        raise ValueError("Service is stopping")
                    self.conditions.prepare_instrument(device_id, record, description)
                    self.devices[device_id] = driver
                    self.device_records[device_id] = record
                    metadata = {k:record[k] for k in METADATA if k in record}
                    self.descriptions[device_id] = json.loads(dumps({"id":device_id, "driver":record["driver"], **description, **metadata}))
                    self.descriptions[device_id]['revision'] = self.store.save_device(record)['revision']
                    self.allocation.limits['device:' + device_id] = concurrency
                    self.monitors.configure(device_id, monitors)
                    self.conditions.index()
                    if persist:
                        saved = {d['id']: d for d in self.config.get('devices', [])}
                        saved[device_id] = record
                        self.config["devices"] = list(saved.values())
                        save_json(self.home / "config.json", self.config)
                        with self.store.transaction():
                            self.store.event("device.registered", device_id=device_id)
                    self.offline.pop(device_id, None)
                    self.errors = [error for error in self.errors if error.get('device') != device_id]
                    from .discovery import connection_key, device_connection, normalize
                    key = device_connection(record)
                    for candidate in self.discovery.get('connections', []):
                        if key and connection_key(candidate) == key:
                            candidate.update(status='registered', id=device_id)
                    normalize(self.discovery)
                    return self.descriptions[device_id]
            except BaseException:
                if not any(getattr(other,'worker',None) is getattr(driver,'worker',object()) for other in self.devices.values()):
                    driver.close()
                raise

    def register_many(self, records):
        if not isinstance(records, list) or len(records) > 1000:
            raise ValueError('Provide up to 1000 instrument descriptions')
        results = []
        for index, record in enumerate(records):
            try:
                results.append({'index': index, 'device': self.register(record)})
            except Exception as error:
                results.append({'index': index, 'error': str(error)})
        return {'results': results}

    def configure_device(self, device_id, definition, expected_revision):
        from .device_configuration import configure
        return configure(self, device_id, definition, expected_revision)

    def identify(self, device_id, request_id=None):
        with self.lock:
            if self.device_records[device_id]['driver'] != 'scpi':
                raise ValueError('Use this instrument SDK to read its identity.')
            if 'identify' not in self.descriptions[device_id]['operations']:
                raise ValueError('This instrument has no identification query.')
            return self.submit({'name':'Instrument identity', 'request_id':request_id,
                                'steps':[{'id':'identity', 'device':device_id, 'operation':'identify'}]})

    def learn_identity(self, device_id, identity):
        from .identification import description, verify_identity
        record = self.device_records[device_id]
        if record.get('identity'):
            try:
                verify_identity(record['identity'], identity)
            except ValueError:
                self.devices[device_id].close()
                raise
        before = copy.deepcopy(record)
        enriched = description(identity, {}, self.instrument_profiles())
        record['identity'] = identity
        record['identity_source'] = 'scpi-idn'
        self.devices[device_id].expected_identity = identity
        if enriched.get('instrument_profile'):
            record.pop('profile_candidates', None)
            record['instrument_profile'] = enriched['instrument_profile']
            record['documentation'] = enriched.get('documentation', [])
            record['config']['operations'] = {**enriched['config'].get('operations', {}),
                                              **record['config'].get('operations', {})}
            if 'protocol' in enriched['config']:
                record['config']['protocol'] = {**enriched['config']['protocol'], **record['config'].get('protocol', {})}
            if enriched.get('conditions'):
                from .identification import builtin_operations, merge_conditions
                merge_conditions(record, enriched['conditions'], builtin_operations('scpi'))
            if not record.get('execution'):
                self.devices[device_id].config.update(record['config'])
        elif enriched.get('profile_candidates') and not record['config'].get('operations'):
            record['profile_candidates'] = enriched['profile_candidates']
        if record.get('execution') and before != record:
            driver = self.devices[device_id]
            driver.description = driver.worker.call('instrument_description',
                {'device': device_id, 'identity': identity, 'config': record['config']}, timeout=driver.worker.startup_timeout)
            driver.description.setdefault('resources', []).append('worker:' + driver.worker_id)
        metadata = {k:record[k] for k in METADATA if k in record}
        if before == record:
            return
        self.descriptions[device_id] = {'id':device_id, 'driver':record['driver'], **allowed(record, self.devices[device_id].describe()), **metadata}
        self.descriptions[device_id]['revision'] = self.store.save_device(record)['revision']
        self.config['devices'] = [record if d['id'] == device_id else d for d in self.config['devices']]
        save_json(self.home / 'config.json', self.config)
        self.conditions.index()

    def instrument_profiles(self):
        return self.instrument_catalog.profiles(self.config)

    def save_instrument_profile(self, profile):
        from .identification import validate_profile
        profile = json.loads(dumps(profile))
        validate_profile(profile)
        operation_parameters(profile['config']['operations'], 'Instrument profile ' + profile['id'] + ' operations')
        with self.lock:
            saved = {p['id']:p for p in self.config.get('instrument_profiles', [])}
            saved[profile['id']] = profile
            self.config['instrument_profiles'] = list(saved.values())
            save_json(self.home/'config.json', self.config)
            for record in list(self.device_records.values()):
                if record['driver'] == 'scpi' and record.get('identity'):
                    self.learn_identity(record['id'], record['identity'])
        self.enrollment.retry()
        return profile

    def replace_device(self, record, enrollment_key=None):
        """Retain an instrument ID when its verified SCPI endpoint changes."""
        with self.registration_lock:
            with self.lock:
                device_id = record['id']
                old = self.device_records[device_id]
                if old['driver'] != 'scpi' or record['driver'] != 'scpi':
                    raise ValueError('Automatic endpoint changes require the SCPI driver.')
                if any(o['device'] == device_id and o['state'] not in TERMINAL for o in self.operations.values()) or self.allocation.used['device:'+device_id]:
                    raise ValueError('This instrument is in use; its new endpoint will be applied after the experiment.')
                self.reconnecting.add(device_id)
            try:
                new = make_driver(record, self.devices)
                with self.lock:
                    self.devices[device_id].close()
                    self.devices[device_id] = new
                    self.device_records[device_id] = record
                    metadata = {k:record[k] for k in METADATA if k in record}
                    self.descriptions[device_id] = {'id':device_id, 'driver':record['driver'], **allowed(record, new.describe()), **metadata}
                    self.descriptions[device_id]['revision'] = self.store.save_device(record)['revision']
                    self.config['devices'] = [record if d['id'] == device_id else d for d in self.config['devices']]
                    save_json(self.home/'config.json', self.config)
                    self.conditions.index()
                    with self.store.transaction():
                        self.store.event('instrument.connection_changed', device_id=device_id, connection=enrollment_key)
                    return self.descriptions[device_id]
            finally:
                with self.lock:
                    self.reconnecting.discard(device_id)

    def _operation(self, run_id, step):
        from .errors import RequestError
        device_id, name = step["device"], step["operation"]
        # A step of a graph or a batch is named in what is refused; a single call has no ID.
        where = f"Step {step['id']}: " if step.get('id') else ''
        if device_id not in self.devices:
            raise ValueError(where + "Device is not registered: " + device_id + platform_model.closest(device_id, self.devices))
        if device_id in self.reconnecting or device_id in self.offline:
            raise ValueError('Device needs reconnection: ' + device_id)
        if device_id in self.released:
            raise RequestError('device_released', 'Reconnect this instrument before submitting operations.', device_id=device_id)
        specs = self.descriptions[device_id]["operations"]
        if name not in specs:
            raise ValueError(where + "Unknown operation: " + device_id + "." + name + platform_model.closest(name, specs))
        if not isinstance(step.get("arguments", {}), dict):
            raise ValueError(where + "arguments is an object")
        bindings = step.get('bindings', {})
        if set(bindings) & step.get('arguments', {}).keys() or set(bindings) - specs[name].get('parameters', {}).keys():
            raise ValueError(where + f'bindings name parameters of {device_id}.{name} not given in arguments')
        static = {**specs[name], 'parameters': {k: v for k, v in specs[name].get('parameters', {}).items() if k not in bindings}}
        try:
            args = parameters(static, step.get("arguments", {}))
        except ValueError as error:
            text = f"Step {step['id']} ({device_id}.{name}): {error}" if step.get('id') else str(error)
            raise RequestError('invalid_arguments', text, status=400, device_id=device_id, operation=name,
                               parameters=static.get('parameters', {})) from None
        requirements = {}
        for source in (self.descriptions[device_id], specs[name], step):
            for key, count in quantities(source.get('requirements', {}), self.capacities).items():
                requirements[key] = max(requirements.get(key, 0), count)
        if 'retry' in step:
            library.retried(step['retry'], f"Step {step['id']}" if step.get('id') else 'A call')
        calibration = self.descriptions[device_id].get('calibration')
        recorded = operation_fields(calibration, name)
        if recorded.get('calibration_expired') and calibration.get('enforce'):
            raise RequestError('calibration_expired', 'Renew this instrument’s calibration before using it.',
                               device_id=device_id, calibration=calibration['id'], valid_until=calibration['valid_until'])
        return {"id":identifier("op"), "run_id":run_id, "step":step.get("id"), "device":device_id,
                "operation":name, "arguments":args, "bindings": bindings, "requirements": requirements,
                "effect":specs[name].get('effect', 'change'),
                "after":platform_model.dependencies(step), "state":"queued", "created":now(),
                "resources":sorted(set(self._resources([device_id], name) + self._resources(step.get("resources", [])))),
                "request_id":step.get("request_id"),
                "device_revision":self.descriptions[device_id].get('revision'), **recorded,
                **{key: step[key] for key in ('derive', 'gathers', 'swept_by', 'continues', 'retake', 'retry') if step.get(key)}}

    def _described(self, device, operation):
        """A registered device operation's description, or None."""
        return self.descriptions.get(device, {}).get('operations', {}).get(operation)

    @staticmethod
    def _locate(operations):
        """What operations read from sweeps and ranges, how to gather a sweep and where to read whether it stopped
        name operations by ID."""
        ids = {op['step']: op['id'] for op in operations}
        for op in operations:
            if any(map(library.by_id, op['bindings'].values())):
                op['bindings'] = {key: library.locate(binding, ids) if library.by_id(binding) else binding
                                  for key, binding in op['bindings'].items()}
            if 'gathers' in op:
                op['gathers'] = {sweep: library.locate_gathering(found, ids) for sweep, found in op['gathers'].items()}
            if 'continues' in op:
                op['continues'] = [library.locate(marker, ids) for marker in op['continues']]

    def _recorded(self, operation_id):
        return self.operations.get(operation_id) or self.store.get('operations', operation_id)

    def _assembled(self, run):
        """A step graph's result from its operations completed so far: each Basic Operation's result under its
        step, each use of a saved operation its named result, and each sweep what it gathered; the operations
        of a sweep's points and of maintenance inside the experiment stay in their records."""
        ops = (self.operations[oid] for oid in run['operations'])
        by_step = {op['step']: op for op in ops if not op.get('maintenance')}
        sweeps = run.get('sweeps', {})
        result = {step: op.get('result') for step, op in by_step.items()
                  if op['state'] == 'completed' and not op.get('swept_by')}
        if run.get('calls') or sweeps:
            result.update(library.results(run.get('calls', {}), sweeps, by_step, self.home / 'runs' / run['id'] / 'output'))
        return result

    def run_result(self, run_id):
        """The saved result of an experiment; while a step graph runs, what its completed operations give so far."""
        with self.lock:
            run = self.runs.get(run_id)
            if run is not None and run['kind'] == 'workflow' and run['state'] in {'queued', 'running'}:
                return self._assembled(run) or None
        return self.store.get('runs', run_id).get('result')

    def submit(self, spec, package=None, procedure=None):
        """Accept an experiment. ``procedure`` names the saved platform procedure it carries out."""
        from .errors import RequestError
        with self.lock:
            if self.stopping:
                raise ValueError("Service is stopping")
            spec = json.loads(dumps(spec))
            unknown = sorted(spec.keys() - set(platform_model.SUBMISSION)) if isinstance(spec, dict) else None
            if unknown is None:
                raise ValueError('Submit an object: ' + ', '.join(platform_model.SUBMISSION))
            if procedure:
                spec['procedure'] = procedure
            preview = spec.pop('preview', False)
            if not isinstance(preview, bool):
                raise ValueError('preview is true to see what a step graph would do without running it')
            program = None
            if package is None and spec.get('kind', 'workflow') == 'workflow':
                # A saved Workflow Operation that is a program runs as this experiment.
                program = library.program_step(spec.get('steps'), self.library.get)
                if program:
                    package = (self.home / 'library' / program['operation'] / f"{program['revision']}.zip").read_bytes()
            kind = 'package' if package is not None else spec.get('kind', 'workflow')
            if kind not in {'workflow', 'package', 'external'} or (kind == 'package' and package is None):
                raise ValueError('Use a workflow, an external experiment or a submitted package')
            if kind == 'external':
                if spec.get('steps'):
                    raise ValueError('Submit external experiment operations through its run ID')
                if not isinstance(spec.get('request_id'), str) or not spec['request_id'].strip():
                    raise ValueError('Provide request_id for the external experiment')
            previous = None if preview else self.store.request("runs", spec.get("request_id"))
            if previous:
                if previous['kind'] != kind or previous["spec"] != spec or (package is not None and not program and (self.home / "runs" / previous["id"] / "package.zip").read_bytes() != package):
                    raise ValueError("request_id already names a different experiment")
                return previous
            if unknown:
                raise ValueError('A submission has no ' + ' or '.join(unknown) + '; it takes '
                                 + ', '.join(platform_model.SUBMISSION))
            if not procedure and (kind == 'workflow' or program):
                # New graphs and program wrappers are strict. Identical retries and saved procedures retain
                # their accepted fields; loading an old revision is not a new write.
                platform_model.fields(spec.get('steps'))
                if program:
                    ignored = sorted(spec['steps'][0].keys() - {'id', 'operation', 'arguments', 'after', 'bindings'})
                    if ignored:
                        raise ValueError(f"Step {program['step']}: a saved program does not take " + ', '.join(ignored)
                                         + '; put resources and requirements on the experiment; express its loop '
                                         'and retries in the program')
            if len(self.runs) >= int(self.config.get('max_active_runs', 10000)):
                raise QueueFull('The service has reached its configured number of active experiments')
            run_id = identifier("run")
            platform_id = spec.get('platform')
            platform = self.platforms.get(platform_id) if platform_id else None
            if platform_id and platform is None:
                raise ValueError('Unknown platform: ' + str(platform_id))
            if platform and spec.get('platform_revision', platform['revision']) != platform['revision']:
                raise ValueError('Platform revision changed')
            run = {"id":run_id, "state":"queued", "created":now(), "request_id":spec.get("request_id"),
                   "kind":kind, "name":spec.get("name", program['operation'] if program else "Experiment"),
                   "project":spec.get("project"), "experiment":spec.get("experiment"), "spec":spec,
                   "parameters":program['arguments'] if program else spec.get("parameters", {}),
                   "resources":self._resources(spec.get("resources", [])),
                   "requirements":quantities(spec.get('requirements', {}), self.capacities),
                   "devices":{}, "operations":[], "operation_count":0, "result":None}
            if platform:
                run['platform'] = json.loads(dumps(platform))
                outside = set(spec.get('resources', [])) & (self.devices.keys() - set(platform['definition']['devices']))
                if outside:
                    raise ValueError('Reserved devices must belong to the selected platform')
            operations = []
            if program:
                run['program_call'] = program
            if kind == 'workflow':
                # A step without device uses a saved Standard or Workflow Operation; a swept step runs at each point.
                steps, calls, sweeps = library.expand(spec.get("steps", []), self.library.get, self._described)
                platform_model.steps(steps)
                operations = [self._operation(run_id, s) for s in steps]
                if calls:
                    run['calls'] = calls
                    for op, step in zip(operations, steps):
                        if 'calls' in step:
                            op['calls'] = step['calls']
                if sweeps:
                    run['sweeps'] = sweeps
                self._locate(operations)
                if platform and any(op['device'] not in platform['definition']['devices'] for op in operations):
                    raise ValueError('Workflow devices must belong to the selected platform')
                if run["resources"] or run['requirements']:
                    run["resources"] = sorted(set(run["resources"]).union(*(op["resources"] for op in operations)))
                    for op in operations:
                        for key, count in op['requirements'].items():
                            if key[5:] in spec.get('requirements', {}) and count > run['requirements'][key]:
                                raise ValueError('Step needs more capacity than the experiment reservation: ' + key)
                            run['requirements'][key] = max(run['requirements'].get(key, 0), count)
            if len(self.operations) + len(operations) > int(self.config.get('max_active_operations', 100000)):
                raise QueueFull('The service has reached its configured number of active operations')
            required = self.conditions.required(spec, run.get('calls'), program)
            if 'upkeep' in spec:
                from .conditions import experiment_upkeep
                if not required:
                    raise ValueError('upkeep keeps the conditions an experiment requires; name them in requires')
                run['upkeep'] = experiment_upkeep(spec['upkeep'])
                amounts, _ = self.conditions.requirements(required)
                if kind != 'workflow':
                    missing = {k: n for k, n in amounts.items() if n > self._amounts(run, reserve=True).get(k, 0)}
                    if missing:
                        raise RequestError('maintenance_resources', 'Reserve the devices and resource quantities used by '
                                           'maintenance when starting an experiment that keeps its conditions.', resources=missing)
                elif run['resources'] or run['requirements']:
                    # A step graph that reserves what its steps use reserves what keeping its conditions uses too.
                    run['resources'] = sorted(set(run['resources']) | {k for k in amounts if not k.startswith('pool:')})
                    for key, count in amounts.items():
                        if key.startswith('pool:'):
                            run['requirements'][key] = max(run['requirements'].get(key, 0), count)
            if preview:
                return self._preview(kind, operations, required)
            if required:
                # The experiment is accepted only while what it relies on holds; the record keeps what that was.
                # One that keeps its conditions is accepted, and keeps them before its first operation.
                try:
                    run['conditions'] = self.conditions.require(required)
                except RequestError as error:
                    if error.code != 'conditions_not_held' or 'upkeep' not in run:
                        raise
                    run['conditions'] = {}
                run['requires'] = required
                for op in operations:
                    op['requires'] = required
            used = {op['device'] for op in operations}
            if package is not None:
                used = set(platform['definition']['devices']) if platform else (set(spec.get('resources', [])) & self.devices.keys() or self.devices.keys())
            elif kind == 'external':
                used = set(platform['definition']['devices']) if platform else set(spec.get('resources', [])) & self.devices.keys()
            run['devices'] = copy.deepcopy({name: self.descriptions[name] for name in used})
            run['device_configurations'] = copy.deepcopy({name: self.device_records[name] for name in used})
            directory = self.home / "runs" / run_id
            directory.mkdir(parents=True)
            (directory / "output").mkdir()
            if package is not None:
                run["package"] = self._unpack(directory, package)
            save_json(directory / "experiment.json", {"id":run_id, **spec})
            run["operations"] = [op["id"] for op in operations]
            run['operation_count'] = len(operations)
            with self.store.transaction():
                self.store.put("runs", run)
                self.store.event("run.accepted", run_id, name=run["name"], kind_of_run=run["kind"])
                for op in operations:
                    self.store.put("operations", op)
                    self.store.event("operation.queued", run_id, operation_id=op["id"], device=op["device"], operation=op["operation"])
            self.runs[run_id] = run
            self.operations.update({op["id"]:op for op in operations})
            self._index_run(run)
            self.conditions.rely(run_id, required)
            if run.get('upkeep') and kind == 'external':
                self.conditions.keep(run, required)
            self.wake.set()
            return json.loads(dumps(run))

    def _unpack(self, directory, data):
        package_dir = directory / "package"
        package_dir.mkdir()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(f.file_size for f in archive.infolist()) > 128 * 1024 * 1024:
                raise ValueError("Expanded package exceeds 128 MiB")
            for member in archive.infolist():
                path = package_dir / relative_path(member.filename.rstrip("/"))
                if member.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(member))
        manifest_path = package_dir / "package.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"command":["{python}", "experiment.py"]}
        command = manifest.get("command", ["{python}", "experiment.py"])
        if not isinstance(command, list) or not command or any(not isinstance(s, str) or not s for s in command):
            raise ValueError("package.json command must be a nonempty string array")
        manifest["command"] = command
        (directory / "package.zip").write_bytes(data)
        return manifest

    def invoke(self, run_id, step):
        with self.lock:
            step = json.loads(dumps({'arguments': {}, **step}))
            old = self.store.request("operations", step.get("request_id"))
            if old:
                if old['run_id'] != run_id:
                    raise ValueError('request_id already names a different operation')
                saved_run = self.runs.get(run_id) or self.store.get('runs', run_id)
                same = (old.get('request') == step if 'request' in old else
                        old['device'] == step['device'] and old['operation'] == step['operation'] and
                        old['arguments'] == parameters(saved_run['devices'][step['device']]['operations'][step['operation']], step['arguments']))
                if not same:
                    raise ValueError("request_id already names a different operation")
                return old
            unknown = sorted(step.keys() - set(platform_model.CALL))
            if unknown:
                # With argument for arguments, an operation whose parameters all have defaults would run on them.
                raise ValueError('A call has no ' + ' or '.join(unknown) + '; it takes ' + ', '.join(platform_model.CALL))
            run = self.runs.get(run_id)
            if self.stopping or not run or not (run['kind'] == 'package' and run['state'] == 'running' or
                               run['kind'] == 'external' and run['state'] in {'queued', 'running'}):
                raise ValueError("Operations require an active external experiment or running package")
            if len(self.operations) >= int(self.config.get('max_active_operations', 100000)):
                raise QueueFull('The service has reached its configured number of active operations')
            op = self._operation(run_id, step)
            if run.get('requires'):
                op['requires'] = run['requires']
            if run.get('platform') and op['device'] not in run['platform']['definition']['devices']:
                raise ValueError('Operation device must belong to the selected platform')
            if run['resources'] or run.get('requirements'):
                reserved = self._amounts(run, reserve=True)
                if any(count > reserved.get(key, 0) for key, count in self._amounts(op).items()):
                    raise ValueError('Include every device and resource quantity used by this experiment at submission')
            if op["after"]:
                raise ValueError("Programs express dependencies by waiting for operation results")
            if op['bindings']:
                raise ValueError('Programs pass results directly as operation arguments')
            if run.get('requires') and not self.conditions.keep(run, run['requires']):
                self.conditions.require(run['requires'], run_id)
            op['request'] = step
            if op['device'] not in run['devices']:
                run['devices'][op['device']] = copy.deepcopy(self.descriptions[op['device']])
                run['device_configurations'][op['device']] = copy.deepcopy(self.device_records[op['device']])
            run["operations"].append(op["id"])
            run['operation_count'] = run.get('operation_count', 0) + 1
            run['involved_resources'] = sorted(set(run['involved_resources']) | self._amounts(op).keys())
            with self.store.transaction():
                self.store.put("runs", run); self.store.put("operations", op)
                self.store.event("operation.queued", run_id, operation_id=op["id"], device=op["device"], operation=op["operation"])
            self.operations[op["id"]] = op
            self.ready[run_id][op['id']] = None
            self.remaining[run_id] += 1
            for key, count in self._amounts(op).items():
                self.minimum_capacities[run_id][key] = max(self.minimum_capacities[run_id].get(key, 0), count)
            self.wake.set()
            return json.loads(dumps(op))

    def finish(self, run_id):
        from .errors import RequestError
        with self.lock:
            run = self.runs.get(run_id) or self.store.get('runs', run_id)
            if run['kind'] != 'external':
                raise RequestError('not_external', 'Only external experiments are finished by their client.')
            if run['state'] == 'completed':
                return copy.deepcopy(run)
            if run['state'] not in {'queued', 'running'}:
                raise RequestError('experiment_not_active', 'Read the experiment state before finishing it.')
            if self.remaining[run_id]:
                raise RequestError('operations_pending', 'Wait for accepted instrument operations before finishing.')
            self._finish(run, 'completed')
            self.wake.set()
            return copy.deepcopy(run)

    def _save_run(self, run, kind):
        with self.store.transaction():
            self.store.put("runs", run)
            self.store.event(kind, run["id"], state=run["state"], error=run.get("error"))

    def cancel(self, run_id):
        with self.lock:
            run = self.runs.get(run_id)
            if not run:
                return self.store.get("runs", run_id)
            if run["state"] in TERMINAL:
                return json.loads(dumps(run))
            run["state"] = "cancelling"
            run["end_state"] = "cancelled"
            self._save_run(run, "run.cancelling")
            self._stop_pending(run)
            self.wake.set()
            return json.loads(dumps(run))

    def _stop_pending(self, run):
        for op_id in run["operations"]:
            op = self.operations[op_id]
            if op["state"] == "queued":
                op.update(state="cancelled", ended=now())
                self._operation_ended(op)
                with self.store.transaction():
                    self.store.put("operations", op)
                    self.store.event("operation.cancelled", run["id"], operation_id=op_id)
            elif op["state"] == "running":
                self.cancellations[op_id].set()
        process = self.processes.get(run["id"])
        if process and process.poll() is None:
            self._terminate(process)
            run.setdefault("terminate_at", time.monotonic())

    @staticmethod
    def _terminate(process, force=False):
        if hasattr(process, 'terminate') and hasattr(process, 'done'):
            process.terminate()
            return
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            else:
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except (ProcessLookupError, OSError, subprocess.SubprocessError):
            pass

    def _start_package(self, run):
        directory = self.home / "runs" / run["id"]
        env = os.environ.copy()
        env.update(IOP_ENDPOINT=self.endpoint, IOP_TOKEN=self.config["token"], IOP_RUN_ID=run["id"],
                   IOP_RUN_DIR=str(directory / "output"), IOP_PARAMETERS_JSON=dumps(run["parameters"]),
                   PYTHONUNBUFFERED="1")
        if run.get('platform'):
            env['IOP_PLATFORM_ID'] = run['platform']['id']
            env['IOP_PLATFORM_REVISION'] = str(run['platform']['revision'])
        source_path = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = source_path + os.pathsep + env.get("PYTHONPATH", "")
        command = [sys.executable if part == "{python}" else part for part in run["package"]["command"]]
        from .jobs import NativeJob
        from .process_driver import executable
        try:
            executable()
        except ValueError:
            pass
        else:
            self.processes[run['id']] = NativeJob(command, directory / 'package', env,
                directory / 'stdout.txt', directory / 'stderr.txt',
                timeout=run['package'].get('timeout', 0), notify=self.wake.set)
            return
        with open(directory / "stdout.txt", "wb") as out, open(directory / "stderr.txt", "wb") as err:
            options = {"creationflags":subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session":True}
            self.processes[run["id"]] = subprocess.Popen(command, cwd=directory / "package", env=env,
                                                          stdin=subprocess.DEVNULL, stdout=out, stderr=err, **options)

    def _finish(self, run, state):
        call = run.get('program_call')
        if call:
            run['result'] = {call['step']: run.get('result')}
            self._record_use(call['operation'], call['revision'], run['id'], call['step'], state)
        run.update(state=state, ended=now())
        run.pop("terminate_at", None)
        if state == 'interrupted':
            self.interrupted[run['id']] = {key: run[key] for key in ('id', 'name', 'kind', 'ended') if key in run}
        self.allocation.finish(run['id'])
        directory = self.home / "runs" / run["id"]
        save_json(directory / "run.json", run)
        for root in [directory / "output"]:
            for path in root.rglob("*"):
                if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()):
                    self._file_record(run["id"], path, "output/" + path.relative_to(root).as_posix())
        for name in ("stdout.txt", "stderr.txt", "experiment.json", "package.zip"):
            if (directory / name).is_file():
                self._file_record(run["id"], directory / name, name)
        self._save_run(run, "run." + state)
        # Completed records live in SQLite; only active work remains in memory.
        for op_id in run["operations"]:
            self.operations.pop(op_id, None)
            self.dependents.pop(op_id, None)
            self.remaining_dependencies.pop(op_id, None)
        self.ready.pop(run['id'], None)
        self.remaining.pop(run['id'], None)
        self.step_ids.pop(run['id'], None)
        self.ranges.pop(run['id'], None)
        self.doubted.pop(run['id'], None)
        self.retaking.pop(run['id'], None)
        self.call_progress.pop(run['id'], None)
        self.minimum_capacities.pop(run['id'], None)
        process = self.processes.get(run['id'])
        if process and hasattr(process, 'close'):
            process.close()
        self.processes.pop(run["id"], None)
        self.runs.pop(run["id"], None)

    def _file_record(self, run_id, path, name, operation_ids=None):
        existing = self.store.artifact_named(run_id, name)
        if existing and existing.get('stream'):
            return existing
        item = {**(existing or {}), "id":existing['id'] if existing else identifier("file"), "run_id":run_id, "name":name, "size":path.stat().st_size,
                "media_type":mimetypes.guess_type(name)[0] or "application/octet-stream",
                "created":existing['created'] if existing else now()}
        if operation_ids is not None:
            item['operation_ids'] = operation_ids
        with self.store.transaction():
            self.store.db.execute('INSERT INTO artifacts(id,run_id,body,name) VALUES(?,?,?,?) '
                'ON CONFLICT(id) DO UPDATE SET body=excluded.body', (item['id'], run_id, dumps(item), name))
            if not existing:
                self.store.event("artifact.saved", run_id, artifact_id=item["id"], name=name, size=item["size"])
        return item

    def save_result(self, run_id, result):
        with self.lock:
            run = self.runs.get(run_id)
            if not run or not (run['state'] == 'running' or run['kind'] == 'external' and run['state'] == 'queued'):
                raise ValueError("Run is not running")
            run["result"] = json.loads(dumps(result))
            save_json(self.home / "runs" / run_id / "output" / "result.json", result)
            self._save_run(run, "run.result")
            return result

    def checkpoint(self, run_id, name, state):
        from .recovery import checkpoint
        return checkpoint(self, run_id, name, state)

    def resume(self, run_id, request_id):
        from .recovery import resume
        return resume(self, run_id, request_id)

    def artifact(self, run_id, name, data, operation_ids=()):
        return self.artifact_stream(run_id, name, io.BytesIO(data), len(data), operation_ids)

    def artifact_stream(self, run_id, name, stream, length, operation_ids=()):
        if type(length) is not int or length < 0:
            raise ValueError('File length must be a nonnegative integer')
        self.store.get('runs', run_id)
        if not isinstance(operation_ids, (list, tuple)) or any(not isinstance(oid, str) for oid in operation_ids):
            raise ValueError('operation_ids must be an array of operation IDs')
        operation_ids = list(dict.fromkeys(operation_ids))
        for oid in operation_ids:
            op = self.store.get('operations', oid)
            if op['run_id'] != run_id or op['state'] != 'completed':
                raise ValueError('Link files to completed operations in the same experiment')
        rel = relative_path(name)
        incoming = self.home / 'incoming'
        incoming.mkdir(exist_ok=True)
        temporary = incoming / identifier('upload')
        try:
            with temporary.open('xb') as file:
                left = length
                while left:
                    chunk = stream.read(min(left, 256 * 1024))
                    if not chunk:
                        raise ValueError('File transfer ended before the declared length')
                    file.write(chunk)
                    left -= len(chunk)
            with self.lock:
                path = self.home / 'runs' / run_id / 'output' / rel
                root = (self.home / 'runs' / run_id / 'output').resolve()
                if not path.resolve().is_relative_to(root):
                    raise ValueError('File must remain in the run output directory')
                if path.exists():
                    raise ValueError('An output file already has this name')
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary.rename(path)
                return self._file_record(run_id, path, 'output/' + rel.as_posix(), operation_ids)
        finally:
            temporary.unlink(missing_ok=True)

    def reconnect(self, device_id):
        with self.registration_lock:
            with self.lock:
                pending = next((d for d in self.config.get('devices', []) if d['id'] == device_id), None) if device_id not in self.devices else None
            if pending is not None:
                self.register(pending)
                return {'devices': [device_id], 'state': 'connected'}
            with self.lock:
                driver = self.devices[device_id]
                worker = getattr(driver, 'worker', None)
                affected = {name for name, other in self.devices.items()
                            if name == device_id or (worker is not None and getattr(other, 'worker', None) is worker)}
                affected.update(self.config.get('released_devices', {}).get(device_id, {}).get('group', []))
                if any(o['device'] in affected and o['state'] not in TERMINAL for o in self.operations.values()):
                    raise ValueError('Wait for or cancel device operations before reconnecting')
                if any(self.allocation.used['device:' + name] for name in affected):
                    raise ValueError('An experiment still reserves this device')
                self.reconnecting.update(affected)
                registry = {k: v for k, v in self.devices.items() if k not in affected}
            created = {}
            try:
                driver.close()
                for name in sorted(affected):
                    created[name] = make_driver(self.device_records[name], registry)
                    registry[name] = created[name]
                    if allowed(self.device_records[name], created[name].describe()) != {k: v for k, v in self.descriptions[name].items() if k not in {'id', 'driver', 'revision', *METADATA}}:
                        raise ValueError('Driver description changed; update the device configuration before reuse')
                with self.lock:
                    self.devices.update(created)
                    for name in affected:
                        self.offline.pop(name, None)
                    self.released.difference_update(affected)
                    for name in affected:
                        self.config.get('released_devices', {}).pop(name, None)
                    save_json(self.home / 'config.json', self.config)
                    with self.store.transaction():
                        self.store.event('devices.reconnected', devices=sorted(affected))
                return {'devices': sorted(affected), 'state': 'connected'}
            except Exception as error:
                for new in created.values():
                    new.close()
                with self.lock:
                    self.offline.update({name: str(error) for name in affected})
                raise
            finally:
                with self.lock:
                    self.reconnecting.difference_update(affected)

    def release_device(self, device_id):
        """Release an idle connection and keep it released until explicit reconnection."""
        from .errors import RequestError
        with self.registration_lock:
            with self.lock:
                driver = self.devices[device_id]
                worker = getattr(driver, 'worker', None)
                affected = {name for name, other in self.devices.items()
                            if name == device_id or worker is not None and getattr(other, 'worker', None) is worker}
                affected.update(self.config.get('released_devices', {}).get(device_id, {}).get('group', []))
                if (any(o['device'] in affected and o['state'] not in TERMINAL for o in self.operations.values()) or
                        any(self.allocation.used['device:' + name] for name in affected)):
                    raise RequestError('device_busy', 'Finish or cancel accepted operations before releasing this connection.')
                snapshots = {name: {'description': self.devices[name].describe(), 'group': sorted(affected)} for name in affected}
                self.reconnecting.update(affected)
            try:
                for name in affected:
                    self.devices[name].close()
                with self.lock:
                    self.released.update(affected)
                    self.config.setdefault('released_devices', {}).update(snapshots)
                    save_json(self.home / 'config.json', self.config)
                    with self.store.transaction():
                        self.store.event('devices.released', devices=sorted(affected))
                return {'devices': sorted(affected), 'state': 'released'}
            except Exception as error:
                with self.lock:
                    self.offline.update({name: str(error) for name in affected})
                raise
            finally:
                with self.lock:
                    self.reconnecting.difference_update(affected)

    def _preview(self, kind, operations, required):
        """What a step graph would do, without running it: how many operations of each instrument, the conditions
        it relies on and their state, and how long its operations would take one after another, each at the
        median its kind took over the most recent ones."""
        if kind != 'workflow':
            raise ValueError('preview shows what a step graph would do')
        counts = {}
        for op in operations:
            key = op['device'] + '.' + op['operation']
            counts[key] = counts.get(key, 0) + 1
        took = self.store.durations()
        return {'preview': True, 'operations': len(operations),
                'by_operation': {key: {'operations': n, **({'median_s': took[key][0], 'from': took[key][1]} if key in took else {})}
                                 for key, n in counts.items()},
                'estimated_s': sum(took[key][0] * n for key, n in counts.items()) if counts.keys() <= took.keys() else None,
                'requires': {c: self.conditions.state(c)['state'] for c in self.conditions.closure(required)}}

    def _ends(self, run):
        """When a running step graph would end at its pace so far: the time it has run, scaled by what is left over
        what it has done since it started or continued, each operation counting as long as those of its kind took
        in it. None until each kind left has ended once."""
        took, left, done, start = {}, {}, 0, stamp(run['started'])
        for oid in run['operations']:
            op = self.operations[oid]
            if op.get('maintenance'):
                continue
            kind = (op['device'], op['operation'])
            if op['state'] == 'completed' and op.get('started') and op.get('ended'):
                began = stamp(op['started'])
                seconds = stamp(op['ended']) - began
                total, count = took.get(kind, (0, 0))
                took[kind] = (total + seconds, count + 1)
                if began >= start:
                    done += seconds
            elif op['state'] in ('queued', 'running'):
                left[kind] = left.get(kind, 0) + 1
        if not left or not done or left.keys() - took.keys():
            return None
        rest = sum(count * took[kind][0] / took[kind][1] for kind, count in left.items())
        clock = time.time()
        return datetime.fromtimestamp(clock + (clock - start) * rest / done, timezone.utc).isoformat()

    def run_info(self, run_id, details=True):
        result = self.store.get('runs', run_id, summary=not details)
        with self.lock:
            run = self.runs.get(run_id)
            ends = self._ends(run) if run is not None and run['kind'] == 'workflow' and run['state'] == 'running' else None
        if ends:
            result['estimated_end'] = ends
        result['operation_states'] = self.store.operation_states(run_id)
        result['progress'] = {name: {k: v for k, v in record.items() if k != 'state'}
                              for name, record in self.store.checkpoints(run_id).items()}
        result['can_resume'] = (result['state'] in TERMINAL - {'completed'} and
            (result['kind'] in ('external', 'workflow') or result['kind'] == 'package' and
             result.get('package', {}).get('resume') is True and bool(result['progress'])))
        if details:
            result['operation_records'] = list(reversed(self.store.list('operations', run_id, 10000)))
        return result

    def _progress(self, op_id, value):
        with self.progress_lock:
            self.progress_updates[op_id] = value
            current = time.monotonic()
            if current - self.progress_times.get(op_id, 0) >= 0.2:
                self.progress_times[op_id] = current
                self.wake.set()

    def _tick(self):
        with self.progress_lock:
            progress, self.progress_updates = self.progress_updates, {}
        if progress:
            with self.store.transaction():
                for op_id, value in progress.items():
                    op = self.operations.get(op_id)
                    if op and op['state'] == 'running':
                        op['progress'] = value
                        self.store.put('operations', op)
                        self.store.event('operation.progress', op['run_id'], operation_id=op_id, progress=value)
        # Only completed futures and newly ready dependencies enter the scheduler.
        while not self.completed.empty():
            op_id = self.completed.get()
            future = self.futures[op_id]
            op = self.operations[op_id]; run = self.runs[op['run_id']]
            try:
                op['result'], derived = future.result()
                if derived is not None:
                    op['derived'] = derived
                if op['operation'] == 'identify' and self.device_records[op['device']]['driver'] == 'scpi' and op['result'].get('identity'):
                    self.learn_identity(op['device'], op['result']['identity'])
                op['state'] = 'completed'
                self._qualify(op)
            except CancelledError:
                op['state'] = 'cancelled'
                if run['state'] == 'running':
                    run.update(state='cancelling', end_state='cancelled')
                    self._stop_pending(run)
            except BaseException as error:
                from .process_driver import DriverUnavailable
                from .drivers import UnconfirmedChange
                if (op.get('retry') and self.tries.get(op_id, 0) < op['retry']['times'] and run['state'] == 'running'
                        and op_id not in self.refused and not isinstance(error, (DriverUnavailable, UnconfirmedChange))):
                    self._again(op, error)
                    continue
                op.update(state='failed', error=f'{type(error).__name__}: {error}')
                if isinstance(error, (DriverUnavailable, UnconfirmedChange)):
                    op['outcome_unknown'] = True
                    if isinstance(error, DriverUnavailable):
                        worker = getattr(self.devices[op['device']], 'worker', None)
                        self.offline.update({name: str(error) for name, driver in self.devices.items()
                            if name == op['device'] or (worker is not None and getattr(driver, 'worker', None) is worker)})
                    affected = self._amounts(op).keys() | self._amounts(run, reserve=True).keys()
                    self.store.hold_resources(affected, op_id, str(error))
                    self.resource_holds = self.store.resource_holds()
                if run['state'] == 'running':
                    run.update(state='cancelling', end_state='failed', error=op['error'])
                    self._save_run(run, 'run.failing')
                    self._stop_pending(run)
            op['ended'] = now()
            output = self.home / 'runs' / run['id'] / 'output'
            written = output / 'operations' / op_id
            if written.is_dir():
                # Files a driver wrote for this operation are listed and downloadable as soon as it ends.
                op['files'] = [{key: item[key] for key in ('id', 'name', 'size')} for item in (
                    self._file_record(run['id'], path, 'output/' + path.relative_to(output).as_posix(), [op_id])
                    for path in sorted(written.rglob('*'))
                    if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(written.resolve()))]
            self.communication[op['device']] = {'time': op['ended'], 'operation_id': op_id,
                'state': op['state'], 'error': op.get('error')}
            with self.store.transaction():
                self.store.put('operations', op)
                self.store.event('operation.' + op['state'], run['id'], operation_id=op_id,
                                 result=op.get('result'), error=op.get('error'))
            self._operation_ended(op)
            self._release(op_id)
            if run['kind'] in {'package', 'external'}:
                # Long-lived programs keep only in-flight calls in memory. All
                # completed operations remain in the indexed SQLite history.
                run['operations'].remove(op_id)
                self.operations.pop(op_id)
                with self.store.transaction():
                    self.store.put('runs', run)
        for run in list(self.runs.values()):
            unresolved = set(run['involved_resources']) & self.resource_holds.keys()
            observation = bool(unresolved) and run['kind'] == 'workflow' and all(
                self.operations[oid].get('effect') == 'observe' or not (set(self.operations[oid]['resources']) & unresolved)
                for oid in run['operations'])
            if run['state'] in {'queued', 'running'} and unresolved and not observation:
                run.update(state='cancelling', end_state='interrupted',
                           error='Device outcome is unresolved: ' + ', '.join(sorted(unresolved)))
                self._stop_pending(run)
            if run['state'] == 'queued':
                continue
            process = self.processes.get(run['id'])
            if process and run['state'] == 'cancelling' and process.poll() is None and time.monotonic() - run.get('terminate_at', time.monotonic()) > 3:
                self._terminate(process, force=True)
            process_done = process is None or process.poll() is not None
            if run['kind'] == 'package' and process_done and process is not None:
                run['exit_code'] = process.returncode
                if process.returncode and run['state'] == 'running':
                    run.update(state='cancelling', end_state='failed', error='Experiment process exited with code ' + str(process.returncode))
                    self._stop_pending(run)
            if self.remaining[run['id']] == 0 and process_done:
                if run['state'] == 'cancelling':
                    self._finish(run, run.get('end_state', 'cancelled'))
                elif run['kind'] == 'workflow' or process is not None:
                    if run['kind'] == 'workflow':
                        run['result'] = self._assembled(run)
                    self._finish(run, 'completed')
        if self.stopping:
            return
        waiting = set()
        package_slots = self.package_count - len(self.processes)
        for run in list(self.runs.values()):
            if run['state'] != 'queued':
                continue
            if run['kind'] == 'package' and package_slots <= 0:
                continue
            amounts = self._amounts(run, reserve=True)
            unavailable = [k for k, v in self.minimum_capacities[run['id']].items() if v > self.allocation.limit(k)]
            if unavailable:
                run.update(state='cancelling', end_state='failed', error='Configured capacity no longer fits the experiment: ' + ', '.join(sorted(set(unavailable))))
                self._stop_pending(run)
                continue
            if not self.allocation.can_reserve(amounts, waiting):
                waiting.update(amounts)
                continue
            missing = [self.operations[oid]['device'] for oid in run['operations'] if self.operations[oid]['device'] not in self.devices]
            if missing:
                run.update(state='cancelling', end_state='failed', error='Missing registered devices: ' + ', '.join(missing))
                self._stop_pending(run)
                continue
            self.allocation.reserve(run['id'], amounts)
            run.update(state='running', started=now())
            self._save_run(run, 'run.started')
            if run['kind'] == 'package':
                try:
                    if run.get('upkeep'):
                        self.conditions.keep(run, run.get('requires', []))
                    self._start_package(run)
                    package_slots -= 1
                except Exception as error:
                    run.update(state='cancelling', end_state='failed', error=str(error))
        maintaining = {job['run_id'] for job in self.conditions.jobs.values() if job['state'] == 'running'}
        # Points in doubt are taken again once no pass runs in their experiment, before its other operations.
        for rid in [rid for rid, doubted in self.doubted.items()
                    if doubted and rid not in maintaining and self.runs.get(rid, {}).get('state') == 'running']:
            try:
                self._retake(self.runs[rid], list(self.doubted.pop(rid)))
            except ValueError as error:
                # The points stay in doubt, as without retake, such as when their instrument went offline.
                self.errors.append({'time': now(), 'error': f'Retaking points of {rid}: {error}'})
        # Round-robin between experiments; a large ready set cannot monopolize starts.
        run_ids = [rid for rid, ready in self.ready.items() if ready and self.runs[rid]['state'] == 'running']
        if self.last_scheduled_run in run_ids:
            index = run_ids.index(self.last_scheduled_run) + 1
            run_ids = run_ids[index:] + run_ids[:index]
        candidates = deque((rid, iter(self.ready[rid])) for rid in run_ids)
        # An experiment that keeps its conditions starts no operation while a pass is due; the pass starts inside
        # it once none of its operations is running, and its operations continue after the pass.
        for rid in run_ids:
            run = self.runs[rid]
            if run.get('upkeep') and rid not in maintaining and self.conditions.keep(run, run['requires']):
                maintaining.add(rid)
        blocked = set()
        dispatch, stops = [], {}
        pooled = len(self.futures) - len(self.waiting_steps)
        while candidates and pooled < self.worker_count:
            rid, items = candidates.popleft()
            try:
                op_id = next(items)
            except StopIteration:
                continue
            candidates.append((rid, items))
            op = self.operations[op_id]
            # While a pass keeps its conditions, an experiment's operations wait for it; while points in doubt are
            # taken again, its other operations wait for them, except the maintenance those retakes rely on.
            if rid in maintaining and not op.get('maintenance') or self._deferred(op):
                continue
            stop = self._stopping(op) if op.get('continues') else None
            if stop:
                stops.setdefault((rid, stop[0]['sweep']), stop)
                continue
            amounts = self._amounts(op)
            if not self.allocation.can_claim(rid, amounts, blocked | waiting):
                blocked.update(amounts)
                continue
            if (op.get('requires') and self.runs[rid].get('upkeep') and self.conditions.lapsed(op['requires'])
                    and self.conditions.keep(self.runs[rid], op['requires'])):
                # A condition came due since the experiment kept it: the operation waits for the pass that renews it.
                maintaining.add(rid)
                continue
            self.last_scheduled_run = rid
            op.update(state='running', started=now())
            op.pop('retry_at', None)
            self.allocation.claim(op_id, rid, amounts)
            output = self.home / 'runs' / rid / 'output' / 'operations' / op_id
            context = Context(rid, op_id, output, lambda value, oid=op_id: self._progress(oid, value))
            self.cancellations[op_id] = context
            if not getattr(self.devices[op['device']], 'dedicated_thread', False):
                pooled += 1
            try:
                if op.get('requires'):
                    op['conditions'] = self.conditions.require(op['requires'], rid)
                if op.get('bindings'):
                    args = dict(op['arguments'])
                    inputs = set()
                    for name, binding in op['bindings'].items():
                        if not library.by_id(binding):
                            source = op.get('binding_ids', {}).get(name) or self.step_ids[rid][binding['step']]
                            binding = {**binding, 'step': source}
                        args[name] = library.read(binding, self._recorded, self.home / 'runs' / rid / 'output',
                                                  known=self.ranges.setdefault(rid, {}))
                        inputs.update(library.input_operations(binding, self._recorded))
                    op['arguments'] = parameters(self.descriptions[op['device']]['operations'][op['operation']], args)
                    op['input_operations'] = sorted(inputs)
                for source in op.get('input_operations', []):
                    for condition, entry in self._recorded(source).get('in_doubt', {}).items():
                        found = op.setdefault('in_doubt', {}).setdefault(condition, dict(entry))
                        found['source_operations'] = sorted(set(found.get('source_operations', [])) | {source})
                error = None
            except Exception as error:
                dispatch.append((op, context, error))
            else:
                dispatch.append((op, context, None))
                if not op.get('maintenance'):
                    # An operation of the experiment starts: its upkeep kept what it relies on.
                    self.conditions.renewed.pop(rid, None)
        # Commit one group of starts before any of those device calls execute.
        # Iterators remain valid because ready queues change only after selection.
        if dispatch:
            try:
                with self.store.transaction():
                    for op, _, _ in dispatch:
                        self.store.put('operations', op)
                        self.store.event('operation.started', op['run_id'], operation_id=op['id'], device=op['device'], operation=op['operation'])
            except Exception:
                for op, _, _ in dispatch:
                    op['state'] = 'queued'
                    op.pop('started', None)
                    self.allocation.release(op['id'])
                    self.cancellations.pop(op['id'], None)
                raise
        for op, context, error in dispatch:
            op_id = op['id']
            self.ready[op['run_id']].pop(op_id)
            if error is not None:
                # Refused before its instrument was called, it is not tried again.
                from concurrent.futures import Future
                future = Future(); future.set_exception(error)
                self.refused.add(op_id)
            elif getattr(self.devices[op['device']], 'dedicated_thread', False):
                future = self._own_thread(self._call, self.devices[op['device']], op, context, self._status(op))
                self.waiting_steps.add(op_id)
            else:
                future = self.pool.submit(self._call, self.devices[op['device']], op, context, self._status(op))
            self.futures[op_id] = future
            def completed(_, oid=op_id):
                self.completed.put(oid)
                self.wake.set()
            future.add_done_callback(completed)
        for (rid, _), (marker, found) in stops.items():
            self._stop_sweep(self.runs[rid], marker, found)
        if stops:
            self.wake.set()

    def _stopping(self, op):
        """A sweep this operation continues that the point before it stopped, and where; or None."""
        output, known = self.home / 'runs' / op['run_id'] / 'output', self.ranges.setdefault(op['run_id'], {})
        for marker in op['continues']:
            found = library.stopped(marker, self._recorded, output, known)
            if found:
                return marker, found
        return None

    def _stop_sweep(self, run, marker, found):
        """Skip the points of a sweep after the one whose reading met its until: they never reach an instrument,
        and the steps after the sweep run as when it completes. The operation that holds the sweep records where
        it stopped."""
        sweep, kept = marker['sweep'], run['kind'] in {'package', 'external'}
        skipped = [self.operations[oid] for oid in run['operations']
                   if self.operations[oid]['state'] == 'queued' and sweep in self.operations[oid].get('swept_by', ())]
        ended = now()
        for op in skipped:
            op.update(state='skipped', ended=ended)
        self.operations[marker['step']]['gathers'][sweep]['stopped'] = found
        if kept:
            # As completed ones, the operations of a long-lived program then stay only in the indexed history.
            gone = {op['id'] for op in skipped}
            run['operations'] = [oid for oid in run['operations'] if oid not in gone]
        with self.store.transaction():
            self.store.event('sweep.stopped', run['id'], sweep=sweep, skipped=len(skipped), **found)
            for op in skipped:
                self.store.put('operations', op)
                self.store.event('operation.skipped', run['id'], operation_id=op['id'])
            if kept:
                self.store.put('runs', run)
        for op in skipped:
            self._operation_ended(op)
        if kept:
            for op in skipped:
                del self.operations[op['id']]

    @staticmethod
    def _call(driver, op, context, status=None):
        """Run one operation on its driver outside the scheduler lock. Its result is made JSON here, and the
        views that bindings and named results read from its arrays are computed from the files it saved.
        ``status`` is the operation of the same instrument that says whether the reading is valid."""
        result = driver.invoke(op['operation'], op['arguments'], context)
        if status is not None and isinstance(result, dict):
            from .drivers import judged_quality
            arguments = status.get('arguments', {})
            label = status['operation'] + (' ' + json.dumps(arguments) if arguments else '')
            reason = None
            try:
                found = driver.invoke(status['operation'], arguments, context)
                quality = found.get('quality') if isinstance(found, dict) else None
                if isinstance(quality, dict) and quality.get('level', 'good') != 'good':
                    reason = f"{label} reported {quality['level']}: {quality.get('detail', quality['level'])}"
                else:
                    for key in status.get('value', ['value']):
                        found = found[key]
                    if not judged_quality(status, found):
                        reason = f'{label} returned {found}'
            except CancelledError:
                raise
            except Exception as error:
                reason = f'{label} gave no status ({type(error).__name__}: {error})'
            if reason:
                prior = result.get('quality')
                prior = prior if isinstance(prior, dict) else {}
                level = prior.get('level', 'good')
                reasons = [str(prior.get('detail') or level), reason] if level != 'good' else [reason]
                result = {**result, 'quality': {**prior, 'level': level if level != 'good' else 'uncertain',
                          'source': prior.get('source', 'instrument'), 'detail': '; '.join(dict.fromkeys(reasons))}}
        try:
            result = json.loads(dumps(result))
        except ValueError:
            result = json.loads(dumps(finite(result)))
        return result, views.derive(result, context.output_dir, op['derive']) if op.get('derive') else None

    @staticmethod
    def _own_thread(function, *args):
        """Run a call that may wait a long time, such as a manual step, outside the instrument worker pool."""
        from concurrent.futures import Future
        future = Future()

        def run():
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(function(*args))
                except BaseException as error:
                    future.set_exception(error)
        threading.Thread(target=run, name='iop-waiting-step', daemon=True).start()
        return future

    def _loop(self):
        while True:
            self.wake.wait(0.25); self.wake.clear()
            with self.lock:
                try:
                    self._tick()
                except Exception as error:
                    self.errors.append({"time":now(), "error":str(error)})
                    self.errors = self.errors[-20:]
                if self.stopping and not self.futures and not self.processes:
                    return

    def metrics(self):
        with self.lock:
            return {"devices":len(self.devices), "platforms":len(self.platforms), "queued_runs":sum(r["state"] == "queued" for r in self.runs.values()),
                    "active_runs":sum(r["state"] != "queued" for r in self.runs.values()), "active_operations":len(self.futures),
                    "queued_operations":sum(o["state"] == "queued" for o in self.operations.values()),
                    "workers":self.worker_count, "package_workers":self.package_count, "resources":self.resource_info(), "resource_holds":list(self.resource_holds.values()), "errors":self.errors[-5:]}

    def close(self):
        with self.lock:
            self.stopping = True
            self.discovery_stop.set()
            for run in list(self.runs.values()):
                if run["state"] != "queued":
                    run.update(state="cancelling", end_state="interrupted")
                    self._stop_pending(run)
            self.wake.set()
        self.conditions.stop()
        if self.thread.is_alive():
            self.thread.join()
        self.monitors.stop()
        self.pool.shutdown(wait=True)
        for scanner in (self.discovery_thread, self.scan_request):
            if scanner and scanner.is_alive():
                scanner.join()
        for driver in self.devices.values():
            driver.close()
        self.store.close()

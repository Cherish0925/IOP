"""Operating conditions: what must hold on a platform for its experiments, kept true over time.

An experiment has its meaning only while the system is in its working state: the environment is
within bounds, instruments have settled, references and calibrations are current, the signal path
is aligned, loops are locked, the operating point is set and the end-to-end signal is good. Each of
these is a condition. A condition names

- ``verify``: a short reference measurement, one device operation or saved operation;
- ``accept``: the tolerance on its result; ``usable`` optionally says when the measurement itself
  means something, because an unusable result points at what the condition depends on;
- ``correct``: operations that restore it, tried in order;
- ``valid_for``: how long an accepted verification is trusted;
- ``after``: the conditions it depends on.

Keeping a system in its working state is one loop run in dependency order: verify, compare,
correct, verify again. It runs when a platform is brought up, when a verification has expired and
when something disturbed the system. The procedure follows Kelly et al., "Physical qubit
calibration on a directed acyclic graph" (arXiv:1803.03226), stated for any platform.
"""
import hashlib
import math
import statistics
import threading
from collections import deque
from datetime import datetime, timezone
from . import batches, library
from .errors import RequestError
from .platforms import name, strings
from .store import dumps, now

# From the outside in: each kind presupposes the ones before it.
KINDS = ('environment', 'equilibrium', 'reference', 'calibration', 'alignment', 'lock', 'operating_point', 'signal')
OUTCOMES = ('holds', 'drifted', 'unusable')
FIELDS = {'id', 'description', 'kind', 'after', 'verify', 'accept', 'usable', 'reference', 'correct', 'settle',
          'valid_for', 'note'}
RECORDS = 20


def seconds(text):
    return datetime.fromisoformat(text).timestamp()


def criterion(value, what):
    """A tolerance: where the value is in the result and the range or value it must have."""
    if not isinstance(value, dict) or value.keys() - {'value', 'minimum', 'maximum', 'equals'}:
        raise ValueError(what + ' gives value (a path into the result) with minimum, maximum or equals')
    path = value.get('value', ['value'])
    if not isinstance(path, list) or any(isinstance(p, bool) or not isinstance(p, (str, int)) for p in path):
        raise ValueError(what + ': value is a list of result keys or indices')
    for key in ('minimum', 'maximum'):
        if key in value and (isinstance(value[key], bool) or not isinstance(value[key], (int, float))
                             or not math.isfinite(value[key])):
            raise ValueError(f'{what}: {key} must be a finite number')
    if not {'minimum', 'maximum', 'equals'} & value.keys():
        raise ValueError(what + ': set minimum, maximum or equals')
    return {**value, 'value': path}


def judge(spec, result):
    """The value a tolerance looks at and whether it is satisfied; raises when it cannot be read."""
    value = result
    for key in spec['value']:
        value = value[key]
    met = not ('equals' in spec and value != spec['equals'])
    for key in ('minimum', 'maximum'):
        if key in spec:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{value!r} is not a finite number')
            met = met and (value >= spec[key] if key == 'minimum' else value <= spec[key])
    return value, met


def step(value, what):
    if (not isinstance(value, dict) or value.keys() - {'device', 'operation', 'arguments'}
            or not isinstance(value.get('operation'), str) or not value['operation']
            or not isinstance(value.get('device', ''), str) or not isinstance(value.get('arguments', {}), dict)):
        raise ValueError(what + ' is one operation: {"device", "operation", "arguments"}, or a saved operation without device')
    return value


def label(item):
    return (item['device'] + '.' if 'device' in item else '') + item['operation']


def check(definitions, taken=()):
    """Validate the conditions of one platform; ``taken`` are the names used by other platforms."""
    if not isinstance(definitions, list) or any(not isinstance(c, dict) for c in definitions):
        raise ValueError('Platform conditions must be a list of objects')
    names = strings([c.get('id') for c in definitions], 'Condition names')
    checked = []
    for condition in definitions:
        what = 'Condition ' + name(condition['id'], 'Condition name')
        if condition.keys() - FIELDS:
            raise ValueError(what + ': unknown fields ' + ', '.join(sorted(condition.keys() - FIELDS)))
        if condition['id'] in taken:
            raise ValueError(what + ' is already defined on another platform')
        if not isinstance(condition.get('description'), str) or not condition['description'].strip():
            raise ValueError(what + ': describe what must hold')
        if 'kind' in condition and condition['kind'] not in KINDS:
            raise ValueError(what + ': kind is one of ' + ', '.join(KINDS))
        after = strings(condition.get('after', []), what + ' after')
        if set(after) - set(names) or condition['id'] in after:
            raise ValueError(what + ': after names other conditions of this platform')
        corrections = condition.get('correct', [])
        corrections = [corrections] if isinstance(corrections, dict) else corrections
        if not isinstance(corrections, list):
            raise ValueError(what + ': correct is one operation or a list tried in order')
        references = condition.get('reference', {})
        if not isinstance(references, dict) or any(
                not isinstance(path, list) or any(isinstance(p, bool) or not isinstance(p, (str, int)) for p in path)
                for path in references.values()):
            raise ValueError(what + ': reference names values of the verification result by path')
        for key, minimum in (('valid_for', 1e-9), ('settle', 0)):
            if key in condition and (isinstance(condition[key], bool) or not isinstance(condition[key], (int, float))
                                     or not math.isfinite(condition[key]) or condition[key] < minimum):
                raise ValueError(f'{what}: {key} is a duration in seconds')
        if not isinstance(condition.get('note', ''), str):
            raise ValueError(what + ': note is text')
        checked.append({**condition, 'after': after, 'verify': step(condition.get('verify'), what + ' verify'),
                        'accept': criterion(condition.get('accept'), what + ' accept'),
                        **({'usable': criterion(condition['usable'], what + ' usable')} if 'usable' in condition else {}),
                        'correct': [step(item, what + ' correct') for item in corrections]})
    order(checked, names)
    return checked


def order(definitions, targets):
    """The targets and everything they depend on, each after its dependencies."""
    by_name = {c['id']: c for c in definitions}
    needed, pending = [], deque(targets)
    while pending:
        current = pending.popleft()
        if current not in needed:
            needed.append(current)
            pending.extend(by_name[current]['after'])
    counts = {c: sum(1 for d in by_name[c]['after'] if d in needed) for c in needed}
    ready, result = deque(c for c in by_name if c in counts and not counts[c]), []
    while ready:
        current = ready.popleft()
        result.append(current)
        for other in by_name:
            if other in counts and current in by_name[other]['after']:
                counts[other] -= 1
                if not counts[other]:
                    ready.append(other)
    if len(result) != len(needed):
        raise ValueError('Condition dependencies contain a cycle')
    return result


def signature(condition):
    """Changes when the meaning of an accepted verification changes."""
    return hashlib.sha256(dumps([condition['verify'], condition['accept'], condition.get('usable')]).encode()).hexdigest()[:16]


def outcome(condition, result):
    """Classify a verification result: holds, drifted (measurable but out of tolerance) or unusable."""
    value, kept = None, {}
    try:
        value, met = judge(condition['accept'], result)
    except (KeyError, IndexError, TypeError, ValueError):
        return 'unusable', None, kept
    if 'usable' in condition:
        try:
            if not judge(condition['usable'], result)[1]:
                return 'unusable', value, kept
        except (KeyError, IndexError, TypeError, ValueError):
            return 'unusable', value, kept
    for key, path in condition.get('reference', {}).items():
        item = result
        try:
            for part in path:
                item = item[part]
        except (KeyError, IndexError, TypeError):
            item = None
        kept[key] = item
    return ('holds' if met else 'drifted'), value, kept


def summary(condition, rows):
    """What the records of a condition say: how often it drifts, how long it holds, what restores it."""
    verified = [r for r in rows if r['type'] == 'verified']
    result = {'verifications': len(verified), **{o: sum(1 for r in verified if r['outcome'] == o) for o in OUTCOMES}}
    spans, start, last_correction = [], None, None
    corrections = {}
    for row in rows:
        if row['type'] == 'verified':
            if row['outcome'] == 'holds':
                start = start or row['time']
            elif start:
                # It held from the first accepted verification of the stretch until this one found it lost.
                spans.append(seconds(row['time']) - seconds(start))
                start = None
        elif row['type'] == 'invalidated':
            start = None
        elif row['type'] == 'corrected':
            entry = corrections.setdefault(row['by'], {'tried': 0, 'restored': 0})
            entry['tried'] += 1
            entry['restored'] += bool(row['restored'])
            if row['restored']:
                last_correction = row['time']
    if spans:
        result['held'] = {'count': len(spans), 'median_s': round(statistics.median(spans), 3), 'shortest_s': round(min(spans), 3)}
    if corrections:
        result['corrections'] = corrections
    # Drift of the accepted value since the last correction, from a straight line through its verifications.
    points = [(seconds(r['time']), r['value']) for r in verified if r['outcome'] == 'holds'
              and isinstance(r.get('value'), (int, float)) and not isinstance(r['value'], bool)
              and (last_correction is None or r['time'] >= last_correction)]
    if len(points) >= 3 and points[-1][0] > points[0][0]:
        origin = points[0][0]
        xs, ys = [t - origin for t, _ in points], [v for _, v in points]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
        intercept = my - slope * mx
        residual = math.sqrt(sum((y - intercept - slope * x) ** 2 for x, y in zip(xs, ys)) / len(xs))
        trend = {'per_hour': slope * 3600, 'points': len(points), 'residual': residual}
        bound = condition['accept'].get('maximum' if slope > 0 else 'minimum')
        if slope and bound is not None:
            crossing = (bound - intercept) / slope
            if crossing > xs[-1]:
                trend['expected_until'] = datetime.fromtimestamp(origin + crossing, timezone.utc).isoformat()
        result['trend'] = trend
    return result


class Stopped(Exception):
    pass


class StepFailed(Exception):
    pass


class Conditions:
    def __init__(self, engine):
        self.engine = engine
        self.states = engine.store.condition_states()
        self.jobs, self.threads = {}, []
        self.changed = threading.Condition()
        self.stopping = threading.Event()
        self.index()

    def index(self):
        """Conditions by name, from the saved platforms."""
        self.definitions, self.devices = {}, {}
        for platform in self.engine.platforms.values():
            for condition in platform['definition'].get('conditions', []):
                self.definitions[condition['id']] = {**condition, 'platform': platform['id']}
                self.devices[condition['id']] = self._devices(condition)

    def _devices(self, condition):
        devices = set()
        for item in [condition['verify']] + condition['correct']:
            try:
                devices.update(s['device'] for s in library.expand([{'id': 'step', **item}], self.engine.library.get)[0])
            except ValueError:
                pass
        return devices

    def prepare(self, definition):
        """Check the conditions of a platform being saved against its devices and the saved operations."""
        engine = self.engine
        taken = {c for c, d in self.definitions.items() if d['platform'] != definition['id']}
        conditions = check(definition.get('conditions', []), taken)
        for condition in conditions:
            for item in [condition['verify']] + condition['correct']:
                if 'device' not in item and 'program' in (engine.library.get(item['operation']) or {}):
                    raise ValueError(f"Condition {condition['id']}: {item['operation']} is a program; a program runs as its own experiment")
                for flat in library.expand([{'id': 'step', **item}], engine.library.get)[0]:
                    if flat['device'] not in definition['devices']:
                        raise ValueError(f"Condition {condition['id']}: {flat['device']} must belong to the platform")
                    engine._operation('prepare', flat)
        return conditions

    # State: what is known without touching an instrument.

    def state(self, condition_id, at=None, visiting=()):
        condition, saved = self.definitions[condition_id], self.states.get(condition_id)
        at = at or seconds(now())
        if not saved or saved.get('signature') != signature(condition) or 'verified' not in saved:
            return {'state': 'unknown'}
        shown = {key: saved[key] for key in ('verified', 'value', 'accepted', 'reference') if saved.get(key) not in (None, {})}
        if saved['outcome'] != 'holds':
            return {'state': 'lost', 'outcome': saved['outcome'], **shown, **({'error': saved['error']} if saved.get('error') else {})}
        if saved.get('invalidated'):
            return {'state': 'due', 'reason': saved['invalidated'], **shown}
        for dependency in condition['after']:
            corrected = self.states.get(dependency, {}).get('corrected')
            if corrected and seconds(corrected) > seconds(saved['accepted']):
                return {'state': 'due', 'reason': dependency + ' was corrected since', **shown}
        if 'valid_for' in condition:
            until = seconds(saved['accepted']) + condition['valid_for']
            if at > until:
                return {'state': 'due', 'reason': 'valid_for elapsed', **shown}
            shown['valid_until'] = datetime.fromtimestamp(until, timezone.utc).isoformat()
        for dependency in condition['after']:
            if dependency not in visiting and self.state(dependency, at, (*visiting, condition_id))['state'] != 'holds':
                return {'state': 'due', 'reason': dependency + ' does not hold', **shown}
        return {'state': 'holds', **shown}

    def listing(self, platform=None):
        with self.engine.lock:
            if platform is not None and platform not in self.engine.platforms:
                raise KeyError(platform)
            rows = []
            for platform_id in sorted({d['platform'] for d in self.definitions.values()}):
                if platform in (None, platform_id):
                    definitions = [d for d in self.definitions.values() if d['platform'] == platform_id]
                    for condition_id in order(definitions, [d['id'] for d in definitions]):
                        condition = self.definitions[condition_id]
                        rows.append({'id': condition_id, 'platform': platform_id, 'description': condition['description'],
                                     **({'kind': condition['kind']} if 'kind' in condition else {}),
                                     'after': condition['after'], **self.state(condition_id)})
            return rows

    def describe(self, condition_id):
        with self.engine.lock:
            condition = self.definitions[condition_id]
            rows = self.engine.store.condition_records(condition_id)
            return {**condition, **self.state(condition_id), 'statistics': summary(condition, rows), 'records': rows[-RECORDS:]}

    # Experiments that rely on conditions.

    def required(self, spec, calls=None, program=None):
        """The conditions a submission names, and those named by the saved operations it uses."""
        names = list(strings(spec.get('requires', []), 'requires'))
        for call in list((calls or {}).values()) + ([program] if program else []):
            names.extend(self.engine.library[call['operation']].get('requires', []))
        return sorted(set(names))

    def require(self, names, run_id=None):
        """Refuse when a required condition does not hold; otherwise return what the experiment relies on."""
        relied, lacking = {}, {}
        for condition_id in names:
            if condition_id not in self.definitions:
                raise ValueError('Unknown condition: ' + condition_id)
            state = self.state(condition_id)
            (relied if state['state'] == 'holds' else lacking)[condition_id] = state
        if lacking:
            text = '; '.join(f"{c} is {s['state']}" + (f" ({s['reason']})" if s.get('reason') else '') for c, s in lacking.items())
            raise RequestError('conditions_not_held', text + '. Maintain these conditions, then submit again.',
                               conditions=lacking, maintain='/v1/conditions/maintain',
                               **({'run_id': run_id} if run_id else {}))
        return {c: {k: s[k] for k in ('accepted', 'value', 'reference') if k in s} for c, s in relied.items()}

    def rely(self, run_id, names):
        """Remember that this experiment's data depend on these conditions from now on."""
        for condition_id in names:
            saved = self.states.get(condition_id)
            if saved is not None and run_id not in saved.setdefault('relying', []):
                saved['relying'].append(run_id)
                self.engine.store.record_condition(condition_id, saved, None, None)

    # Records.

    def _state(self, condition_id):
        mark = signature(self.definitions[condition_id])
        saved = self.states.get(condition_id)
        if not saved or saved.get('signature') != mark:
            saved = {'signature': mark}
        self.states[condition_id] = saved
        return saved

    def verified(self, condition_id, result, value, kept, run_id, error=None):
        engine = self.engine
        with engine.lock:
            saved, time = self._state(condition_id), now()
            record = {'type': 'verified', 'time': time, 'outcome': result, 'value': value, 'run_id': run_id,
                      **({'error': error} if error else {})}
            relying, doubtful = saved.get('relying', []), []
            if result == 'holds':
                saved.update(accepted=time, reference=kept)
                saved.pop('invalidated', None)
            else:
                # Data taken since the last accepted verification relied on a condition now found lost.
                for other in relying:
                    run = engine.runs.get(other) or engine.store.get('runs', other)
                    if run and condition_id not in run.setdefault('in_doubt', {}):
                        run['in_doubt'][condition_id] = {'since': saved.get('accepted'), 'found': time, 'outcome': result}
                        engine.store.put('runs', run)
                        doubtful.append(other)
            saved['relying'] = [other for other in relying if other in engine.runs]
            saved.update(verified=time, outcome=result, value=value, error=error)
            engine.store.record_condition(condition_id, saved, record, 'condition.' + result, run_id=run_id, value=value,
                                          **({'in_doubt': doubtful} if doubtful else {}))

    def corrected(self, condition_id, by, restored, before, after, run_id, error=None):
        with self.engine.lock:
            saved, time = self._state(condition_id), now()
            record = {'type': 'corrected', 'time': time, 'by': by, 'restored': restored, 'before': before, 'after': after,
                      'run_id': run_id, **({'error': error} if error else {})}
            if restored:
                saved['corrected'] = time
            self.engine.store.record_condition(condition_id, saved, record, 'condition.corrected', run_id=run_id, by=by,
                                               restored=restored)

    def invalidate(self, names, reason):
        """Something disturbed the system: these conditions are verified again before they are relied on."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Say what disturbed the system')
        with self.engine.lock:
            for condition_id in strings(names, 'Conditions'):
                if condition_id not in self.definitions:
                    raise KeyError(condition_id)
            for condition_id in names:
                saved = self._state(condition_id)
                saved['invalidated'] = reason
                self.engine.store.record_condition(condition_id, saved, {'type': 'invalidated', 'time': now(), 'reason': reason},
                                                   'condition.invalidated', reason=reason)
            return [row for row in self.listing() if row['id'] in names]

    def disturbed(self, device, reason):
        """A monitored reading of this device left its limits: what depends on the device is due."""
        with self.engine.lock:
            names = [c for c, devices in self.devices.items() if device in devices and self.states.get(c, {}).get('accepted')
                     and not self.states[c].get('invalidated')]
            if names:
                self.invalidate(names, reason)

    # Maintenance: the loop.

    def maintain(self, platform=None, conditions=None, run_id=None, request_id=None, force=False):
        engine = self.engine
        with engine.lock:
            if request_id is not None:
                if not isinstance(request_id, str) or not request_id.strip():
                    raise ValueError('request_id is a nonempty string')
                previous = engine.store.maintenance(request_id=request_id)
                if previous:
                    return self.job(previous['id'])
            if not conditions and platform is None and run_id in engine.runs:
                # Inside an experiment the default is what that experiment relies on.
                conditions = engine.runs[run_id].get('requires')
            if conditions:
                for condition_id in strings(conditions, 'Conditions'):
                    if condition_id not in self.definitions:
                        raise ValueError('Unknown condition: ' + condition_id)
                platforms = {self.definitions[c]['platform'] for c in conditions}
                if len(platforms) > 1 or platform not in (None, *platforms):
                    raise ValueError('Maintain the conditions of one platform at a time')
                platform, targets = platforms.pop(), list(conditions)
            else:
                if platform not in engine.platforms:
                    raise ValueError('Name a platform or its conditions')
                targets = [c for c, d in self.definitions.items() if d['platform'] == platform]
                if not targets:
                    raise ValueError('This platform has no conditions')
            active = [job for job in self.jobs.values() if job['platform'] == platform and job['state'] == 'running']
            if active:
                raise RequestError('maintenance_active', 'This platform is being maintained; read that maintenance.',
                                   maintenance=active[0]['id'])
            from .engine import identifier
            job = {'id': identifier('maintenance'), 'request_id': request_id, 'platform': platform, 'conditions': targets,
                   'force': bool(force), 'state': 'running', 'started': now(), 'report': [], 'steps': 0,
                   'run_id': run_id, 'own_run': run_id is None}
            if run_id is None:
                # Steps take their devices one at a time, so other experiments are not held for the whole pass.
                job['run_id'] = engine.submit({'kind': 'external', 'name': 'Maintain ' + platform, 'platform': platform,
                                               'request_id': job['id']})['id']
            else:
                run = engine.runs.get(run_id)
                if not run or not (run['kind'] == 'external' and run['state'] in {'queued', 'running'} or
                                   run['kind'] == 'package' and run['state'] == 'running'):
                    raise ValueError('Maintenance inside an experiment requires an active experiment program')
            self.jobs[job['id']] = job
            engine.store.save_maintenance(job)
            thread = threading.Thread(target=self._work, args=(job,), name='iop-maintenance', daemon=True)
            self.threads.append(thread)
            thread.start()
            return self._public(job)

    def job(self, job_id, wait=0):
        wait = float(wait)
        if not 0 <= wait <= 25:
            raise ValueError('Wait must be between 0 and 25 seconds')
        with self.changed:
            job = self.jobs.get(job_id)
            if job is not None and job['state'] == 'running' and wait:
                self.changed.wait_for(lambda: job['state'] != 'running', wait)
        if job is None:
            job = self.engine.store.maintenance(job_id)
            if job is None:
                raise KeyError(job_id)
            if job['state'] == 'running':
                # The service stopped while this pass was running.
                job = {**job, 'state': 'interrupted'}
        return self._public(job)

    @staticmethod
    def _public(job):
        return {**{k: v for k, v in job.items() if k not in ('steps', 'own_run', 'force')}, 'complete': job['state'] != 'running'}

    def _work(self, job):
        engine = self.engine
        try:
            done, investigated = {}, set()
            for condition_id in job['conditions']:
                self._ensure(job, condition_id, done, investigated)
            # A correction makes what depends on it due again: verify those before the pass ends.
            for _ in self.definitions:
                with engine.lock:
                    stale = [c for c, result in done.items() if result == 'holds' and self.state(c)['state'] != 'holds']
                if not stale:
                    break
                for condition_id in stale:
                    del done[condition_id]
                for condition_id in job['conditions']:
                    self._ensure(job, condition_id, done, investigated)
            job['holds'] = all(done[c] == 'holds' for c in job['conditions'])
            job['state'] = 'completed'
        except Stopped:
            job['state'] = 'interrupted'
        except Exception as error:
            job.update(state='failed', error=f'{type(error).__name__}: {error}')
        job['ended'] = now()
        try:
            with engine.lock:
                run = engine.runs.get(job['run_id'])
                if job['own_run'] and run and not engine.stopping:
                    if job['state'] == 'completed':
                        engine.save_result(job['run_id'], {'holds': job['holds'], 'conditions': job['report']})
                        engine.finish(job['run_id'])
                    else:
                        engine.cancel(job['run_id'])
                elif run and job['state'] == 'completed':
                    # The experiment continues on the verifications just made.
                    self.rely(job['run_id'], [c for c in run.get('requires', []) if self.state(c)['state'] == 'holds'])
                if not engine.stopping:
                    engine.store.save_maintenance(job)
        except Exception as error:
            job.setdefault('error', f'{type(error).__name__}: {error}')
        with self.changed:
            self.changed.notify_all()

    def _ensure(self, job, condition_id, done, investigated, investigate=False):
        """Bring one condition to hold: dependencies first, then verify, then correct."""
        if condition_id in done and not (investigate and condition_id not in investigated):
            return done[condition_id]
        condition = self.definitions[condition_id]
        entry = {'condition': condition_id}
        for dependency in condition['after']:
            if self._ensure(job, dependency, done, investigated) != 'holds':
                entry.update(result='blocked', by=dependency)
                return self._report(job, done, entry)
        with self.engine.lock:
            held = self.state(condition_id)
        if held['state'] == 'holds' and not job['force'] and not investigate:
            entry.update(result='held', **{k: held[k] for k in ('value', 'verified') if k in held})
            return self._report(job, done, entry)
        investigated.add(condition_id)
        result, value = self._verify(job, condition)
        if result == 'unusable' and condition['after']:
            # The measurement itself is not meaningful: look at what it depends on, whatever their state says.
            for dependency in condition['after']:
                if self._ensure(job, dependency, done, investigated, investigate=True) != 'holds':
                    entry.update(result='blocked', by=dependency)
                    return self._report(job, done, entry)
            result, value = self._verify(job, condition)
        entry['value'] = value
        if result == 'holds':
            entry['result'] = 'verified'
            return self._report(job, done, entry)
        entry['found'] = result
        for item in condition['correct']:
            before = value
            try:
                self._execute(job, 'correct', item)
            except StepFailed as error:
                self.corrected(condition_id, label(item), False, before, None, job['run_id'], str(error))
                continue
            if self.stopping.wait(condition.get('settle', 0)):
                raise Stopped()
            result, value = self._verify(job, condition)
            self.corrected(condition_id, label(item), result == 'holds', before, value, job['run_id'])
            if result == 'holds':
                entry.update(result='corrected', by=label(item), value=value)
                return self._report(job, done, entry)
        entry.update(result='lost', value=value)
        return self._report(job, done, entry)

    def _report(self, job, done, entry):
        previous = next((e for e in job['report'] if e['condition'] == entry['condition']), None)
        if previous is not None:
            job['report'].remove(previous)
        job['report'].append(entry)
        done[entry['condition']] = 'holds' if entry['result'] in ('held', 'verified', 'corrected') else entry['result']
        with self.engine.lock:
            if not self.engine.stopping:
                self.engine.store.save_maintenance(job)
        return done[entry['condition']]

    def _verify(self, job, condition):
        try:
            result = self._execute(job, 'verify', condition['verify'])
        except StepFailed as error:
            self.verified(condition['id'], 'unusable', None, {}, job['run_id'], str(error))
            return 'unusable', None
        found, value, kept = outcome(condition, result)
        self.verified(condition['id'], found, value, kept, job['run_id'])
        return found, value

    def _execute(self, job, name_, item):
        """Run one operation inside the experiment of this pass and return its result."""
        engine, request_id = self.engine, f"{job['id']}.{job['steps']}"
        job['steps'] += 1
        try:
            receipt = batches.submit(engine, job['run_id'], [{'id': name_, **item}], request_id, maintenance=True)
            while not receipt['complete']:
                if self.stopping.is_set() or engine.stopping:
                    raise Stopped()
                receipt = batches.describe(engine, job['run_id'], request_id, wait=1)
        except (ValueError, KeyError) as error:
            run = engine.runs.get(job['run_id'])
            if not run or run['state'] not in {'queued', 'running'}:
                raise Stopped() from None
            raise StepFailed(str(error)) from None
        failed = [op for op in receipt['operations'] if op['state'] != 'completed']
        if failed:
            run = engine.runs.get(job['run_id'])
            if not run or run['state'] not in {'queued', 'running'}:
                raise Stopped()
            raise StepFailed(str(failed[0].get('error') or failed[0]['state']))
        if name_ in receipt.get('results', {}):
            return receipt['results'][name_]
        return receipt['operations'][-1].get('result')

    def stop(self):
        self.stopping.set()
        for thread in self.threads:
            thread.join(timeout=10)

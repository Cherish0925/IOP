"""Platform-defined operating conditions, observations and bounded correction procedures.

Dependencies and acceptable measurements are supplied by the experimenter. Maintenance uses
ordinary operations and the existing scheduler; it does not infer physical causality. The
dependency-graph approach is informed by Kelly et al., arXiv:1803.03226.
"""
import copy
import math
import statistics
import threading
from collections import deque
from datetime import datetime, timezone
from . import batches, library
from .errors import RequestError
from .platforms import name, strings
from .store import dumps, now

# Suggested labels. Dependencies come from each platform's explicit `after` relationships.
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
    if 'minimum' in value and 'maximum' in value and value['minimum'] > value['maximum']:
        raise ValueError(what + ': minimum must not exceed maximum')
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


GOALS = ('maximize', 'minimize', 'center')


def number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def tuning(value, accept, what):
    """A correction the Runtime performs itself: adjust settings against the verification value."""
    if value.keys() - {'tune', 'goal', 'evaluations', 'until', 'gain'}:
        raise ValueError(what + ': tune takes goal, evaluations, until and gain')
    knobs = value['tune'] if isinstance(value['tune'], list) else [value['tune']]
    if not knobs or any(not isinstance(k, dict) for k in knobs):
        raise ValueError(what + ': tune lists the settings to adjust')
    checked = []
    for knob in knobs:
        if knob.keys() - {'device', 'operation', 'argument', 'arguments', 'read', 'step', 'resolution', 'minimum', 'maximum'}:
            raise ValueError(what + ': a setting has device, operation, argument, read, step, minimum, maximum and resolution')
        if any(not isinstance(knob.get(key), str) or not knob[key] for key in ('device', 'operation', 'argument')):
            raise ValueError(what + ': a setting names the device, the operation that sets it and its argument')
        if not isinstance(knob.get('arguments', {}), dict) or knob['argument'] in knob.get('arguments', {}):
            raise ValueError(what + ': arguments holds the other, fixed arguments of the setting operation')
        read = knob.get('read')
        if (not isinstance(read, dict) or read.keys() - {'device', 'operation', 'arguments', 'value'}
                or any(not isinstance(read.get(key), str) or not read[key] for key in ('device', 'operation'))
                or not isinstance(read.get('value', ['value']), list)):
            raise ValueError(what + ': read is the operation that returns the present setting, with value as the path to it')
        if not all(number(knob.get(key)) for key in ('step', 'minimum', 'maximum')) or knob['step'] <= 0 \
                or knob['minimum'] >= knob['maximum']:
            raise ValueError(what + ': a setting needs step > 0 and minimum < maximum; the Runtime never leaves that range')
        resolution = knob.get('resolution', knob['step'] / 16)
        if not number(resolution) or not 0 < resolution <= knob['step']:
            raise ValueError(what + ': resolution is the smallest step worth taking, at most step')
        checked.append({**knob, 'resolution': resolution, 'read': {**read, 'value': read.get('value', ['value'])}})
    if 'equals' in accept or not {'minimum', 'maximum'} & accept.keys():
        raise ValueError(what + ': tune needs a numeric tolerance (minimum or maximum) to improve on')
    goal = value.get('goal', 'center' if {'minimum', 'maximum'} <= accept.keys() else 'maximize' if 'minimum' in accept else 'minimize')
    if goal not in GOALS:
        raise ValueError(what + ': goal is one of ' + ', '.join(GOALS))
    if goal == 'center' and not {'minimum', 'maximum'} <= accept.keys():
        raise ValueError(what + ': center needs both minimum and maximum in accept')
    evaluations = value.get('evaluations', 40)
    if isinstance(evaluations, bool) or not isinstance(evaluations, int) or not 1 <= evaluations <= 1000:
        raise ValueError(what + ': evaluations is the number of settings that may be tried, 1 to 1000')
    if value.get('until', 'optimum') not in ('optimum', 'accept'):
        raise ValueError(what + ': until is optimum, or accept to stop once the tolerance is met')
    if not number(value.get('gain', 0)) or value.get('gain', 0) < 0:
        raise ValueError(what + ': gain is the smallest improvement that counts, above the noise of the verification')
    return {'tune': checked, 'goal': goal, 'evaluations': evaluations, 'until': value.get('until', 'optimum'),
            'gain': value.get('gain', 0)}


def label(item):
    if 'tune' in item:
        return 'tune ' + ', '.join(f"{k['device']}.{k['argument']}" for k in item['tune'])
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
        if 'kind' in condition:
            name(condition['kind'], what + ' kind')
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
        accept = criterion(condition.get('accept'), what + ' accept')
        checked.append({**condition, 'after': after, 'verify': step(condition.get('verify'), what + ' verify'),
                        'accept': accept,
                        **({'usable': criterion(condition['usable'], what + ' usable')} if 'usable' in condition else {}),
                        'correct': [tuning(item, accept, what + ' correct') if isinstance(item, dict) and 'tune' in item
                                    else step(item, what + ' correct') for item in corrections]})
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
                # Observation interval, not a measurement of the actual instant of failure.
                spans.append(seconds(row['time']) - seconds(start))
                start = None
        elif row['type'] == 'invalidated':
            start = None
            last_correction = row['time']
        elif row['type'] == 'corrected':
            entry = corrections.setdefault(row['by'], {'tried': 0, 'restored': 0})
            entry['tried'] += 1
            entry['restored'] += bool(row['restored'])
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
            if crossing > xs[-1] and math.isfinite(crossing):
                try:
                    trend['expected_until'] = datetime.fromtimestamp(origin + crossing, timezone.utc).isoformat()
                except (OverflowError, ValueError, OSError):
                    pass
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
            if 'tune' in item:
                devices.update(d for knob in item['tune'] for d in (knob['device'], knob['read']['device']))
                continue
            try:
                devices.update(s['device'] for s in library.expand([{'id': 'step', **item}], self.engine.library.get)[0])
            except ValueError:
                pass
        return devices

    def basis(self, condition):
        """Keep the actual measurement definition and the versions that give it meaning."""
        result = self._measurement_basis(condition)
        result['dependencies'] = {c: self._measurement_basis(self.definitions[c])
                                  for c in self.closure(condition['after'])}
        return result

    def _measurement_basis(self, condition):
        result = {k: condition[k] for k in ('verify', 'accept', 'usable', 'after', 'reference') if k in condition}
        try:
            steps, calls = library.expand([{'id': 'verify', **condition['verify']}], self.engine.library.get)
            result['operations'] = {c['operation']: c['revision'] for c in calls.values()}
            result['devices'] = {s['device']: self.engine.descriptions.get(s['device'], {}).get('revision') for s in steps}
        except ValueError:
            result['unavailable'] = True
        return copy.deepcopy(result)

    def closure(self, names):
        return order(list(self.definitions.values()), names)

    def requirements(self, names):
        """Resources used together during observation, correction and confirmation."""
        engine, amounts, revisions = self.engine, {}, {}
        for condition_id in self.closure(names):
            condition = self.definitions[condition_id]
            items = [condition['verify']]
            for correction in condition['correct']:
                if 'tune' in correction:
                    for knob in correction['tune']:
                        items.extend([self._set(knob, knob['minimum']),
                                      {k: v for k, v in knob['read'].items() if k != 'value'}])
                else:
                    items.append(correction)
            for item in items:
                expanded, calls = library.expand([{'id': 'step', **item}], engine.library.get)
                revisions.update({c['operation']: c['revision'] for c in calls.values()})
                for flat in expanded:
                    op = engine._operation('prepare', flat)
                    for key, count in engine._amounts(op, reserve=True).items():
                        amounts[key] = max(amounts.get(key, 0), count)
        return amounts, revisions

    def prepare(self, definition):
        """Check the conditions of a platform being saved against its devices and the saved operations."""
        engine = self.engine
        taken = {c for c, d in self.definitions.items() if d['platform'] != definition['id']}
        conditions = check(definition.get('conditions', []), taken)
        for condition in conditions:
            for item in [condition['verify']] + condition['correct']:
                if 'tune' in item:
                    for knob in item['tune']:
                        if {knob['device'], knob['read']['device']} - set(definition['devices']):
                            raise ValueError(f"Condition {condition['id']}: tuned devices must belong to the platform")
                        # Both ends of the range must be values the setting operation accepts.
                        for bound in (knob['minimum'], knob['maximum']):
                            engine._operation('prepare', self._set(knob, bound))
                        engine._operation('prepare', {k: v for k, v in knob['read'].items() if k != 'value'})
                    continue
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
        if not saved or saved.get('basis') != self.basis(condition) or 'verified' not in saved:
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
        for condition_id in self.closure(names):
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
        for condition_id in self.closure(names):
            saved = self.states.get(condition_id)
            if saved is not None and run_id not in saved.setdefault('relying', []):
                saved['relying'].append(run_id)
                self.engine.store.record_condition(condition_id, saved, None, None)

    # Records.

    def _state(self, condition_id):
        mark = self.basis(self.definitions[condition_id])
        saved = self.states.get(condition_id)
        if not saved or saved.get('basis') != mark:
            saved = {'basis': mark}
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
            if result != 'holds':
                downstream = [c for c, definition in self.definitions.items() if condition_id in definition['after']]
                if downstream:
                    self.invalidate(downstream, 'dependency no longer holds: ' + condition_id)

    def corrected(self, condition_id, by, restored, before, after, run_id, error=None, tuned=None):
        with self.engine.lock:
            saved, time = self._state(condition_id), now()
            record = {'type': 'corrected', 'time': time, 'by': by, 'restored': restored, 'before': before, 'after': after,
                      'run_id': run_id, **({'error': error} if error else {}), **({'tuned': tuned} if tuned else {})}
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
            affected = set(names)
            while True:
                downstream = {c for c, definition in self.definitions.items() if affected.intersection(definition['after'])}
                if downstream <= affected:
                    break
                affected.update(downstream)
            for condition_id in affected:
                saved = self._state(condition_id)
                saved['invalidated'] = reason if condition_id in names else 'dependency disturbed: ' + reason
                time = now()
                for run_id in saved.get('relying', []):
                    run = self.engine.runs.get(run_id)
                    if run and condition_id not in run.setdefault('in_doubt', {}):
                        run['in_doubt'][condition_id] = {'since': time, 'found': time, 'outcome': 'disturbed', 'reason': reason}
                        self.engine.store.put('runs', run)
                self.engine.store.record_condition(condition_id, saved, {'type': 'invalidated', 'time': time, 'reason': reason},
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
            request = {'platform': platform, 'conditions': sorted(strings(conditions, 'Conditions')) if conditions else None,
                       'run_id': run_id, 'force': bool(force)}
            if request_id is not None:
                if not isinstance(request_id, str) or not request_id.strip():
                    raise ValueError('request_id is a nonempty string')
                previous = engine.store.maintenance(request_id=request_id)
                if previous:
                    if previous.get('request') != request:
                        raise ValueError('request_id already names a different maintenance request')
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
                   'run_id': run_id, 'own_run': run_id is None, 'request': request}
            amounts, job['operation_revisions'] = self.requirements(targets)
            if run_id is None:
                # Keep coupled devices together through the complete correction cycle.
                job['run_id'] = engine.submit({'kind': 'external', 'name': 'Maintain ' + platform, 'platform': platform,
                                               'resources': sorted(k[7:] for k in amounts if k.startswith(('device:', 'shared:'))),
                                               'requirements': {k[5:]: n for k, n in amounts.items() if k.startswith('pool:')},
                                               'request_id': job['id']})['id']
            else:
                run = engine.runs.get(run_id)
                if not run or not (run['kind'] == 'external' and run['state'] in {'queued', 'running'} or
                                   run['kind'] == 'package' and run['state'] == 'running'):
                    raise ValueError('Maintenance inside an experiment requires an active experiment program')
                if engine.remaining.get(run_id, 0):
                    raise RequestError('maintenance_busy', 'Wait for this experiment\'s current operations before maintenance.',
                                       run_id=run_id)
                reserved = engine._amounts(run, reserve=True)
                missing = {k: n for k, n in amounts.items() if n > reserved.get(k, 0)}
                if missing:
                    raise RequestError('maintenance_resources',
                        'Reserve the devices and resource quantities used by maintenance when starting this experiment.',
                        resources=missing, run_id=run_id)
            self.jobs[job['id']] = job
            engine.store.save_maintenance(job)
            thread = threading.Thread(target=self._work, args=(job,), name='iop-maintenance', daemon=True)
            self.threads = [t for t in self.threads if t.is_alive()] + [thread]
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
        return {**{k: v for k, v in job.items() if k not in ('steps', 'own_run', 'force', 'request')}, 'complete': job['state'] != 'running'}

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
            with engine.lock:
                job['holds'] = job['holds'] and all(self.state(c)['state'] == 'holds' for c in job['conditions'])
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
        engine.wake.set()
        with engine.lock:
            completed = [key for key, value in self.jobs.items() if value['state'] != 'running']
            for key in completed[:-RECORDS]:
                self.jobs.pop(key, None)

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
            before, tuned = value, None
            self.invalidate([condition_id], 'correction started: ' + label(item))
            try:
                if 'tune' in item:
                    tuned = self._tune(job, condition, item, value, result)
                else:
                    self._execute(job, 'correct', item)
            except StepFailed as error:
                self.corrected(condition_id, label(item), False, before, None, job['run_id'], str(error))
                continue
            if self.stopping.wait(condition.get('settle', 0)):
                raise Stopped()
            result, value = self._verify(job, condition)
            self.corrected(condition_id, label(item), result == 'holds', before, value, job['run_id'], tuned=tuned)
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

    @staticmethod
    def _set(knob, position):
        return {'device': knob['device'], 'operation': knob['operation'],
                'arguments': {**knob.get('arguments', {}), knob['argument']: position}}

    def _tune(self, job, condition, item, value, initial_outcome):
        """Adjust the settings one at a time against the verification value, halving the step when no move helps.

        A bounded search without derivatives: each setting is stepped in the direction that improves the
        value and stepped back when it does not, so the system is left at the best setting that was seen.
        """
        knobs, goal, gain = item['tune'], item['goal'], item['gain']
        accept = condition['accept']
        middle = (accept['minimum'] + accept['maximum']) / 2 if goal == 'center' else None

        def score(reading):
            if not number(reading):
                return -math.inf
            return reading if goal == 'maximize' else -reading if goal == 'minimize' else -abs(reading - middle)

        positions = []
        for knob in knobs:
            present = self._execute(job, 'read', {k: v for k, v in knob['read'].items() if k != 'value'})
            try:
                for key in knob['read']['value']:
                    present = present[key]
            except (KeyError, IndexError, TypeError):
                present = None
            if not number(present):
                raise StepFailed(f"{knob['read']['device']}.{knob['read']['operation']} did not return the present setting")
            positions.append(present)
        start, steps = list(positions), [knob['step'] for knob in knobs]
        for index, knob in enumerate(knobs):
            bounded = min(max(positions[index], knob['minimum']), knob['maximum'])
            if bounded != positions[index]:
                self._execute(job, 'set', self._set(knob, bounded))
                positions[index] = bounded
        used = 0
        if positions != start:
            if self.stopping.wait(condition.get('settle', 0)):
                raise Stopped()
            initial_outcome, value, _ = outcome(condition, self._execute(job, 'verify', condition['verify']))
            used += 1
        best, met = score(value) if initial_outcome != 'unusable' else -math.inf, False
        met = item['until'] == 'accept' and initial_outcome == 'holds'
        while used < item['evaluations'] and not met:
            moved = False
            for index, knob in enumerate(knobs):
                if steps[index] < knob['resolution']:
                    continue
                for direction in (1, -1):
                    advanced = False
                    while used < item['evaluations'] and not met:
                        candidate = min(max(positions[index] + direction * steps[index], knob['minimum']), knob['maximum'])
                        if candidate == positions[index]:
                            break
                        self._execute(job, 'set', self._set(knob, candidate))
                        if self.stopping.wait(condition.get('settle', 0)):
                            raise Stopped()
                        found, reading, _ = outcome(condition, self._execute(job, 'verify', condition['verify']))
                        used += 1
                        candidate_score = score(reading) if found != 'unusable' else -math.inf
                        if candidate_score > best + gain:
                            positions[index], best, advanced, moved = candidate, candidate_score, True, True
                            met = item['until'] == 'accept' and found == 'holds'
                        else:
                            self._execute(job, 'set', self._set(knob, positions[index]))
                            break
                    if advanced or met:
                        break
                if met:
                    break
            if not moved:
                steps = [size / 2 for size in steps]
                if all(size < knob['resolution'] for size, knob in zip(steps, knobs)):
                    break
        return {'evaluations': used, 'from': start, 'to': positions}

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

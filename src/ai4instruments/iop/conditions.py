"""Operating conditions of platforms and instruments: observations and bounded correction procedures.

Dependencies and acceptable measurements are supplied by the experimenter. Maintenance uses
ordinary operations and the existing scheduler; it does not infer physical causality. The
dependency-graph approach is informed by Kelly et al., arXiv:1803.03226.

A tolerance judges one reading against a range, or several readings by a statistic: their mean,
standard deviation, span or drift per second, which states that a quantity is steady. It may
instead compare the reading with a target, a nominal or certified value or the setpoint the
instrument reports, within an absolute and a relative tolerance. These forms do not depend on
the kind of quantity or instrument.

An instrument's own conditions (warm-up, references, overloads, self-calibration) belong to its
device record, or to the model description that supplies it, and are named ``<device>.<id>``. They
use only that instrument's operations, so they travel with the model to every laboratory; platform
conditions may depend on them.

A platform that declares ``upkeep`` has its conditions kept by the Runtime: when its instruments
are idle, a pass starts for what is unknown, due, about to expire, forecast to leave its tolerance,
or lost and due for another attempt. An experiment that declares it, a step graph, a continuous
experiment or a program, has the conditions it requires kept inside it, between its operations.
Each pass is recorded like any other, with ``automatic`` and its reasons, and never runs a step
that asks a person.
"""
import copy
import math
import re
import statistics
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from time import monotonic
from . import batches, library
from .engine import SATISFIED
from .drivers import parameters
from .errors import RequestError
from .platforms import closest, name, strings
from .store import dumps, now

# Suggested labels. Dependencies come from each platform's explicit `after` relationships.
KINDS = ('environment', 'equilibrium', 'reference', 'calibration', 'alignment', 'lock', 'operating_point', 'signal')
OUTCOMES = ('holds', 'drifted', 'unusable')
FIELDS = {'id', 'description', 'kind', 'after', 'verify', 'samples', 'interval', 'accept', 'usable', 'reference',
          'correct', 'settle', 'valid_for', 'note'}
# What a tolerance can judge in the samples of a verification: their mean, their standard deviation (repeatability),
# their span (peak to peak) or the slope of a straight line through them per second (drift).
STATISTICS = ('mean', 'std', 'span', 'slope')
TOLERANCE = {'value', 'minimum', 'maximum', 'equals', 'statistic', 'target', 'within', 'relative'}
RECORDS = 20
# Passes an experiment's upkeep runs in a row without one of its operations starting, before it gives up.
RENEWALS = 3
LOCAL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}')


def seconds(text):
    return datetime.fromisoformat(text).timestamp()


def criterion(value, what):
    """A tolerance: where the value is in the result, which statistic of the samples is judged, and the range or
    value it must have, or the target it must agree with."""
    if not isinstance(value, dict) or value.keys() - TOLERANCE:
        raise ValueError(what + ' gives value (a path into the result) with minimum, maximum or equals, '
                                'or with a target and within or relative; statistic chooses mean, std, span or slope')
    path = value.get('value', ['value'])
    if not isinstance(path, list) or any(isinstance(p, bool) or not isinstance(p, (str, int)) for p in path):
        raise ValueError(what + ': value is a list of result keys or indices')
    if 'statistic' in value and value['statistic'] not in STATISTICS:
        raise ValueError(what + ': statistic is one of ' + ', '.join(STATISTICS))
    if 'target' in value:
        if value.keys() & {'minimum', 'maximum', 'equals'}:
            raise ValueError(what + ': a target takes within or relative, not minimum, maximum or equals')
        target = value['target']
        if not number(target):
            if not isinstance(target, dict):
                raise ValueError(what + ': target is a number, or the reading that gives it: '
                                        '{"device", "operation", "arguments", "value"}')
            where = target.get('value', ['value'])
            if not isinstance(where, list) or any(isinstance(p, bool) or not isinstance(p, (str, int)) for p in where):
                raise ValueError(what + ' target: value is a list of result keys or indices')
            target = {**step({k: v for k, v in target.items() if k != 'value'}, what + ' target'), 'value': where}
        if not {'within', 'relative'} & value.keys():
            raise ValueError(what + ': give within, an absolute tolerance, or relative, a fraction of the target, or both')
        for key in ('within', 'relative'):
            if key in value and (not number(value[key]) or value[key] < 0):
                raise ValueError(f'{what}: {key} is a nonnegative number')
        return {**value, 'value': path, 'target': target}
    if value.keys() & {'within', 'relative'}:
        raise ValueError(what + ': within and relative belong to a target')
    for key in ('minimum', 'maximum'):
        if key in value and (isinstance(value[key], bool) or not isinstance(value[key], (int, float))
                             or not math.isfinite(value[key])):
            raise ValueError(f'{what}: {key} must be a finite number')
    if not {'minimum', 'maximum', 'equals'} & value.keys():
        raise ValueError(what + ': set minimum, maximum or equals')
    if 'minimum' in value and 'maximum' in value and value['minimum'] > value['maximum']:
        raise ValueError(what + ': minimum must not exceed maximum')
    return {**value, 'value': path}


def statistic(name, values, times):
    """One statistic of the samples of a verification; ``times`` are when they were taken, in seconds."""
    if name == 'mean':
        return statistics.fmean(values)
    if name == 'std':
        return statistics.stdev(values)
    if name == 'span':
        return max(values) - min(values)
    mt, mv = statistics.fmean(times), statistics.fmean(values)
    spread = sum((t - mt) ** 2 for t in times)
    if not spread:
        raise ValueError('samples taken at one instant have no slope')
    return sum((t - mt) * (v - mv) for t, v in zip(times, values)) / spread


def judge(spec, results, times=None, target=None):
    """The value a tolerance looks at in the samples and whether it is satisfied; raises when it cannot be read.

    Several samples are judged by their statistic, the mean unless another is named. With a target the value
    judged is the deviation from it, accepted within an absolute and a relative tolerance.
    """
    results = results if isinstance(results, list) else [results]
    values = []
    for result in results:
        value = result
        for key in spec['value']:
            value = value[key]
        values.append(value)
    name = spec.get('statistic', 'mean' if len(values) > 1 else None)
    if name:
        if not all(number(v) for v in values):
            raise ValueError('the samples are not all finite numbers')
        value = statistic(name, values, times if times is not None else list(range(len(values))))
    else:
        value = values[0]
    if 'target' in spec:
        if not number(value) or not number(target):
            raise ValueError('a deviation needs a finite number and target')
        deviation = value - target
        return deviation, abs(deviation) <= spec.get('within', 0) + spec.get('relative', 0) * abs(target)
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


def bounds(spec):
    """The lowest and highest value a tolerance accepts, where they are fixed numbers; for a target, of the deviation."""
    if 'target' in spec:
        if spec.get('relative') and not number(spec['target']):
            return None, None
        width = spec.get('within', 0) + spec.get('relative', 0) * abs(spec['target'] if number(spec['target']) else 0)
        return -width, width
    return spec.get('minimum'), spec.get('maximum')


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
    targeted = 'target' in accept
    if not targeted and ('equals' in accept or not {'minimum', 'maximum'} & accept.keys()):
        raise ValueError(what + ': tune needs a numeric tolerance (minimum, maximum or a target) to improve on')
    two_sided = targeted or {'minimum', 'maximum'} <= accept.keys()
    goal = value.get('goal', 'center' if two_sided else 'maximize' if 'minimum' in accept else 'minimize')
    if goal not in GOALS:
        raise ValueError(what + ': goal is one of ' + ', '.join(GOALS))
    if goal == 'center' and not two_sided:
        raise ValueError(what + ': center needs both minimum and maximum in accept, or a target')
    evaluations = value.get('evaluations', 40)
    if isinstance(evaluations, bool) or not isinstance(evaluations, int) or not 1 <= evaluations <= 1000:
        raise ValueError(what + ': evaluations is the number of settings that may be tried, 1 to 1000')
    if value.get('until', 'optimum') not in ('optimum', 'accept'):
        raise ValueError(what + ': until is optimum, or accept to stop once the tolerance is met')
    if not number(value.get('gain', 0)) or value.get('gain', 0) < 0:
        raise ValueError(what + ': gain is the smallest improvement that counts, above the noise of the verification')
    return {'tune': checked, 'goal': goal, 'evaluations': evaluations, 'until': value.get('until', 'optimum'),
            'gain': value.get('gain', 0)}


def setting(knob, position):
    """The operation that puts one tuned setting at a position."""
    return {'device': knob['device'], 'operation': knob['operation'],
            'arguments': {**knob.get('arguments', {}), knob['argument']: position}}


def readings(condition):
    """What a verification observes: its operation, and the readings that give the targets of its tolerances."""
    items = [condition['verify']]
    for key in ('accept', 'usable'):
        target = (condition.get(key) or {}).get('target')
        if isinstance(target, dict):
            items.append({k: v for k, v in target.items() if k != 'value'})
    return items


def operations_used(condition):
    """Every operation a condition may call: its observations, its corrections and both ends of a tuned range."""
    items = readings(condition)
    for correction in condition['correct']:
        if 'tune' in correction:
            for knob in correction['tune']:
                items += [setting(knob, knob['minimum']), setting(knob, knob['maximum']),
                          {k: v for k, v in knob['read'].items() if k != 'value'}]
        else:
            items.append(correction)
    return items


def label(item):
    if 'tune' in item:
        return 'tune ' + ', '.join(f"{k['device']}.{k['argument']}" for k in item['tune'])
    return (item['device'] + '.' if 'device' in item else '') + item['operation']


def check(definitions, taken=(), external=()):
    """Validate the conditions of one platform or instrument.

    ``taken`` are names defined elsewhere in the laboratory; ``external`` are conditions of the
    platform's instruments, which ``after`` may name.
    """
    if not isinstance(definitions, list) or any(not isinstance(c, dict) for c in definitions):
        raise ValueError('Platform conditions must be a list of objects')
    names = strings([c.get('id') for c in definitions], 'Condition names')
    checked = []
    for condition in definitions:
        what = 'Condition ' + name(condition['id'], 'Condition name')
        if condition.keys() - FIELDS:
            raise ValueError(what + ': unknown fields ' + ', '.join(sorted(condition.keys() - FIELDS)))
        if condition['id'] in taken:
            raise ValueError(what + ' is already defined on another platform or instrument')
        if not isinstance(condition.get('description'), str) or not condition['description'].strip():
            raise ValueError(what + ': describe what must hold')
        if 'kind' in condition:
            name(condition['kind'], what + ' kind')
        after = strings(condition.get('after', []), what + ' after')
        if set(after) - set(names) - set(external) or condition['id'] in after:
            raise ValueError(what + ': after names other conditions of this platform or of its instruments')
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
        samples = condition.get('samples', 1)
        if isinstance(samples, bool) or not isinstance(samples, int) or not 1 <= samples <= 1000:
            raise ValueError(what + ': samples is the number of readings a verification takes, 1 to 1000')
        if 'interval' in condition and (not number(condition['interval']) or condition['interval'] < 0):
            raise ValueError(what + ': interval is the time between samples in seconds')
        accept = criterion(condition.get('accept'), what + ' accept')
        for spec in (accept, condition.get('usable') or {}):
            if spec.get('statistic') in ('std', 'span', 'slope') and samples < 2:
                raise ValueError(what + ': std, span and slope compare several readings; set samples to 2 or more')
        checked.append({**condition, 'after': after, 'verify': step(condition.get('verify'), what + ' verify'),
                        'accept': accept,
                        **({'usable': criterion(condition['usable'], what + ' usable')} if 'usable' in condition else {}),
                        'correct': [tuning(item, accept, what + ' correct') if isinstance(item, dict) and 'tune' in item
                                    else step(item, what + ' correct') for item in corrections]})
    # Instrument conditions never depend on platform conditions, so a cycle lies within this list.
    order([{**c, 'after': [a for a in c['after'] if a in names]} for c in checked], names)
    return checked


def upkeep(value, names):
    """The upkeep a platform declares; ``names`` are the conditions of the platform and its instruments."""
    if not isinstance(value, dict) or value.keys() - {'conditions', 'ahead_s', 'retry_s', 'correct'}:
        raise ValueError('upkeep takes conditions, ahead_s, retry_s and correct')
    for key, minimum in (('ahead_s', 0), ('retry_s', 1e-9)):
        if key in value and (isinstance(value[key], bool) or not isinstance(value[key], (int, float))
                             or not math.isfinite(value[key]) or value[key] < minimum):
            raise ValueError(f'upkeep {key} is a duration in seconds')
    if not isinstance(value.get('correct', True), bool):
        raise ValueError('upkeep correct is true, or false to observe without correcting')
    kept = strings(value.get('conditions', list(names)), 'upkeep conditions')
    if set(kept) - set(names):
        raise ValueError('upkeep conditions name conditions of this platform or its instruments: ' +
                         ', '.join(sorted(set(kept) - set(names))))
    return {'conditions': kept, 'ahead_s': float(value.get('ahead_s', 0)), 'retry_s': float(value.get('retry_s', 1800)),
            'correct': value.get('correct', True)}


def experiment_upkeep(value):
    """The upkeep an experiment declares for the conditions it requires."""
    if value is True:
        value = {}
    if not isinstance(value, dict) or 'conditions' in value:
        raise ValueError('An experiment keeps the conditions it requires: upkeep takes ahead_s, retry_s and correct')
    settings = upkeep(value, [])
    del settings['conditions']
    return settings


def instrument(device_id, definitions):
    """The conditions of one instrument, named ``<device>.<id>`` in the laboratory.

    They are observed and restored with the instrument's own operations; ``device`` may be omitted.
    """
    if not isinstance(definitions, list) or any(not isinstance(c, dict) for c in definitions):
        raise ValueError('Instrument conditions must be a list of objects')
    for local in strings([c.get('id') for c in definitions], 'Instrument condition names'):
        if not LOCAL.fullmatch(local):
            raise ValueError(f'Instrument condition {local}: use letters, digits, _ and -; the device name is added')

    def own(item, what):
        if not isinstance(item, dict):
            return item
        if 'tune' in item:
            knobs = []
            for knob in item['tune'] if isinstance(item['tune'], list) else [item['tune']]:
                if isinstance(knob, dict):
                    knob = {**knob, 'device': knob.get('device', device_id)}
                    if isinstance(knob.get('read'), dict):
                        knob['read'] = {**knob['read'], 'device': knob['read'].get('device', device_id)}
                    if device_id != knob['device'] or isinstance(knob.get('read'), dict) and device_id != knob['read']['device']:
                        raise ValueError(what + ': an instrument condition uses only that instrument')
                knobs.append(knob)
            return {**item, 'tune': knobs}
        if item.get('device', device_id) != device_id:
            raise ValueError(what + ': an instrument condition uses only that instrument')
        return {**item, 'device': device_id}

    prefix, named = device_id + '.', []
    for condition in definitions:
        what = 'Instrument condition ' + condition['id']
        after, corrections = condition.get('after', []), condition.get('correct', [])
        corrections = [corrections] if isinstance(corrections, dict) else corrections
        tolerances = {key: {**condition[key], 'target': own(condition[key]['target'], f'{what} {key} target')}
                      for key in ('accept', 'usable') if isinstance(condition.get(key), dict)
                      and isinstance(condition[key].get('target'), dict)}
        named.append({**condition, **tolerances, 'id': prefix + condition['id'],
                      'after': [prefix + a if isinstance(a, str) else a for a in after] if isinstance(after, list) else after,
                      'verify': own(condition.get('verify'), what + ' verify'),
                      'correct': [own(item, what + ' correct') for item in corrections] if isinstance(corrections, list)
                      else corrections})
    return check(named)


def available(conditions, operations, builtin=()):
    """Check that each operation the conditions call exists with valid arguments.

    ``operations`` maps names to their descriptions; ``builtin`` names exist without one.
    """
    for condition in conditions:
        for item in operations_used(condition):
            if item['operation'] in builtin and item['operation'] not in operations:
                continue
            spec = operations.get(item['operation'])
            if spec is None:
                raise ValueError(f"Condition {condition['id']}: {item['device']} has no operation {item['operation']}")
            try:
                parameters(spec, item.get('arguments', {}))
            except ValueError as error:
                raise ValueError(f"Condition {condition['id']}: {item['device']}.{item['operation']}: {error}") from None


def order(definitions, targets):
    """The targets and everything they depend on, each after its dependencies."""
    by_name = {c['id']: c for c in definitions}
    needed, pending = [], deque(targets)
    while pending:
        current = pending.popleft()
        # A dependency that is not defined (an instrument not registered now) is left out; its dependents report it.
        if current not in needed and current in by_name:
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


def outcome(condition, results, times=None, targets=None):
    """Classify a verification: holds, drifted (measurable but out of tolerance) or unusable.

    ``results`` are the readings of its samples (one result is one sample) taken at ``times``; ``targets``
    holds what was read for each tolerance whose target is a reading.
    """
    results = results if isinstance(results, list) else [results]
    value, kept, targets = None, {}, targets or {}

    def judged(key):
        spec = condition[key]
        return judge(spec, results, times, spec['target'] if number(spec.get('target')) else targets.get(key))
    try:
        value, met = judged('accept')
    except (KeyError, IndexError, TypeError, ValueError, statistics.StatisticsError):
        return 'unusable', None, kept
    if 'usable' in condition:
        try:
            if not judged('usable')[1]:
                return 'unusable', value, kept
        except (KeyError, IndexError, TypeError, ValueError, statistics.StatisticsError):
            return 'unusable', value, kept
    for key, path in condition.get('reference', {}).items():
        found = []
        for result in results:
            item = result
            try:
                for part in path:
                    item = item[part]
            except (KeyError, IndexError, TypeError):
                item = None
            found.append(item)
        # A reference several samples give is their mean, as a dark level or a baseline is averaged.
        kept[key] = statistics.fmean(found) if len(found) > 1 and all(number(v) for v in found) else found[-1]
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
        low, high = bounds(condition['accept'])
        bound = high if slope > 0 else low
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
        # Per platform: when each condition was last attempted automatically, and what the last pass found.
        self.attempts, self.waiting = {}, {}
        # Passes each experiment's upkeep started in a row while one of its operations was free to start and
        # waited only for its conditions, since one last started.
        self.renewed = {}
        self.wake = threading.Event()
        self.index()

    def index(self):
        """Conditions by name: those of the registered instruments, then those of the saved platforms."""
        self.definitions, self.devices, self.errors = {}, {}, {}
        for device_id, record in sorted(self.engine.device_records.items()):
            if record.get('conditions'):
                try:
                    for condition in instrument(device_id, record['conditions']):
                        self.definitions[condition['id']] = {**condition, 'device': device_id}
                except ValueError as error:
                    self.errors[device_id] = str(error)
        for platform_id in sorted(self.engine.platforms):
            for condition in self.engine.platforms[platform_id]['definition'].get('conditions', []):
                self.definitions[condition['id']] = {**condition, 'platform': platform_id}
        for condition_id, condition in self.definitions.items():
            self.devices[condition_id] = self._devices(condition)
        if hasattr(self, 'wake'):
            self.wake.set()

    def scope(self, platform_id):
        """The conditions of a platform and of its instruments."""
        members = set(self.engine.platforms[platform_id]['definition']['devices'])
        return [c for c, d in self.definitions.items() if d.get('platform') == platform_id or d.get('device') in members]

    def _scope(self, conditions, platform, device):
        """The platform or instrument whose maintenance covers these conditions."""
        owners = [self.definitions[c] for c in conditions]
        platforms = {d['platform'] for d in owners if 'platform' in d}
        devices = {d['device'] for d in owners if 'device' in d}
        if len(platforms) > 1:
            raise ValueError('Maintain the conditions of one platform at a time')
        chosen = platforms.pop() if platforms else platform
        if chosen is not None and device is None and platform in (None, chosen) and chosen in self.engine.platforms \
                and devices <= set(self.engine.platforms[chosen]['definition']['devices']):
            return chosen, None
        if not platforms and platform is None and len(devices) == 1 and device in (None, *devices):
            return None, devices.pop()
        raise ValueError('Maintain the conditions of one platform and its instruments, or of one instrument, at a time')

    def _devices(self, condition):
        devices = set()
        for item in readings(condition) + condition['correct']:
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
        result = {k: condition[k] for k in ('verify', 'samples', 'interval', 'accept', 'usable', 'after', 'reference')
                  if k in condition}
        try:
            steps, calls, _ = library.expand([{'id': 'verify', **condition['verify']}], self.engine.library.get)
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
            for item in operations_used(self.definitions[condition_id]):
                expanded, calls, _ = library.expand([{'id': 'step', **item}], engine.library.get)
                revisions.update({c['operation']: c['revision'] for c in calls.values()})
                for flat in expanded:
                    op = engine._operation('prepare', flat)
                    for key, count in engine._amounts(op, reserve=True).items():
                        amounts[key] = max(amounts.get(key, 0), count)
        return amounts, revisions

    def prepare(self, definition):
        """Check the conditions of a platform being saved against its devices and the saved operations."""
        engine = self.engine
        taken = {c for c, d in self.definitions.items() if d.get('platform') != definition['id']}
        external = {c for c, d in self.definitions.items() if d.get('device') in definition['devices']}
        conditions = check(definition.get('conditions', []), taken, external)
        if 'upkeep' in definition:
            definition['upkeep'] = upkeep(definition['upkeep'], [c['id'] for c in conditions] + sorted(external))
        for condition in conditions:
            for item in readings(condition) + condition['correct']:
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

    def prepare_instrument(self, device_id, record, description):
        """Check an instrument's own conditions against its operations; returns them with laboratory names."""
        conditions = instrument(device_id, record['conditions']) if record.get('conditions') else []
        names = {c['id'] for c in conditions}
        for condition_id, definition in self.definitions.items():
            if condition_id in names and definition.get('device') != device_id:
                raise ValueError(f'Condition {condition_id} is already defined on a platform or another instrument')
        removed = {c for c, d in self.definitions.items() if d.get('device') == device_id} - names
        for condition_id, definition in self.definitions.items():
            if 'platform' in definition and removed & set(definition['after']):
                raise ValueError(f"Platform condition {condition_id} depends on {', '.join(sorted(removed & set(definition['after'])))}; "
                                 f"revise platform {definition['platform']} first")
        available(conditions, description['operations'])
        return conditions

    # State: what is known without touching an instrument.

    def state(self, condition_id, at=None, visiting=()):
        condition, saved = self.definitions[condition_id], self.states.get(condition_id)
        at = at or seconds(now())
        missing = [d for d in condition['after'] if d not in self.definitions]
        if missing:
            return {'state': 'unknown', 'reason': ', '.join(missing) + ' not defined'}
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
        if saved.get('forecast'):
            # When the straight line through the accepted values reaches the tolerance.
            shown['forecast'] = saved['forecast']
        return {'state': 'holds', **shown}

    def listing(self, platform=None, device=None):
        """Conditions with their state, in dependency order: all, a platform's with its instruments', or an instrument's."""
        with self.engine.lock:
            if platform is not None and platform not in self.engine.platforms:
                raise KeyError(platform)
            if device is not None and device not in self.engine.devices:
                raise KeyError(device)
            selected = (self.scope(platform) if platform is not None else
                        [c for c, d in self.definitions.items() if d.get('device') == device] if device is not None
                        else list(self.definitions))
            local = [{**self.definitions[c], 'after': [a for a in self.definitions[c]['after'] if a in selected]} for c in selected]
            rows = []
            for condition_id in order(local, selected):
                condition = self.definitions[condition_id]
                owner = {'platform': condition['platform']} if 'platform' in condition else {'device': condition['device']}
                rows.append({'id': condition_id, **owner, 'description': condition['description'],
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
                raise ValueError('Unknown condition: ' + condition_id + closest(condition_id, self.definitions))
        for condition_id in self.closure(names):
            state = self.state(condition_id)
            (relied if state['state'] == 'holds' else lacking)[condition_id] = state
        if lacking:
            text = '; '.join(f"{c} is {s['state']}" + (f" ({s['reason']})" if s.get('reason') else '') for c, s in lacking.items())
            if self.renewed.pop(run_id, 0) >= RENEWALS:
                # Upkeep restored them each time, and each time they came due before an operation could start.
                # The operation after this one starts counting again.
                raise RequestError('conditions_not_held', f'{text}, again after upkeep renewed what this experiment '
                                   f'requires {RENEWALS} times in a row without one of its operations starting. A validity '
                                   'shorter than the time from a verification to the next operation cannot be kept. '
                                   'Review maintenance and acquisition timing and the valid_for setting; extend validity '
                                   'only when observations support it.', conditions=lacking, run_id=run_id)
            raise RequestError('conditions_not_held', text + '. Maintain these conditions, then submit again.',
                               conditions=lacking, maintain='/v1/conditions/maintain',
                               **({'run_id': run_id} if run_id else {}))
        return {c: {k: s[k] for k in ('accepted', 'value', 'reference') if k in s} for c, s in relied.items()}

    def lapsed(self, names):
        """Whether a condition these rest on has come due or is unknown: what a pass of upkeep renews."""
        return any(self.state(condition_id)['state'] in ('due', 'unknown') for condition_id in self.closure(names))

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
                # Data taken since the last accepted verification relied on a condition now found lost: the
                # experiment keeps the first such interval, and each operation in progress since then is marked.
                entry = {'since': saved.get('accepted'), 'found': time, 'outcome': result}
                for other in relying:
                    run = engine.runs.get(other) or engine.store.get('runs', other)
                    if run and condition_id not in run.setdefault('in_doubt', {}):
                        run['in_doubt'][condition_id] = entry
                        engine.store.put('runs', run)
                        doubtful.append(other)
                    if saved.get('accepted'):
                        engine.doubt(other, condition_id, saved['accepted'], entry)
            saved['relying'] = [other for other in relying if other in engine.runs]
            saved.update(verified=time, outcome=result, value=value, error=error)
            saved.pop('forecast', None)
            if result == 'holds':
                for attempts in self.attempts.values():
                    attempts.pop(condition_id, None)
                trend = summary(self.definitions[condition_id], engine.store.condition_records(condition_id) + [record]).get('trend', {})
                if trend.get('expected_until'):
                    saved['forecast'] = trend['expected_until']
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
                saved.pop('forecast', None)
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
                saved.pop('forecast', None)
                time = now()
                entry = {'since': time, 'found': time, 'outcome': 'disturbed', 'reason': reason}
                for run_id in saved.get('relying', []):
                    run = self.engine.runs.get(run_id)
                    if run and condition_id not in run.setdefault('in_doubt', {}):
                        run['in_doubt'][condition_id] = entry
                        self.engine.store.put('runs', run)
                    # An operation in progress took data through the disturbance.
                    self.engine.doubt(run_id, condition_id, time, entry)
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

    def maintain(self, platform=None, conditions=None, run_id=None, request_id=None, force=False, device=None,
                 automatic=None):
        engine = self.engine
        with engine.lock:
            request = {'platform': platform, 'conditions': sorted(strings(conditions, 'Conditions')) if conditions else None,
                       'run_id': run_id, 'force': bool(force), **({'device': device} if device is not None else {})}
            if request_id is not None:
                if not isinstance(request_id, str) or not request_id.strip():
                    raise ValueError('request_id is a nonempty string')
                previous = engine.store.maintenance(request_id=request_id)
                if previous:
                    if previous.get('request') != request:
                        raise ValueError('request_id already names a different maintenance request')
                    return self.job(previous['id'])
            if not conditions and platform is None and device is None and run_id in engine.runs:
                # Inside an experiment the default is what that experiment relies on.
                conditions = engine.runs[run_id].get('requires')
            run = engine.runs.get(run_id)
            if run and platform is None and device is None:
                platform = run.get('platform', {}).get('id')
            if conditions:
                for condition_id in strings(conditions, 'Conditions'):
                    if condition_id not in self.definitions:
                        raise ValueError('Unknown condition: ' + condition_id + closest(condition_id, self.definitions))
                # An experiment without a platform may reserve several instruments. Its reservation
                # below is the scope; standalone maintenance still names one platform or instrument.
                if run is None or platform is not None or device is not None:
                    platform, device = self._scope(conditions, platform, device)
                targets = list(conditions)
            elif device is not None:
                if device not in engine.devices:
                    raise ValueError('Name a platform, an instrument or their conditions')
                targets = [c for c, d in self.definitions.items() if d.get('device') == device]
                if not targets:
                    raise ValueError('This instrument has no conditions')
            else:
                if platform not in engine.platforms:
                    raise ValueError('Name a platform or its conditions')
                targets = self.scope(platform)
                if not targets:
                    raise ValueError('This platform has no conditions')
            closure = self.closure(targets)
            active = [job for job in self.jobs.values() if job['state'] == 'running' and set(closure) & set(job['closure'])]
            if active:
                raise RequestError('maintenance_active', 'These conditions are being maintained; read that maintenance.',
                                   maintenance=active[0]['id'])
            from .engine import identifier
            job = {'id': identifier('maintenance'), 'request_id': request_id, 'platform': platform,
                   **({'device': device} if device is not None else {}), 'conditions': targets, 'closure': closure,
                   'force': bool(force), 'state': 'running', 'started': now(), 'report': [], 'steps': 0,
                   'run_id': run_id, 'own_run': run_id is None, 'request': request,
                   # An automatic pass records why it started; it renews what is about to expire and never asks a person.
                   **({'automatic': True, 'reasons': automatic['reasons'], 'renew': automatic['renew'],
                       'observe_only': not automatic['correct']} if automatic else {})}
            amounts, job['operation_revisions'] = self.requirements(targets)
            if run_id is None:
                # Keep coupled devices together through the complete correction cycle.
                job['run_id'] = engine.submit({'kind': 'external', 'name': 'Maintain ' + (platform or device) +
                                                       (' (upkeep)' if automatic else ''),
                                               **({'platform': platform} if platform is not None else {}),
                                               'resources': sorted(k[7:] for k in amounts if k.startswith(('device:', 'shared:'))),
                                               'requirements': {k[5:]: n for k, n in amounts.items() if k.startswith('pool:')},
                                               'request_id': job['id']})['id']
            else:
                run = engine.runs.get(run_id)
                if not run or not (run['state'] == 'running' or run['kind'] == 'external' and run['state'] == 'queued'):
                    raise ValueError('Maintenance inside an experiment requires an active experiment')
                # A pass starts between the experiment's operations; those queued meanwhile wait for it.
                if engine.running(run_id):
                    raise RequestError('maintenance_busy', 'Wait for this experiment\'s operations in progress before maintenance.',
                                       run_id=run_id)
                reserved = engine._amounts(run, reserve=True)
                missing = {k: n for k, n in amounts.items() if n > reserved.get(k, 0)}
                # A step graph without a reservation claims each instrument when a step uses it, as its own steps do.
                if missing and (reserved or run['kind'] != 'workflow'):
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
        return {**{k: v for k, v in job.items() if k not in ('steps', 'own_run', 'force', 'request', 'closure', 'renew',
                                                             'observe_only')},
                'complete': job['state'] != 'running'}

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
            if dependency not in self.definitions or self._ensure(job, dependency, done, investigated) != 'holds':
                entry.update(result='blocked', by=dependency)
                return self._report(job, done, entry)
        with self.engine.lock:
            held = self.state(condition_id)
        if held['state'] == 'holds' and not job['force'] and condition_id not in job.get('renew', ()) and not investigate:
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
        if job.get('observe_only') and condition['correct']:
            entry.update(result='lost', value=value, not_corrected='upkeep observes only')
            return self._report(job, done, entry)
        left = []
        for item in condition['correct']:
            before, tuned = value, None
            if job.get('automatic') and self._asks_person(item):
                left.append(label(item))
                continue
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
        entry.update(result='lost', value=value, **({'left_for_a_person': left} if left else {}))
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

    def _observe(self, job, condition):
        """The readings of one verification: its samples, ``interval`` apart, and the targets it compares with."""
        results, times, targets = [], [], {}
        for index in range(condition.get('samples', 1)):
            if index and self.stopping.wait(condition.get('interval', 0)):
                raise Stopped()
            results.append(self._execute(job, 'verify', condition['verify']))
            times.append(monotonic())
        for key in ('accept', 'usable'):
            target = (condition.get(key) or {}).get('target')
            if isinstance(target, dict):
                found = self._execute(job, 'target', {k: v for k, v in target.items() if k != 'value'})
                try:
                    for part in target['value']:
                        found = found[part]
                except (KeyError, IndexError, TypeError):
                    found = None
                targets[key] = found
        return results, times, targets

    def _verify(self, job, condition):
        try:
            observed = self._observe(job, condition)
        except StepFailed as error:
            self.verified(condition['id'], 'unusable', None, {}, job['run_id'], str(error))
            return 'unusable', None
        found, value, kept = outcome(condition, *observed)
        self.verified(condition['id'], found, value, kept, job['run_id'])
        return found, value

    _set = staticmethod(setting)

    def _asks_person(self, item):
        """Whether a correction includes a step that a person performs."""
        if 'tune' in item:
            return False
        try:
            steps = library.expand([{'id': 'step', **item}], self.engine.library.get)[0]
        except ValueError:
            return False
        return any(self.engine.device_records.get(s['device'], {}).get('driver') == 'manual' for s in steps)

    # Upkeep: the Runtime keeps the conditions of platforms that declare it.

    def start(self):
        thread = threading.Thread(target=self._keep, name='iop-upkeep', daemon=True)
        self.threads.append(thread)
        thread.start()

    def _keep(self):
        while not self.stopping.is_set():
            self.wake.wait(float(self.engine.config.get('upkeep_interval_s', 2)))
            self.wake.clear()
            if self.stopping.is_set() or self.engine.stopping:
                return
            for platform_id in list(self.engine.platforms):
                try:
                    self._upkeep(platform_id)
                except Exception as error:
                    with self.engine.lock:
                        self._wait(platform_id, f'{type(error).__name__}: {error}')
            # Experiments that keep their own conditions are kept between their operations.
            with self.engine.lock:
                for run in [r for r in self.engine.runs.values() if r.get('upkeep') and r['state'] in ('queued', 'running')]:
                    try:
                        self.keep(run, run.get('requires', []))
                    except (ValueError, KeyError) as error:
                        self._wait(run['id'], str(error))
                for store in (self.waiting, self.attempts, self.renewed):
                    for key in [k for k in store if k not in self.engine.platforms and k not in self.engine.runs]:
                        del store[key]

    def plan(self, platform_id, at=None):
        """What an automatic pass would maintain now and why, and when the next one is foreseen."""
        settings = self.engine.platforms[platform_id]['definition'].get('upkeep')
        if settings is None:
            return None
        return self._plan(settings['conditions'], settings, platform_id, at)

    def _plan(self, names, settings, key, at=None):
        at = at or seconds(now())
        reasons, renew, upcoming = {}, [], []
        attempts = self.attempts.get(key, {})
        for condition_id in names:
            if condition_id not in self.definitions:
                continue
            state, reason = self.state(condition_id, at), None
            if state['state'] in ('unknown', 'lost'):
                reason = state['state']
            elif state['state'] == 'due':
                reason = 'due: ' + state.get('reason', '')
            else:
                # The forecast decides when to look again, never what to change: one observation ahead_s before
                # the drift line reaches the tolerance and one when it does. Each moment triggers once, since an
                # observation made after it moves accepted past it.
                accepted = seconds(state['accepted'])
                moments = []
                if state.get('valid_until'):
                    moments.append((seconds(state['valid_until']) - settings['ahead_s'], 'validity ends'))
                if state.get('forecast'):
                    crossing = seconds(state['forecast'])
                    moments += [(crossing - settings['ahead_s'], 'forecast reaches the tolerance'),
                                (crossing, 'forecast reaches the tolerance')]
                for when, why in moments:
                    if accepted < when <= at:
                        reason = reason or why
                    elif when > at:
                        upcoming.append(when)
            if reason is None:
                continue
            # An attempt that did not end with the condition held is repeated only after retry_s.
            retry = attempts.get(condition_id, -math.inf) + settings['retry_s']
            if at < retry:
                upcoming.append(retry)
                continue
            reasons[condition_id] = reason
            if state['state'] == 'holds':
                renew.append(condition_id)
        stamp = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
        return {'reasons': reasons, 'renew': renew, 'correct': settings['correct'],
                'next': stamp(min(upcoming)) if upcoming and not reasons else None}

    def _busy(self, keys):
        """An active experiment or a held resource that uses these devices or shared objects."""
        engine = self.engine
        for run in engine.runs.values():
            used = set(run.get('involved_resources', [])) | {'device:' + d for d in run.get('devices', {})}
            if run['state'] not in ('completed', 'failed', 'cancelled', 'interrupted') and used & keys:
                return 'experiment ' + run['id']
        held = keys & engine.resource_holds.keys()
        if held:
            return 'held resource ' + sorted(held)[0].split(':', 1)[1]
        return None

    def _wait(self, key, reason):
        """Remember what upkeep of a platform or an experiment waits for; record each new reason once."""
        if self.waiting.get(key) != reason:
            self.waiting[key] = reason
            if reason:
                scope = {'platform': key} if key in self.engine.platforms else {}
                with self.engine.store.transaction():
                    self.engine.store.event('upkeep.waiting', None if scope else key, reason=reason, **scope)

    def _upkeep(self, platform_id):
        engine = self.engine
        with engine.lock:
            if platform_id not in engine.platforms or engine.stopping:
                return
            plan = self.plan(platform_id)
            if not plan or not plan['reasons']:
                self._wait(platform_id, None)
                return
            targets = list(plan['reasons'])
            closure = set(self.closure(targets))
            if any(job['state'] == 'running' and closure & set(job['closure']) for job in self.jobs.values()):
                return
            amounts, _ = self.requirements(targets)
            reason = self._busy({k for k in amounts if k.startswith(('device:', 'shared:'))})
            if reason:
                self._wait(platform_id, 'waiting for ' + reason)
                return
            self._wait(platform_id, None)
            self._start(platform_id, targets, plan, platform=platform_id)

    def _start(self, key, targets, plan, **scope):
        """Start an automatic pass and remember the attempt, so a failed one waits retry_s."""
        job = self.maintain(conditions=targets, request_id=f'upkeep.{key}.{uuid.uuid4().hex[:12]}', automatic=plan, **scope)
        time = seconds(job['started'])
        for condition_id in targets:
            self.attempts.setdefault(key, {})[condition_id] = time
        with self.engine.store.transaction():
            self.engine.store.event('upkeep.started', job['run_id'], maintenance=job['id'], conditions=targets,
                                    **({'platform': scope['platform']} if 'platform' in scope else {}))
        return job

    def keep(self, run, names):
        """Inside an experiment that declares upkeep: start or join a pass when what it relies on needs one.

        Returns True while its operations wait: a pass runs in the experiment, or one is due and starts once
        none of its operations is running. A pass counts when it starts while an operation of the experiment is
        free to start and waits only for its conditions. After RENEWALS such passes in a row with no operation
        started between them, no further pass starts: that operation is refused with the reason. Passes that keep
        the conditions of an experiment whose operations wait for an instrument, or which has none to run, do
        not count: it may stay so for any time.
        """
        if not run.get('upkeep') or not names or run['kind'] != 'external' and run['state'] == 'queued':
            return False
        for job in self.jobs.values():
            if job['state'] == 'running' and job['run_id'] == run['id']:
                uncovered = [c for c in self.closure(names) if c not in job['closure'] and self.state(c)['state'] != 'holds']
                if uncovered:
                    raise RequestError('maintenance_active', 'Wait for this maintenance before submitting operations '
                                       'that require additional conditions.', maintenance=job['id'], conditions=uncovered)
                return True
        plan = self._plan(self.closure(names), run['upkeep'], run['id'])
        if not plan['reasons']:
            self._wait(run['id'], None)
            return False
        if self.engine.running(run['id']):
            return True
        if self.renewed.get(run['id'], 0) >= RENEWALS:
            return False
        try:
            waits = self.engine.startable(run['id'])
            self._start(run['id'], list(plan['reasons']), plan, run_id=run['id'])
            self.renewed[run['id']] = self.renewed.get(run['id'], 0) + 1 if waits else 0
        except RequestError as error:
            if error.code != 'maintenance_active':
                self._wait(run['id'], str(error))
                return False
            # Another pass keeps these conditions; the experiment waits for it.
            self._wait(run['id'], 'waiting for maintenance ' + error.details['maintenance'])
            return True
        except (ValueError, KeyError) as error:
            self._wait(run['id'], str(error))
            return False
        self._wait(run['id'], None)
        return True

    def upkeep_status(self, platform=None):
        """For each platform and active experiment that declares upkeep: its settings, the next foreseen pass,
        what it waits for and the last pass."""
        with self.engine.lock:
            rows = []
            for run in self.engine.runs.values():
                if run.get('upkeep') and platform is None:
                    plan = self._plan(self.closure(run.get('requires', [])), run['upkeep'], run['id'])
                    last = max((j for j in self.jobs.values() if j.get('automatic') and j['run_id'] == run['id']),
                               key=lambda j: j['started'], default=None)
                    rows.append({'run': run['id'], 'upkeep': run['upkeep'], 'next': plan['next'], 'pending': plan['reasons'],
                                 **({'waiting': self.waiting[run['id']]} if self.waiting.get(run['id']) else {}),
                                 **({'last': {k: last[k] for k in ('id', 'state', 'started', 'ended', 'holds') if k in last}}
                                    if last else {})})
            for platform_id in sorted(self.engine.platforms):
                settings = self.engine.platforms[platform_id]['definition'].get('upkeep')
                if settings is None or platform not in (None, platform_id):
                    continue
                plan = self.plan(platform_id)
                last = max((j for j in self.jobs.values() if j.get('automatic') and j['platform'] == platform_id),
                           key=lambda j: j['started'], default=None)
                rows.append({'platform': platform_id, 'upkeep': settings, 'next': plan['next'],
                             'pending': plan['reasons'], **({'waiting': self.waiting[platform_id]} if self.waiting.get(platform_id) else {}),
                             **({'last': {k: last[k] for k in ('id', 'state', 'started', 'ended', 'holds', 'run_id') if k in last}}
                                if last else {})})
            return rows

    def _tune(self, job, condition, item, value, initial_outcome):
        """Adjust the settings one at a time against the verification value, halving the step when no move helps.

        A bounded search without derivatives: each setting is stepped in the direction that improves the
        value and stepped back when it does not, so the system is left at the best setting that was seen.
        """
        knobs, goal, gain = item['tune'], item['goal'], item['gain']
        accept = condition['accept']
        # A target is met at zero deviation; a range at its middle.
        middle = (0 if 'target' in accept else (accept['minimum'] + accept['maximum']) / 2) if goal == 'center' else None

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
            initial_outcome, value, _ = outcome(condition, *self._observe(job, condition))
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
                        found, reading, _ = outcome(condition, *self._observe(job, condition))
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
        failed = [op for op in receipt['operations'] if op['state'] not in SATISFIED]
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
        self.wake.set()
        for thread in self.threads:
            thread.join(timeout=10)

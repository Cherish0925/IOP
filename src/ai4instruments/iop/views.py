"""Views of saved arrays: statistics, an envelope along one axis and exact values in a window.

A trace, spectrum, image or buffer is read without moving all of its values. One axis is followed;
every other axis is either fixed with ``at`` or summarized. A last axis of ``re``/``im`` pairs is
read as ``abs`` unless ``at`` chooses ``re``, ``im`` or ``arg``. iop-execd computes the view in its
own process; without it, Python computes the same view when the elements read stay within
``PYTHON_LIMIT``.
"""
import array
import itertools
import json
import math
import operator
import subprocess
import sys
from pathlib import Path
from .arrays import DTYPES, _typecode

POINTS = 64
MAX_POINTS = 2000
PYTHON_LIMIT = 4_000_000
COMPONENTS = ['re', 'im']


def arrays_in(result, prefix=''):
    """Paths of the arrays in an operation result: '' for the result itself, else e.g. 'items.trace'."""
    found = []
    if isinstance(result, dict):
        data = result.get('data')
        if isinstance(data, dict) and isinstance(data.get('dtype'), str) and isinstance(data.get('shape'), list):
            found.append(prefix)
        for key, value in result.items():
            if key not in ('data', 'values') and isinstance(value, dict):
                found += arrays_in(value, f'{prefix}.{key}' if prefix else key)
    return found


def select(result, field=None):
    """The array a field names; a result that holds one array needs no field."""
    paths = arrays_in(result)
    shown = ', '.join(repr(p) for p in paths)
    if field is None:
        if not paths:
            raise ValueError('This operation result holds no array')
        if len(paths) > 1:
            raise ValueError('This result holds several arrays; name one with field: ' + shown)
        field = paths[0]
    if field not in paths:
        raise ValueError(f'No array at {field!r}; this result holds: {shown or "none"}')
    holder = result
    for key in field.split('.') if field else []:
        holder = holder[key]
    return field, holder


def _axis(axes, shape, key, what):
    if isinstance(key, str) and key.isdigit():
        key = int(key)
    if isinstance(key, int) and not isinstance(key, bool):
        if 0 <= key < len(shape):
            return key
    elif isinstance(key, str):
        for index, axis in enumerate(axes):
            if axis.get('name') == key:
                return index
    names = [axis['name'] for axis in axes] if axes else [str(i) for i in range(len(shape))]
    raise ValueError(f'{what}: no axis {key!r}; the axes are ' + ', '.join(names))


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _coordinates(axis, size):
    """Start and step of a uniformly spaced axis, or None."""
    if not _number(axis.get('start')):
        return None
    if _number(axis.get('step')) and axis['step']:
        return float(axis['start']), float(axis['step'])
    if _number(axis.get('stop')) and size > 1:
        return float(axis['start']), (float(axis['stop']) - float(axis['start'])) / (size - 1)
    return None


def _positions(coordinates, size, low, high):
    """The positions whose coordinate lies between low and high."""
    start, step = coordinates
    # Fractional positions of the two bounds, kept finite so that open bounds select to the end.
    lo, hi = sorted(min(max((c - start) / step, -1.0), float(size)) for c in (low, high))
    first = max(math.ceil(lo - 1e-9), 0)
    last = min(math.floor(hi + 1e-9), size - 1)
    if first > last:
        raise ValueError(f'No position of this axis lies between {low} and {high}')
    return first, last + 1


def _short(value):
    """Seven significant digits: an envelope shows a shape, not exact values."""
    return None if value is None else float(f'{value:.7g}')


def plan(data, along=None, at=None, start=None, stop=None, low=None, high=None, points=POINTS):
    """Resolve axis names, labels and coordinates into the request iop-execd and Python compute."""
    shape, axes = list(data['shape']), list(data.get('axes') or [])
    if len(axes) != len(shape) or any(not isinstance(a, dict) or not isinstance(a.get('name'), str) for a in axes):
        axes = []
    named = (lambda i: axes[i]['name']) if axes else (lambda i: i)
    along = 0 if along is None else _axis(axes, shape, along, 'along')
    last = len(shape) - 1
    component = last if (last > 0 and shape[last] == 2 and axes and axes[last].get('labels') == COMPONENTS
                         and along != last) else None
    complex_mode, fixed, shown = None, {}, {}
    if at is not None and not isinstance(at, dict):
        raise ValueError('at maps axes to an index or a label')
    for key, value in (at or {}).items():
        axis = _axis(axes, shape, key, 'at')
        if axis == along:
            raise ValueError('at fixes axes other than the one the view follows')
        if axis == component and value in ('abs', 'arg'):
            complex_mode = value
        elif isinstance(value, str) and not value.isdigit():
            labels = axes[axis].get('labels') if axes else None
            if not isinstance(labels, list) or value not in labels:
                raise ValueError(f'at: axis {named(axis)!r} has no label {value!r}'
                                 + (f'; its labels are {", ".join(map(str, labels))}' if isinstance(labels, list) else '')
                                 + ('; abs and arg read complex values' if axis == component else ''))
            fixed[axis] = labels.index(value)
        elif isinstance(value, bool) or not isinstance(value, (int, str)) or not 0 <= int(value) < shape[axis]:
            raise ValueError(f'at: axis {named(axis)!r} has positions 0 to {shape[axis] - 1}')
        else:
            fixed[axis] = int(value)
        shown[named(axis)] = value
    if component is not None and component not in fixed and complex_mode is None:
        # Complex values are read as their modulus unless a component or the argument is chosen.
        complex_mode = 'abs'
        shown[named(component)] = 'abs'
    size = shape[along]
    coordinates = _coordinates(axes[along], size) if axes else None
    if low is not None or high is not None:
        if coordinates is None:
            raise ValueError('This axis has no coordinates; select positions with start and stop')
        if start is not None or stop is not None:
            raise ValueError('Select positions with start and stop, or coordinates with from and to, not both')
        start, stop = _positions(coordinates, size, -math.inf if low is None else float(low),
                                 math.inf if high is None else float(high))
    start = 0 if start is None else start
    stop = size if stop is None else stop
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, stop)) or not 0 <= start < stop <= size:
        raise ValueError(f'start and stop are positions from 0 to {size} along this axis, start before stop')
    if isinstance(points, bool) or not isinstance(points, int) or not 1 <= points <= MAX_POINTS:
        raise ValueError(f'points is an integer from 1 to {MAX_POINTS}')
    request = {'dtype': data['dtype'], 'byte_order': data.get('byte_order', 'little'), 'shape': shape,
               'along': along, 'fixed': [[a, i] for a, i in sorted(fixed.items())],
               'start': start, 'stop': stop, 'points': points}
    if complex_mode:
        request['complex'] = complex_mode
    summarized = [named(i) for i in range(len(shape)) if i not in fixed and i != along
                  and not (i == component and complex_mode)]
    return request, {'axes': axes, 'coordinates': coordinates, 'at': shown, 'summarized': summarized, 'fixed': fixed}


def native(request):
    """The view from iop-execd, or None when no executor that offers views is installed."""
    from .process_driver import executable
    try:
        command = [executable(), '--array-view']
    except ValueError:
        return None
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    try:
        done = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True, timeout=300,
                              **options)
        reply = json.loads(done.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if not isinstance(reply, dict):
        return None
    if 'error' in reply:
        raise ValueError(reply['error'])
    return reply


def _read(request, low, high, stride, width):
    """The raw elements of the first-axis slabs low to high, as an array of numbers."""
    size = DTYPES[request['dtype']]
    if sum(n for _, n in request['segments']) < math.prod(request['shape']) * size:
        raise ValueError('The files hold fewer bytes than the array description')
    skip, length, payload = low * stride * width * size, (high - low) * stride * width * size, bytearray()
    for path, n in request['segments']:
        if skip >= n:
            skip -= n
            continue
        with open(path, 'rb') as file:
            file.seek(skip)
            payload += file.read(min(n - skip, length - len(payload)))
        skip = 0
        if len(payload) >= length:
            break
    if len(payload) < length:
        raise ValueError('A file holds fewer bytes than recorded')
    flat = array.array(_typecode(request['dtype']))
    flat.frombytes(payload)
    if flat.itemsize > 1 and (request.get('byte_order', 'little') == 'big') != (sys.byteorder == 'big'):
        flat.byteswap()
    return flat


def python(request, values=None):
    """The view iop-execd computes, computed in Python from the file or from a flat list of values."""
    shape = list(request['shape'])
    derive = request.get('complex')
    logical = shape[:-1] if derive else shape
    dims, along, start, stop = len(logical), request['along'], request['start'], request['stop']
    stride = [1] * dims
    for d in range(dims - 2, -1, -1):
        stride[d] = stride[d + 1] * logical[d + 1]
    width = 2 if derive else 1
    fixed = {a: i for a, i in request['fixed']}
    low, high = (start, stop) if along == 0 else (fixed[0], fixed[0] + 1) if 0 in fixed else (0, logical[0])
    if values is None:
        if (high - low) * stride[0] * width > PYTHON_LIMIT:
            raise ValueError(f'Views reading more than {PYTHON_LIMIT} elements are computed by iop-execd; install '
                             'an IOP package that includes it, or set IOP_EXECD to its executable')
        flat = _read(request, low, high, stride[0], width)
    else:
        flat = values[low * stride[0] * width:high * stride[0] * width]
    n = stop - start
    points = min(request['points'], n)
    bounds = [start + -(-b * n // points) for b in range(points + 1)]
    free = [d for d in range(dims) if d != along and d not in fixed]

    def index(offset):
        """The index, in the selected array, of an offset counted from the first slab read."""
        absolute, result = offset + low * stride[0], []
        for d in range(dims):
            result.append(absolute // stride[d])
            absolute %= stride[d]
        return result

    if not free:
        # One element per position: the selection is a strided slice.
        first = sum(fixed[d] * stride[d] for d in fixed) - low * stride[0] + start * stride[along]
        step = stride[along]
        end = first + (n - 1) * step + 1
        if derive:
            re = flat[2 * first:2 * end - 1:2 * step]
            im = flat[2 * first + 1:2 * end:2 * step]
            seq = list(map(math.hypot, re, im)) if derive == 'abs' else list(map(math.atan2, im, re))
        else:
            seq = list(map(float, flat[first:end:step]))
        counts = [bounds[b + 1] - bounds[b] for b in range(points)]
        clean = math.isfinite(sum(seq))
        parts = [seq[bounds[b] - start:bounds[b + 1] - start] for b in range(points)]
        if not clean:
            parts = [list(filter(math.isfinite, part)) for part in parts]
        finite_values = list(itertools.chain.from_iterable(parts)) if not clean else seq
        finite = len(finite_values)
        stats = {'min': None, 'max': None, 'mean': None, 'std': None, 'min_index': None, 'max_index': None}
        if finite:
            smallest, largest = min(finite_values), max(finite_values)
            mean = math.fsum(finite_values) / finite
            deviations = list(map(operator.sub, finite_values, itertools.repeat(mean)))
            stats = {'min': smallest, 'max': largest, 'mean': mean,
                     'std': math.sqrt(math.fsum(map(operator.mul, deviations, deviations)) / finite),
                     'min_index': index(first + seq.index(smallest) * step),
                     'max_index': index(first + seq.index(largest) * step)}
        return {'count': n, 'finite': finite, **stats,
                'buckets': {'start': bounds[:-1], 'count': counts, 'finite': [len(p) for p in parts],
                            'min': [min(p) if p else None for p in parts], 'max': [max(p) if p else None for p in parts],
                            'mean': [math.fsum(p) / len(p) if p else None for p in parts]}}

    # Several elements per position: walk the slabs read in storage order, as iop-execd does.
    count = finite = 0
    mean = m2 = 0.0
    smallest = largest = None
    buckets = [[0, 0, math.inf, -math.inf, 0.0] for _ in range(points)]
    position = [low] + [0] * (dims - 1)
    for offset in range((high - low) * stride[0]):
        p = position[along]
        if start <= p < stop and all(position[d] == i for d, i in fixed.items()):
            if derive:
                re, im = float(flat[2 * offset]), float(flat[2 * offset + 1])
                x = math.hypot(re, im) if derive == 'abs' else math.atan2(im, re)
            else:
                x = float(flat[offset])
            bucket = buckets[(p - start) * points // n]
            bucket[0] += 1
            count += 1
            if math.isfinite(x):
                bucket[1] += 1
                bucket[2], bucket[3] = min(bucket[2], x), max(bucket[3], x)
                bucket[4] += x
                finite += 1
                delta = x - mean
                mean += delta / finite
                m2 += delta * (x - mean)
                if smallest is None or x < smallest[0]:
                    smallest = (x, offset)
                if largest is None or x > largest[0]:
                    largest = (x, offset)
        for d in range(dims - 1, -1, -1):
            position[d] += 1
            if position[d] < logical[d] or d == 0:
                break
            position[d] = 0
    return {'count': count, 'finite': finite, 'min': smallest and smallest[0], 'max': largest and largest[0],
            'mean': mean if finite else None, 'std': math.sqrt(m2 / finite) if finite else None,
            'min_index': smallest and index(smallest[1]), 'max_index': largest and index(largest[1]),
            'buckets': {'start': bounds[:-1], 'count': [b[0] for b in buckets], 'finite': [b[1] for b in buckets],
                        'min': [b[2] if b[1] else None for b in buckets], 'max': [b[3] if b[1] else None for b in buckets],
                        'mean': [b[4] / b[1] if b[1] else None for b in buckets]}}


def _flatten(values):
    if isinstance(values, list):
        for item in values:
            yield from _flatten(item)
    else:
        yield math.nan if values is None else float(values)


def view(holder, source=None, along=None, at=None, start=None, stop=None, low=None, high=None, points=POINTS,
         field='', use_native=True):
    """What one array says: a summary, and exact values when each position holds one element within
    ``points`` positions, otherwise the minimum, maximum and mean of ``points`` buckets of positions.

    ``source`` is the file that holds the array, or ``[[path, size], ...]`` for the chunks of a stream;
    without it the inline values are read.
    """
    data = holder['data']
    request, context = plan(data, along, at, start, stop, low, high, points)
    if source is not None:
        request['segments'] = source if isinstance(source, list) else [[str(source), Path(source).stat().st_size]]
        computed = native(request) if use_native else None
        if computed is None:
            computed = python(request)
    else:
        inline = holder.get('values')
        if inline is None:
            raise ValueError('This array has no saved file and no inline values')
        flat = list(_flatten(inline))
        if len(flat) != math.prod(data['shape']):
            raise ValueError('The inline values do not match the array shape')
        computed = python(request, flat)
    axes, along, coordinates = context['axes'], request['along'], context['coordinates']
    place = (lambda p: coordinates[0] + p * coordinates[1]) if coordinates else None
    following = {'axis': axes[along]['name'] if axes else along, 'start': request['start'], 'stop': request['stop']}
    if coordinates:
        ends = place(request['start']), place(request['stop'] - 1)
        following.update({'low': min(ends), 'high': max(ends), 'step': coordinates[1]})
    if axes and isinstance(axes[along].get('unit'), str):
        following['unit'] = axes[along]['unit']
    unit = data.get('unit')
    for axis, index in context['fixed'].items():
        # An axis whose positions are different quantities, such as the channels of a stream, gives each its unit.
        units = axes[axis].get('units') if axes else None
        if isinstance(units, list) and index < len(units) and isinstance(units[index], str):
            unit = units[index]
    if request.get('complex') == 'arg':
        unit = 'rad'
    summary = {'count': computed['count']}
    if computed['count'] != computed['finite']:
        summary['nonfinite'] = computed['count'] - computed['finite']
    for key in ('min', 'max', 'mean', 'std'):
        summary[key] = computed[key]
    for key in ('min', 'max'):
        position = computed[key + '_index']
        if position is not None:
            summary[key + '_index'] = position
            if coordinates:
                summary[key + '_at'] = place(position[along])
    result = {'field': field, 'dtype': data['dtype'], 'shape': data['shape'], **({'unit': unit} if unit else {}),
              'along': following, **({'at': context['at']} if context['at'] else {}),
              **({'summarized': context['summarized']} if context['summarized'] else {}), 'summary': summary}
    buckets = computed['buckets']
    if all(c == 1 for c in buckets['count']):
        result['values'] = buckets['mean']
    else:
        result['envelope'] = {'position': buckets['start'],
                              **({'coordinate': [_short(place(p)) for p in buckets['start']]} if coordinates else {}),
                              'min': [_short(v) for v in buckets['min']], 'max': [_short(v) for v in buckets['max']],
                              'mean': [_short(v) for v in buckets['mean']]}
    return result


def options(query):
    """View options from URL query values: at is a JSON object."""
    def integer(key):
        try:
            return int(query[key]) if key in query else None
        except ValueError:
            raise ValueError(key + ' is an integer') from None

    def number(key):
        try:
            return float(query[key]) if key in query else None
        except ValueError:
            raise ValueError(key + ' is a number') from None
    at = None
    if 'at' in query:
        try:
            at = json.loads(query['at'])
        except ValueError:
            raise ValueError('at is a JSON object that maps axes to an index or a label') from None
    unknown = set(query) - {'field', 'along', 'at', 'start', 'stop', 'low', 'high', 'points'}
    if unknown:
        raise ValueError('Unknown view options: ' + ', '.join(sorted(unknown)))
    return {'field': query.get('field'), 'along': query.get('along'), 'at': at, 'start': integer('start'),
            'stop': integer('stop'), 'low': number('low'), 'high': number('high'),
            'points': integer('points') if 'points' in query else POINTS}


def read(result, folder, field=None, **options):
    """The view of an array in an operation's result, read from the file the operation saved in folder."""
    from .engine import relative_path
    field, holder = select(result or {}, field)
    name, path = holder['data'].get('file'), None
    if name:
        path = folder / relative_path(name)
        if not path.resolve().is_relative_to(folder.resolve()) or not path.is_file():
            raise ValueError('The array file of this operation is missing')
    return view(holder, path, field=field, **options)


def operation_array(engine, operation_id, field=None, **options):
    """The view of an array a completed operation returned."""
    op = engine.store.get('operations', operation_id)
    if op['state'] != 'completed':
        raise ValueError('Arrays are read from completed operations')
    folder = engine.home / 'runs' / op['run_id'] / 'output' / 'operations' / operation_id
    return {'operation_id': operation_id, **read(op.get('result'), folder, field, **options)}


# A step binding or a saved operation's named result can take its value through a view of an array:
# {"step": ..., "view": {...}, "path": [...]} reads path inside the view. These views are computed
# once, when the operation that returned the array completes, and kept in its record as "derived".
OPTIONS = ('field', 'along', 'at', 'start', 'stop', 'low', 'high', 'points')


def check(value, what):
    """Validate the view of a binding or named result; axis names are resolved against the array later."""
    if not isinstance(value, dict) or set(value) - set(OPTIONS):
        raise ValueError(f'{what}: view takes ' + ', '.join(OPTIONS))
    kinds = {'field': (str,), 'along': (str, int), 'at': (dict,), 'start': (int,), 'stop': (int,),
             'low': (int, float), 'high': (int, float), 'points': (int,)}
    for key, item in value.items():
        if isinstance(item, bool) or not isinstance(item, kinds[key]):
            raise ValueError(f'{what}: view {key} has the wrong type')
    return value


def key(source):
    """The name under which a view and a path into it are kept in an operation's derived values."""
    return json.dumps({'view': source['view'], 'path': list(source.get('path', []))}, sort_keys=True,
                      separators=(',', ':'))


def derive(result, folder, sources):
    """Compute the views requested on an operation's arrays: {key: {"value": ...} or {"error": ...}}.

    The measurement stands whatever happens here; a view that cannot be read leaves its reason for the
    step or named result that needs it.
    """
    derived = {}
    for source in sources:
        try:
            value = read(result, folder, **source['view'])
            for part in source.get('path', []):
                value = value[part]
            derived[key(source)] = {'value': value}
        except (KeyError, IndexError, TypeError) as error:
            derived[key(source)] = {'error': f'the view has no {error}'}
        except Exception as error:
            derived[key(source)] = {'error': str(error) if isinstance(error, ValueError) else f'{type(error).__name__}: {error}'}
    return derived

"""Views of saved arrays: statistics, an envelope along one axis or a map along two, exact values in a
window, and the line at a peak or a dip.

A trace, spectrum, image or buffer is read without moving all of its values. One axis is followed, or
two for a map; every other axis is either fixed with ``at`` or summarized. A last axis of ``re``/``im``
pairs is read as ``abs`` unless ``at`` chooses ``re``, ``im``, ``arg`` or ``power``, the squared modulus.
Along one axis with the others fixed, ``peak`` finds the line at the highest value, or the lowest for a
dip: its center between samples, its value, the opposite extreme as its base, and its width where it is
halfway to the base. iop-execd computes the view in its own process; without it, Python computes the
same view when the elements read stay within ``PYTHON_LIMIT``.
"""
import array
import itertools
import json
import math
import operator
import struct
import subprocess
import sys
from pathlib import Path
from .arrays import DTYPES, _typecode

POINTS = 64
MAX_POINTS = 2000
LINES = 1000
# What a fit draws through a trace: a straight line, or a line of Lorentzian or Gaussian shape. Each step of a
# Lorentzian or Gaussian fit visits every value, so it takes at most SHAPED of them over all rows.
FITS = ('line', 'lorentzian', 'gaussian')
SHAPED = 1_000_000
LN2 = math.log(2)
MAX_CELLS = 250_000
PYTHON_LIMIT = 4_000_000
COMPONENTS = ['re', 'im']


def _arrays(result, keys=()):
    """The keys leading to each array in a result."""
    if isinstance(result, dict):
        data = result.get('data')
        if isinstance(data, dict) and isinstance(data.get('dtype'), str) and isinstance(data.get('shape'), list):
            yield keys
        for key, value in result.items():
            if key not in ('data', 'values') and isinstance(value, dict):
                yield from _arrays(value, keys + (key,))


def arrays_in(result):
    """Paths of the arrays in a result: '' for the result itself, else e.g. 'items.trace'."""
    return ['.'.join(keys) for keys in _arrays(result)]


def select(result, field=None):
    """The array a field names; a result that holds one array needs no field. Keys may hold dots, as the
    steps of a saved operation do in an experiment's result."""
    found = {}
    for keys in _arrays(result):
        found.setdefault('.'.join(keys), keys)
    shown = ', '.join(repr(p) for p in found)
    if field is None:
        if not found:
            raise ValueError('This result holds no array')
        if len(found) > 1:
            raise ValueError('This result holds several arrays; name one with field: ' + shown)
        field = next(iter(found))
    if field not in found:
        raise ValueError(f'No array at {field!r}; this result holds: {shown or "none"}')
    holder = result
    for key in found[field]:
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
    """The coordinates of an axis: start and step when evenly spaced, a list with one for each position, or None."""
    listed = axis.get('coordinates')
    if listed is not None:
        if not isinstance(listed, list) or len(listed) != size or not all(map(_number, listed)):
            raise ValueError(f"Axis {axis.get('name')!r} lists {len(listed) if isinstance(listed, list) else 'no'} "
                             f"coordinates for its {size} positions")
        return [float(c) for c in listed]
    if not _number(axis.get('start')):
        return None
    if _number(axis.get('step')) and axis['step']:
        return float(axis['start']), float(axis['step'])
    if _number(axis.get('stop')) and size > 1 and axis['stop'] != axis['start']:
        return float(axis['start']), (float(axis['stop']) - float(axis['start'])) / (size - 1)
    return None


def _place(coordinates):
    """The coordinate of a position."""
    if isinstance(coordinates, list):
        return coordinates.__getitem__
    start, step = coordinates
    return lambda p: start + p * step


def _positions(coordinates, size, low, high):
    """The positions whose coordinate lies between low and high."""
    if isinstance(coordinates, list):
        pairs = list(zip(coordinates, coordinates[1:]))
        if not (all(a < b for a, b in pairs) or all(a > b for a, b in pairs)):
            raise ValueError('low and high select along coordinates that increase or decrease; '
                             'select positions with start and stop')
        inside = [p for p, c in enumerate(coordinates) if min(low, high) <= c <= max(low, high)]
        if not inside:
            raise ValueError(f'No position of this axis lies between {low} and {high}')
        return inside[0], inside[-1] + 1
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


def plan(data, along=None, at=None, start=None, stop=None, low=None, high=None, points=POINTS, peak=None,
         prominence=None, level=None, fit=None):
    """Resolve axis names, labels and coordinates into the request iop-execd and Python compute. ``along``
    names one axis, or a list of two for a map; start, stop, low and high then give one value per axis
    (None for none) and points a number for each axis or for both. ``peak`` is max or min, and with
    ``prominence`` every line that stands out by that much is read; ``level`` reads where the trace crosses
    that value, and ``fit`` "line" the straight line through it."""
    shape, axes = list(data['shape']), list(data.get('axes') or [])
    if len(axes) != len(shape) or any(not isinstance(a, dict) or not isinstance(a.get('name'), str) for a in axes):
        axes = []
    named = (lambda i: axes[i]['name']) if axes else (lambda i: i)
    several = isinstance(along, list)
    if several and not 1 <= len(along) <= 2:
        raise ValueError('along names one axis, or a list of two for a map')
    followed = [_axis(axes, shape, key, 'along') for key in (along if several else [0 if along is None else along])]
    if len(set(followed)) != len(followed):
        raise ValueError('along names different axes')

    def each(value, what):
        if not several:
            return [value]
        if value is None:
            return [None] * len(followed)
        if not isinstance(value, list) or len(value) != len(followed):
            raise ValueError(f'{what} gives one value for each axis along, or none')
        return value
    windows = list(zip(each(start, 'start'), each(stop, 'stop'), each(low, 'low'), each(high, 'high')))
    counts = points if isinstance(points, list) else [points] * len(followed)
    last = len(shape) - 1
    component = last if (last > 0 and shape[last] == 2 and axes and axes[last].get('labels') == COMPONENTS
                         and last not in followed) else None
    complex_mode, fixed, shown = None, {}, {}
    if at is not None and not isinstance(at, dict):
        raise ValueError('at maps axes to an index or a label')
    for key, value in (at or {}).items():
        axis = _axis(axes, shape, key, 'at')
        if axis in followed:
            raise ValueError('at fixes axes other than those the view follows')
        if axis == component and value in ('abs', 'arg', 'power'):
            complex_mode = value
        elif isinstance(value, str) and not value.isdigit():
            labels = axes[axis].get('labels') if axes else None
            if not isinstance(labels, list) or value not in labels:
                raise ValueError(f'at: axis {named(axis)!r} has no label {value!r}'
                                 + (f'; its labels are {", ".join(map(str, labels))}' if isinstance(labels, list) else '')
                                 + ('; abs, arg and power read complex values' if axis == component else ''))
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
    if prominence is not None and (peak is None or isinstance(prominence, bool) or not isinstance(prominence, (int, float))
                                   or not math.isfinite(prominence) or prominence <= 0):
        raise ValueError('prominence is a positive number, read with a peak or a dip')
    if level is not None and (isinstance(level, bool) or not isinstance(level, (int, float)) or not math.isfinite(level)):
        raise ValueError('level is a finite number')
    if peak is not None and peak not in ('max', 'min'):
        raise ValueError('peak is max, or min for a dip')
    if fit not in (None, *FITS):
        raise ValueError('fit is line, lorentzian or gaussian')
    traced = peak is not None or level is not None or fit is not None
    if traced:
        # An axis of one position is read at it.
        fixed.update((axis, 0) for axis, size in enumerate(shape)
                     if size == 1 and axis not in followed and axis not in fixed)
    starts, stops, coordinates = [], [], []
    for axis, (first, end, lowest, highest) in zip(followed, windows):
        size = shape[axis]
        found = _coordinates(axes[axis], size) if axes else None
        if lowest is not None or highest is not None:
            if found is None:
                raise ValueError('This axis has no coordinates; select positions with start and stop')
            if first is not None or end is not None:
                raise ValueError('Select positions with start and stop, or coordinates with low and high, not both')
            first, end = _positions(found, size, -math.inf if lowest is None else float(lowest),
                                    math.inf if highest is None else float(highest))
        first = 0 if first is None else first
        end = size if end is None else end
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (first, end)) or not 0 <= first < end <= size:
            raise ValueError(f'start and stop are positions from 0 to {size} along this axis, start before stop')
        starts.append(first), stops.append(end), coordinates.append(found)
    if len(counts) != len(followed) or any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_POINTS
                                           for n in counts):
        raise ValueError(f'points is an integer from 1 to {MAX_POINTS}, or one for each axis along')
    if math.prod(min(n, b - a) for n, a, b in zip(counts, starts, stops)) > MAX_CELLS:
        raise ValueError(f'A map has at most {MAX_CELLS} buckets; ask for fewer points')
    request = {'dtype': data['dtype'], 'byte_order': data.get('byte_order', 'little'), 'shape': shape,
               'along': followed, 'fixed': [[a, i] for a, i in sorted(fixed.items())],
               'start': starts, 'stop': stops, 'points': counts}
    if complex_mode:
        request['complex'] = complex_mode
    free = [i for i in range(len(shape)) if i not in fixed and i not in followed and not (i == component and complex_mode)]
    summarized = [named(i) for i in free]
    if traced:
        what = 'peak' if peak is not None else 'level' if level is not None else 'fit'
        if len(followed) > 1 or len(free) > 1:
            raise ValueError(f'A {what} is read along one axis, at each position of at most one other; fix the others '
                             'with at: ' + ', '.join(map(str, summarized)))
        if free:
            # At each position of the one other axis not fixed, the trace along the axis followed is read.
            if prominence is not None:
                raise ValueError('prominence reads the lines of one trace; fix the other axes with at: '
                                 + ', '.join(map(str, summarized)))
            if shape[free[0]] > LINES:
                raise ValueError(f'A {what} is read at most at {LINES} positions of another axis; fix '
                                 f'{summarized[0]} with at')
            request['rows'] = free[0]
        if fit in ('lorentzian', 'gaussian') and (stops[0] - starts[0]) * (shape[free[0]] if free else 1) > SHAPED:
            raise ValueError(f'A Lorentzian or Gaussian is fitted to at most {SHAPED} values; select fewer around the '
                             'line with low and high')
        if peak is not None:
            request['peak'] = peak
        if prominence is not None:
            request['prominence'] = prominence
        if level is not None:
            request['level'] = level
        if fit is not None:
            request['fit'] = fit
        listed = coordinates[0]
        if isinstance(listed, list):
            selected = listed[starts[0]:stops[0]]
            if peak is not None and not (all(a < b for a, b in zip(selected, selected[1:]))
                                         or all(a > b for a, b in zip(selected, selected[1:]))):
                raise ValueError('A peak needs coordinates that strictly increase or decrease')
            request['coordinates'] = selected
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
    dims = len(logical)
    followed = list(zip(request['along'], request['start'], request['stop'], request['points']))
    stride = [1] * dims
    for d in range(dims - 2, -1, -1):
        stride[d] = stride[d + 1] * logical[d + 1]
    width = 2 if derive else 1
    fixed = {a: i for a, i in request['fixed']}
    first = next(((start, stop) for axis, start, stop, _ in followed if axis == 0), None)
    low, high = first or ((fixed[0], fixed[0] + 1) if 0 in fixed else (0, logical[0]))
    if values is None:
        if (high - low) * stride[0] * width > PYTHON_LIMIT:
            raise ValueError(f'Views reading more than {PYTHON_LIMIT} elements are computed by iop-execd; install '
                             'an IOP package that includes it, or set IOP_EXECD to its executable')
        flat = _read(request, low, high, stride[0], width)
    else:
        flat = values[low * stride[0] * width:high * stride[0] * width]
    counts = [min(points, stop - start) for _, start, stop, points in followed]
    bounds = [[start + -(-b * (stop - start) // n) for b in range(n + 1)]
              for (_, start, stop, _), n in zip(followed, counts)]
    free = [d for d in range(dims) if d not in fixed and all(d != axis for axis, *_ in followed)]

    def index(offset):
        """The index, in the selected array, of an offset counted from the first slab read."""
        absolute, result = offset + low * stride[0], []
        for d in range(dims):
            result.append(absolute // stride[d])
            absolute %= stride[d]
        return result

    if len(followed) == 1 and not free:
        # One element per position of one axis: the selection is a strided slice.
        along, start, stop, _ = followed[0]
        n, points, bounds = stop - start, counts[0], bounds[0]
        first = sum(fixed[d] * stride[d] for d in fixed) - low * stride[0] + start * stride[along]
        step = stride[along]
        end = first + (n - 1) * step + 1
        if derive:
            re = flat[2 * first:2 * end - 1:2 * step]
            im = flat[2 * first + 1:2 * end:2 * step]
            if derive == 'power':
                seq = [float(a) * float(a) + float(b) * float(b) for a, b in zip(re, im)]
            else:
                seq = list(map(math.hypot, re, im)) if derive == 'abs' else list(map(math.atan2, im, re))
        else:
            seq = list(map(float, flat[first:end:step]))
        counted = [bounds[b + 1] - bounds[b] for b in range(points)]
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
                'buckets': {'start': [bounds[:-1]], 'count': counted, 'finite': [len(p) for p in parts],
                            'min': [min(p) if p else None for p in parts], 'max': [max(p) if p else None for p in parts],
                            'mean': [math.fsum(p) / len(p) if p else None for p in parts]},
                **_traced(seq, start, request)}

    # Several elements per position, or two axes followed: walk the slabs read in storage order, as iop-execd does.
    # A peak, a level or a fit along one axis is read in the trace at each position of the other axis not fixed.
    rows = request.get('rows')
    traces = [[] for _ in range(logical[rows])] if rows is not None else None
    count = finite = 0
    mean = m2 = 0.0
    smallest = largest = None
    buckets = [[0, 0, math.inf, -math.inf, 0.0] for _ in range(math.prod(counts))]
    position = [low] + [0] * (dims - 1)
    for offset in range((high - low) * stride[0]):
        if (all(start <= position[axis] < stop for axis, start, stop, _ in followed)
                and all(position[d] == i for d, i in fixed.items())):
            if derive:
                re, im = float(flat[2 * offset]), float(flat[2 * offset + 1])
                x = (math.hypot(re, im) if derive == 'abs' else math.atan2(im, re) if derive == 'arg'
                     else re * re + im * im)
            else:
                x = float(flat[offset])
            if traces is not None:
                traces[position[rows]].append(x)
            cell = 0
            for (axis, start, stop, _), n in zip(followed, counts):
                cell = cell * n + (position[axis] - start) * n // (stop - start)
            bucket = buckets[cell]
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
    found = {'count': count, 'finite': finite, 'min': smallest and smallest[0], 'max': largest and largest[0],
             'mean': mean if finite else None, 'std': math.sqrt(m2 / finite) if finite else None,
             'min_index': smallest and index(smallest[1]), 'max_index': largest and index(largest[1]),
             'buckets': {'start': [b[:-1] for b in bounds], 'count': [b[0] for b in buckets],
                         'finite': [b[1] for b in buckets],
                         'min': [b[2] if b[1] else None for b in buckets], 'max': [b[3] if b[1] else None for b in buckets],
                         'mean': [b[4] / b[1] if b[1] else None for b in buckets]}}
    if traces is not None:
        each = [_traced(trace, followed[0][1], request) for trace in traces]
        found['rows'] = {key: [row[key] for row in each] for key in each[0]}
    return found


def _traced(seq, first, request):
    """What a peak, a level and a fit read in one trace that starts at position ``first``, as iop-execd reads them."""
    found = {}
    if request.get('peak'):
        if 'prominence' in request:
            found['peaks'] = _lines(seq, first, request['peak'], request.get('coordinates'), request['prominence'])
        else:
            found['peak'] = _line(seq, first, request['peak'], request.get('coordinates'))
    if 'level' in request:
        found['crossings'] = _crossings(seq, first, request['level'], request.get('coordinates'))
    if 'fit' in request:
        found['fit'] = (_fit(seq, first, request.get('coordinates')) if request['fit'] == 'line'
                        else _shaped(seq, first, request.get('coordinates'), request['fit']))
    return found


def _shaped(seq, first, coordinates, shape):
    """The line of Lorentzian or Gaussian shape through the finite values of a trace that starts at position
    ``first``, as iop-execd fits it: by least squares (Levenberg-Marquardt) in its positions or listed coordinates;
    its center, its full width at half its height, its height above its base (below for a dip), the base, the root
    mean square of what it leaves and the number of values. It starts from the value farthest from the median,
    with the median as base and the half width where the trace first comes halfway back on either side. None for
    fewer than five places or a flat trace. Its standard errors come with it when at least two values lie within
    its width at half height."""
    place = (lambda index: float(first + index)) if coordinates is None else coordinates.__getitem__
    points = [(place(i), y) for i, y in enumerate(seq) if math.isfinite(y)]
    n = len(points)
    if n < 5:
        return None
    # Places measured from their middle in half spans keep coordinates such as frequencies well conditioned.
    low, high = math.inf, -math.inf
    for x, _ in points:
        low, high = min(low, x), max(high, x)
    middle, half = (low + high) / 2.0, (high - low) / 2.0
    y = [value for _, value in points]
    base = sorted(y)[n // 2]
    top = bottom = 0
    for k in range(1, n):
        if y[k] > y[top]:
            top = k
        if y[k] < y[bottom]:
            bottom = k
    k = top if y[top] - base >= base - y[bottom] else bottom
    height = y[k] - base
    if half == 0.0 or height == 0.0:
        return None
    t = [(x - middle) / half for x, _ in points]

    def back(order):
        return next((abs(t[j] - t[k]) for j in order if (y[j] - base) / height <= 0.5), None)
    left, right = back(range(k - 1, -1, -1)), back(range(k + 1, n))
    width = ((left + right) / 2.0 if left is not None and right is not None
             else left if left is not None else right if right is not None else 0.05)
    p = [base, height, t[k], width if width > 0.0 else 0.05]

    def model(p, at):
        # The shape at u = (x - center) / half width, and the derivatives of the line by base, height, center and
        # half width there.
        u = (at - p[2]) / p[3]
        if shape == 'lorentzian':
            s = 1.0 / (1.0 + u * u)
            g = 2.0 * s * s
        else:
            s = math.exp(-LN2 * u * u)
            g = 2.0 * LN2 * s
        q = p[1] * g / p[3]
        return p[0] + p[1] * s, (1.0, s, q * u, q * u * u)

    def squares(p):
        total = 0.0
        for at, value in zip(t, y):
            off = value - model(p, at)[0]
            total += off * off
        return total
    cost, damping = squares(p), 1e-3
    for _ in range(200):
        a, g = [[0.0] * 4 for _ in range(4)], [0.0] * 4
        for at, value in zip(t, y):
            fitted, d = model(p, at)
            for j in range(4):
                g[j] += d[j] * (value - fitted)
                for i in range(4):
                    a[j][i] += d[j] * d[i]
        settled = False
        while True:
            m = [row[:] for row in a]
            for j in range(4):
                m[j][j] += damping * a[j][j]
            step = _solve(m, g)
            if step is not None:
                trial = [p[j] + step[j] for j in range(4)]
                nxt = squares(trial)
                if nxt < cost:
                    break
            damping *= 10.0
            if damping > 1e16:
                settled = True
                break
        if settled:
            break
        improved = cost - nxt
        p, cost, damping = trial, nxt, damping / 10.0
        if improved <= 1e-12 * cost:
            break
    found = {'center': _finite(middle + p[2] * half), 'width': _finite(2.0 * abs(p[3]) * half), 'height': _finite(p[1]),
             'base': _finite(p[0]), 'residual': _finite(math.sqrt(cost / n)), 'count': n}
    if sum(1 for at in t if abs(at - p[2]) <= abs(p[3])) < 2:
        # Fewer than two values within its width at half height do not resolve the line, such as one drawn
        # through a single noisy value: its errors would say nothing.
        found['errors'] = None
    else:
        # Standard errors from what the line leaves and the curvature of the fit at its end, the values taken as
        # independent: the diagonal of the inverse of the normal equations, scaled by the residual variance.
        a = [[0.0] * 4 for _ in range(4)]
        for at in t:
            d = model(p, at)[1]
            for j in range(4):
                for i in range(4):
                    a[j][i] += d[j] * d[i]
        variance = cost / (n - 4)
        columns = [_solve(a, [1.0 if i == j else 0.0 for i in range(4)]) for j in range(4)]
        if all(column is not None for column in columns):
            v = [variance * columns[j][j] for j in range(4)]
            found['errors'] = {'center': _finite(math.sqrt(v[2]) * half) if v[2] >= 0 else None,
                               'width': _finite(2.0 * math.sqrt(v[3]) * half) if v[3] >= 0 else None,
                               'height': _finite(math.sqrt(v[1])) if v[1] >= 0 else None,
                               'base': _finite(math.sqrt(v[0])) if v[0] >= 0 else None}
        else:
            found['errors'] = None
    return {**found, **({'coordinates': True} if coordinates is not None else {})}


def _solve(m, b):
    """The solution of four linear equations, by elimination with partial pivoting, as iop-execd solves them;
    None when they are singular."""
    m, b = [row[:] for row in m], list(b)
    for col in range(4):
        pivot = col
        for row in range(col + 1, 4):
            if abs(m[row][col]) > abs(m[pivot][col]):
                pivot = row
        if m[pivot][col] == 0.0:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        b[col], b[pivot] = b[pivot], b[col]
        above, top = m[col][:], b[col]
        for row in range(col + 1, 4):
            f = m[row][col] / above[col]
            for k in range(col, 4):
                m[row][k] -= f * above[k]
            b[row] -= f * top
    x = [0.0] * 4
    for row in range(3, -1, -1):
        s = b[row]
        for k in range(row + 1, 4):
            s -= m[row][k] * x[k]
        x[row] = s / m[row][row]
    return x


def _finite(value):
    return value if math.isfinite(value) else None


def _line(seq, first, peak, coordinates=None):
    """The line at the extreme of a trace that starts at position ``first``, as iop-execd finds it: the first
    highest value (lowest for a dip) and its position, refined between its neighbours by the parabola through
    the three; the opposite extreme as the base; and the positions, interpolated, where the trace on either
    side first falls halfway from the line's value to the base. Values that are not finite are passed over."""
    sign = 1.0 if peak == 'max' else -1.0
    top, base = None, math.nan
    for i, y in enumerate(seq):
        if math.isfinite(y):
            if top is None or sign * y > sign * seq[top]:
                top = i
            if math.isnan(base) or sign * y < sign * base:
                base = y
    return None if top is None else _describe(seq, first, top, base, sign, coordinates)


def _lines(seq, first, peak, coordinates, least):
    """The lines of a trace that stand out by at least ``least``, as iop-execd finds them: each highest value
    (lowest for a dip) between lower ones whose prominence, its height above the higher of the lowest values
    on either side before a higher one or the end of the trace, is that much. That higher lowest value is the
    line's base, and the line is described as ``_line`` describes the one at the extreme. In order of
    position."""
    sign = 1.0 if peak == 'max' else -1.0
    finite = [i for i, y in enumerate(seq) if math.isfinite(y)]
    z = [sign * seq[i] for i in finite]

    def lowest(order):
        # The lowest value from each one back to the nearest higher one, kept for each value on a stack of
        # higher ones: a value that rises above those before it takes in their lowest values.
        found, higher = [0.0] * len(z), []
        for k in order:
            low = z[k]
            while higher and higher[-1][0] <= z[k]:
                low = min(low, higher.pop()[1])
            found[k] = low
            higher.append((z[k], low))
        return found
    left, right = lowest(range(len(z))), lowest(range(len(z) - 1, -1, -1))
    found = []
    for k in range(1, len(z) - 1):
        if z[k - 1] < z[k] and z[k + 1] <= z[k]:
            base = max(left[k], right[k])
            if z[k] - base >= least:
                if len(found) == LINES:
                    raise ValueError(f'More than {LINES} lines stand out by this prominence; ask for a larger one')
                found.append({**_describe(seq, first, finite[k], sign * base, sign, coordinates),
                              'prominence': _finite(z[k] - base)})
    return found


def _crossings(seq, first, level, coordinates=None):
    """Where a trace that starts at position ``first`` crosses ``level``, as iop-execd finds it, in order:
    between neighbouring finite values, one below the level and the other at or above it, on the straight line
    through them, rising when the later is the higher. Values that are not finite are passed over."""
    place = (lambda index: first + index) if coordinates is None else coordinates.__getitem__
    found, last = [], None
    for j, y in enumerate(seq):
        if not math.isfinite(y):
            continue
        if last is not None:
            x = seq[last]
            if (x >= level) != (y >= level):
                if len(found) == LINES:
                    raise ValueError(f'The trace crosses this level more than {LINES} times; select fewer positions')
                found.append({'index': first + last,
                              'position': _finite(place(last) + (level - x) / (y - x) * (place(j) - place(last))),
                              'rising': y > x, **({'coordinates': True} if coordinates is not None else {})})
        last = j
    return found


def _fit(seq, first, coordinates=None):
    """The straight line through the finite values of a trace that starts at position ``first``, as iop-execd fits
    it: by least squares in its positions or listed coordinates, taken from their means; its slope, its value at
    zero, where it reaches zero, the root mean square of what it leaves and the number of values. None for fewer
    than two places."""
    place = (lambda index: float(first + index)) if coordinates is None else coordinates.__getitem__
    points = [(place(i), y) for i, y in enumerate(seq) if math.isfinite(y)]
    n = len(points)
    if n < 2:
        return None
    sx = sy = 0.0
    for x, y in points:
        sx += x
        sy += y
    mx, my = sx / n, sy / n
    sxx = sxy = 0.0
    for x, y in points:
        dx = x - mx
        sxx += dx * dx
        sxy += dx * (y - my)
    if sxx == 0.0:
        return None
    slope = sxy / sxx
    squares = 0.0
    for x, y in points:
        left = (y - my) - slope * (x - mx)
        squares += left * left
    found = {'slope': _finite(slope), 'intercept': _finite(my - slope * mx),
             'zero': _finite(mx - my / slope) if slope != 0.0 else None, 'residual': _finite(math.sqrt(squares / n)),
             'count': n}
    if n > 2:
        # Standard errors from what the line leaves, the values taken as independent: the slope's, and that of
        # where it reaches zero from the slope and the mean, which are uncorrelated.
        variance = squares / (n - 2.0)
        found['errors'] = {'slope': _finite(math.sqrt(variance / sxx)),
                           'zero': _finite(math.sqrt(variance / (slope * slope) * (1.0 / n + my * my / (slope * slope * sxx))))
                           if slope != 0.0 else None}
    return {**found, **({'coordinates': True} if coordinates is not None else {})}


def _describe(seq, first, i, base, sign, coordinates):
    """The line at position ``i`` of a trace over a base: its position refined between its neighbours by the
    parabola through the three, its value there, and the positions, interpolated, where the trace on either
    side first comes halfway from that value to the base."""
    y1 = seq[i]
    place = (lambda index: first + index) if coordinates is None else coordinates.__getitem__
    center, value = float(place(i)), y1
    if 0 < i < len(seq) - 1 and math.isfinite(seq[i - 1]) and math.isfinite(seq[i + 1]):
        y0, y2 = seq[i - 1], seq[i + 1]
        # Fit in the axis's coordinates, using offsets from the middle sample to avoid subtracting
        # squared absolute frequencies. Equal index spacing is insufficient for a logarithmic sweep.
        left, right = place(i - 1) - place(i), place(i + 1) - place(i)
        slope_left, slope_right = (y0 - y1) / left, (y2 - y1) / right
        curvature = (slope_right - slope_left) / (right - left)
        slope = slope_left - curvature * left
        if sign * curvature < 0.0:
            offset = -slope / (2.0 * curvature)
            if math.isfinite(offset) and min(left, right) <= offset <= max(left, right):
                center += offset
                value = y1 + 0.5 * slope * offset
    half = (value + base) / 2.0

    def crossing(positions):
        # From the line outward, the first value at or past half height, between it and the last one inside.
        if sign * (value - base) <= 0.0:
            return None
        inside = i
        for j in positions:
            y = seq[j]
            if not math.isfinite(y):
                continue
            if sign * (y - half) > 0.0:
                inside = j
                continue
            return _finite(place(j) + (half - y) / (seq[inside] - y) * (place(inside) - place(j)))
        return None
    return {'index': first + i, 'position': _finite(center), 'value': _finite(value),
            'base': _finite(base), 'left': crossing(range(i - 1, -1, -1)), 'right': crossing(range(i + 1, len(seq))),
            **({'coordinates': True} if coordinates is not None else {})}


def _between(coordinates, position):
    """The coordinate at a position that may lie between two, on the straight line through theirs."""
    if not isinstance(coordinates, list):
        start, step = coordinates
        return start + position * step
    if len(coordinates) == 1:
        return coordinates[0]
    lower = min(max(math.floor(position), 0), len(coordinates) - 2)
    return coordinates[lower] + (position - lower) * (coordinates[lower + 1] - coordinates[lower])


def _peak(found, coordinates):
    """A line found in positions, given in the coordinates of its axis when it has them: its center, the width
    between where it is at half height on either side, those two, its value and its base."""
    place = (lambda p: p) if coordinates is None or found.get('coordinates') else (lambda p: _between(coordinates, p))
    center = place(found['position'])
    sides = [place(found[side]) for side in ('left', 'right') if found[side] is not None]
    line = {'center': center}
    if len(sides) == 2:
        line['width'] = abs(sides[1] - sides[0])
    for side in sorted(sides):
        line['low' if side < center else 'high'] = side
    line.update(value=found['value'], base=found['base'])
    if 'prominence' in found:
        line['prominence'] = found['prominence']
    line['index'] = found['index']
    return line


def _crossing(found, coordinates):
    """A crossing found in positions, at the coordinate of its axis when it has them."""
    at = found['position'] if coordinates is None or found.get('coordinates') else _between(coordinates, found['position'])
    return {'at': at, 'rising': found['rising'], 'index': found['index']}


def _fitted(found, coordinates):
    """A fit made in positions, given in the coordinates of its axis when it has them: a straight line's slope per
    unit of the axis, its value at coordinate zero, the coordinate where it reaches zero and what it leaves; or the
    center, width, height and base of a line of Lorentzian or Gaussian shape and what it leaves; with the standard
    errors of the slope and where it reaches zero, or of the center, width, height and base."""
    if found is None:
        return None
    shaped = 'center' in found
    line = {key: found[key] for key in (('center', 'width', 'height', 'base') if shaped else ('slope', 'intercept', 'zero'))
            + ('residual', 'count', 'errors') if key in found}
    errors = found.get('errors')
    if isinstance(coordinates, tuple) and not found.get('coordinates') and found['center' if shaped else 'slope'] is not None:
        start, step = coordinates
        across = (lambda value: None if value is None else value * abs(step))
        if shaped:
            line.update(center=start + found['center'] * step, width=across(found['width']))
            if errors:
                line['errors'] = {**errors, 'center': across(errors['center']), 'width': across(errors['width'])}
        else:
            line.update(slope=found['slope'] / step, intercept=found['intercept'] - found['slope'] * start / step,
                        zero=None if found['zero'] is None else start + found['zero'] * step)
            if errors:
                line['errors'] = {'slope': None if errors['slope'] is None else errors['slope'] / abs(step),
                                  'zero': across(errors['zero'])}
    return line


def _flatten(values):
    if isinstance(values, list):
        for item in values:
            yield from _flatten(item)
    else:
        yield math.nan if values is None else float(values)


def view(holder, source=None, along=None, at=None, start=None, stop=None, low=None, high=None, points=POINTS,
         peak=None, prominence=None, level=None, fit=None, field='', use_native=True):
    """What one array says: a summary, and exact values when each position holds one element within
    ``points`` positions, otherwise the minimum, maximum and mean of ``points`` buckets of positions. Along
    two axes, the same over a map of buckets. With ``peak``, the line at the highest or lowest value, or with
    ``prominence`` every line that stands out by that much; with ``level``, each place the trace crosses it;
    with ``fit`` "line", the straight line through it.

    ``source`` is the file that holds the array, or ``[[path, size], ...]`` for the chunks of a stream or
    the files of a sweep's points; without it the inline values are read.
    """
    data = holder['data']
    request, context = plan(data, along, at, start, stop, low, high, points, peak, prominence, level, fit)
    if source is not None:
        request['segments'] = source if isinstance(source, list) else [[str(source), Path(source).stat().st_size]]
        computed = native(request) if use_native else None
        wanted = ['rows'] if 'rows' in request else (
            [('peaks' if prominence is not None else 'peak')] * (peak is not None) + ['crossings'] * (level is not None)
            + ['fit'] * (fit is not None))
        if (computed is None or any(key not in computed for key in wanted)
                or request.get('coordinates') and computed.get('peak') and not computed['peak'].get('coordinates')):
            # An iop-execd that does not find lines, crossings or fits leaves them to Python.
            computed = python(request)
    else:
        inline = holder.get('values')
        if inline is None:
            raise ValueError('This array has no saved file and no inline values')
        flat = list(_flatten(inline))
        if len(flat) != math.prod(data['shape']):
            raise ValueError('The inline values do not match the array shape')
        computed = python(request, flat)
    axes, buckets = context['axes'], computed['buckets']
    places = [_place(c) if c else None for c in context['coordinates']]
    exact = all(c == 1 for c in buckets['count'])
    following = []
    for i, axis in enumerate(request['along']):
        first, end, found = request['start'][i], request['stop'][i], context['coordinates'][i]
        item = {'axis': axes[axis]['name'] if axes else axis, 'start': first, 'stop': end}
        listed = found[first:end] if isinstance(found, list) else None
        if found:
            span = listed or (places[i](first), places[i](end - 1))
            item.update({'low': min(span), 'high': max(span)})
            if not listed:
                item['step'] = found[1]
            elif exact:
                # Listed coordinates are not implied by a step: each value comes with its own.
                item['coordinates'] = listed
        if axes and isinstance(axes[axis].get('unit'), str):
            item['unit'] = axes[axis]['unit']
        following.append(item)
    unit = data.get('unit')
    for axis, index in context['fixed'].items():
        # An axis whose positions are different quantities, such as the channels of a stream, gives each its unit.
        units = axes[axis].get('units') if axes else None
        if isinstance(units, list) and index < len(units) and isinstance(units[index], str):
            unit = units[index]
    if request.get('complex') == 'arg':
        unit = 'rad'
    elif request.get('complex') == 'power' and unit:
        unit += '²'
    one = len(following) == 1
    summary = {'count': computed['count']}
    if computed['count'] != computed['finite']:
        summary['nonfinite'] = computed['count'] - computed['finite']
    for key in ('min', 'max', 'mean', 'std'):
        summary[key] = computed[key]
    for key in ('min', 'max'):
        position = computed[key + '_index']
        if position is not None:
            summary[key + '_index'] = position
            at_ = [place(position[axis]) if place else None for place, axis in zip(places, request['along'])]
            if any(c is not None for c in at_):
                summary[key + '_at'] = at_[0] if one else at_
    result = {'field': field, 'dtype': data['dtype'], 'shape': data['shape'], **({'unit': unit} if unit else {}),
              'along': following[0] if one else following, **({'at': context['at']} if context['at'] else {}),
              **({'summarized': context['summarized']} if context['summarized'] else {}), 'summary': summary}
    # Lines, crossings and fits are given in the coordinates of the axis followed.
    shown = {'peak': lambda line: line and _peak(line, context['coordinates'][0]),
             'peaks': lambda lines: [_peak(line, context['coordinates'][0]) for line in lines],
             'crossings': lambda found: [_crossing(item, context['coordinates'][0]) for item in found],
             'fit': lambda fit: _fitted(fit, context['coordinates'][0])}
    result.update({key: shown[key](computed[key]) for key in shown if key in computed and computed[key] is not None})
    if 'rows' in computed:
        # At each position of the other axis: its coordinate or label, then what the trace there gave.
        axis = request['rows']
        rows = {'axis': axes[axis]['name'] if axes else axis}
        found = _coordinates(axes[axis], data['shape'][axis]) if axes else None
        if axes and isinstance(axes[axis].get('unit'), str):
            rows['unit'] = axes[axis]['unit']
        if axes and isinstance(axes[axis].get('labels'), list):
            rows['labels'] = axes[axis]['labels']
        elif found is not None:
            rows['coordinates'] = found if isinstance(found, list) else [_place(found)(p) for p in range(data['shape'][axis])]
        rows.update({key: [shown[key](item) if item is not None else None for item in items]
                     for key, items in computed['rows'].items()})
        result['rows'] = rows
    sizes = [len(starts) for starts in buckets['start']]
    shaped = (lambda values: values) if one else (lambda values: _nest(values, sizes))
    if exact:
        result['values'] = shaped(buckets['mean'])
    else:
        coordinates = [[_short(place(p)) for p in starts] if place else None
                       for place, starts in zip(places, buckets['start'])]
        result['envelope'] = {'position': buckets['start'][0] if one else buckets['start'],
                              **({'coordinate': coordinates[0] if one else coordinates}
                                 if any(c is not None for c in coordinates) else {}),
                              'min': shaped([_short(v) for v in buckets['min']]),
                              'max': shaped([_short(v) for v in buckets['max']]),
                              'mean': shaped([_short(v) for v in buckets['mean']])}
    return result


def _nest(values, sizes):
    """Row-major values as nested lists of these sizes."""
    for size in reversed(sizes[1:]):
        values = [values[i:i + size] for i in range(0, len(values), size)]
    return values


def options(query):
    """View options from URL query values: at is a JSON object; a JSON list gives one value per axis along."""
    def one(key, kind, text):
        try:
            return kind(text)
        except ValueError:
            raise ValueError(f'{key} is {"an integer" if kind is int else "a number"}') from None

    def value(key, kind):
        text = query.get(key)
        if text is None:
            return None
        if not text.startswith('['):
            return one(key, kind, text) if kind else text
        try:
            items = json.loads(text)
        except ValueError:
            raise ValueError(f'{key} is a JSON list with one value for each axis along') from None
        return [item if item is None or not kind else one(key, kind, str(item)) for item in items]
    at = None
    if 'at' in query:
        try:
            at = json.loads(query['at'])
        except ValueError:
            raise ValueError('at is a JSON object that maps axes to an index or a label') from None
    unknown = set(query) - set(OPTIONS)
    if unknown:
        raise ValueError('Unknown view options: ' + ', '.join(sorted(unknown)))
    return {'field': query.get('field'), 'along': value('along', None), 'at': at, 'start': value('start', int),
            'stop': value('stop', int), 'low': value('low', float), 'high': value('high', float),
            'points': value('points', int) if 'points' in query else POINTS, 'peak': query.get('peak'),
            'prominence': value('prominence', float), 'level': value('level', float), 'fit': query.get('fit')}


def _source(holder, folder=None, output=None):
    """Where an array's bytes are, as [[path, size], ...]: the file its operation saved in folder, or the files the
    points of a sweep saved in the experiment's ``output`` folder; None when the record holds its values."""
    from .engine import relative_path
    data = holder['data']
    if isinstance(data.get('files'), list) and holder.get('values') is None:
        if output is None:
            raise ValueError("This array is read from the files of a sweep's points; read it with the experiment ID")
        sizes = data.get('file_sizes') or [data['bytes'] // len(data['files'])] * len(data['files'])
        if len(sizes) != len(data['files']) or any(type(n) is not int or n < 0 for n in sizes) or sum(sizes) != data['bytes']:
            raise ValueError('Array file sizes must describe every segment and add up to its bytes')
        segments = []
        for item, size in zip(data['files'], sizes):
            path = output / relative_path(item)
            if not path.resolve().is_relative_to(output.resolve()) or not path.is_file():
                raise ValueError('The array file of a point of this sweep is missing')
            segments.append([str(path), size])
        return segments
    if data.get('file') and folder is not None:
        path = folder / relative_path(data['file'])
        if not path.resolve().is_relative_to(folder.resolve()) or not path.is_file():
            raise ValueError('The array file of this operation is missing')
        return [[str(path), path.stat().st_size]]
    if holder.get('values') is None:
        raise ValueError('This array is kept in the file of the operation that returned it; read it with that '
                         'operation ID')
    return None


def read(result, folder, field=None, output=None, **options):
    """The view of an array in a result: from the file its operation saved in folder, from the files the points
    of a sweep saved in the experiment's ``output`` folder, or from the values the record holds."""
    field, holder = select(result or {}, field)
    return view(holder, _source(holder, folder, output), field=field, **options)


_NPY = {'int8': 'i1', 'uint8': 'u1', 'int16': 'i2', 'uint16': 'u2', 'int32': 'i4', 'uint32': 'u4', 'int64': 'i8',
        'uint64': 'u8', 'float32': 'f4', 'float64': 'f8'}


def npy(holder, segments=None):
    """A whole array as a NumPy .npy file, from these files in order or from the values the record holds: its
    length and its bytes in pieces. A last axis of re/im pairs becomes complex values."""
    data = holder['data']
    kind, shape = data['dtype'], list(data['shape'])
    order = '>' if data.get('byte_order') == 'big' else '<'
    descr = ('|' if DTYPES[kind] == 1 else order) + _NPY[kind]
    axes = data.get('axes') or []
    if (kind in ('float32', 'float64') and len(shape) > 1 and shape[-1] == 2 and len(axes) == len(shape)
            and isinstance(axes[-1], dict) and axes[-1].get('labels') == COMPONENTS):
        shape, descr = shape[:-1], order + ('c8' if kind == 'float32' else 'c16')
    size = math.prod(data['shape']) * DTYPES[kind]
    if segments is None:
        try:
            block = array.array(_typecode(kind), [math.nan if v is None else v for v in _leaves(holder['values'])])
        except (TypeError, OverflowError):
            raise ValueError(f'The values this record holds are not all {kind}') from None
        if (order == '>') != (sys.byteorder == 'big') and block.itemsize > 1:
            block.byteswap()
        payload = block.tobytes()
        if len(payload) != size:
            raise ValueError('The inline values do not match the array shape')
    elif sum(n for _, n in segments) < size:
        raise ValueError('The files hold fewer bytes than the array description')
    text = "{'descr': '%s', 'fortran_order': False, 'shape': %r, }" % (descr, tuple(shape))
    pad = -(10 + len(text) + 1) % 64
    header = b'\x93NUMPY\x01\x00' + struct.pack('<H', len(text) + pad + 1) + text.encode('latin-1') + b' ' * pad + b'\n'

    def pieces():
        yield header
        if segments is None:
            yield payload
            return
        remaining = size
        for path, available in segments:
            with open(path, 'rb') as file:
                while remaining and available:
                    block = file.read(min(1 << 20, available, remaining))
                    if not block:
                        raise ValueError('A file holds fewer bytes than recorded')
                    available -= len(block)
                    remaining -= len(block)
                    yield block
            if not remaining:
                break
    return len(header) + size, pieces()


def _leaves(values):
    if isinstance(values, list):
        for item in values:
            yield from _leaves(item)
    else:
        yield values


def _operation(engine, operation_id):
    op = engine.store.get('operations', operation_id)
    if op['state'] != 'completed':
        raise ValueError('Arrays are read from completed operations')
    return op.get('result'), engine.home / 'runs' / op['run_id'] / 'output' / 'operations' / operation_id


def _held(engine, run_id, request_id=None):
    if request_id is None:
        held = engine.run_result(run_id)
    else:
        from .batches import describe
        held = describe(engine, run_id, request_id).get('results')
    return held, engine.home / 'runs' / run_id / 'output'


def operation_array(engine, operation_id, field=None, **options):
    """The view of an array a completed operation returned."""
    result, folder = _operation(engine, operation_id)
    return {'operation_id': operation_id, **read(result, folder, field, **options)}


def operation_npy(engine, operation_id, field=None):
    """An array a completed operation returned, as a .npy file."""
    result, folder = _operation(engine, operation_id)
    field, holder = select(result or {}, field)
    return npy(holder, _source(holder, folder))


def result_array(engine, run_id, request_id=None, field=None, **options):
    """The view of an array an experiment's result holds, such as what a sweep gathered; with ``request_id``,
    one the results of that batch hold."""
    held, output = _held(engine, run_id, request_id)
    return {'run_id': run_id, **({'request_id': request_id} if request_id else {}),
            **read(held, None, field, output=output, **options)}


def result_npy(engine, run_id, request_id=None, field=None):
    """An array an experiment's result, or a batch's results, hold, as a .npy file."""
    held, output = _held(engine, run_id, request_id)
    field, holder = select(held or {}, field)
    return npy(holder, _source(holder, None, output))


# A step binding or a saved operation's named result can take its value through a view of an array:
# {"step": ..., "view": {...}, "path": [...]} reads path inside the view. These views are computed
# once, when the operation that returned the array completes, and kept in its record as "derived".
OPTIONS = ('field', 'along', 'at', 'start', 'stop', 'low', 'high', 'points', 'peak', 'prominence', 'level', 'fit')


def check(value, what):
    """Validate the view of a binding or named result; axis names are resolved against the array later."""
    if not isinstance(value, dict) or set(value) - set(OPTIONS):
        raise ValueError(f'{what}: view takes ' + ', '.join(OPTIONS))
    kinds = {'field': (str,), 'along': (str, int), 'at': (dict,), 'start': (int,), 'stop': (int,),
             'low': (int, float), 'high': (int, float), 'points': (int,), 'peak': (str,), 'prominence': (int, float),
             'level': (int, float), 'fit': (str,)}
    if value.get('peak', 'max') not in ('max', 'min'):
        raise ValueError(f'{what}: view peak is max, or min for a dip')
    if value.get('fit', 'line') not in FITS:
        raise ValueError(f'{what}: view fit is line, lorentzian or gaussian')
    if 'prominence' in value and 'peak' not in value:
        raise ValueError(f'{what}: view prominence reads the lines of a peak or a dip; give peak')
    for key, item in value.items():
        # Along two axes, the options other than field, at, peak, prominence, level and fit give one value per axis.
        items = item if isinstance(item, list) and key not in ('field', 'at', 'peak', 'prominence', 'level', 'fit') else [item]
        if any(isinstance(i, bool) or not (isinstance(i, kinds[key]) or i is None and key in ('start', 'stop', 'low', 'high'))
               for i in items) or not items:
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

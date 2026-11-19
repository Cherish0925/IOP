"""Sweeps: a step run at each value of one or more of its parameters, its results gathered into arrays.

A step with ``sweep`` runs once for each combination of the values it gives, one point after another,
the first name changing slowest. A range gives evenly spaced values from start to stop, or around a
center over a span; its ends may be read from earlier results, as bindings are, and each point then
reads its value when it runs. An adaptive range places each point from what the earlier ones read:
where the reading changes most, points come closer together. A step that repeats runs that many times
at each point, over a last axis named repeat, or that many times in turn without a sweep. A sweep with
``until`` stops after a point whose reading is beyond a limit, before the next point runs. What the
points return is gathered:
numbers in the same place become an array over the sweep, with one axis for each swept name; arrays of
one shape in the same place combine into one array with the sweep's axes in front, from the values the
records hold or from the files the points saved, read in order; other values are kept when they are the
same at every point that has one. Everything else stays in the record of each point.
"""
import copy
import itertools
import math
from . import arrays

MAX_POINTS = 10_000
ADAPTIVE_POINTS = 1000
REPEAT = 'repeat'
ENDS = ({'start', 'stop'}, {'center', 'span'})
# An adaptive range does not divide intervals narrower than this part of its even spacing.
FINEST = 1024
_LEFT = object()


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _ends(spec):
    """The first and last value of a range given by start and stop, or by center and span."""
    for key in spec.keys() - {'points', 'adapt'}:
        if not _number(spec[key]):
            raise ValueError(f'{key} is {spec[key]!r}; the ends of a range are finite numbers')
    if 'start' in spec:
        return spec['start'], spec['stop']
    center, span = spec['center'], spec['span']
    if type(center) is int and type(span) is int and span % 2 == 0:
        return center - span // 2, center + span // 2
    return center - span / 2, center + span / 2


def _value(start, stop, points, index):
    if points == 1 or start == stop:
        return start
    # Integers stay integers when they divide evenly, so integer parameters can be swept.
    if type(start) is int and type(stop) is int and (stop - start) % (points - 1) == 0:
        return start + index * ((stop - start) // (points - 1))
    return float(stop) if index == points - 1 else float(start + index * ((stop - start) / (points - 1)))


def _axis(name, start, stop, points):
    if points == 1:
        return {'name': name, 'coordinates': [float(start)]}
    return {'name': name} if start == stop else {'name': name, 'start': start, 'stop': stop}


def _count(name, spec):
    """Validate the size before allocating values, including ranges read from earlier results."""
    if isinstance(spec, list):
        points = len(spec)
    elif (isinstance(spec, dict) and spec.keys() - {'points', 'adapt'} in ENDS
          and type(spec.get('points')) is int and spec['points'] > 0):
        points = spec['points']
    else:
        raise ValueError(f'sweep {name}: give a list of values, or start, stop and points (a positive integer), '
                         'or center, span and points')
    if points > MAX_POINTS:
        raise ValueError(f'A sweep has at most {MAX_POINTS} points; {name} has {points}')
    if 'adapt' in spec:
        path = spec['adapt']
        if not isinstance(path, list) or not path or any(type(part) not in (str, int) for part in path):
            raise ValueError(f'sweep {name}: adapt is the path to the number each point reads, such as ["power"]')
        if not 2 <= points <= ADAPTIVE_POINTS:
            raise ValueError(f'sweep {name}: an adaptive range has 2 to {ADAPTIVE_POINTS} points')
    return points


def taken(name, spec):
    """The values one swept name takes, in order, and the axis they give. The values of a range whose ends are
    read from earlier results are found when each point runs: {"range": spec, "index": i}."""
    points = _count(name, spec)
    if isinstance(spec, list):
        if spec and all(map(_number, spec)):
            return list(spec), {'name': name, **arrays.coordinates(spec)}
        if spec and all(isinstance(value, str) for value in spec):
            return list(spec), {'name': name, 'labels': list(spec)}
        raise ValueError(f'sweep {name}: list the numbers, or the strings, it takes in turn')
    if 'adapt' in spec or any(isinstance(spec[key], dict) for key in spec.keys() - {'points'}):
        if 'adapt' in spec and not any(isinstance(spec[key], dict) for key in spec.keys() - {'points', 'adapt'}):
            try:
                _divisible(spec)
            except ValueError as error:
                raise ValueError(f'sweep {name}: {error}') from None
        return [{'range': spec, 'index': index} for index in range(points)], {'name': name, 'range': spec}
    try:
        start, stop = _ends(spec)
    except ValueError as error:
        raise ValueError(f'sweep {name}: {error}') from None
    return [_value(start, stop, points, index) for index in range(points)], _axis(name, start, stop, points)


def value(spec, index):
    """The value at a point of a range whose ends are known."""
    start, stop = _ends(spec)
    return _value(start, stop, spec['points'], index)


def axis(name, spec):
    """The axis of a range whose ends are known."""
    start, stop = _ends(spec)
    return _axis(name, start, stop, spec['points'])


def _divisible(spec):
    """The ends of an adaptive range, which must leave room for all of its points."""
    start, stop = _ends(spec)
    if start == stop:
        raise ValueError('the ends of an adaptive range differ')
    if type(start) is int and type(stop) is int and spec['points'] > abs(stop - start) + 1:
        raise ValueError(f'{spec["points"]} integer points do not fit between {start} and {stop}')
    return start, stop


def adapted(spec, readings, known=()):
    """The values of an adaptive range once its points have given these readings, one value more than them:
    its two ends, then each time the middle of the interval between neighbouring values whose ends differ
    most, in spacing over the range and in reading over the readings' spread so far. Intervals narrower than
    1/FINEST of the even spacing are not divided, so an edge does not take every point, and integer ends give
    integers. A reading that is not a finite number counts only by its spacing. ``known`` are values already
    found for the first of these readings."""
    start, stop = _divisible(spec)
    integer = type(start) is int and type(stop) is int
    span = abs(stop - start)
    finest = 1 if integer else span / ((spec['points'] - 1) * FINEST)
    values = list(known[:len(readings) + 1]) or [start]
    if len(values) == 1 and readings:
        values.append(stop)
    while len(values) < len(readings) + 1:
        count = len(values)
        order = sorted(range(count), key=values.__getitem__)
        finite = [y for y in readings[:count] if _number(y)]
        height = max(finite) - min(finite) if len(finite) > 1 else 0
        largest, chosen = -1.0, None
        for a, b in zip(order, order[1:]):
            spacing = values[b] - values[a]
            if spacing <= finest:
                continue
            loss = (spacing / span) ** 2
            if height and _number(readings[a]) and _number(readings[b]):
                loss += ((readings[b] - readings[a]) / height) ** 2
            if loss > largest:
                largest, chosen = loss, (values[a], spacing)
        if chosen is None:
            raise ValueError('every interval of this adaptive range is already as narrow as it is divided')
        low, spacing = chosen
        values.append(low + spacing // 2 if integer else low + spacing / 2)
    return values


def limits(until):
    """Check what stops a sweep: the path to the number each point reads, and the limits it stays within."""
    if (not isinstance(until, dict) or until.keys() - {'path', 'above', 'below'} or not until.keys() & {'above', 'below'}
            or not isinstance(until.get('path'), list) or not until['path']
            or any(type(part) not in (str, int) for part in until['path'])
            or not all(_number(until[key]) for key in until.keys() & {'above', 'below'})):
        raise ValueError('until gives the path to the number each point reads, such as ["current"], and the number '
                         'it stops above, below, or both')
    if until.get('below', -math.inf) >= until.get('above', math.inf):
        raise ValueError('until stops a sweep outside the band from below to above, so below is less than above')
    return until


def beyond(until, reading):
    """Whether a point's reading stops its sweep: beyond a limit, or not a number, so the limit is not known to
    hold."""
    return not _number(reading) or reading > until.get('above', math.inf) or reading < until.get('below', -math.inf)


def grid(sweep, repeat=None):
    """The arguments each point adds, in order, and the shape and axes of the sweep. With ``repeat``, each
    point runs that many times in turn, over a last axis named repeat; the sweep may then be empty."""
    if repeat is not None and (type(repeat) is not int or not 1 <= repeat <= MAX_POINTS):
        raise ValueError(f'repeat is how many times a step runs at each point, from 1 to {MAX_POINTS}')
    if not isinstance(sweep, dict) or not sweep and repeat is None:
        raise ValueError('sweep maps parameters to the values they take')
    if repeat is not None and REPEAT in sweep:
        raise ValueError('The axis a step repeats over is named repeat; it does not also sweep a parameter so named')
    shape = [_count(name, spec) for name, spec in sweep.items()] + ([repeat] if repeat is not None else [])
    if len(shape) > 1 and any(isinstance(spec, dict) and 'adapt' in spec for spec in sweep.values()):
        raise ValueError('An adaptive sweep varies one parameter and does not repeat; sweep or repeat in a step '
                         'around it')
    if math.prod(shape) > MAX_POINTS:
        raise ValueError(f'A sweep has at most {MAX_POINTS} points; this one has {math.prod(shape)}')
    found = [taken(name, spec) for name, spec in sweep.items()]
    points = [dict(zip(sweep, combination)) for combination in itertools.product(*(values for values, _ in found))]
    axes = [axis for _, axis in found]
    if repeat is not None:
        points = [copy.deepcopy(point) for point in points for _ in range(repeat)]
        axes.append({'name': REPEAT, 'start': 0, 'step': 1})
    return points, shape, axes


def _array(value):
    data = value.get('data') if isinstance(value, dict) else None
    return isinstance(data, dict) and isinstance(data.get('dtype'), str) and isinstance(data.get('shape'), list)


def _leaves(values):
    if isinstance(values, list):
        for item in values:
            yield from _leaves(item)
    else:
        yield values


def _combined(items, present, shape, axes):
    """Arrays of one shape as one array with the sweep's axes in front: from the values the records hold, or
    from the files every point saved, named from the experiment's output folder."""
    data = [value['data'] for value in present]
    first = data[0]
    if any(d['dtype'] != first['dtype'] or d['shape'] != first['shape'] for d in data):
        return _LEFT
    inline = all(value.get('values') is not None for value in present)
    saved = (len(present) == len(items) and all(isinstance(d.get('file'), str) or d.get('files') for d in data)
             and all(d.get('byte_order', 'little') == first.get('byte_order', 'little') for d in data))
    if not (inline or saved):
        return _LEFT
    inner = first['shape']
    named = all(isinstance(d.get('axes'), list) and len(d['axes']) == len(inner) for d in data)
    # Preserve each common axis independently: a changing swept bias does not erase a shared
    # wavelength axis. Coordinates that differ between points cannot describe the combined axis.
    own = [axis if all(d['axes'][index] == axis for d in data)
           else {key: axis[key] for key in ('name', 'unit') if key in axis}
           for index, axis in enumerate(first['axes'])] if named else \
        [{'name': f'axis{index + 1}'} for index in range(len(inner))]
    size = math.prod(inner)
    combined = {'dtype': first['dtype'], 'shape': list(shape) + inner, 'count': size * len(items),
                'axes': copy.deepcopy(axes) + copy.deepcopy(own)}
    if isinstance(first.get('unit'), str) and all(d.get('unit') == first['unit'] for d in data):
        combined['unit'] = first['unit']
    if not inline:
        # Read in order, the points' files are the combined array; nothing is copied.
        files, sizes = [], []
        for d in data:
            point_files = d['files'] if d.get('files') else [d['file']]
            point_bytes = size * arrays.DTYPES[d['dtype']]
            files.extend(point_files)
            sizes.extend(d.get('file_sizes') or [point_bytes // len(point_files)] * len(point_files))
        combined.update(byte_order=first.get('byte_order', 'little'), bytes=size * arrays.DTYPES[first['dtype']] * len(items),
                        files=files, file_sizes=sizes)
        return {'data': combined}
    flat = [leaf for value in items for leaf in (_leaves(value['values']) if value is not None else [None] * size)]
    return {'data': combined, 'values': arrays.nest(flat, combined['shape'])}


def _gather(items, shape, axes):
    present = [value for value in items if value is not None]
    if not present:
        return None
    if any(_array(value) for value in present):
        return _combined(items, present, shape, axes) if all(map(_array, present)) else _LEFT
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in present):
        return {'data': {'dtype': 'float64', 'shape': list(shape), 'count': len(items), 'axes': copy.deepcopy(axes)},
                'values': arrays.nest(list(items), shape)}
    if all(isinstance(value, dict) for value in present):
        gathered = {}
        for key in dict.fromkeys(key for value in present for key in value):
            found = _gather([None if value is None else value.get(key) for value in items], shape, axes)
            if found is not _LEFT:
                gathered[key] = found
        # A unit beside a single number is that number's unit, as in {"value": 0.25, "unit": "V"}.
        numbers = [key for key, value in gathered.items() if _array(value) and len(value['data']['shape']) == len(shape)]
        if isinstance(gathered.get('unit'), str) and len(numbers) == 1 and 'unit' not in gathered[numbers[0]]['data']:
            gathered[numbers[0]]['data']['unit'] = gathered['unit']
        return gathered
    return present[0] if all(value == present[0] for value in present) else _LEFT


def gather(results, shape, axes):
    """What a sweep returns, from what each of its points returned in order (None for a point without it)."""
    found = _gather(results, shape, axes)
    return None if found is _LEFT else found

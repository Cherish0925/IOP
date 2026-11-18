"""Sweeps: a step run at each value of one or more of its parameters, its results gathered into arrays.

A step with ``sweep`` runs once for each combination of the values it gives, one point after another,
the first name changing slowest. What the points return is gathered: numbers in the same place become an
array over the sweep, with one axis for each swept name; arrays of one shape in the same place combine
into one array with the sweep's axes in front, from the values the records hold or from the files the
points saved, read in order; other values are kept when they are the same at every point that has one.
Everything else stays in the record of each point.
"""
import copy
import itertools
import math
from . import arrays

MAX_POINTS = 10_000
_LEFT = object()


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def taken(name, spec):
    """The values one swept name takes, in order, and the axis they give."""
    if isinstance(spec, list):
        if spec and all(map(_number, spec)):
            return list(spec), {'name': name, **arrays.coordinates(spec)}
        if spec and all(isinstance(value, str) for value in spec):
            return list(spec), {'name': name, 'labels': list(spec)}
        raise ValueError(f'sweep {name}: list the numbers, or the strings, it takes in turn')
    if (not isinstance(spec, dict) or set(spec) != {'start', 'stop', 'points'} or not _number(spec['start'])
            or not _number(spec['stop']) or type(spec['points']) is not int or spec['points'] < 1):
        raise ValueError(f'sweep {name}: give a list of values, or start, stop and points (a positive integer)')
    start, stop, points = spec['start'], spec['stop'], spec['points']
    if points == 1:
        return [start], {'name': name, 'coordinates': [float(start)]}
    if start == stop:
        return [start] * points, {'name': name}
    # Integers stay integers when they divide evenly, so integer parameters can be swept.
    if type(start) is int and type(stop) is int and (stop - start) % (points - 1) == 0:
        values = [start + i * ((stop - start) // (points - 1)) for i in range(points)]
    else:
        step = (stop - start) / (points - 1)
        values = [float(start + i * step) for i in range(points - 1)] + [float(stop)]
    return values, {'name': name, 'start': start, 'stop': stop}


def grid(sweep):
    """The arguments each point adds, in order, and the shape and axes of the sweep."""
    if not isinstance(sweep, dict) or not sweep:
        raise ValueError('sweep maps parameters to the values they take')
    found = [taken(name, spec) for name, spec in sweep.items()]
    shape = [len(values) for values, _ in found]
    if math.prod(shape) > MAX_POINTS:
        raise ValueError(f'A sweep has at most {MAX_POINTS} points; this one has {math.prod(shape)}')
    points = [dict(zip(sweep, combination)) for combination in itertools.product(*(values for values, _ in found))]
    return points, shape, [axis for _, axis in found]


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
    saved = (len(present) == len(items) and all(isinstance(d.get('file'), str) for d in data)
             and all(d.get('byte_order', 'little') == first.get('byte_order', 'little') for d in data))
    if not (inline or saved):
        return _LEFT
    inner = first['shape']
    own = first.get('axes') if all(d.get('axes') == first.get('axes') for d in data) else None
    if not (isinstance(own, list) and len(own) == len(inner)):
        # Coordinates that differ between points are not those of the combined axis; names and units are.
        named = all(isinstance(d.get('axes'), list) and len(d['axes']) == len(inner) for d in data)
        own = [{key: axis[key] for key in ('name', 'unit') if key in axis} for axis in first['axes']] if named else \
            [{'name': f'axis{index + 1}'} for index in range(len(inner))]
    size = math.prod(inner)
    combined = {'dtype': first['dtype'], 'shape': list(shape) + inner, 'count': size * len(items),
                'axes': copy.deepcopy(axes) + copy.deepcopy(own)}
    if isinstance(first.get('unit'), str) and all(d.get('unit') == first['unit'] for d in data):
        combined['unit'] = first['unit']
    if not inline:
        # Read in order, the points' files are the combined array; nothing is copied.
        combined.update(byte_order=first.get('byte_order', 'little'), bytes=size * arrays.DTYPES[first['dtype']] * len(items),
                        files=[d['file'] for d in data])
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

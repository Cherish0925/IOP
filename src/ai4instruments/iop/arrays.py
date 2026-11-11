"""Array data shared by drivers: IEEE 488.2 blocks, element types and one result description.

A driver that returns waveforms, spectra, images or other numerical arrays describes them with
the same fields: dtype, byte_order, shape, count, bytes, sha256 and, when known, unit and axes.
The raw payload is saved in the operation's output directory; small arrays are also returned as
nested lists in ``values``.
"""
import array
import hashlib
import math
import sys

DTYPES = {'int8': 1, 'uint8': 1, 'int16': 2, 'uint16': 2, 'int32': 4, 'uint32': 4,
          'int64': 8, 'uint64': 8, 'float32': 4, 'float64': 8}
ORDERS = ('little', 'big')
INLINE_LIMIT = 1024
_CODES = {'int': 'bhilq', 'uint': 'BHILQ', 'float': 'fd'}


def _typecode(dtype):
    kind = 'float' if dtype.startswith('float') else 'uint' if dtype.startswith('uint') else 'int'
    for code in _CODES[kind]:
        if array.array(code).itemsize == DTYPES[dtype]:
            return code
    raise ValueError('This Python build has no ' + dtype + ' array type')


def _dtype(value):
    if value not in DTYPES:
        raise ValueError('dtype must be one of ' + ', '.join(DTYPES))
    return value


def _order(value):
    if value not in ORDERS:
        raise ValueError('byte_order must be little or big')
    return value


def check_result(spec):
    """Validate an operation's array result description; return it with defaults filled in."""
    encoding = spec.get('encoding', 'block')
    if encoding not in ('block', 'ascii'):
        raise ValueError('Array encoding must be block or ascii')
    checked = {'encoding': encoding, 'dtype': _dtype(spec.get('dtype', 'float64' if encoding == 'ascii' else '')),
               'byte_order': _order(spec.get('byte_order', 'little'))}
    if encoding == 'ascii':
        if checked['dtype'] != 'float64':
            raise ValueError('ASCII arrays are read as float64')
        separator = spec.get('separator', ',')
        if not isinstance(separator, str) or not separator:
            raise ValueError('separator must be a nonempty string')
        checked['separator'] = separator
    shape = spec.get('shape')
    if shape is not None:
        if (not isinstance(shape, list) or not shape or any(type(n) is not int or n == 0 or n < -1 for n in shape)
                or shape.count(-1) > 1):
            raise ValueError('shape is a list of positive sizes with at most one -1')
        checked['shape'] = shape
    if 'unit' in spec:
        if not isinstance(spec['unit'], str):
            raise ValueError('unit must be a string')
        checked['unit'] = spec['unit']
    if 'axes' in spec:
        axes = spec['axes']
        if not isinstance(axes, list) or any(not isinstance(a, dict) or not isinstance(a.get('name'), str) for a in axes):
            raise ValueError('axes is a list of objects with a name')
        if shape is not None and len(axes) != len(shape):
            raise ValueError('Describe one axis for each dimension of shape')
        checked['axes'] = axes
    limit = spec.get('inline_limit', INLINE_LIMIT)
    if type(limit) is not int or limit < 0:
        raise ValueError('inline_limit must be a nonnegative integer')
    checked['inline_limit'] = limit
    if 'file' in spec and (not isinstance(spec['file'], str) or not spec['file'].strip()):
        raise ValueError('file must be a nonempty name')
    if 'file' in spec:
        checked['file'] = spec['file']
    return checked


def check_data(spec, parameters):
    """Validate a binary write description: which parameter supplies the array and how it is encoded."""
    name = spec.get('parameter')
    if name not in parameters or parameters[name].get('type') != 'array':
        raise ValueError('data.parameter must name an array parameter of the operation')
    return {'parameter': name, 'dtype': _dtype(spec.get('dtype', '')), 'byte_order': _order(spec.get('byte_order', 'little'))}


def block(payload):
    """IEEE 488.2 definite-length arbitrary block: #<digits><length><payload>."""
    length = str(len(payload))
    if len(length) > 9:
        raise ValueError('Binary block exceeds the IEEE 488.2 definite-length size')
    return b'#' + str(len(length)).encode() + length.encode() + bytes(payload)


def parse_block(data):
    """Return (payload, end) for a complete definite-length block in data, or None if more bytes are needed."""
    start = data.find(b'#')
    head = bytes(data if start < 0 else data[:start])
    if head.strip():
        raise ValueError('Expected a binary block; the instrument returned ' + repr(head[:80]))
    if start < 0 or len(data) < start + 2:
        return None
    digits = data[start + 1:start + 2]
    if not digits.isdigit():
        raise ValueError('Malformed binary block header')
    size = int(digits)
    if size == 0:
        raise ValueError('Indefinite-length blocks (#0) are not supported; select a definite-length block format on the instrument')
    if len(data) < start + 2 + size:
        return None
    length = bytes(data[start + 2:start + 2 + size])
    if not length.isdigit():
        raise ValueError('Malformed binary block length')
    begin = start + 2 + size
    end = begin + int(length)
    if len(data) < end:
        return None
    return bytes(data[begin:end]), end


def decode(payload, dtype, byte_order='little'):
    values = array.array(_typecode(dtype))
    if len(payload) % values.itemsize:
        raise ValueError(f'{len(payload)} bytes do not form whole {dtype} elements')
    values.frombytes(payload)
    if byte_order != sys.byteorder and values.itemsize > 1:
        values.byteswap()
    return values.tolist()


def encode(values, dtype, byte_order='little'):
    if not isinstance(values, list):
        raise ValueError('Array data must be a list of numbers')
    integer = not dtype.startswith('float')
    for value in values:
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                or integer and not isinstance(value, int)):
            raise ValueError('Array data must contain finite ' + ('integers' if integer else 'numbers'))
    try:
        result = array.array(_typecode(dtype), values)
    except OverflowError:
        raise ValueError('Array values are outside the ' + dtype + ' range') from None
    if byte_order != sys.byteorder and result.itemsize > 1:
        result.byteswap()
    return result.tobytes()


def parse_ascii(text, separator=','):
    try:
        return [float(item) for item in text.split(separator) if item.strip()]
    except ValueError:
        raise ValueError('Expected numbers separated by ' + repr(separator) + '; the instrument returned ' + repr(text[:80])) from None


def resolve_shape(count, shape=None):
    if shape is None:
        return [count]
    known = math.prod(n for n in shape if n != -1)
    if -1 in shape:
        if count % known:
            raise ValueError(f'{count} elements do not fit shape {shape}')
        return [count // known if n == -1 else n for n in shape]
    if known != count:
        raise ValueError(f'{count} elements do not fit shape {shape}')
    return list(shape)


def nest(values, shape):
    for size in reversed(shape[1:]):
        values = [values[i:i + size] for i in range(0, len(values), size)]
    return values


def result(payload, spec, context=None, name='data'):
    """Describe an array payload, save it beside the operation and return the result fields.

    ``spec`` is a checked result description. ``context`` is the operation context; without an
    output directory, as when a driver is called directly, every value is returned inline.
    """
    size = DTYPES[spec['dtype']]
    if len(payload) % size:
        raise ValueError(f"{len(payload)} bytes do not form whole {spec['dtype']} elements")
    count = len(payload) // size
    shape = resolve_shape(count, spec.get('shape'))
    data = {'dtype': spec['dtype'], 'byte_order': spec['byte_order'], 'shape': shape, 'count': count,
            'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
    for key in ('unit', 'axes'):
        if key in spec:
            data[key] = spec[key]
    saved = False
    if hasattr(context, 'file'):
        filename = spec.get('file', name + '.bin')
        context.file(filename).write_bytes(payload)
        data['file'] = filename
        saved = True
    fields = {'data': data}
    # Large arrays stay in their file; decoding them only to count would cost time and memory.
    if not saved or count <= spec['inline_limit']:
        fields['values'] = nest(decode(payload, spec['dtype'], spec['byte_order']), shape)
    return fields

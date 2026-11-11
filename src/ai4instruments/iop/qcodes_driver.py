"""Instruments controlled through QCoDeS drivers.

QCoDeS drivers declare every parameter with its unit, validator and whether it can be read or set.
This adapter turns that metadata into IOP operations: ``get.<path>`` reads a parameter and
``set.<path>`` sets it, with types, ranges and units taken from the driver. Arrays use the shared
description in ``arrays``; setpoints become axes.
"""
import fnmatch
import importlib
import math
import re
from . import arrays
from .drivers import Driver
from .store import now

_DTYPES = {'float64': 'float64', 'float32': 'float32', 'int8': 'int8', 'int16': 'int16', 'int32': 'int32',
           'int64': 'int64', 'uint8': 'uint8', 'uint16': 'uint16', 'uint32': 'uint32', 'uint64': 'uint64', 'bool': 'uint8'}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and abs(value) < 1e18


def field(vals, unit=''):
    """JSON parameter description from a QCoDeS validator; unknown validators accept any JSON value."""
    from qcodes import validators as v
    result = {'unit': unit} if unit else {}
    if isinstance(vals, v.Bool):
        result['type'] = 'boolean'
    elif isinstance(vals, (v.Ints, v.Numbers)):
        result['type'] = 'integer' if isinstance(vals, v.Ints) else 'number'
        for key, bound in (('minimum', vals.min_value), ('maximum', vals.max_value)):
            if _finite(bound):
                result[key] = int(bound) if result['type'] == 'integer' else float(bound)
    elif isinstance(vals, v.Enum):
        options = [plain(value) for value in vals.valid_values]
        if all(isinstance(o, (str, int, float)) and not isinstance(o, bool) for o in options):
            result['enum'] = sorted(options, key=lambda o: (isinstance(o, str), o))
            if all(isinstance(o, str) for o in options):
                result['type'] = 'string'
    elif isinstance(vals, v.Strings):
        result['type'] = 'string'
    elif isinstance(vals, v.Arrays):
        result.update(type='array', items={'type': 'number'})
    return result


def plain(value):
    """Convert NumPy scalars and complex numbers to JSON values."""
    if hasattr(value, 'item') and getattr(value, 'shape', None) == ():
        value = value.item()
    if isinstance(value, complex):
        return {'re': value.real, 'im': value.imag}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def array_fields(values, context, name, unit='', axes=None):
    """Save a NumPy array with the shared description; complex values gain a last re/im axis."""
    import numpy
    data = numpy.asarray(values)
    axes = list(axes or [])
    if numpy.iscomplexobj(data):
        data = numpy.ascontiguousarray(data, dtype='<c16').view('<f8').reshape(data.shape + (2,))
        axes = axes + [{'name': 'component', 'labels': ['re', 'im']}] if axes else []
    dtype = _DTYPES.get(data.dtype.name)
    if dtype is None:
        raise ValueError(f'{name} returned {data.dtype} values; only numeric arrays are supported')
    if data.ndim == 0 or 0 in data.shape:
        raise ValueError(f'{name} returned an empty array')
    payload = numpy.ascontiguousarray(data, dtype=numpy.dtype(dtype).newbyteorder('<')).tobytes()
    spec = {'dtype': dtype, 'byte_order': 'little', 'shape': list(data.shape)}
    if unit:
        spec['unit'] = unit
    if axes and len(axes) == data.ndim:
        spec['axes'] = axes
    return arrays.result(payload, arrays.check_result(spec), context, name)


class QCoDeS(Driver):
    """Config: instrument ("module:Class"), optional address, arguments, parameters and exclude patterns."""

    def __init__(self, *args):
        super().__init__(*args)
        target = self.config.get('instrument')
        module, separator, class_name = str(target).partition(':')
        if not separator or not module or not class_name:
            raise ValueError('Set instrument to the QCoDeS driver class as module:Class')
        arguments = self.config.get('arguments', {})
        if not isinstance(arguments, dict):
            raise ValueError('arguments must be an object of driver keyword arguments')
        for key in ('parameters', 'exclude'):
            patterns = self.config.get(key, [])
            if not isinstance(patterns, list) or any(not isinstance(p, str) or not p for p in patterns):
                raise ValueError(key + ' must be a list of parameter name patterns')
        from qcodes.instrument import Instrument
        cls = getattr(importlib.import_module(module), class_name)
        if not (isinstance(cls, type) and issubclass(cls, Instrument)):
            raise ValueError(target + ' is not a QCoDeS instrument class')
        if 'address' in self.config:
            arguments = {'address': self.config['address'], **arguments}
        # QCoDeS names are identifiers and unique within one process.
        name = re.sub(r'\W', '_', self.id)
        if not name.isidentifier():
            name = 'iop_' + name
        self.instrument = cls(name, **arguments)
        self.parameters = self._collect()
        if not self.parameters:
            raise ValueError('No QCoDeS parameters remain after the parameters and exclude patterns')

    def _collect(self):
        from qcodes.instrument import InstrumentModule
        include, exclude = self.config.get('parameters', ['*']), self.config.get('exclude', [])
        found = {}

        def walk(node, prefix):
            for key, parameter in node.parameters.items():
                path = prefix + key
                if (any(fnmatch.fnmatchcase(path, p) for p in include)
                        and not any(fnmatch.fnmatchcase(path, p) for p in exclude)):
                    found[path] = parameter
            for key, module in node.submodules.items():
                # Channel lists repeat channels that are also registered individually.
                if isinstance(module, InstrumentModule):
                    walk(module, prefix + key + '.')
        walk(self.instrument, '')
        return found

    def describe(self):
        from qcodes.parameters import ArrayParameter, MultiParameter, ParameterWithSetpoints
        operations = {'snapshot': {'description': 'Read the QCoDeS snapshot of cached parameter values', 'effect': 'observe',
                                   'parameters': {}}}
        for path, parameter in self.parameters.items():
            unit = getattr(parameter, 'unit', '') or ''
            label = getattr(parameter, 'label', '') or path
            if parameter.gettable:
                result = {'unit': unit} if unit else {}
                if isinstance(parameter, (ArrayParameter, ParameterWithSetpoints)):
                    result['type'] = 'array'
                elif isinstance(parameter, MultiParameter):
                    result['type'] = 'object'
                else:
                    result.update({k: v for k, v in field(parameter.vals).items() if k in ('type', 'enum')})
                operations['get.' + path] = {'description': 'Read ' + label, 'effect': 'observe', 'parameters': {},
                                             'result': result}
            if parameter.settable and not isinstance(parameter, (ArrayParameter, MultiParameter)):
                operations['set.' + path] = {'description': 'Set ' + label, 'parameters': {'value': field(parameter.vals, unit)}}
        description = {'name': self.config.get('name', self.id), 'mode': 'hardware', 'operations': operations,
                       'connection': {k: self.config[k] for k in ('instrument', 'address') if k in self.config}}
        # VISA drivers read *IDN? when they connect; use that cached reply without querying again.
        idn = self.instrument.parameters.get('IDN')
        identity = idn.cache.get(get_if_invalid=False) if idn is not None else None
        if isinstance(identity, dict):
            description['identity'] = {k: str(identity[k]) for k in ('vendor', 'model', 'serial', 'firmware') if identity.get(k)}
        return description

    def invoke(self, operation, arguments, cancelled):
        from qcodes.parameters import ArrayParameter, MultiParameter, ParameterWithSetpoints
        if operation == 'snapshot':
            return {'snapshot': self.instrument.snapshot(update=False), 'time': now(), 'source': 'driver'}
        kind, _, path = operation.partition('.')
        parameter = self.parameters[path]
        unit = getattr(parameter, 'unit', '') or ''
        name = operation.replace('.', '-')
        if kind == 'set':
            parameter.set(arguments['value'])
            return {'value': arguments['value'], **({'unit': unit} if unit else {}), 'time': now(), 'source': 'driver'}
        value = parameter.get()
        result = {'time': now(), 'source': 'device'}
        if isinstance(parameter, ParameterWithSetpoints):
            axes = [{'name': sp.short_name, **({'unit': sp.unit} if sp.unit else {})} for sp in parameter.setpoints]
            result.update(array_fields(value, cancelled, name, unit, axes))
            result['setpoints'] = [array_fields(sp.get(), cancelled, name + '-' + sp.short_name, sp.unit)
                                   for sp in parameter.setpoints]
        elif isinstance(parameter, ArrayParameter):
            axes = [{'name': n, **({'unit': u} if u else {})}
                    for n, u in zip(parameter.setpoint_names or (), parameter.setpoint_units or ())]
            result.update(array_fields(value, cancelled, name, unit, axes))
        elif isinstance(parameter, MultiParameter):
            result['items'] = {}
            for item, item_value, item_unit in zip(parameter.names, value, parameter.units):
                if getattr(item_value, 'ndim', 0):
                    result['items'][item] = array_fields(item_value, cancelled, name + '-' + item, item_unit)
                else:
                    result['items'][item] = {'value': plain(item_value), **({'unit': item_unit} if item_unit else {})}
        elif getattr(value, 'ndim', 0):
            result.update(array_fields(value, cancelled, name, unit))
        else:
            result['value'] = plain(value)
            if unit:
                result['unit'] = unit
        return result

    def close(self):
        instrument = getattr(self, 'instrument', None)
        if instrument is not None:
            instrument.close()
            self.instrument = None

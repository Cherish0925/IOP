"""Python instrument libraries and vendor SDKs, used from a description.

``driver: "python"`` creates one object of an installed library, such as a pylablib, PyMeasure or
InstrumentKit device or a vendor SDK class, and binds named operations to its methods and
attributes. The description states the calls, their parameters, ranges and units; no driver class
is written. Every call to the object runs on one thread, as COM and DLL based SDKs require.
"""
import base64
import dataclasses
import datetime
import enum
import importlib
import inspect
import queue
import threading
import time
from concurrent.futures import CancelledError, Future
from . import arrays
from .drivers import Driver, UnconfirmedChange
from .store import now


def path(value, what):
    """A dotted attribute path; numbers index sequences. Private names are not reachable."""
    if not isinstance(value, str) or not value or any(not part or part.startswith('_') for part in value.split('.')):
        raise ValueError(what + ' is a dotted name such as stage.move_to, without private parts')
    return value.split('.')


def walk(target, parts):
    for part in parts:
        target = target[int(part)] if part.isdigit() else getattr(target, part)
    return target


def plain(value):
    """JSON form of what a library returned."""
    if hasattr(value, 'item') and getattr(value, 'shape', None) == ():
        value = value.item()
    if isinstance(value, enum.Enum):
        return value.name
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, complex):
        return {'re': value.real, 'im': value.imag}
    if isinstance(value, (bytes, bytearray)):
        return {'base64': base64.b64encode(value).decode()}
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if hasattr(value, '_asdict'):
        value = value._asdict()
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = {item.name: getattr(value, item.name) for item in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(item) for item in value]
    if hasattr(value, 'tolist') and getattr(value, 'ndim', 0):
        return plain(value.tolist())
    return str(value)


def numeric_array(value):
    return getattr(value, 'ndim', 0) and getattr(getattr(value, 'dtype', None), 'kind', '') in 'biufc'


def check(name, spec):
    """Validate one operation of a description."""
    if not isinstance(spec, dict):
        raise ValueError(f'Operation {name}: describe it with an object')
    kinds = [key for key in ('call', 'get', 'set') if key in spec]
    if len(kinds) != 1:
        raise ValueError(f'Operation {name}: give exactly one of call, get or set')
    path(spec[kinds[0]], f'Operation {name}: {kinds[0]}')
    parameters = spec.get('parameters', {})
    if not isinstance(parameters, dict):
        raise ValueError(f'Operation {name}: parameters must be an object')
    if kinds[0] == 'get' and parameters or kinds[0] == 'set' and len(parameters) != 1:
        raise ValueError(f'Operation {name}: get takes no parameter and set takes exactly one')
    positional = spec.get('positional', [])
    if not isinstance(positional, list) or len(set(positional)) != len(positional) or set(positional) - set(parameters):
        raise ValueError(f'Operation {name}: positional lists parameters of the operation in call order')
    fixed = spec.get('fixed', {})
    if not isinstance(fixed, dict) or set(fixed) & set(parameters):
        raise ValueError(f'Operation {name}: fixed gives constant keyword arguments that are not parameters')
    if (positional or fixed) and kinds[0] != 'call':
        raise ValueError(f'Operation {name}: positional and fixed belong to a call')
    completion = spec.get('completion')
    if completion is not None:
        if not isinstance(completion, dict) or ('call' in completion) == ('get' in completion):
            raise ValueError(f'Operation {name}: completion reads one call or get until it equals a value')
        path(completion.get('call', completion.get('get')), f'Operation {name}: completion')
        for key, default in (('interval', 0.1), ('timeout', 30)):
            number = completion.get(key, default)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or number <= 0:
                raise ValueError(f'Operation {name}: completion {key} must be positive')
    if 'on_cancel' in spec:
        if not isinstance(spec['on_cancel'], dict) or set(spec['on_cancel']) != {'call'}:
            raise ValueError(f'Operation {name}: on_cancel names one call, such as stop')
        path(spec['on_cancel']['call'], f'Operation {name}: on_cancel')
    result = spec.get('result', {})
    items = result.get('items', {}) if isinstance(result, dict) else None
    if not isinstance(items, dict):
        raise ValueError(f'Operation {name}: result is an object; items names several returned values')
    for item in (result, *items.values()):
        if not isinstance(item, dict) or not isinstance(item.get('path', []), list) or not isinstance(item.get('unit', ''), str):
            raise ValueError(f'Operation {name}: a result has a unit and an optional path into the returned value')


class Caller:
    """Runs every call to one object on the same thread."""
    def __init__(self, name):
        self.tasks = queue.Queue()
        threading.Thread(target=self.run, name='iop-python-' + name, daemon=True).start()

    def run(self):
        while True:
            task = self.tasks.get()
            if task is None:
                return
            future, function = task
            try:
                future.set_result(function())
            except BaseException as error:
                future.set_exception(error)

    def call(self, function):
        future = Future()
        self.tasks.put((future, function))
        return future.result()

    def stop(self):
        self.tasks.put(None)


class PythonObject(Driver):
    """Config: object ("module:Class" or a factory), arguments, keywords, context, close, operations
    and unconfirmed_change."""

    def __init__(self, *args):
        super().__init__(*args)
        module, separator, attribute = str(self.config.get('object', '')).partition(':')
        if not separator or not module or not attribute:
            raise ValueError('Set object to the library class or factory as module:Class')
        self.factory = module, path(attribute, 'object')
        if not isinstance(self.config.get('arguments', []), list) or not isinstance(self.config.get('keywords', {}), dict):
            raise ValueError('arguments is a list and keywords an object, passed when the object is created')
        operations = self.config.get('operations')
        if not isinstance(operations, dict) or not operations:
            raise ValueError('Describe the operations that call the object')
        for name, spec in operations.items():
            check(name, spec)
        self.unconfirmed = self.config.get('unconfirmed_change', 'fail')
        if self.unconfirmed not in ('fail', 'hold'):
            raise ValueError('unconfirmed_change must be fail or hold')
        if 'close' in self.config:
            path(self.config['close'], 'close')
        self.device = self.manager = None
        self.caller = Caller(self.id)
        try:
            self.caller.call(self._create)
        except BaseException:
            self.caller.stop()
            raise

    def _create(self):
        factory = walk(importlib.import_module(self.factory[0]), self.factory[1])
        device = factory(*self.config.get('arguments', []), **self.config.get('keywords', {})) if callable(factory) else factory
        if self.config.get('context'):
            self.manager, device = device, device.__enter__()
        self.device = device

    def describe(self):
        operations = {}
        for name, spec in self.config['operations'].items():
            operations[name] = dict(spec)
            if 'get' in spec:
                operations[name].setdefault('effect', 'observe')
            operations[name].setdefault('parameters', {})
        return {'name': self.config.get('name', self.id), 'mode': 'hardware', 'operations': operations,
                'connection': {'object': self.config['object'], 'unconfirmed_change': self.unconfirmed}}

    def invoke(self, operation, arguments, cancelled):
        if self.device is None:
            raise ConnectionError('The object is closed; reconnect the device')
        spec = self.config['operations'][operation]
        return self.caller.call(lambda: self._run(operation, spec, arguments, cancelled))

    def _read(self, spec):
        target = walk(self.device, path(spec.get('call', spec.get('get')), 'completion'))
        return target() if 'call' in spec else target

    def _run(self, operation, spec, arguments, cancelled):
        if cancelled is not None and cancelled.is_set():
            raise CancelledError(f'{operation} cancelled before invocation')
        change = spec.get('effect', 'observe' if 'get' in spec else 'change') != 'observe'
        started = False
        stopped = False
        try:
            if 'get' in spec:
                value = walk(self.device, path(spec['get'], 'get'))
            elif 'set' in spec:
                *parents, attribute = path(spec['set'], 'set')
                owner = walk(self.device, parents)
                started = True
                setattr(owner, attribute, next(iter(arguments.values())))
                value = None
            else:
                function = walk(self.device, path(spec['call'], 'call'))
                order = spec.get('positional', [])
                leading = []
                for name in order:
                    if name not in arguments:
                        break
                    leading.append(arguments[name])
                keywords = {name: value for name, value in arguments.items() if name not in order[:len(leading)]}
                keywords.update(spec.get('fixed', {}))
                try:
                    signature = inspect.signature(function)
                except (TypeError, ValueError):
                    signature = None  # Some native SDK methods do not expose a Python signature.
                if signature is not None:
                    signature.bind(*leading, **keywords)
                started = True
                value = function(*leading, **keywords)
            completion = spec.get('completion')
            if completion:
                deadline = time.monotonic() + completion.get('timeout', 30)
                while plain(self._read(completion)) != completion.get('equals', True):
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f'{operation} did not complete within {completion.get("timeout", 30):g} s')
                    if cancelled is not None and cancelled.wait(completion.get('interval', 0.1)):
                        if 'on_cancel' in spec:
                            walk(self.device, path(spec['on_cancel']['call'], 'on_cancel'))()
                            stopped = plain(self._read(completion)) == completion.get('equals', True)
                        raise CancelledError(f'{operation} cancelled')
        except Exception as error:
            if isinstance(error, CancelledError) and stopped:
                raise
            # Exceptions inside an SDK may follow an action, including TypeError and ValueError.
            # Argument binding above happens before the method starts and never holds the device.
            if change and started and self.unconfirmed == 'hold':
                raise UnconfirmedChange(f'{operation} raised {type(error).__name__}: {error}') from error
            raise
        result = spec.get('result', {})
        fields = {'time': now(), 'source': 'driver' if value is None else 'device'}
        if 'items' in result:
            fields['items'] = {name: self._value(value, item, cancelled, f'{operation}-{name}')
                               for name, item in result['items'].items()}
        elif value is not None:
            fields.update(self._value(value, result, cancelled, operation))
        return fields

    @staticmethod
    def _value(value, result, context, name):
        for key in result.get('path', []):
            value = value[key]
        unit = result.get('unit', '')
        if numeric_array(value):
            options = {key: result[key] for key in ('file', 'inline_limit') if key in result}
            return arrays.ndarray(value, context, name.replace('.', '-'), unit, result.get('axes'), **options)
        return {'value': plain(value), **({'unit': unit} if unit else {})}

    def close(self):
        if self.device is None:
            return

        def release():
            device, self.device = self.device, None
            if self.manager is not None:
                self.manager.__exit__(None, None, None)
            elif 'close' in self.config or callable(getattr(device, 'close', None)):
                walk(device, path(self.config.get('close', 'close'), 'close'))()
        try:
            self.caller.call(release)
        finally:
            self.caller.stop()

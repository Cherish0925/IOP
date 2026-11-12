"""EPICS Channel Access: process variables of IOCs at beamlines, accelerators, laser facilities and labs.

Each listed PV becomes ``get.<name>`` (observe) and, with write access, ``set.<name>``. Units,
control limits and enumeration strings come from the IOC, so the Runtime checks values before a put
is sent. A put waits for put-completion: a motor's set returns when the move has finished.
"""
import struct
import threading
import time
from concurrent.futures import CancelledError
from datetime import datetime, timezone
from . import arrays
from .drivers import Driver, UnconfirmedChange

# Native Channel Access types: (parameter type, dtype, minimum, maximum).
TYPES = {'STRING': ('string', None, None, None), 'INT': ('integer', 'int16', -2**15, 2**15 - 1),
         'FLOAT': ('number', 'float32', -3.4028234663852886e38, 3.4028234663852886e38),
         'ENUM': ('integer', 'uint16', 0, 2**16 - 1), 'CHAR': ('integer', 'uint8', 0, 255),
         'LONG': ('integer', 'int32', -2**31, 2**31 - 1), 'DOUBLE': ('number', 'float64', None, None)}
PACK = {'int16': 'h', 'float32': 'f', 'uint16': 'H', 'uint8': 'B', 'int32': 'i', 'float64': 'd'}


def text(value):
    if isinstance(value, bytes):
        try:
            return value.decode()
        except UnicodeDecodeError:
            return value.decode('latin-1')
    return str(value)


def plain(data, kind):
    values = data.tolist() if hasattr(data, 'tolist') else list(data)
    return [text(v) for v in values] if kind == 'STRING' else values


class EPICS(Driver):
    """Config: pvs (names, or objects with name, wait and put_timeout), optional prefix, timeout,
    put_timeout and unconfirmed_change."""

    def __init__(self, *args):
        super().__init__(*args)
        prefix = self.config.get('prefix', '')
        if not isinstance(prefix, str):
            raise ValueError('prefix must be text')
        entries = self.config.get('pvs')
        if not isinstance(entries, list) or not entries:
            raise ValueError('List the process variables in pvs')
        self.timeout = float(self.config.get('timeout', 5))
        default_put = float(self.config.get('put_timeout', 300))
        if not 0 < self.timeout <= 300 or not 0 < default_put <= 86400:
            raise ValueError('timeout must be up to 300 s and put_timeout up to 86400 s')
        self.unconfirmed = self.config.get('unconfirmed_change', 'fail')
        if self.unconfirmed not in ('fail', 'hold'):
            raise ValueError('unconfirmed_change must be fail or hold')
        self.pvs = {}
        for entry in entries:
            entry = {'name': entry} if isinstance(entry, str) else entry
            if not isinstance(entry, dict) or not isinstance(entry.get('name'), str) or not entry['name']:
                raise ValueError('Each pvs entry is a name or an object with name')
            if entry['name'] in self.pvs:
                raise ValueError('PV listed twice: ' + entry['name'])
            wait = entry.get('wait', True)
            if not isinstance(wait, bool):
                raise ValueError(entry['name'] + ': wait must be true or false')
            self.pvs[entry['name']] = {'full': prefix + entry['name'], 'wait': wait,
                                       'put_timeout': float(entry.get('put_timeout', default_put))}
        from caproto.threading.client import Context
        # Each device has its own context and search client; closing the device closes both.
        self.context = Context(timeout=self.timeout)
        try:
            self._connect()
        except BaseException:
            self.close()
            raise

    def _connect(self):
        from caproto import ChannelType
        names = list(self.pvs)
        channels = self.context.get_pvs(*(self.pvs[n]['full'] for n in names), timeout=self.timeout)
        missing = []
        for name, channel in zip(names, channels):
            try:
                channel.wait_for_connection(timeout=self.timeout)
            except Exception:
                missing.append(self.pvs[name]['full'])
            self.pvs[name]['channel'] = channel
        if missing:
            raise ValueError('No IOC answered for ' + ', '.join(missing) +
                             '; check the names and EPICS_CA_ADDR_LIST of the service')
        for name in names:
            entry = self.pvs[name]
            channel = entry['channel']
            kind = ChannelType(channel.channel.native_data_type).name
            control = channel.read(data_type='control', timeout=self.timeout).metadata
            entry.update(kind=kind, count=channel.channel.native_data_count,
                         unit=text(getattr(control, 'units', b'') or b''),
                         labels=[text(s) for s in getattr(control, 'enum_strings', ()) or ()],
                         writable=bool(int(channel.access_rights) & 2))
            low, high = getattr(control, 'lower_ctrl_limit', 0), getattr(control, 'upper_ctrl_limit', 0)
            entry['limits'] = (low, high) if kind not in ('STRING', 'ENUM') and low < high else None

    def describe(self):
        operations = {}
        scalars = [name for name, entry in self.pvs.items() if entry['count'] == 1]
        if scalars:
            operations['snapshot'] = {'description': 'Read every scalar PV together', 'effect': 'observe', 'parameters': {}}
        for name, entry in self.pvs.items():
            result = {'unit': entry['unit']} if entry['unit'] else {}
            operations['get.' + name] = {'effect': 'observe', 'parameters': {}, **({'result': result} if result else {})}
            if entry['writable']:
                operations['set.' + name] = {'parameters': {'value': self._field(entry)}}
                if entry['wait']:
                    operations['set.' + name]['description'] = 'Put and wait for completion'
        return {'name': self.config.get('name', self.id), 'mode': 'hardware', 'operations': operations,
                'connection': {'prefix': self.config.get('prefix', ''), 'unconfirmed_change': self.unconfirmed}}

    @staticmethod
    def _field(entry):
        kind, _, low, high = TYPES[entry['kind']]
        spec = {'type': kind}
        if entry['kind'] == 'ENUM' and entry['labels']:
            spec['enum'] = list(range(len(entry['labels'])))
            spec['description'] = ', '.join(f'{i}: {label}' for i, label in enumerate(entry['labels']))
        elif low is not None:
            spec.update(minimum=low, maximum=high)
        if entry['limits']:
            spec['minimum'] = max(spec.get('minimum', entry['limits'][0]), entry['limits'][0])
            spec['maximum'] = min(spec.get('maximum', entry['limits'][1]), entry['limits'][1])
        if entry['count'] > 1:
            spec = {'type': 'array', 'items': spec, 'maxItems': entry['count']}
        if entry['unit']:
            spec['unit'] = entry['unit']
        return spec

    def invoke(self, operation, arguments, cancelled):
        kind, _, name = operation.partition('.')
        if operation == 'snapshot':
            return self._snapshot()
        entry = self.pvs[name]
        if kind == 'set':
            return self._put(operation, entry, arguments['value'], cancelled)
        try:
            response = entry['channel'].read(data_type='time', timeout=self.timeout)
        except Exception as error:
            raise RuntimeError(f'{operation}: no reply from the IOC ({type(error).__name__} {error})') from None
        return self._reading(operation, entry, response, cancelled)

    def _reading(self, operation, entry, response, context):
        from caproto import AlarmSeverity, AlarmStatus
        metadata = response.metadata
        severity = AlarmSeverity(metadata.severity)
        if severity == AlarmSeverity.INVALID_ALARM:
            raise RuntimeError(f'{operation}: the IOC marks the value INVALID ({AlarmStatus(metadata.status).name})')
        values = plain(response.data, entry['kind'])
        unit = {'unit': entry['unit']} if entry['unit'] else {}
        dtype = TYPES[entry['kind']][1]
        if entry['count'] > 1 and dtype and entry['kind'] != 'ENUM':
            payload = struct.pack('<' + str(len(values)) + PACK[dtype], *values)
            result = arrays.result(payload, arrays.check_result({'dtype': dtype, 'byte_order': 'little', **unit}),
                                   context, operation.replace('.', '-').replace(':', '-'))
        else:
            value = values[0] if len(values) == 1 else values
            result = {'value': value, **unit}
            if entry['kind'] == 'ENUM' and isinstance(value, int) and value < len(entry['labels']):
                result['label'] = entry['labels'][value]
        if severity != AlarmSeverity.NO_ALARM:
            result['alarm'] = {'severity': severity.name, 'status': AlarmStatus(metadata.status).name}
        result['time'] = datetime.fromtimestamp(metadata.timestamp, timezone.utc).isoformat()
        result['source'] = 'device'
        return result

    def _put(self, operation, entry, value, cancelled):
        done = threading.Event()
        replies = []

        def completed(response):
            replies.append(response)
            done.set()
        data = value if isinstance(value, list) else [value]
        try:
            # Nothing has been sent when this raises: the channel was not connected.
            entry['channel'].write(data, wait=False, callback=completed if entry['wait'] else None,
                                   notify=entry['wait'], timeout=self.timeout)
        except Exception as error:
            raise RuntimeError(f'{operation}: not sent ({type(error).__name__} {error})') from None
        if entry['wait']:
            deadline = time.monotonic() + entry['put_timeout']
            while not done.wait(0.1):
                expired = time.monotonic() > deadline
                if expired or (cancelled is not None and cancelled.is_set()):
                    reason = f'no completion after {entry["put_timeout"]:g} s' if expired else 'stopped waiting'
                    message = f'{operation}: {reason}; the record may still be processing'
                    if self.unconfirmed == 'hold':
                        raise UnconfirmedChange(message)
                    raise RuntimeError(message) if expired else CancelledError(message)
            status = getattr(replies[0], 'status', None)
            if status is not None and getattr(status, 'success', 1) != 1:
                raise RuntimeError(f'{operation}: the IOC refused the put ({getattr(status, "name", status)})')
        return {'value': value, **({'unit': entry['unit']} if entry['unit'] else {}),
                'time': datetime.now(timezone.utc).isoformat(), 'source': 'driver',
                **({'completed': True} if entry['wait'] else {})}

    def _snapshot(self):
        names = [name for name, entry in self.pvs.items() if entry['count'] == 1]
        replies, done, lock = {}, threading.Event(), threading.Lock()

        def receive(name):
            def callback(response):
                with lock:
                    replies[name] = response
                    if len(replies) == len(names):
                        done.set()
            return callback
        for name in names:
            self.pvs[name]['channel'].read(data_type='time', wait=False, callback=receive(name))
        if not done.wait(self.timeout):
            missing = sorted(set(names) - set(replies))
            raise RuntimeError('snapshot: no reply for ' + ', '.join(missing))
        values = {}
        for name in names:
            data = plain(replies[name].data, self.pvs[name]['kind'])
            values[name] = data[0] if len(data) == 1 else data
        return {'variables': values, 'time': datetime.now(timezone.utc).isoformat(), 'source': 'device'}

    def close(self):
        context = getattr(self, 'context', None)
        if context is not None:
            try:
                context.disconnect()
            except Exception:
                pass
            self.context = None

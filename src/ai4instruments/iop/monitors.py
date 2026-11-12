"""Periodic observation of instruments between experiments, with optional resource holds.

A monitor reads one observation operation of a device at a fixed interval and compares one value
with its limits. Changes of state are recorded as events. With ``on_violation: "hold"``, a value
outside its limits, or a failed read, holds the device and any listed resources once, when the state
changes: experiments using them stop, and they return to use only after an observation is recorded
through ``/v1/resources/resolve``. The condition can then be corrected; a new violation after a
return to ok holds again. This is a software stop; hardware interlocks remain on the equipment.
"""
import math
import threading
import time
import uuid
from . import platforms
from .drivers import parameters
from .store import now


def check(record, description):
    monitors = record.get('monitors', [])
    if not isinstance(monitors, list):
        raise ValueError('monitors must be a list')
    checked, seen = [], set()
    for monitor in monitors:
        if not isinstance(monitor, dict):
            raise ValueError('Each monitor is an object')
        mid = platforms.name(monitor.get('id'), 'Monitor ID')
        if mid in seen:
            raise ValueError('Duplicate monitor ID: ' + mid)
        seen.add(mid)
        operation = monitor.get('operation')
        spec = description['operations'].get(operation)
        if spec is None:
            raise ValueError(f'Monitor {mid}: unknown operation {operation}')
        if spec.get('effect') != 'observe':
            raise ValueError(f'Monitor {mid}: {operation} must be described with effect "observe"')
        interval = monitor.get('interval_s')
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not interval >= 0.1:
            raise ValueError(f'Monitor {mid}: interval_s must be at least 0.1')
        path = monitor.get('value', ['value'])
        if not isinstance(path, list) or any(isinstance(p, bool) or not isinstance(p, (str, int)) for p in path):
            raise ValueError(f'Monitor {mid}: value is a list of result keys or indices')
        for key in ('minimum', 'maximum'):
            if key in monitor and (isinstance(monitor[key], bool) or not isinstance(monitor[key], (int, float))
                                   or not math.isfinite(monitor[key])):
                raise ValueError(f'Monitor {mid}: {key} must be a finite number')
        if not {'minimum', 'maximum', 'equals'} & monitor.keys():
            raise ValueError(f'Monitor {mid}: set minimum, maximum or equals')
        action = monitor.get('on_violation', 'record')
        if action not in ('record', 'hold'):
            raise ValueError(f'Monitor {mid}: on_violation must be record or hold')
        hold = monitor.get('hold', [])
        if hold:
            platforms.strings(hold, f'Monitor {mid} hold')
        checked.append({**monitor, 'arguments': parameters(spec, monitor.get('arguments', {})),
                        'value': path, 'on_violation': action, 'hold': list(hold), 'interval_s': float(interval)})
    return checked


def evaluate(monitor, result):
    """Return (value, violation) for a completed read; a missing or non-numeric value is a violation."""
    value = result
    for key in monitor['value']:
        value = value[key]
    violation = 'equals' in monitor and value != monitor['equals']
    for key, outside in (('minimum', lambda v, limit: v < limit), ('maximum', lambda v, limit: v > limit)):
        if key in monitor:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'Monitored value {value!r} is not a finite number')
            violation = violation or outside(value, monitor[key])
    return value, violation


class Monitors:
    def __init__(self, engine):
        self.engine = engine
        self.definitions = {}
        self.state = {}
        self.stopping = threading.Event()
        self.changed = threading.Event()
        self.thread = None

    def configure(self, device_id, monitors):
        """Replace a device's monitors. Called with the engine lock held."""
        self.definitions[device_id] = monitors
        names = {m['id'] for m in monitors}
        for key in [k for k in self.state if k[0] == device_id and k[1] not in names]:
            del self.state[key]
        for monitor in monitors:
            self.state.setdefault((device_id, monitor['id']), {'state': 'pending', 'due': 0.0, 'reading': False})
        self.changed.set()

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, name='iop-monitors', daemon=True)
            self.thread.start()

    def stop(self):
        self.stopping.set()
        self.changed.set()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join()

    def summary(self, only=None):
        with self.engine.lock:
            selected = sorted(self.definitions.items()) if only is None else [(only, self.definitions.get(only, []))]
            rows = []
            for device, monitors in selected:
                for monitor in monitors:
                    state = self.state[(device, monitor['id'])]
                    rows.append({'device': device, 'id': monitor['id'], 'operation': monitor['operation'],
                                 'on_violation': monitor['on_violation'],
                                 **{k: state[k] for k in ('state', 'value', 'time', 'error') if k in state}})
            return rows

    def _loop(self):
        # Sleep until the next reading is due, a definition changes or a reading ends.
        while not self.stopping.is_set():
            # Clear before reading the state, so a change after this point ends the next wait.
            self.changed.clear()
            with self.engine.lock:
                if self.engine.stopping:
                    return
                waiting = [(device, m) for device, monitors in self.definitions.items() for m in monitors
                           if not self.state[(device, m['id'])]['reading']]
                for device, monitor in waiting:
                    if self.state[(device, monitor['id'])]['due'] <= time.monotonic():
                        self._start(device, monitor)
                due = [self.state[(d, m['id'])]['due'] for d, m in waiting if not self.state[(d, m['id'])]['reading']]
            self.changed.wait(max(0.01, min(due) - time.monotonic()) if due else None)

    def _start(self, device, monitor):
        engine, state = self.engine, self.state[(device, monitor['id'])]
        state['due'] = time.monotonic() + monitor['interval_s']
        key = 'device:' + device
        if device not in engine.devices or device in engine.offline or device in engine.released or device in engine.reconnecting:
            state['state'] = 'unavailable'
            return
        # A monitor reads only an idle device; an experiment that reserves it reads it itself.
        if engine.allocation.used[key] >= engine.allocation.limit(key):
            if state['state'] == 'pending':
                state['state'] = 'skipped'
            return
        claim = 'monitor:' + uuid.uuid4().hex
        engine.allocation.claim(claim, None, {key: 1})
        state['reading'] = True
        driver = engine.devices[device]

        def read():
            try:
                outcome = (driver.invoke(monitor['operation'], dict(monitor['arguments']), threading.Event()), None)
            except Exception as error:
                outcome = (None, error)
            self._finish(device, monitor, claim, *outcome)
        engine.pool.submit(read)

    def _finish(self, device, monitor, claim, result, failure):
        engine = self.engine
        with engine.lock:
            engine.allocation.release(claim)
            state = self.state.get((device, monitor['id']))
            if state is None:
                return
            state['reading'] = False
            state['time'] = now()
            self.changed.set()
            try:
                if failure is not None:
                    raise failure
                value, violation = evaluate(monitor, result)
                state.update(value=value, error=None)
                current = 'violation' if violation else 'ok'
            except Exception as error:
                state.update(value=None, error=f'{type(error).__name__}: {error}')
                current = 'error'
            previous = state['state']
            state['state'] = current
            if current != previous and not (previous in ('pending', 'skipped', 'unavailable') and current == 'ok'):
                with engine.store.transaction():
                    engine.store.event('monitor.' + current, device_id=device, monitor=monitor['id'],
                                       value=state['value'], error=state['error'])
            # Hold once on entering a violation or failed read; after the hold is resolved,
            # the device stays usable so the condition can be corrected. A later return to
            # ok followed by a new violation holds again.
            entering = current in ('violation', 'error') and previous not in ('violation', 'error')
            if entering:
                # What was verified with this device is verified again before experiments rely on it.
                engine.conditions.disturbed(device, f'monitor {device}.{monitor["id"]} left its limits')
            if entering and monitor['on_violation'] == 'hold':
                origin = f'monitor:{device}.{monitor["id"]}'
                if not any(h.get('operation_id') == origin for h in engine.resource_holds.values()):
                    resources = {'device:' + device} | {'device:' + r if r in engine.devices else 'shared:' + r
                                                        for r in monitor['hold']}
                    reason = (f'Monitor {monitor["id"]} read {state["value"]!r}' if current == 'violation'
                              else f'Monitor {monitor["id"]} could not read: {state["error"]}')
                    engine.store.hold_resources(sorted(resources), origin, reason)
                    engine.resource_holds = engine.store.resource_holds()
                    engine.wake.set()

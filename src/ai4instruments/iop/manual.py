"""Steps a person performs, scheduled and recorded like instrument operations.

Each operation carries an instruction. When it runs, it waits as a task until a person reports it
``done`` with the requested readings, ``failed``, or ``unsure``. An unsure change is recorded like an
unconfirmed instrument action: the device and the experiment's reserved resources are held until
what was observed is recorded.
"""
import string
import threading
import uuid
from concurrent.futures import CancelledError
from .drivers import Driver, UnconfirmedChange, parameters
from .store import now


class Manual(Driver):
    # Waiting for a person must not occupy the worker pool that serves instruments.
    dedicated_thread = True

    def __init__(self, *args):
        super().__init__(*args)
        operations = self.config.get('operations')
        if not isinstance(operations, dict) or not operations:
            raise ValueError('Describe the manual steps as operations')
        for name, spec in operations.items():
            if not isinstance(spec, dict) or not isinstance(spec.get('instruction'), str) or not spec['instruction'].strip():
                raise ValueError(f'Manual step {name}: give an instruction')
            described = spec.get('parameters', {})
            for _, field, _, _ in string.Formatter().parse(spec['instruction']):
                if field is not None and field not in described:
                    raise ValueError(f'Manual step {name}: describe instruction parameter {field}')
            readings = spec.get('readings', {})
            if not isinstance(readings, dict) or any(not isinstance(r, dict) for r in readings.values()):
                raise ValueError(f'Manual step {name}: readings describe each requested value')
        self.lock = threading.Lock()
        self.tasks = {}

    def describe(self):
        return {'name': self.config.get('name', self.id), 'mode': 'manual', 'operations': self.config['operations']}

    def pending(self):
        with self.lock:
            return [dict(entry['task']) for entry in self.tasks.values() if entry['outcome'] is None]

    def report(self, operation_id, report):
        if not isinstance(report, dict):
            raise ValueError('Report status and readings as an object')
        status = report.get('status')
        if status not in ('done', 'failed', 'unsure'):
            raise ValueError('status must be done, failed or unsure')
        for key in ('note', 'by'):
            if not isinstance(report.get(key, ''), str):
                raise ValueError(key + ' must be text')
        with self.lock:
            entry = self.tasks.get(operation_id)
            if entry is None or entry['outcome'] is not None:
                raise KeyError(operation_id)
            readings = report.get('readings', {})
            if status == 'done':
                readings = parameters({'parameters': entry['task']['readings']}, readings)
            elif not isinstance(readings, dict):
                raise ValueError('readings must be an object')
            entry['outcome'] = {'status': status, 'readings': readings, 'note': report.get('note', ''),
                                'by': report.get('by', ''), 'time': now()}
            entry['done'].set()
        return {'operation_id': operation_id, 'status': status}

    def invoke(self, operation, arguments, cancelled):
        spec = self.config['operations'][operation]
        identifier = getattr(cancelled, 'operation_id', None) or 'manual-' + uuid.uuid4().hex
        task = {'operation_id': identifier, 'run_id': getattr(cancelled, 'run_id', None), 'device': self.id,
                'operation': operation, 'instruction': spec['instruction'].format_map(arguments),
                'arguments': arguments, 'readings': spec.get('readings', {}), 'created': now()}
        entry = {'task': task, 'done': threading.Event(), 'outcome': None}
        with self.lock:
            self.tasks[identifier] = entry
        try:
            while not entry['done'].wait(0.2):
                if cancelled.is_set():
                    raise CancelledError('Manual step cancelled')
        finally:
            with self.lock:
                self.tasks.pop(identifier, None)
        outcome = entry['outcome']
        detail = (outcome['note'] or 'no note') + (f" ({outcome['by']})" if outcome['by'] else '')
        if outcome['status'] == 'failed':
            raise RuntimeError('Reported not done: ' + detail)
        if outcome['status'] == 'unsure':
            if spec.get('effect', 'change') == 'observe':
                raise RuntimeError('Reported without a reading: ' + detail)
            raise UnconfirmedChange('Reported unsure whether the step took effect: ' + detail)
        return {'instruction': task['instruction'], 'readings': outcome['readings'], 'note': outcome['note'],
                'by': outcome['by'], 'time': outcome['time'], 'source': 'person'}

    def close(self):
        with self.lock:
            for entry in self.tasks.values():
                if entry['outcome'] is None:
                    entry['outcome'] = {'status': 'unsure', 'readings': {}, 'note': 'Service stopped while waiting',
                                        'by': '', 'time': now()}
                    entry['done'].set()


def tasks(engine):
    with engine.lock:
        drivers = [d for d in engine.devices.values() if isinstance(d, Manual)]
    return sorted((task for driver in drivers for task in driver.pending()), key=lambda t: t['created'])


def report(engine, operation_id, body):
    with engine.lock:
        drivers = [d for d in engine.devices.values() if isinstance(d, Manual)]
    for driver in drivers:
        try:
            return driver.report(operation_id, body)
        except KeyError:
            continue
    raise KeyError(operation_id)

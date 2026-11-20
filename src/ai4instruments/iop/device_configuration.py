"""Replace idle instrument configurations and retain their previous definitions."""
import copy
import fnmatch
import math
from .config import save_json
from .drivers import make_driver, span
from .errors import RequestError


METADATA = ('identity', 'identity_source', 'instrument_profile', 'documentation', 'calibration')


def allowed(record, description):
    """Keep only the operations a device record allows, with their numeric arguments within the limits it sets;
    identify stays available for discovery."""
    patterns = record.get('allowed_operations')
    if patterns is not None:
        if not isinstance(patterns, list) or any(not isinstance(p, str) or not p for p in patterns):
            raise ValueError('allowed_operations must be a list of operation name patterns')
        operations = {name: spec for name, spec in description.get('operations', {}).items()
                      if any(fnmatch.fnmatchcase(name, p) for p in patterns)}
        if not operations:
            raise ValueError('allowed_operations matches none of the driver operations')
        if 'identify' in description.get('operations', {}):
            operations.setdefault('identify', description['operations']['identify'])
        description = {**description, 'operations': operations}
    if 'limits' in record:
        description = {**description, 'operations': limited(record['limits'], description.get('operations', {}))}
    return description


def limited(limits, operations):
    """The operations with the bounds a device record sets on numeric arguments, for what its setup tolerates:
    within what the instrument accepts, never beyond it. Each bound keeps its note under limit."""
    if not isinstance(limits, dict):
        raise ValueError('limits maps operations to bounds on their numeric parameters')
    operations = copy.deepcopy(operations)
    for name, bounds in limits.items():
        if name not in operations or not isinstance(bounds, dict) or not bounds:
            raise ValueError(f'limits {name}: name an operation of this instrument and bounds on its parameters')
        fields = operations[name].get('parameters', {})
        for parameter, bound in bounds.items():
            field, what = fields.get(parameter), f'limits {name}.{parameter}'
            if not isinstance(field, dict) or field.get('type') not in ('number', 'integer'):
                raise ValueError(f'{what}: name a numeric parameter of that operation')
            if (not isinstance(bound, dict) or bound.keys() - {'minimum', 'maximum', 'note'}
                    or not bound.keys() & {'minimum', 'maximum'} or not isinstance(bound.get('note', ''), str)
                    or any(isinstance(bound[k], bool) or not isinstance(bound[k], (int, float)) or not math.isfinite(bound[k])
                           for k in bound.keys() & {'minimum', 'maximum'})):
                raise ValueError(f'{what}: give minimum, maximum or both as numbers, and a note if wanted')
            low, high = bound.get('minimum', field.get('minimum', -math.inf)), bound.get('maximum', field.get('maximum', math.inf))
            if low > high:
                raise ValueError(f'{what}: minimum is above maximum')
            if low < field.get('minimum', -math.inf) or high > field.get('maximum', math.inf):
                raise ValueError(f'{what}: the instrument takes nothing {span(field)}; a limit stays within what it takes')
            field.update({k: bound[k] for k in ('minimum', 'maximum') if k in bound},
                         limit=bound.get('note') or 'set for this instrument')
    return operations


def describe(record, driver):
    description = allowed(record, driver.describe())
    operations = description.get('operations')
    if not isinstance(operations, dict) or not operations or any(not isinstance(s, dict) for s in operations.values()):
        raise ValueError('Driver must describe its operations')
    concurrency = description.get('concurrency', 1)
    if type(concurrency) is not int or concurrency < 1:
        raise ValueError('Driver concurrency must be a positive integer')
    return {'id': record['id'], 'driver': record['driver'], **description,
            **{k: record[k] for k in METADATA if k in record}}


def configure(engine, device_id, definition, expected_revision):
    from .engine import TERMINAL
    from .discovery import connection_conflict
    definition = copy.deepcopy(definition)
    if 'calibration' in definition:
        from .calibration import check
        check(definition['calibration'])
    if definition.get('id') != device_id:
        raise ValueError('Keep the instrument ID when editing its configuration')
    with engine.registration_lock:
        with engine.lock:
            released = engine.config.get('released_devices', {}).get(device_id)
            if released and len(released.get('group', [])) > 1:
                raise RequestError('shared_worker', 'Reconnect the shared SDK worker before changing its configuration.')
            old = engine.device_records[device_id]
            current = engine.store.device_history(device_id, 1)[0]
            if type(expected_revision) is not int or expected_revision != current['revision']:
                raise RequestError('configuration_changed', 'Read the current instrument configuration before saving.',
                                   current_revision=current['revision'], details_url=f'/v1/devices/{device_id}/history')
            if old == definition:
                return current
            driver = engine.devices[device_id]
            worker = getattr(driver, 'worker', None)
            if worker and any(d is not driver and getattr(d, 'worker', None) is worker for d in engine.devices.values()):
                raise RequestError('shared_worker', 'Update the shared SDK configuration together, then restart the service.')
            if (engine.allocation.used['device:' + device_id] or
                    any(o['device'] == device_id and o['state'] not in TERMINAL for o in engine.operations.values()) or
                    any(device_id in r.get('devices', {}) for r in engine.runs.values())):
                raise RequestError('device_busy', 'Wait for this instrument’s accepted experiments before changing its configuration.',
                                   device_id=device_id)
            if any(d['id'] != device_id and connection_conflict(definition, d) for d in engine.device_records.values()):
                raise RequestError('connection_in_use', 'Another instrument already uses this address.')
            # Reuse identity unless the caller explicitly registers a different instrument.
            if old.get('identity') != definition.get('identity'):
                raise ValueError('Keep the saved instrument identity; register a replacement instrument with a new ID')
            engine.reconnecting.add(device_id)
            registry = {k: v for k, v in engine.devices.items() if k != device_id}
        new = None
        config_written = False
        try:
            driver.close()
            new = make_driver(definition, registry)
            description = describe(definition, new)
            from .monitors import check
            monitors = check(definition, description)
            with engine.lock:
                engine.conditions.prepare_instrument(device_id, definition, description)
                config = copy.deepcopy(engine.config)
                config['devices'] = [definition if d['id'] == device_id else d for d in config['devices']]
                config.get('released_devices', {}).pop(device_id, None)
                save_json(engine.home / 'config.json', config)
                config_written = True
                version = engine.store.save_device(definition)
                engine.config.update(config)
                engine.devices[device_id] = new
                engine.device_records[device_id] = definition
                engine.descriptions[device_id] = dict(description, revision=version['revision'])
                engine.allocation.limits['device:' + device_id] = description.get('concurrency', 1)
                engine.monitors.configure(device_id, monitors)
                engine.conditions.index()
                engine.offline.pop(device_id, None)
                engine.released.discard(device_id)
            return version
        except BaseException:
            if config_written:
                save_json(engine.home / 'config.json', engine.config)
            if new is not None:
                new.close()
            try:
                restored = driver if released else make_driver(old, registry)
                with engine.lock:
                    engine.devices[device_id] = restored
            except Exception as error:
                with engine.lock:
                    engine.offline[device_id] = str(error)
            raise
        finally:
            with engine.lock:
                engine.reconnecting.discard(device_id)

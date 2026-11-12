"""Submit dependent operations together into an existing experiment."""
import copy
import json
import time
import uuid
from . import library, platforms
from .resources import QueueFull
from .store import dumps


def submit(engine, run_id, steps, request_id, maintenance=False):
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError('Provide a stable request_id for the operation batch')
    if not isinstance(steps, list) or not steps:
        raise ValueError('Provide a nonempty list of operation steps')
    # Missing dependencies mean sequence; an explicit [] starts an independent branch.
    steps = json.loads(dumps(steps))
    if any(not isinstance(step, dict) or any(not isinstance(step.get(k), str) or not step[k]
           for k in ('id', 'operation')) or not isinstance(step.get('device', ''), str) for step in steps):
        raise ValueError('Each step needs id and operation, with device for a device operation')
    previous = []
    for step in steps:
        step.setdefault('after', previous)
        previous = [step['id']]
        if 'request_id' in step:
            raise ValueError('The batch request_id identifies its steps; omit per-step request_id')
    submitted = steps
    with engine.lock:
        with engine.store.lock:
            row = engine.store.db.execute('SELECT body FROM operation_batches WHERE request_id=?', (request_id,)).fetchone()
        if row:
            record = json.loads(row[0])
            if record['run_id'] != run_id or record['steps'] != submitted:
                raise ValueError('request_id already names a different operation batch')
            return describe(engine, run_id, request_id)
        # A step without device uses a saved Standard or Workflow Operation.
        steps, calls = library.expand(submitted, engine.library.get)
        platforms.steps(steps)
        run = engine.runs.get(run_id)
        if engine.stopping or not run or not (run['kind'] == 'external' and run['state'] in {'queued', 'running'} or
                                             run['kind'] == 'package' and run['state'] == 'running'):
            raise ValueError('An operation batch requires an active experiment program')
        if len(engine.operations) + len(steps) > int(engine.config.get('max_active_operations', 100000)):
            raise QueueFull('The service has reached its configured number of active operations')
        required = sorted(set([] if maintenance else run.get('requires', [])) | set(engine.conditions.required({}, calls)))
        if required:
            # Work continues only while what it relies on holds; a pass of maintenance restores it.
            relied = engine.conditions.require(required, run_id)
        operations = [engine._operation(run_id, step) for step in steps]
        for op, step in zip(operations, steps):
            if maintenance:
                op['maintenance'] = True
            if required:
                op['requires'] = required
            if 'calls' in step:
                op['calls'], op['batch'] = step['calls'], request_id
        by_step = {op['step']: op for op in operations}
        reserved = engine._amounts(run, reserve=True)
        updated = copy.deepcopy(run)
        for op in operations:
            if run.get('platform') and op['device'] not in run['platform']['definition']['devices']:
                raise ValueError('Operation device must belong to the selected platform')
            if reserved and any(n > reserved.get(k, 0) for k, n in engine._amounts(op).items()):
                raise ValueError('Reserve every device and resource used by this experiment')
            op['request_id'] = 'batch-' + uuid.uuid5(uuid.NAMESPACE_URL, dumps([run_id, request_id, op['step']])).hex
            op['after_ids'] = [by_step[s]['id'] for s in op['after']]
            op['binding_ids'] = {key: by_step[value['step']]['id'] for key, value in op['bindings'].items()}
            if op['device'] not in updated['devices']:
                updated['devices'][op['device']] = copy.deepcopy(engine.descriptions[op['device']])
                updated['device_configurations'][op['device']] = copy.deepcopy(engine.device_records[op['device']])
        ids = [op['id'] for op in operations]
        updated['operations'].extend(ids)
        updated['operation_count'] += len(ids)
        updated['involved_resources'] = sorted(set(updated['involved_resources']).union(
            *(engine._amounts(op).keys() for op in operations)))
        added = [] if maintenance else [c for c in required if c not in run.get('requires', [])]
        if added:
            updated['requires'] = required
            updated['conditions'] = {**run.get('conditions', {}), **relied}
        record = {'request_id': request_id, 'run_id': run_id, 'steps': submitted, 'operation_ids': ids}
        if calls:
            record['calls'] = calls
        with engine.store.transaction():
            engine.store.db.execute('INSERT INTO operation_batches VALUES(?,?,?)', (request_id, run_id, dumps(record)))
            engine.store.put('runs', updated)
            for op in operations:
                engine.store.put('operations', op)
                engine.store.event('operation.queued', run_id, operation_id=op['id'], device=op['device'], operation=op['operation'])
        run.update(updated)
        engine.operations.update({op['id']: op for op in operations})
        engine.conditions.rely(run_id, required)
        engine.remaining[run_id] += len(operations)
        for op in operations:
            engine._follow_calls(op, calls)
            engine.remaining_dependencies[op['id']] = len(op['after_ids'])
            for parent in op['after_ids']:
                engine.dependents.setdefault(parent, []).append(op['id'])
            if not op['after_ids']:
                engine.ready[run_id][op['id']] = None
            for key, count in engine._amounts(op).items():
                engine.minimum_capacities[run_id][key] = max(engine.minimum_capacities[run_id].get(key, 0), count)
        engine.wake.set()
        return describe(engine, run_id, request_id)


def describe(engine, run_id, request_id, wait=0):
    wait = float(wait)
    if not 0 <= wait <= 25:
        raise ValueError('Batch wait must be between 0 and 25 seconds')
    deadline = time.monotonic() + wait
    with engine.store.changed:
        row = engine.store.db.execute('SELECT body FROM operation_batches WHERE request_id=? AND run_id=?', (request_id, run_id)).fetchone()
        if row is None:
            raise KeyError(request_id)
        batch = json.loads(row[0])
        while True:
            operations = [engine.store.get('operations', oid) for oid in batch['operation_ids']]
            pending = any(op['state'] in {'queued', 'running'} for op in operations)
            remaining = deadline - time.monotonic()
            if not pending or remaining <= 0:
                receipt = {'request_id': request_id, 'run_id': run_id, 'complete': not pending, 'operations': operations}
                if batch.get('calls'):
                    # The named result of each saved operation used in this batch.
                    receipt['results'] = library.results(batch['calls'], {op['step']: op.get('result') for op in operations})
                return receipt
            engine.store.changed.wait(remaining)

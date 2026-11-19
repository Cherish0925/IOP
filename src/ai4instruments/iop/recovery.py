"""Continue experiment programs from their explicitly saved progress, and step graphs from where they stopped."""
import copy
import json
import shutil
from .errors import RequestError
from .store import now, dumps


def checkpoint(engine, run_id, name, state):
    from .platforms import name as valid_name
    valid_name(name, 'Progress name')
    state = json.loads(dumps(state))
    with engine.lock:
        run = engine.runs.get(run_id)
        if not run or not (run['kind'] == 'package' and run['state'] == 'running' or
                           run['kind'] == 'external' and run['state'] in {'queued', 'running'}):
            raise RequestError('program_not_running', 'Save progress from a running experiment program.')
        if engine.remaining[run_id]:
            raise RequestError('operations_pending', 'Wait for the program’s instrument operations before saving progress.')
        return engine.store.save_checkpoint(run_id, name, state)


def again(op):
    """An operation of a step graph that did not complete, queued again: how it ended stays under attempts."""
    if 'started' in op:
        op.setdefault('attempts', []).append(
            {key: op[key] for key in ('state', 'error', 'outcome_unknown', 'started', 'ended') if op.get(key)})
    for key in ('started', 'ended', 'error', 'result', 'derived', 'progress', 'files', 'outcome_unknown',
                'conditions', 'in_doubt', 'retry_at'):
        op.pop(key, None)
    op['state'] = 'queued'
    return op


def resume(engine, run_id, request_id):
    from .engine import TERMINAL, SATISFIED
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError('Provide request_id for this continuation')
    with engine.lock:
        run = engine.store.get('runs', run_id)
        if run.get('resume_request_id') == request_id:
            return run
        if engine.stopping or run_id in engine.runs or run['state'] not in TERMINAL - {'completed'}:
            raise RequestError('program_not_stopped', 'Continue an interrupted, failed or cancelled experiment.')
        if run['kind'] not in ('external', 'workflow') and (run['kind'] != 'package' or run['package'].get('resume') is not True):
            raise RequestError('resume_not_defined', 'The experiment package must declare resume: true and read its saved progress.')
        checkpoints = engine.store.checkpoints(run_id)
        if not checkpoints and run['kind'] == 'package':
            raise RequestError('progress_missing', 'This program has not saved a progress point.')
        unresolved = set(run['involved_resources']) & engine.resource_holds.keys()
        if unresolved:
            raise RequestError('device_outcome_unknown', 'Record the affected instrument state before continuing.',
                resources=sorted(unresolved), action='/v1/resources/resolve')
        changed = [name for name, description in run['devices'].items()
                   if ({k: v for k, v in engine.descriptions.get(name, {}).items() if k != 'revision'} !=
                       {k: v for k, v in description.items() if k != 'revision'}) or
                   (name in run.get('device_configurations', {}) and
                    engine.device_records.get(name) != run['device_configurations'][name]) or
                   name in engine.offline or name in engine.reconnecting or name in engine.released]
        if changed:
            raise RequestError('instruments_changed', 'Restore the experiment’s instrument descriptions before continuing.', devices=changed)
        if run.get('platform') and engine.platforms.get(run['platform']['id'], {}).get('definition') != run['platform']['definition']:
            raise RequestError('platform_changed', 'The saved platform has changed since this experiment started.')
        from .resources import QueueFull
        if len(engine.runs) >= int(engine.config.get('max_active_runs', 10000)):
            raise QueueFull('The service has reached its configured number of active experiments')
        operations = engine.store.operations(run_id) if run['kind'] == 'workflow' else []
        if len(engine.operations) + len(operations) > int(engine.config.get('max_active_operations', 100000)):
            raise QueueFull('The service has reached its configured number of active operations')
        relied = None
        if run.get('requires') and not run.get('upkeep'):
            # Continued as it was accepted, while what it relies on holds; one that keeps its conditions restores them.
            relied = engine.conditions.require(run['requires'], run_id)
        # A step graph keeps what completed, or was skipped as its sweep stopped, and runs the rest again; the
        # maintenance it held ran for that moment and is not repeated.
        queued = [again(op) for op in operations if op['state'] not in SATISFIED and not op.get('maintenance')]
        attempt = run.get('attempt', 1)
        directory = engine.home / 'runs' / run_id
        # Each attempt retains its output; saved measurement files stay in the same directory.
        for name in ('stdout.txt', 'stderr.txt'):
            path = directory / name
            if path.is_file():
                target = directory / 'attempts' / str(attempt) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                engine._file_record(run_id, target, target.relative_to(directory).as_posix())
        previous = run['state']
        run = copy.deepcopy(run)
        for key in ('ended', 'started', 'error', 'end_state', 'exit_code', 'terminate_at'):
            run.pop(key, None)
        run.update(state='queued', attempt=attempt + 1, resume_request_id=request_id, resumed=now(), result=None,
                   operations=run['operations'] if run['kind'] == 'workflow' else [])
        if relied is not None:
            run['conditions'] = relied
        with engine.store.transaction():
            engine.store.put('runs', run)
            for op in queued:
                engine.store.put('operations', op)
                # What a call that did not complete wrote is not data.
                engine.store.forget_files(run_id, 'output/operations/' + op['id'])
            engine.store.event('run.resumed', run_id, attempt=run['attempt'], previous_state=previous,
                               **({'queued': len(queued)} if run['kind'] == 'workflow' else {}))
        for op in queued:
            shutil.rmtree(directory / 'output' / 'operations' / op['id'], ignore_errors=True)
        engine.runs[run_id] = run
        engine.operations.update((op['id'], op) for op in operations)
        engine._index_run(run)
        engine.conditions.rely(run_id, run.get('requires', []))
        for op in operations:
            # Points a lost condition left in doubt in a sweep that retakes them are taken again first.
            engine._doubted(op)
        retakes = {op['id'] for op in queued if op.get('retakes')}
        if retakes:
            engine.retaking[run_id] = retakes
        engine.wake.set()
        return copy.deepcopy(run)

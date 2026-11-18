"""Standard and Workflow Operations: reusable compositions saved in the laboratory.

Operations have three roles. **Basic Operations** are the operations of registered devices: one
control or observation each. A **Standard Operation** combines them into a reusable method with
parameters, stated conditions and a named result. A **Workflow Operation** organizes methods across
devices, in sequence and in parallel, or, when it decides from results, as a saved program.
Standard and Workflow Operations are saved here, can be read and revised, and are used as one step
of any experiment: a step without ``device`` names one. The Runtime replaces such a step by its
Basic Operations before scheduling, or runs the program, and records every use. Any step can sweep
parameters: it then runs at each of their values and what it returns is gathered into arrays.
"""
import copy
import re
from collections import deque
from . import sweeps as sweeping, views
from .drivers import parameters
from .platforms import dependencies, name

LAYERS = ('standard', 'workflow')
PLACEHOLDER = re.compile(r'^\$\{([^{}]+)\}$')
DEPTH = 16


def placeholders(value):
    """Parameter names used as "${name}" anywhere in a value."""
    if isinstance(value, str):
        found = PLACEHOLDER.match(value)
        return {found[1]} if found else set()
    if isinstance(value, dict):
        return set().union(*(placeholders(item) for item in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(placeholders(item) for item in value)) if value else set()
    return set()


def fill(value, arguments):
    """Replace "${name}" by the argument, keeping its JSON type."""
    if isinstance(value, str):
        found = PLACEHOLDER.match(value)
        return arguments[found[1]] if found else value
    if isinstance(value, dict):
        return {key: fill(item, arguments) for key, item in value.items()}
    if isinstance(value, list):
        return [fill(item, arguments) for item in value]
    return value


def sequenced(steps):
    """Steps of a saved operation run in the order written; ``after: []`` starts a parallel branch."""
    steps = copy.deepcopy(steps)
    previous = []
    for step in steps:
        step.setdefault('after', previous)
        previous = [step['id']]
    return steps


def check(definition, lookup):
    """Validate a Standard or Workflow Operation; ``lookup`` returns saved operations by name."""
    if not isinstance(definition, dict):
        raise ValueError('Describe the operation with an object')
    unknown = set(definition) - {'id', 'layer', 'description', 'conditions', 'requires', 'parameters', 'steps', 'result',
                                'program'}
    if unknown:
        raise ValueError('Unknown operation fields: ' + ', '.join(sorted(unknown)))
    name(definition.get('id'), 'Operation name')
    if definition.get('layer') not in LAYERS:
        raise ValueError('layer is standard for a reusable method or workflow for an organization of methods')
    if not isinstance(definition.get('description'), str) or not definition['description'].strip():
        raise ValueError('Describe what the operation achieves')
    if not isinstance(definition.get('conditions', ''), str):
        raise ValueError('conditions state in words when the result has its meaning')
    requires = definition.get('requires', [])
    if not isinstance(requires, list) or any(not isinstance(c, str) or not c for c in requires) or len(set(requires)) != len(requires):
        raise ValueError('requires names the platform conditions that must hold when the operation is used')
    declared = definition.get('parameters', {})
    if not isinstance(declared, dict) or any(not isinstance(spec, dict) for spec in declared.values()):
        raise ValueError('Each parameter needs a description object')
    if 'program' in definition:
        # A Workflow Operation that decides from results is a saved program; the Runtime runs it.
        if definition['layer'] != 'workflow' or 'steps' in definition or 'result' in definition:
            raise ValueError('A program is a Workflow Operation without steps; it saves its own result')
        return definition
    steps = definition.get('steps')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 1000 or any(not isinstance(step, dict) for step in steps):
        raise ValueError('An operation has between 1 and 1000 steps')
    identifiers = [step.get('id') for step in steps]
    if any(not isinstance(i, str) or not i or '.' in i for i in identifiers) or len(set(identifiers)) != len(steps):
        raise ValueError('Each step needs its own id, without dots')
    for step in steps:
        if not isinstance(step.get('operation'), str) or not step['operation']:
            raise ValueError(f"Step {step['id']}: name a device operation, or a saved operation without device")
        if 'device' in step and (not isinstance(step['device'], str) or not step['device']):
            raise ValueError(f"Step {step['id']}: device must be a name")
        if not isinstance(step.get('arguments', {}), dict):
            raise ValueError(f"Step {step['id']}: arguments must be an object")
        if 'sweep' in step:
            swept(step)
        used = placeholders(step.get('arguments', {})) | placeholders(step.get('device', '')) | placeholders(step.get('sweep', {}))
        if used - declared.keys():
            raise ValueError(f"Step {step['id']}: describe parameter " + ', '.join(sorted(used - declared.keys())))
        if 'device' not in step:
            inner = lookup(step['operation'])
            if inner is None:
                raise ValueError(f"Step {step['id']}: {step['operation']} is not a saved operation; a device operation needs device")
            if definition['layer'] == 'standard' and inner['layer'] == 'workflow':
                raise ValueError(f"Step {step['id']}: a Standard Operation is built from Basic and Standard Operations")
            if inner['id'] == definition['id']:
                raise ValueError('An operation cannot use itself')
            if 'program' in inner:
                raise ValueError(f"Step {step['id']}: {inner['id']} is a program; programs run as their own experiment")
    ordered = sequenced(steps)
    for step in ordered:
        if set(dependencies(step)) - set(identifiers):
            raise ValueError(f"Step {step['id']}: after and bindings name steps of this operation")
    counts = {step['id']: len(dependencies(step)) for step in ordered}
    children = {step['id']: [] for step in ordered}
    for step in ordered:
        for parent in dependencies(step):
            children[parent].append(step['id'])
    ready, visited = deque(i for i in identifiers if not counts[i]), 0
    while ready:
        visited += 1
        for child in children[ready.popleft()]:
            counts[child] -= 1
            if not counts[child]:
                ready.append(child)
    if visited != len(steps):
        raise ValueError('Step dependencies contain a cycle')
    result = definition.get('result', {})
    if not isinstance(result, dict):
        raise ValueError('result names values taken from steps')
    for key, source in result.items():
        if (not isinstance(source, dict) or source.get('step') not in identifiers or set(source) - {'step', 'path', 'view'}
                or not isinstance(source.get('path', []), list)):
            raise ValueError(f'Result {key}: give the step, an optional view of its array and a path')
        if 'view' in source:
            views.check(source['view'], f'Result {key}')
    return definition


def swept(step):
    """The points, shape and axes of a step's sweep, or None while parameters still give its values."""
    sweep = step['sweep']
    if not isinstance(sweep, dict) or not sweep:
        raise ValueError(f"Step {step['id']}: sweep maps parameters to the values they take")
    if set(sweep) & (set(step.get('arguments', {})) | set(step.get('bindings', {}))):
        raise ValueError(f"Step {step['id']}: a swept parameter is not also given in arguments or bindings")
    if placeholders(sweep):
        return None
    try:
        return sweeping.grid(sweep)
    except ValueError as error:
        raise ValueError(f"Step {step['id']}: {error}") from None


def expand(steps, lookup, describe=None):
    """Replace every step that uses a saved operation by its Basic Operations, and every swept step by
    its points, one after another.

    Returns the flat steps; for each use, a record with the operation, its revision, the arguments and
    where its named results are found; and, for each sweep that is not a point of another, how to
    gather it. Dependencies on a replaced step become dependencies on the steps that finish it.
    ``describe(device, operation)`` gives a device operation's description, for the units of its
    swept parameters.
    """
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
        raise ValueError('Each step must be an object')
    if all('device' in step and 'sweep' not in step for step in steps):
        return derived(steps, {}), {}, {}
    queue = deque((copy.deepcopy(step), []) for step in steps)
    flat, calls, sweeps, finishes = [], {}, {}, {}
    while queue:
        step, enclosing = queue.popleft()
        if 'sweep' in step:
            sweep_id = step.get('id')
            if not isinstance(sweep_id, str) or not sweep_id or not isinstance(step.get('operation'), str):
                raise ValueError('Each step needs id and operation')
            found = swept(step)
            if found is None:
                raise ValueError(f'Step {sweep_id}: give the values of the sweep; "${{name}}" takes them from the '
                                 'parameters of a saved operation')
            points, shape, axes = found
            spec = (describe(step['device'], step['operation']) if describe else None) if 'device' in step \
                else lookup(step['operation'])
            for axis in axes:
                unit = ((spec or {}).get('parameters') or {}).get(axis['name'], {}).get('unit')
                if isinstance(unit, str) and unit:
                    axis['unit'] = unit
            base = {key: value for key, value in step.items() if key != 'sweep'}
            previous, copies = list(step.get('after', [])), []
            for index, values in enumerate(points):
                point = copy.deepcopy(base)
                point.update(id=f'{sweep_id}.{index}', arguments={**base.get('arguments', {}), **values}, after=previous)
                previous = [point['id']]
                copies.append((point, enclosing))
            queue.extendleft(reversed(copies))
            sweeps[sweep_id] = {'points': [point['id'] for point, _ in copies], 'shape': shape, 'axes': axes}
            finishes[sweep_id] = [previous[0]]
            continue
        if 'device' in step:
            if enclosing:
                step['calls'] = enclosing
            flat.append(step)
            continue
        call = step.get('id')
        if not isinstance(call, str) or not call or not isinstance(step.get('operation'), str):
            raise ValueError('Each step needs id and operation')
        if len(enclosing) >= DEPTH:
            raise ValueError('Saved operations are nested too deeply; one of them uses itself')
        definition = lookup(step['operation'])
        if definition is None:
            raise ValueError(f"{step['operation']} is not a saved operation; a device operation needs device")
        if 'program' in definition:
            raise ValueError(f"{definition['id']} is a program: submit it as the only step of an experiment")
        bound = step.get('bindings', {})
        if not isinstance(bound, dict) or not isinstance(step.get('arguments', {}), dict):
            raise ValueError(f'Step {call}: arguments and bindings must be objects')
        declared = definition.get('parameters', {})
        if set(bound) - declared.keys() or set(bound) & step.get('arguments', {}).keys():
            raise ValueError(f'Step {call}: bindings name parameters of {definition["id"]} not given in arguments')
        try:
            arguments = parameters({'parameters': {k: v for k, v in declared.items() if k not in bound}}, step.get('arguments', {}))
        except ValueError as error:
            raise ValueError(f'Step {call} ({definition["id"]}): {error}') from None
        inner = sequenced(definition['steps'])
        depended = {parent for item in inner for parent in dependencies(item)}
        outer = list(step.get('after', []))
        replaced, last = [], []
        for item in inner:
            item_id = item['id']
            if item_id not in depended:
                last.append(f'{call}.{item_id}')
            item['id'] = f'{call}.{item_id}'
            after = [f'{call}.{parent}' for parent in item.get('after', [])]
            item['after'] = after if after else list(outer)
            bindings = {key: {**binding, 'step': f'{call}.{binding["step"]}'} for key, binding in item.get('bindings', {}).items()}
            values = {}
            for key, value in item.get('arguments', {}).items():
                found = PLACEHOLDER.match(value) if isinstance(value, str) else None
                if found and found[1] in bound:
                    # The value comes from an earlier result; the inner step receives the same binding.
                    bindings[key] = bound[found[1]]
                elif found and found[1] not in arguments:
                    continue
                else:
                    if placeholders(value) & bound.keys() or placeholders(value) - arguments.keys():
                        raise ValueError(f'Step {call}: a bound or omitted parameter must be a whole argument of {item_id}')
                    values[key] = fill(value, arguments)
            item['arguments'] = values
            if bindings:
                item['bindings'] = bindings
            if 'device' in item:
                if placeholders(item['device']) & bound.keys():
                    raise ValueError(f'Step {call}: the device of {item_id} must be given as an argument')
                item['device'] = fill(item['device'], arguments)
            if 'sweep' in item:
                if placeholders(item['sweep']) - arguments.keys():
                    raise ValueError(f'Step {call}: the sweep of {item_id} takes its values from arguments, '
                                     'not from bindings or omitted parameters')
                item['sweep'] = fill(item['sweep'], arguments)
            for key in ('resources', 'requirements'):
                if key in step and key not in item:
                    item[key] = copy.deepcopy(step[key])
            replaced.append((item, enclosing + [call]))
        queue.extendleft(reversed(replaced))
        finishes[call] = last
        calls[call] = {'operation': definition['id'], 'revision': definition['revision'], 'layer': definition['layer'],
                       'arguments': arguments, 'enclosing': enclosing,
                       'result': {key: {**source, 'step': f'{call}.{source["step"]}', 'path': list(source.get('path', []))}
                                  for key, source in definition.get('result', {}).items()}}

    def finished(step_id, depth=0):
        if step_id not in finishes:
            return [step_id]
        if depth > DEPTH:
            raise ValueError('Saved operations are nested too deeply')
        return [final for inner_id in finishes[step_id] for final in finished(inner_id, depth + 1)]

    def source(binding):
        """Follow a binding into saved operations until it names a Basic Operation or a sweep."""
        if 'sweep' in binding:
            return binding
        step_id, path, view = binding['step'], list(binding.get('path', [])), binding.get('view')
        while step_id in calls:
            if view is not None:
                raise ValueError(f'{step_id} is a saved operation; a view reads an array a device operation returned, '
                                 'so name the value in the result of the saved operation')
            named = calls[step_id]['result']
            if not path or path[0] not in named:
                raise ValueError(f'{step_id} returns ' + (', '.join(named) or 'no named result') + '; bind one of them')
            inner = named[path[0]]
            step_id, path, view = inner['step'], inner['path'] + path[1:], inner.get('view')
        found = {'step': step_id, 'path': path, **({'view': view} if view is not None else {})}
        if step_id in sweeps:
            # The value is read from what the sweep gathered, once its last point has finished.
            found.update(step=finished(step_id)[-1], sweep=gathering(step_id))
        return found

    def gathering(sweep_id):
        """How to gather a sweep: what each point returned, from its operation or its named results."""
        record = sweeps[sweep_id]
        points = [{'named': {key: source(item) for key, item in calls[point]['result'].items()}} if point in calls
                  else {'step': point} for point in record['points']]
        return {'id': sweep_id, 'shape': record['shape'], 'axes': record['axes'], 'points': points}

    for step in flat:
        step['after'] = list(dict.fromkeys(final for parent in step.get('after', []) for final in finished(parent)))
        if step.get('bindings'):
            step['bindings'] = {key: source(binding) for key, binding in step['bindings'].items()}
            for binding in step['bindings'].values():
                if 'sweep' in binding:
                    step['after'] = list(dict.fromkeys(step['after'] + finished(binding['sweep']['id'])))
    for call, record in calls.items():
        record['result'] = {key: source(item) for key, item in record['result'].items()}
        record['steps'] = [step['id'] for step in flat if call in step.get('calls', [])]
    outer = {sweep_id: gathering(sweep_id) for sweep_id in sweeps if not within(sweep_id, sweeps)}
    return derived(flat, calls), calls, outer


def within(step_id, sweeps):
    """Whether a step or use is a point of one of these sweeps, or part of one."""
    return any(step_id.startswith(sweep_id + '.') for sweep_id in sweeps)


def locate(source, ids):
    """A binding with the steps of the sweep it reads replaced by their operation IDs."""
    if 'sweep' in source:
        sweep = source['sweep']
        points = [{'named': {key: locate(item, ids) for key, item in point['named'].items()}} if 'named' in point
                  else {**point, 'step': ids[point['step']]} for point in sweep['points']]
        return {**source, 'step': ids[source['step']], 'sweep': {**sweep, 'points': points}}
    return {**source, 'step': ids[source['step']]}


def derived(flat, calls):
    """Give each device step the views that bindings and named results read from its arrays."""
    wanted = {}
    sources = [b for step in flat for b in step.get('bindings', {}).values() if isinstance(b, dict)]
    for item in sources + [s for record in calls.values() for s in record['result'].values()]:
        # A view of what a sweep gathered is read from its points' records when it is needed.
        if 'view' in item and 'sweep' not in item:
            wanted.setdefault(item['step'], {})[views.key(item)] = {'view': item['view'], 'path': list(item.get('path', []))}
    if not wanted:
        return flat
    # Steps a caller submitted are not changed in place: an identical retry compares them.
    return [{**step, 'derive': list(wanted[step['id']].values())} if step.get('id') in wanted else step for step in flat]


def program_step(steps, lookup):
    """The step of a submission that runs a saved program, or None. A program runs alone."""
    if not isinstance(steps, list):
        return None
    programs = [step for step in steps if isinstance(step, dict) and 'device' not in step
                and isinstance(step.get('operation'), str) and 'program' in (lookup(step['operation']) or {})]
    if not programs:
        return None
    step = programs[0]
    if len(steps) != 1 or step.get('after') or step.get('bindings'):
        raise ValueError(f"{step['operation']} is a program: submit it as the only step of an experiment")
    if not isinstance(step.get('id'), str) or not step['id'] or not isinstance(step.get('arguments', {}), dict):
        raise ValueError('Each step needs id, and arguments as an object')
    definition = lookup(step['operation'])
    try:
        arguments = parameters({'parameters': definition.get('parameters', {})}, step.get('arguments', {}))
    except ValueError as error:
        raise ValueError(f"Step {step['id']} ({definition['id']}): {error}") from None
    return {'step': step['id'], 'operation': definition['id'], 'revision': definition['revision'],
            'layer': 'workflow', 'arguments': arguments}


def value(operation, source):
    """What a binding or named result takes from a completed operation: a path into its result, or into a view
    of one of its arrays computed when it completed."""
    if 'view' in source:
        found = operation.get('derived', {}).get(views.key(source))
        if found is None or 'error' in found:
            raise ValueError(f"{operation.get('step')}: view of its array: " + (found or {}).get('error', 'not computed'))
        return found['value']
    item = operation.get('result')
    for part in source.get('path', []):
        item = item[part]
    return item


def read(source, record, output=None, located=False):
    """What a binding or named result takes; ``record`` returns the operation record of a step it names, and
    ``output`` is the experiment's output folder, where the points of a sweep saved their files."""
    if 'sweep' not in source:
        operation = record(source['step'])
        if operation is None:
            raise KeyError(source['step'])
        found = value(operation, source)
        return _located(found, operation) if located else found
    found = gathered(source['sweep'], record, output)
    if 'view' in source:
        found = views.read(found, None, output=output, **source['view'])
    for part in source.get('path', []):
        found = found[part]
    return found


def _located(found, operation):
    """A value with the file of each array in it named from the experiment's output folder."""
    if isinstance(found, list):
        return [_located(item, operation) for item in found]
    if not isinstance(found, dict):
        return found
    located = {key: item if key in ('data', 'values') else _located(item, operation) for key, item in found.items()}
    data = found.get('data')
    if isinstance(data, dict) and isinstance(data.get('dtype'), str) and isinstance(data.get('file'), str):
        located['data'] = {**data, 'file': f"operations/{operation['id']}/{data['file']}"}
    return located


def gathered(sweep, record, output=None):
    """What a sweep returns: the results of its points gathered into arrays over its axes."""
    found = []
    for point in sweep['points']:
        if 'named' in point:
            found.append(_named(point['named'], record, output, located=True))
        else:
            operation = record(point['step'])
            found.append(_located(operation.get('result'), operation)
                         if operation and operation.get('state') == 'completed' else None)
    return sweeping.gather(found, sweep['shape'], sweep['axes'])


def _named(sources, record, output=None, located=False):
    named = {}
    for key, source in sources.items():
        try:
            named[key] = read(source, record, output, located)
        except (KeyError, IndexError, TypeError, ValueError):
            named[key] = None
    return named


def results(calls, sweeps, by_step, output=None):
    """The named result of each use and what each sweep gathered, from the records of completed steps. The
    uses that are points of a sweep are part of what it gathered."""
    found = {call: _named(record['result'], by_step.get, output) for call, record in calls.items()
             if not within(call, sweeps)}
    found.update({sweep_id: gathered(sweep, by_step.get, output) for sweep_id, sweep in sweeps.items()})
    return found

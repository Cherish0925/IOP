"""Standard and Workflow Operations: reusable compositions saved in the laboratory.

Operations have three roles. **Basic Operations** are the operations of registered devices: one
control or observation each. A **Standard Operation** combines them into a reusable method with
parameters, stated conditions and a named result. A **Workflow Operation** organizes methods across
devices, in sequence and in parallel. Standard and Workflow Operations are saved here, can be read
and revised, and are used as one step of any experiment: a step without ``device`` names one. The
Runtime replaces such a step by its Basic Operations before scheduling and records every use.
"""
import copy
import re
from collections import deque
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
    unknown = set(definition) - {'id', 'layer', 'description', 'conditions', 'parameters', 'steps', 'result'}
    if unknown:
        raise ValueError('Unknown operation fields: ' + ', '.join(sorted(unknown)))
    name(definition.get('id'), 'Operation name')
    if definition.get('layer') not in LAYERS:
        raise ValueError('layer is standard for a reusable method or workflow for an organization of methods')
    if not isinstance(definition.get('description'), str) or not definition['description'].strip():
        raise ValueError('Describe what the operation achieves')
    if not isinstance(definition.get('conditions', ''), str):
        raise ValueError('conditions state in words when the result has its meaning')
    declared = definition.get('parameters', {})
    if not isinstance(declared, dict) or any(not isinstance(spec, dict) for spec in declared.values()):
        raise ValueError('Each parameter needs a description object')
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
        used = placeholders(step.get('arguments', {})) | placeholders(step.get('device', ''))
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
        if (not isinstance(source, dict) or source.get('step') not in identifiers
                or not isinstance(source.get('path', []), list)):
            raise ValueError(f'Result {key}: give the step and an optional path into its result')
    return definition


def expand(steps, lookup):
    """Replace every step that uses a saved operation by its Basic Operations.

    Returns the flat steps and, for each use, a record with the operation, its revision, the
    arguments and where its named results are found. Dependencies on a replaced step become
    dependencies on the steps that finish it.
    """
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
        raise ValueError('Each step must be an object')
    if all('device' in step for step in steps):
        return steps, {}
    queue = deque((copy.deepcopy(step), []) for step in steps)
    flat, calls, finishes = [], {}, {}
    while queue:
        step, enclosing = queue.popleft()
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
            for key in ('resources', 'requirements'):
                if key in step and key not in item:
                    item[key] = copy.deepcopy(step[key])
            replaced.append((item, enclosing + [call]))
        queue.extendleft(reversed(replaced))
        finishes[call] = last
        calls[call] = {'operation': definition['id'], 'revision': definition['revision'], 'layer': definition['layer'],
                       'arguments': arguments, 'enclosing': enclosing,
                       'result': {key: {'step': f'{call}.{source["step"]}', 'path': list(source.get('path', []))}
                                  for key, source in definition.get('result', {}).items()}}

    def finished(step_id, depth=0):
        if step_id not in finishes:
            return [step_id]
        if depth > DEPTH:
            raise ValueError('Saved operations are nested too deeply')
        return [final for inner_id in finishes[step_id] for final in finished(inner_id, depth + 1)]

    def source(binding):
        """Follow a binding into saved operations until it names a Basic Operation."""
        step_id, path = binding['step'], list(binding.get('path', []))
        while step_id in calls:
            named = calls[step_id]['result']
            if not path or path[0] not in named:
                raise ValueError(f'{step_id} returns ' + (', '.join(named) or 'no named result') + '; bind one of them')
            step_id, path = named[path[0]]['step'], named[path[0]]['path'] + path[1:]
        return {'step': step_id, 'path': path}

    for step in flat:
        step['after'] = list(dict.fromkeys(final for parent in step.get('after', []) for final in finished(parent)))
        if step.get('bindings'):
            step['bindings'] = {key: source(binding) for key, binding in step['bindings'].items()}
    for call, record in calls.items():
        record['result'] = {key: source(item) for key, item in record['result'].items()}
        record['steps'] = [step['id'] for step in flat if call in step.get('calls', [])]
    return flat, calls


def results(calls, by_step):
    """The named result of each use, from the results of its completed steps."""
    values = {}
    for call, record in calls.items():
        value = {}
        for key, source in record['result'].items():
            item = by_step.get(source['step'])
            try:
                for part in source['path']:
                    item = item[part]
            except (KeyError, IndexError, TypeError):
                item = None
            value[key] = item
        values[call] = value
    return values

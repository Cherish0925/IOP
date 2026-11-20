"""Measure a signal against a reference and optional shared dark, retaining uncertainty and progress."""
import json
import math
import os
from ai4instruments.iop import Client
from ai4instruments.iop.measurements import RunningMean, normalize


def finite(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + ' must be a finite number')
    if positive and value <= 0:
        raise ValueError(name + ' must be positive')
    return value


def configuration(parameters):
    allowed = {'signal', 'reference', 'dark', 'min_samples', 'max_samples', 'standard_uncertainty',
               'relative_uncertainty', 'reference_relative_uncertainty'}
    if not isinstance(parameters, dict) or set(parameters) - allowed:
        raise ValueError('Parameters take ' + ', '.join(sorted(allowed)))
    if not {'signal', 'reference'} <= parameters.keys():
        raise ValueError('Describe the signal and reference measurements')
    sources = {}
    for name in ('signal', 'reference', 'dark'):
        if name not in parameters:
            continue
        value = parameters[name]
        fields = {'device', 'operation', 'arguments', 'path', 'unit', 'components', 'prepare'}
        if not isinstance(value, dict) or set(value) - fields or not {'device', 'operation', 'path', 'unit'} <= value.keys():
            raise ValueError(name + ' takes device, operation, arguments, path, unit, components and prepare')
        if not all(isinstance(value[key], str) and value[key] for key in ('device', 'operation')):
            raise ValueError(name + '.device and operation must be nonempty names')
        if not isinstance(value['unit'], str):
            raise ValueError(name + '.unit must be text')
        if not isinstance(value['path'], list) or any(not isinstance(k, str) and type(k) is not int for k in value['path']):
            raise ValueError(name + '.path is a list of result keys or array indices')
        if not isinstance(value.get('arguments', {}), dict) or not isinstance(value.get('prepare', []), list):
            raise ValueError(name + '.arguments is an object and prepare is a list of steps')
        components = value.get('components', {})
        if not isinstance(components, dict):
            raise ValueError(name + '.components maps error-source names to signed standard uncertainties')
        for source, component in components.items():
            if not isinstance(source, str) or not source.strip() or source.startswith('repeatability:'):
                raise ValueError('Name each shared uncertainty source; repeatability: is reserved for sampling noise')
            if isinstance(component, dict):
                if set(component) != {'relative'}:
                    raise ValueError('A relative uncertainty contribution is {"relative": signed_fraction}')
                component = component['relative']
            finite(component, source)
        sources[name] = {**value, 'components': components, 'arguments': value.get('arguments', {}),
                         'prepare': value.get('prepare', [])}
    if len({value['unit'] for value in sources.values()}) != 1:
        raise ValueError('Signal, reference and dark must use the same unit')
    minimum, maximum = parameters.get('min_samples', 10), parameters.get('max_samples', 100)
    if type(minimum) is not int or type(maximum) is not int or not 2 <= minimum <= maximum:
        raise ValueError('min_samples and max_samples are integers with 2 <= min_samples <= max_samples')
    targets = {key: finite(parameters[key], key, True) for key in ('standard_uncertainty', 'relative_uncertainty')
               if key in parameters}
    reference_limit = parameters.get('reference_relative_uncertainty', 0.1)
    finite(reference_limit, 'reference_relative_uncertainty', True)
    if reference_limit >= 1:
        raise ValueError('reference_relative_uncertainty must be less than 1')
    return {'sources': sources, 'minimum': minimum, 'maximum': maximum, 'targets': targets,
            'reference_limit': reference_limit}


def reductions(run_id, means, spec):
    estimates = {}
    for name, source in spec['sources'].items():
        parts = {key: component['relative'] * means[name].mean if isinstance(component, dict) else component
                 for key, component in source['components'].items()}
        estimates[name] = means[name].estimate(source=f'repeatability:{run_id}:{name}:{means[name].count}', unit=source['unit'],
                                               components=parts)
    reference = estimates['reference']
    if 'dark' in estimates:
        reference = reference - estimates['dark']
    answer = {'measurements': {name: estimate.to_dict() for name, estimate in estimates.items()},
              'corrected_reference': reference.to_dict(), 'estimate': None, 'precision_reached': False}
    if not reference.value:
        answer['reason'] = 'zero_reference'
    elif reference.standard_uncertainty / abs(reference.value) > spec['reference_limit']:
        answer['reason'] = 'uncertain_reference'
    else:
        estimate = normalize(estimates['signal'], estimates['reference'], estimates.get('dark'))
        answer['estimate'] = estimate.to_dict()
        goals = spec['targets']
        # All requested limits must hold; a relative limit alone cannot certify a zero signal.
        answer['precision_reached'] = bool(goals) and all(
            estimate.standard_uncertainty <= value if key == 'standard_uncertainty'
            else estimate.value != 0 and estimate.standard_uncertainty / abs(estimate.value) <= value
            for key, value in goals.items())
    return answer


def recorded_reads(run, index):
    """Read history once on continuation, retaining only the unfinished round."""
    prefix = f'{run.id}:reference-measurement:{index}:'
    found, cursor = {}, 0
    while True:
        page = run.operations(after=cursor, limit=1000)
        if not page['operations']:
            return found
        for record in page['operations']:
            request = record.get('request_id')
            if isinstance(request, str) and request.startswith(prefix):
                found[request] = record
        cursor = page['cursor']


def read_request(prefix, records):
    """Retain completed reads; explicitly resumed methods replace only known failed reads.

    Replacement names depend on the failed operation, not the program attempt.
    If a replacement completed before another interruption, it is reused too.
    """
    request = prefix
    while request in records:
        previous = records[request]
        if previous.get('outcome_unknown'):
            raise ValueError('The measurement outcome is unknown for ' + previous['id']
                             + '; establish the instrument state and choose a new measurement explicitly')
        if previous['state'] != 'failed':
            break
        request = prefix + ':after:' + previous['id']
    return request


def measure(run, parameters):
    """Reusable from an external experiment, or submitted as this program package."""
    spec = configuration(parameters)
    progress = run.checkpoints().get('reference-measurement', {}).get('state')
    if progress is not None and progress.get('configuration') != spec:
        raise ValueError('The saved measurement uses different parameters; start a new experiment')
    means = {name: RunningMean.from_dict(progress['means'][name]) if progress else RunningMean()
             for name in spec['sources']}
    completed = progress['completed'] if progress else 0
    if type(completed) is not int or completed < 0 or any(mean.count != completed for mean in means.values()):
        raise ValueError('Saved measurement counts disagree')
    records = recorded_reads(run, completed) if progress is not None else {}
    if progress is None:
        # A package needs a progress point even if its first round is interrupted.
        # Save before any preparation, reading or output; a lost reply is recovered
        # by loading this same configuration and empty state on the next attempt.
        run.checkpoint({'configuration': spec, 'completed': 0,
                        'means': {name: mean.to_dict() for name, mean in means.items()}}, name='reference-measurement')
    stream = run.stream('reference-measurement', {'format': 'jsonl', 'method': '(signal-dark)/(reference-dark)',
                                                'unit': '', 'uncertainty': 'first-order standard uncertainty'})
    answer = reductions(run.id, means, spec) if completed >= 2 else {}
    while completed < spec['maximum'] and not (completed >= spec['minimum'] and answer.get('precision_reached')):
        operations, observed = [], {}
        for name, source in spec['sources'].items():
            prefix = f'{run.id}:reference-measurement:{completed}:{name}'
            if source['prepare']:
                prepared = run.batch(source['prepare'], request_id=prefix + ':prepare', maintain=True)
                operations.extend(record['id'] for record in prepared)
            record = run.invoke_record(source['device'], source['operation'], source['arguments'],
                                       request_id=read_request(prefix, records))
            # A reply lost here can be requested again under the same ID; the instrument is not measured twice.
            value = record['result']
            result_unit = value.get('unit') if isinstance(value, dict) else None
            for key in source['path']:
                if isinstance(value, dict) and 'unit' in value:
                    result_unit = value['unit']
                try:
                    value = value[key]
                except (KeyError, IndexError, TypeError):
                    raise ValueError(f'{name}.path does not select a value in the operation result') from None
            finite(value, name + ' reading')
            if result_unit is not None and result_unit != source['unit']:
                raise ValueError(f'{name} returned unit {result_unit!r}, expected {source["unit"]!r}')
            observed[name] = value
            operations.append(record['id'])
        # Update only after all readings of the round are present.
        for name, value in observed.items():
            means[name].add(value)
        completed += 1
        answer = reductions(run.id, means, spec) if completed >= 2 else {'estimate': None, 'precision_reached': False}
        row = {'samples': completed, 'observed': observed, **answer}
        run.save_acquisition(f'reference-measurement/{completed:08d}.json', operations,
                             conditions={'parameters': parameters}, analysis=row)
        stream.append((json.dumps(row, sort_keys=True, allow_nan=False) + '\n').encode('utf-8'), completed - 1)
        run.checkpoint({'configuration': spec, 'completed': completed,
                        'means': {name: mean.to_dict() for name, mean in means.items()}}, name='reference-measurement')
    result = {'samples': completed, 'stop': 'precision' if answer.get('precision_reached') else 'max_samples',
              'stream': 'reference-measurement', **answer}
    run.save_result(result)
    return result


if __name__ == '__main__':
    measure(Client().current_run(), json.loads(os.environ.get('IOP_PARAMETERS_JSON', '{}')))

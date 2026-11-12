"""Line-based command protocols that are not SCPI: motion controllers, lasers, pumps, gauges.

``config.protocol`` states how the instrument's command language differs: separate terminators, an
echoed command, acknowledged writes, error replies and a minimum time between commands. An
operation's ``result`` can take its value out of a reply with a pattern, scale it or map it.
"""
import math
import re

FIELDS = ('read_termination', 'write_termination', 'identify', 'error_reply', 'write_reply', 'echo', 'min_interval')
TRUE, FALSE = ('1', 'on', 'true', 'yes'), ('0', 'off', 'false', 'no')


def pattern(value, what):
    if not isinstance(value, str) or not value:
        raise ValueError(what + ' must be a regular expression')
    try:
        return re.compile(value)
    except re.error as error:
        raise ValueError(f'{what}: {error}') from None


def reply(value, what='reply'):
    """false: the instrument sends nothing; true: one line; {"match": pattern}: a line that must match."""
    if isinstance(value, bool):
        return value
    if isinstance(value, dict) and set(value) == {'match'}:
        return pattern(value['match'], what + '.match')
    raise ValueError(what + ' is true, false or {"match": pattern}')


def protocol(value):
    """Validate ``config.protocol``; return it with defaults filled in and patterns compiled."""
    if not isinstance(value, dict):
        raise ValueError('protocol must be an object')
    if set(value) - set(FIELDS):
        raise ValueError('Unknown protocol fields: ' + ', '.join(sorted(set(value) - set(FIELDS))))
    checked = {}
    for key in ('read_termination', 'write_termination'):
        if key in value:
            if not isinstance(value[key], str) or not value[key]:
                raise ValueError(key + ' must be nonempty text')
            checked[key] = value[key]
    for key, default in (('identify', True), ('echo', False)):
        if not isinstance(value.get(key, default), bool):
            raise ValueError(key + ' must be true or false')
        checked[key] = value.get(key, default)
    checked['error_reply'] = pattern(value['error_reply'], 'error_reply') if 'error_reply' in value else None
    checked['write_reply'] = reply(value.get('write_reply', False), 'write_reply')
    interval = value.get('min_interval', 0)
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 0 <= interval <= 60:
        raise ValueError('min_interval must be between 0 and 60 seconds')
    checked['min_interval'] = interval
    return checked


def reader(result):
    """Return the function that turns a reply line into the operation's value."""
    if not isinstance(result, dict):
        raise ValueError('result must be an object')
    match = pattern(result['match'], 'result.match') if 'match' in result else None
    mapping = result.get('map')
    if mapping is not None and (not isinstance(mapping, dict) or not mapping):
        raise ValueError('result.map gives the value for each reply text')
    kind = result.get('type')
    scale, offset = result.get('scale', 1), result.get('offset', 0)
    for key, number in (('scale', scale), ('offset', offset)):
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError(f'result.{key} must be a number')
    scaled = scale != 1 or offset != 0
    if scaled and kind not in ('number', 'integer'):
        raise ValueError('scale and offset apply to number or integer results')

    def read(text):
        if match:
            found = match.search(text)
            if not found:
                raise ValueError(f'Reply {text!r} does not match {match.pattern!r}')
            text = found.group(1) if found.groups() else found.group(0)
        if mapping is not None:
            if text.strip() not in mapping:
                raise ValueError(f'Reply {text!r} is not one of ' + ', '.join(mapping))
            return mapping[text.strip()]
        try:
            if kind == 'number':
                value = float(text)
            elif kind == 'integer':
                value = int(text.strip())
            elif kind == 'boolean':
                word = text.strip().lower()
                if word not in TRUE + FALSE:
                    raise ValueError
                return word in TRUE
            else:
                return text
        except ValueError:
            raise ValueError(f'Reply {text!r} is not {"a number" if kind == "number" else "an integer" if kind == "integer" else "true or false"}') from None
        return value * scale + offset if scaled else value
    return read


def operation(spec):
    """Check the reply handling of one operation; return (reader, reply, completion pattern)."""
    result = spec.get('result', {})
    read = reader(result) if isinstance(result, dict) and result.get('type') != 'array' else None
    if not isinstance(result, dict):
        raise ValueError('result must be an object')
    expected = reply(spec['reply']) if 'reply' in spec else None
    if expected is not None and 'query' in spec:
        raise ValueError('reply belongs to a write; a query reads its result')
    completion = spec.get('completion')
    finished = None
    if completion is not None:
        if not isinstance(completion, dict) or not isinstance(completion.get('query'), str):
            raise ValueError('completion needs a query')
        if 'match' in completion:
            if 'equals' in completion:
                raise ValueError('completion uses equals or match, not both')
            finished = pattern(completion['match'], 'completion.match')
    return read, expected, finished

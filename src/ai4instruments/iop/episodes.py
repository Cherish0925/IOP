"""Ordered research episodes tied to completed operations in an experiment."""
import json
import math
import re
from .store import dumps, now


def identity(value, name):
    if not isinstance(value, dict) or not all(isinstance(value.get(k), str) and value[k].strip() for k in ('id', 'version')):
        raise ValueError(name + ' needs an id and version')
    return value


def create(engine, run_id, definition):
    definition = dict(definition)
    if set(definition) - {'id', 'task', 'policy', 'seed', 'observation', 'info'}:
        raise ValueError('An episode accepts id, task, policy, seed, observation and info')
    eid = definition.get('id')
    if not isinstance(eid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', eid):
        raise ValueError('An episode ID uses letters, digits, dots, underscores or hyphens')
    identity(definition.get('task'), 'task')
    if definition.get('policy') is not None:
        identity(definition['policy'], 'policy')
    if 'observation' not in definition:
        raise ValueError('Provide the initial observation')
    if definition.get('seed') is not None and type(definition['seed']) is not int:
        raise ValueError('seed must be an integer or null')
    if not isinstance(definition.get('info', {}), dict):
        raise ValueError('info must be an object')
    # Serialize before opening a transaction, including rejection of nonfinite numbers.
    payload = dumps(definition)
    with engine.lock, engine.store.transaction():
        engine.store.get('runs', run_id)
        old = engine.store.db.execute('SELECT definition FROM episodes WHERE run_id=? AND id=?', (run_id, eid)).fetchone()
        if old:
            if json.loads(old[0]) != definition:
                raise ValueError('This episode ID already belongs to different initial conditions')
        else:
            engine.store.db.execute('INSERT INTO episodes VALUES(?,?,?,?,?,?)', (run_id, eid, payload, now(), 0, 'active'))
            engine.store.event('episode.created', run_id, episode_id=eid)
        return describe(engine, run_id, eid)


def describe(engine, run_id, episode_id):
    with engine.store.lock:
        row = engine.store.db.execute('SELECT definition,created,next_sequence,state FROM episodes WHERE run_id=? AND id=?',
                                      (run_id, episode_id)).fetchone()
        if row is None:
            raise KeyError('Unknown episode: ' + episode_id)
        return {'schema_version': 1, 'run_id': run_id, **json.loads(row[0]), 'created': row[1],
                'next_sequence': row[2], 'state': row[3]}


def listing(engine, run_id, offset=0, limit=100):
    offset, limit = max(0, int(offset)), min(1000, max(1, int(limit)))
    with engine.store.lock:
        engine.store.get('runs', run_id)
        rows = engine.store.db.execute('SELECT id FROM episodes WHERE run_id=? ORDER BY created,id LIMIT ? OFFSET ?',
                                      (run_id, limit + 1, offset)).fetchall()
        return {'episodes': [describe(engine, run_id, row[0]) for row in rows[:limit]],
                'next_offset': offset + limit if len(rows) > limit else None}


def append(engine, run_id, episode_id, transition):
    fields = {'sequence', 'action', 'observation', 'reward', 'terminated', 'truncated', 'operation_ids', 'info'}
    if not isinstance(transition, dict) or set(transition) - fields or fields - {'info'} - set(transition):
        raise ValueError('Provide sequence, action, observation, reward, terminated, truncated and operation_ids')
    step = {**transition, 'info': transition.get('info', {})}
    seq, reward = step['sequence'], step['reward']
    if type(seq) is not int or seq < 0:
        raise ValueError('sequence must be a nonnegative integer')
    if reward is not None and (type(reward) not in (int, float) or not math.isfinite(reward)):
        raise ValueError('reward must be finite or null for an unscored observation')
    if any(type(step[k]) is not bool for k in ('terminated', 'truncated')) or not isinstance(step['info'], dict):
        raise ValueError('terminated and truncated must be booleans; info must be an object')
    ids = step['operation_ids']
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Provide distinct completed operation IDs from this experiment')
    dumps(step)
    with engine.lock, engine.store.transaction():
        episode = describe(engine, run_id, episode_id)
        previous = engine.store.db.execute('SELECT body FROM episode_steps WHERE run_id=? AND episode_id=? AND sequence=?',
                                           (run_id, episode_id, seq)).fetchone()
        if previous:
            record = json.loads(previous[0])
            if {k: record[k] for k in fields} != step:
                raise ValueError('This sequence already contains a different transition')
            return record
        if episode['state'] != 'active':
            raise ValueError('This episode has ended; begin another episode for new work')
        if seq != episode['next_sequence']:
            raise ValueError('Next episode sequence is ' + str(episode['next_sequence']))
        for oid in ids:
            op = engine.store.get('operations', oid)
            if op['run_id'] != run_id or op['state'] != 'completed':
                raise ValueError('An episode transition references completed operations from the same experiment')
        record = {'schema_version': 1, 'run_id': run_id, 'episode_id': episode_id, 'time': now(), **step}
        state = 'ended' if step['terminated'] or step['truncated'] else 'active'
        engine.store.db.execute('INSERT INTO episode_steps VALUES(?,?,?,?)', (run_id, episode_id, seq, dumps(record)))
        engine.store.db.execute('UPDATE episodes SET next_sequence=?,state=? WHERE run_id=? AND id=?',
                               (seq + 1, state, run_id, episode_id))
        engine.store.event('episode.transition', run_id, episode_id=episode_id, sequence=seq, state=state)
        return record


def transitions(engine, run_id, episode_id, after=-1, limit=100):
    after, limit = max(-1, int(after)), min(1000, max(1, int(limit)))
    with engine.store.lock:
        episode = describe(engine, run_id, episode_id)
        rows = engine.store.db.execute('SELECT body FROM episode_steps WHERE run_id=? AND episode_id=? AND sequence>? '
                                       'ORDER BY sequence LIMIT ?', (run_id, episode_id, after, limit)).fetchall()
        values = [json.loads(row[0]) for row in rows]
        return {'transitions': values, 'cursor': values[-1]['sequence'] if values else after,
                'next_sequence': episode['next_sequence'], 'state': episode['state']}

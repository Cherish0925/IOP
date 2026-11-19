"""An experiment as one file: its records, its files and a description that research data tools read.

The archive is a ZIP written while it is sent, so an experiment of any size leaves no copy on disk. It holds
the experiment record; its operations and events in order; the verifications and corrections of the
conditions it required while it ran; its data streams, saved progress and research episodes; the saved
operations it used, at the revisions it used; and every file it recorded, under files/. While an experiment
runs, its archive holds what was recorded until then. ro-crate-metadata.json describes the contents as an
RO-Crate 1.1: the experiment is an action whose instruments are the Runtime and the devices it used, and
whose results are its files.
"""
import json
import zipfile
from urllib.parse import quote
from . import episodes, streams
from .config import VERSION
from .store import dumps, now

CRATE = 'https://w3id.org/ro/crate/1.1'
PIECE = 256 * 1024
# Files already compressed are copied into the archive as they are.
PACKED = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.zip', '.gz', '.bz2', '.xz', '.zst', '.npz', '.mp4', '.mkv'}
ACTIVE = 'http://schema.org/ActiveActionStatus'
STATUS = {'completed': 'http://schema.org/CompletedActionStatus', 'failed': 'http://schema.org/FailedActionStatus',
          'cancelled': 'http://schema.org/FailedActionStatus', 'interrupted': 'http://schema.org/FailedActionStatus'}


class Sink:
    """Where the ZIP is written: a stream that counts what it took and cannot seek, so each entry carries its
    sizes after its data. Pieces go out once they reach 256 KiB."""

    def __init__(self, send):
        self.send, self.position, self.pending = send, 0, bytearray()

    def write(self, data):
        self.pending += data
        self.position += len(data)
        if len(self.pending) >= PIECE:
            self.flush()
        return len(data)

    def tell(self):
        return self.position

    def flush(self):
        if self.pending:
            self.send(bytes(self.pending))
            self.pending.clear()


def write(engine, run_id, send):
    """Write the archive of an experiment in pieces given to send; return the names it holds."""
    from .engine import relative_path
    store, sink = engine.store, Sink(send)
    run = store.get('runs', run_id)
    folder = (engine.home / 'runs' / run_id).resolve()
    parts = []
    # Every entry is compressed: a reader finds the end of a compressed entry without its sizes, which a ZIP
    # written while it is sent can only give after the data.
    with zipfile.ZipFile(sink, 'w', zipfile.ZIP_DEFLATED) as archive:

        def record(name, title, values, lines=False):
            """A record of the experiment as JSON, or as JSON Lines one value a line."""
            size = 0
            with archive.open(name, 'w', force_zip64=True) as file:
                for value in values if lines else [values]:
                    data = (dumps(value) + ('\n' if lines else '')).encode('utf-8')
                    file.write(data)
                    size += len(data)
            parts.append({'@id': quote(name), '@type': 'File', 'name': title, 'contentSize': str(size),
                          'encodingFormat': 'application/jsonl' if lines else 'application/json'})

        record('experiment.json', 'Experiment record', run)
        record('operations.jsonl', 'Operations in the order submitted', store.operations(run_id), lines=True)
        record('events.jsonl', 'Events in order', _events(store, run_id), lines=True)
        verifications = _conditions(engine, run)
        if verifications:
            record('conditions.jsonl', 'Verifications and corrections of the required conditions while it ran',
                   verifications, lines=True)
        kept = [{**stream, 'chunks': _chunks(engine, run_id, stream['name'])} for stream in streams.listing(engine, run_id)]
        if kept:
            record('streams.json', 'Data streams and their chunks', kept)
        progress = store.checkpoints(run_id)
        if progress:
            record('checkpoints.json', 'Saved program progress', progress)
        for episode in _episodes(engine, run_id):
            record(f"episodes/{episode[0]['id']}.jsonl", f"Research episode {episode[0]['id']}", episode, lines=True)
        used = _saved_operations(store, run)
        if used:
            record('saved-operations.json', 'Saved operations used, at the revisions used', used)
        for item in _files(store, run_id):
            path = folder / relative_path(item['name'])
            if not path.resolve().is_relative_to(folder) or not path.is_file():
                continue
            name = 'files/' + item['name']
            archive.write(path, name, compresslevel=0 if path.suffix.lower() in PACKED else 1)
            parts.append({'@id': quote(name), '@type': 'File', 'name': item['name'], 'identifier': item['id'],
                          'encodingFormat': item.get('media_type', 'application/octet-stream'),
                          'contentSize': str(path.stat().st_size), 'dateCreated': item['created']})
        description = crate(run, parts, engine.config.get('data_license'))
        archive.writestr('ro-crate-metadata.json', json.dumps(description, ensure_ascii=False, indent=1))
    sink.flush()
    return ['ro-crate-metadata.json'] + [part['name'] for part in parts]


def crate(run, parts, license=None):
    """The RO-Crate 1.1 that describes an archive: the experiment as an action using the Runtime and its devices,
    with the experiment's files as its results, under the license the laboratory gives its data."""
    entities, devices = [], []
    for name, description in sorted(run.get('devices', {}).items()):
        identity = description.get('identity') or {}
        device = {'@id': '#device-' + quote(name), '@type': 'IndividualProduct', 'name': name,
                  'description': f"Registered with driver {description.get('driver', 'unknown')}" +
                                 (f", firmware {identity['firmware']}" if identity.get('firmware') else '')}
        if identity.get('model'):
            device['model'] = identity['model']
        if identity.get('serial_number'):
            device['serialNumber'] = identity['serial_number']
        if identity.get('manufacturer'):
            maker = '#organization-' + quote(identity['manufacturer'])
            device['manufacturer'] = {'@id': maker}
            if all(entity['@id'] != maker for entity in entities):
                entities.append({'@id': maker, '@type': 'Organization', 'name': identity['manufacturer']})
        devices.append(device)
    action = {'@id': '#experiment', '@type': 'CreateAction', 'name': run.get('name') or run['id'],
              'identifier': run['id'], 'startTime': run.get('started') or run['created'],
              'actionStatus': {'@id': STATUS.get(run['state'], ACTIVE)},
              'instrument': [{'@id': '#iop'}] + [{'@id': device['@id']} for device in devices],
              'object': [{'@id': part['@id']} for part in parts if part['@id'] == 'files/experiment.json'],
              'result': [{'@id': part['@id']} for part in parts
                         if part['@id'].startswith('files/') and part['@id'] != 'files/experiment.json']}
    if run.get('ended'):
        action['endTime'] = run['ended']
    if run.get('error'):
        action['error'] = run['error'] if isinstance(run['error'], str) else json.dumps(run['error'])
    count = len(run.get('operations', [])) or run.get('operation_count', 0)
    kind = {'workflow': 'Step graph', 'package': 'Experiment program', 'external': 'Continuous experiment'}.get(
        run['kind'], 'Experiment')
    root = {'@id': './', '@type': 'Dataset', 'identifier': run['id'], 'name': run.get('name') or run['id'],
            'description': f"{kind} {run['id']}, {run['state']}: {count} operations on "
                           f"{', '.join(sorted(run.get('devices', {}))) or 'no devices'}, recorded by IOP Runtime {VERSION}.",
            'dateCreated': run['created'], 'datePublished': now(), 'hasPart': [{'@id': part['@id']} for part in parts],
            'mentions': {'@id': '#experiment'}}
    if isinstance(license, str) and license.strip():
        terms = {'@id': license.strip() if '://' in license else '#license', '@type': 'CreativeWork',
                 'name': license.strip()}
    else:
        terms = {'@id': '#license', '@type': 'CreativeWork', 'name': 'No license stated',
                 'description': 'The laboratory has not stated how this data may be used; ask its owners before reuse.'}
    root['license'] = {'@id': terms['@id']}
    software = {'@id': '#iop', '@type': 'SoftwareApplication', 'name': 'IOP Runtime', 'version': VERSION,
                'url': 'https://github.com/Cherish0925/IOP'}
    descriptor = {'@id': 'ro-crate-metadata.json', '@type': 'CreativeWork', 'conformsTo': {'@id': CRATE},
                  'about': {'@id': './'}}
    graph = [descriptor, root, action, software, terms, *devices, *entities, *parts]
    # One value stands alone rather than in a list, and an empty list is left out.
    graph = [{key: value[0] if isinstance(value, list) and len(value) == 1 else value
              for key, value in entity.items() if value != []} for entity in graph]
    return {'@context': CRATE + '/context', '@graph': graph}


def _events(store, run_id):
    cursor = 0
    while True:
        page = store.events(cursor, run_id, 1000)
        if not page:
            return
        yield from page
        cursor = page[-1]['sequence']


def _conditions(engine, run):
    """What was verified and corrected of the conditions the experiment required, and those they rest on, from
    its start until it ended."""
    names = run.get('requires', [])
    try:
        with engine.lock:
            names = engine.conditions.closure(names)
    except (KeyError, ValueError):
        pass  # A condition no longer defined keeps its records under its name.
    until = run.get('ended') or now()
    return [{'condition': name, **item} for name in names
            for item in engine.store.condition_history(name, run['created'], until)]


def _chunks(engine, run_id, name):
    cursor, found = -1, []
    while True:
        page = streams.chunks(engine, run_id, name, cursor, 1000)
        if not page['chunks']:
            return found
        found.extend(page['chunks'])
        cursor = page['cursor']


def _episodes(engine, run_id):
    """Each research episode as its export gives it: the episode, then its transitions."""
    offset = 0
    while offset is not None:
        page = episodes.listing(engine, run_id, offset, 1000)
        for episode in page['episodes']:
            lines, cursor = [{'type': 'episode', **episode}], -1
            while True:
                found = episodes.transitions(engine, run_id, episode['id'], cursor, 1000)
                if not found['transitions']:
                    break
                lines.extend({'type': 'transition', **item} for item in found['transitions'])
                cursor = found['cursor']
            yield lines
        offset = page['next_offset']


def _saved_operations(store, run):
    """The definitions of the saved operations the experiment used, each at the revision it used."""
    uses = list(run.get('calls', {}).values()) + ([run['program_call']] if run.get('program_call') else [])
    for batch in store.batches(run['id']):
        uses.extend(batch.get('calls', {}).values())
    wanted = sorted({(use['operation'], use['revision']) for use in uses if 'operation' in use and 'revision' in use})
    found = []
    for operation, revision in wanted:
        found.extend(item for item in store.saved_operation_history(operation) if item.get('revision') == revision)
    return found


def _files(store, run_id):
    cursor = 0
    while True:
        page = store.artifact_page(run_id, cursor, 1000)
        yield from page['files']
        if not page['has_more']:
            return
        cursor = page['cursor']

"""MCP tools forward to the same IOP service used by the desktop and SDKs."""
from __future__ import annotations
import fnmatch
import functools
import json
import importlib.util
import os
import sys
from pathlib import Path
from typing import Literal, Any
from urllib.parse import quote, urlencode

# Arrays longer than this reach an agent as their description; the values stay in the operation's file.
AGENT_VALUES = 64
# A submitted step graph longer than this is read from the experiment's files rather than placed in a prompt.
PROMPT_STEPS = 8000

# Hosts that cap or pay for every tool can load only the toolsets a laboratory needs; the three
# descriptions and the guide are always offered. Every tool belongs to exactly one toolset.
ALWAYS = ('lab_context', 'describe_instrument', 'describe_platform', 'guide')
# What an agent reads when a task needs it, so that tool descriptions, read on every turn, stay short.
GUIDES = ('steps', 'sweeps', 'arrays', 'measurements', 'conditions', 'priors', 'experiments', 'programs', 'setup', 'episodes')
TOOLSETS = {
    'setup': ('configuration_schema', 'register_instrument', 'instrument_configuration', 'register_instruments',
              'discover_instruments', 'identify_instrument', 'instrument_profiles', 'save_instrument_profile',
              'retry_instrument_discovery', 'available_adapters', 'configure_platform', 'configure_resources'),
    'experiments': ('saved_operations', 'operating_conditions', 'run_procedure', 'submit_experiment', 'start_experiment', 'invoke_operation',
                    'finish_experiment', 'submit_operations', 'save_acquisition', 'submit_program', 'continue_program',
                    'experiment', 'find_experiments', 'cancel_experiment', 'experiment_updates', 'experiment_files',
                    'operation_result', 'read_array',
                    'save_experiment_file', 'download_file', 'save_experiment'),
    'attention': ('reconnect_instrument', 'release_instrument', 'resolve_resources', 'manual_tasks', 'report_manual_step'),
    'episodes': ('begin_episode', 'record_transition', 'research_episodes'),
}


def toolsets(selection=None):
    """The toolsets named by ``selection`` or IOP_MCP_TOOLSETS, comma separated; all when neither is set."""
    selection = selection or os.environ.get('IOP_MCP_TOOLSETS') or 'all'
    names = [name.strip() for name in selection.split(',') if name.strip()]
    if 'all' in names:
        return list(TOOLSETS)
    unknown = sorted(set(names) - TOOLSETS.keys())
    if unknown or not names:
        raise ValueError('Unknown toolsets: ' + ', '.join(unknown) + '; choose from all, ' + ', '.join(TOOLSETS))
    return [name for name in TOOLSETS if name in names]


def compact(value, values=False):
    """Replace long inline arrays in operation results with a count; programs read them through the SDK."""
    if isinstance(value, list):
        return [compact(item, values) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: compact(item, values) for key, item in value.items()}
    data = value.get('data')
    if (not values and 'values' in result and isinstance(data, dict) and isinstance(data.get('count'), int)
            and 'dtype' in data and data['count'] > AGENT_VALUES):
        del result['values']
        result['values_omitted'] = data['count']
    return result


# Fields of an operation that an experiment overview needs; operation_result returns the full record.
OVERVIEW_FIELDS = ('id', 'step', 'device', 'operation', 'arguments', 'state', 'ended', 'error', 'outcome_unknown',
                   'calibration_expired', 'maintenance', 'in_doubt', 'retakes', 'retaken_by', 'attempts', 'result', 'files')


# What tells one experiment from another when it is looked for; experiment reads the rest.
FOUND_FIELDS = ('id', 'name', 'project', 'experiment', 'request_id', 'kind', 'state', 'created', 'ended', 'operation_count',
                'involved_resources')


def drop_titles(schema):
    """Remove generated titles such as "Run Id" for run_id; property names themselves are kept."""
    if isinstance(schema, list):
        for item in schema:
            drop_titles(item)
    elif isinstance(schema, dict):
        schema.pop('title', None)
        for key, value in schema.items():
            for item in (value.values() if key in ('properties', '$defs', 'definitions') and isinstance(value, dict) else [value]):
                drop_titles(item)


def optional(schema):
    """List a parameter that may be left out by its type alone, without the null alternative and default."""
    for field in schema.get('properties', {}).values():
        options = field.get('anyOf')
        if field.get('default', 0) is None and isinstance(options, list) and {'type': 'null'} in options:
            rest = [option for option in options if option != {'type': 'null'}]
            del field['anyOf'], field['default']
            field.update(rest[0] if len(rest) == 1 else {'anyOf': rest})


def summary(operation):
    return {key: operation[key] for key in OVERVIEW_FIELDS if key in operation}


def overview(page):
    return {**page, 'operations': [summary(op) for op in compact(page['operations'])]}


def waiting(wait):
    wait = float(wait)
    if not 0 <= wait <= 25:
        raise ValueError('wait must be between 0 and 25 seconds')
    return '?' + urlencode({'wait': wait}) if wait else ''


def failure(error):
    """The text an agent reads for a failed call: the service's code first, then its reason."""
    from .client import IOPError
    if isinstance(error, IOPError):
        text = f'{error.code}: {error}' if error.code else str(error)
        retry = error.details.get('retry_after')
        return text + (f' Retry after {retry} s.' if retry else '')
    return f'{type(error).__name__}: {error}'


def guide_text(topic='overview'):
    """The working method, or one of its references: the text of the skill that comes with this package."""
    from .connector import guides, skill
    if topic == 'overview':
        return skill().decode('utf-8').split('---', 2)[-1].lstrip()
    if topic not in GUIDES:
        raise ValueError('Unknown guide: ' + str(topic) + '; choose from overview, ' + ', '.join(GUIDES))
    return guides()[topic].decode('utf-8')


def create_server(client, *, http=False, selection=None):
    chosen = toolsets(selection)
    offered = set(ALWAYS).union(*(TOOLSETS[name] for name in chosen))
    try:
        if sys.version_info >= (3, 11):
            from mcp.server import MCPServer as Server
        else:
            from mcp.server.fastmcp import FastMCP as Server
    except ImportError as error:
        raise RuntimeError('Run the IOP release installer with IOP_AGENT=1, or install ".[agent]" from the source directory.') from error
    from mcp.types import ToolAnnotations
    try:
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:
        from mcp.server.fastmcp.exceptions import ToolError
    from .client import IOPError
    read_only = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

    options = {'stateless_http': True, 'json_response': True} if http and sys.version_info < (3, 11) else {}
    server = Server('IOP Runtime', **options, instructions=(
        'Read lab_context first and do what its next_steps ask. Read describe_instrument before choosing operations and '
        'arguments. Reuse what is saved: instrument and platform descriptions, and the Standard and Workflow Operations '
        'lab_context lists; a step without device uses one. Submit once with a request_id you choose, keep the run ID and '
        'read results later; after a lost reply repeat the identical request, and find_experiments finds the work of an '
        'earlier conversation. guide() gives the working method and '
        'guide(topic) one part with examples: ' + ', '.join(GUIDES) + '. Record only what was observed: resolve_resources '
        'takes the instrument state seen, report_manual_step what the person stated.' +
        ('' if len(chosen) == len(TOOLSETS) else
         f" This connection offers the toolsets {', '.join(chosen)}. When a next step needs a tool of "
         f"{', '.join(name for name in TOOLSETS if name not in chosen)}, ask the person to add that toolset to this connection (--toolsets or IOP_MCP_TOOLSETS).")))

    def tool(**options):
        """Register a tool whose result reaches the agent as compact JSON rather than indented JSON."""
        register = server.tool(structured_output=False, **options)

        def decorate(function):
            if function.__name__ not in offered:
                return function
            @functools.wraps(function)
            def call(*args, **kwargs):
                try:
                    value = function(*args, **kwargs)
                except (IOPError, OSError, ValueError) as error:
                    # Raised as ToolError so the agent reads the reason; other exceptions reach it as the tool name only.
                    raise ToolError(failure(error)) from error
                return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(',', ':'),
                                                                        default=str)
            return register(call)
        return decorate

    # A refresh lists connections without opening them, so the overview stays a read.
    @tool(annotations=read_only)
    def lab_context(refresh: bool = False, after: int | None = None, device_offset: int | None = None) -> dict:
        """Read a compact laboratory overview. Pass its cursor as after on later reads to receive only changes."""
        if device_offset is not None:
            return client.device_page(device_offset)
        if after is not None:
            return client.changes(after)
        # The overview's notes on HTTP paths are for HTTP clients; the tools and guide() tell an agent the same.
        return {key: value for key, value in client.context(refresh).items() if key not in ('operation', 'schemas')}

    @tool(annotations=read_only)
    def describe_instrument(device: str, operations: list[str] | None = None) -> dict:
        """Read a registered instrument's operations, parameters and units. operations takes name patterns such as ["set.ch1.*"] to return only those."""
        description = client.request('/v1/devices/' + quote(device, safe=''))
        if operations:
            description['operations'] = {name: spec for name, spec in description['operations'].items()
                                         if any(fnmatch.fnmatchcase(name, pattern) for pattern in operations)}
        return description

    @tool(annotations=read_only)
    def describe_platform(platform: str) -> dict:
        """Read a platform's devices, wiring, shared resources, procedures and revision. timing.arm_order is a receiver-before-source suggestion, or null for feedback wiring."""
        return client.platform(platform)

    @tool(annotations=read_only)
    def guide(topic: Literal['overview', 'steps', 'sweeps', 'arrays', 'measurements', 'conditions', 'priors', 'experiments', 'programs', 'setup', 'episodes'] = 'overview') -> str:
        """Read the working method or one topic when needed. steps: ordering, bindings, saved operations. sweeps: scans, repeat, seek, stopping. arrays: selections and fits. measurements: averaging and shared errors. conditions: requires and upkeep. priors: physical operating conditions. experiments: submission and continuation. programs: SDK and saved progress. setup: instruments and platforms. episodes: research trajectories."""
        return guide_text(topic)

    @tool(annotations=read_only)
    def configuration_schema(name: Literal['device', 'platform', 'workflow', 'instrument-profile', 'operation']) -> dict:
        """Read the JSON schema before preparing a device, platform, workflow or saved operation definition."""
        return client.schema(name)

    @tool()
    def register_instrument(definition: dict) -> dict:
        """Register an installed driver and connection configuration. Repeating the identical definition reuses the device. limits, e.g. {"set_current": {"current": {"maximum": 0.05, "note": "The diode is damaged above 50 mA"}}}, bounds what this setup tolerates: arguments beyond are refused before the instrument is called, with the note."""
        return client.register(definition)

    @tool()
    def instrument_configuration(device: str, definition: dict | None = None, expected_revision: int | None = None) -> dict:
        """Read current and previous instrument settings, or save a replacement definition at the current revision while idle. Reapply a previous definition to restore it."""
        if definition is None:
            return {'versions': client.device_history(device)}
        return client.configure_device(device, definition, expected_revision)

    @tool()
    def register_instruments(definitions: list[dict]) -> dict:
        """Register multiple instrument descriptions. IDs may be omitted. Each result names its input index and device or error; successful items remain saved."""
        return client.register_many(definitions)

    @tool()
    def discover_instruments(refresh: bool = True) -> dict:
        """Start a shared background scan, or read its cached results with refresh=false. While scanning=true, other laboratory work can continue. Interfaces and USB inventory are separate collections in the summary."""
        return client.start_discovery() if refresh else client.inventory()

    @tool()
    def identify_instrument(device: str, request_id: str | None = None) -> dict:
        """Query a configured SCPI instrument through the experiment scheduler. Identity is saved and matching model operations are added. Returns a run ID immediately."""
        return client.identify(device, request_id).info(details=False)

    @tool(annotations=read_only)
    def instrument_profiles(manufacturer: str | None = None, model: str | None = None,
                            id: str | None = None, offset: int = 0, details: bool = False) -> dict:
        """Find model descriptions by manufacturer/model or ID. Returns a short page with package versions and manuals; use details=true for operation definitions and next_offset for more results."""
        return client.instrument_catalog(manufacturer=manufacturer, model=model, id=id, offset=offset, details=details)

    @tool()
    def save_instrument_profile(definition: dict) -> dict:
        """Save a reusable model description: id, exact manufacturer/model match, driver, config.operations and source documentation. Use commands from that model's programming manual. For text commands that are not SCPI, add config.protocol (terminators, error_reply, write_reply, echo) and result.match, scale or map; read configuration_schema("instrument-profile")."""
        return client.save_instrument_profile(definition)

    @tool()
    def retry_instrument_discovery(connection_key: str | None = None) -> dict:
        """Retry discovery after correcting a connection or installing a driver. Existing experiments keep their instruments."""
        return client.retry_discovery(connection_key)

    @tool()
    def saved_operations(operation: str | None = None, definition: dict | None = None, program: str | None = None,
                         file: str | None = None) -> dict | list:
        """List saved Standard and Workflow Operations; operation reads one, with file reads a saved program file. definition saves a revision. program uploads a directory or ZIP from this host for a Workflow Operation that decides from results. A step without device uses a saved method; a program runs alone. guide("steps") gives the definition and examples."""
        if definition is not None:
            return client.save_operation(definition, program=program)
        if operation and file:
            return client.program_file(operation, file)
        return client.saved_operation(operation) if operation else client.saved_operations()

    @tool()
    def operating_conditions(platform: str | None = None, device: str | None = None, condition: str | None = None,
                             maintain: bool = False,
                             conditions: list[str] | None = None, run_id: str | None = None,
                             request_id: str | None = None, force: bool = False, invalidate: str | None = None,
                             disturbance: str | None = None, maintenance: str | None = None, wait: float = 0) -> dict | list:
        """Read conditions and their states; platform/device filters them, condition returns one with records. maintain=true verifies and corrects in dependency order: pass platform, device or conditions, request_id, and run_id inside an experiment. force verifies again what holds. Replies wait up to 25 s; continue with maintenance=<id>. invalidate=<reason> marks the selected condition(s), platform or device due; disturbance=<event> reports a platform's named event. guide("conditions") explains requires and upkeep."""
        if maintenance:
            return client.request('/v1/maintenance/' + quote(maintenance, safe='') + waiting(wait))
        if invalidate or disturbance:
            names = list(dict.fromkeys([*(conditions or []), *([condition] if condition else [])]))
            if not names and not disturbance and (platform or device):
                names = [row['id'] for row in client.conditions(platform, device)]
            return client.invalidate(names, invalidate, platform=platform, disturbance=disturbance)
        if maintain:
            return client.request('/v1/conditions/maintain' + waiting(wait), {
                'platform': platform, 'conditions': conditions, 'run_id': run_id, 'request_id': request_id, 'force': force,
                **({'device': device} if device else {})})
        return client.condition(condition) if condition else client.conditions(platform, device)

    @tool()
    def configure_platform(definition: dict, expected_revision: int = 0, preview: bool = False) -> dict:
        """Save devices, physical connections, shared resources, procedures and operating conditions. Use the current revision, or 0 for a new platform. Preview checks the definition without saving; neither option executes a procedure."""
        if preview:
            return client.request('/v1/platforms/preview', {'definition': definition})
        return client.configure_platform(definition, expected_revision)

    @tool()
    def configure_resources(capacities: dict[str, int]) -> dict:
        """Set the full collection of named CPU, GPU, software-license or other resource capacities. Read lab_context for current values first."""
        return client.configure_resources(capacities)

    @tool()
    def run_procedure(platform: str, request_id: str, procedure: str = 'initialize') -> dict:
        """Execute a saved platform procedure. Returns immediately with its run ID."""
        return client.procedure(platform, procedure, request_id).info(details=False)

    @tool()
    def submit_experiment(steps: list[dict], request_id: str, name: str = 'Experiment', project: str | None = None,
                          platform: str | None = None, resources: list[str] | None = None,
                          requirements: dict[str, int] | None = None, requires: list[str] | None = None,
                          upkeep: bool = False, preview: bool = False) -> dict:
        """Submit a step graph and return its run ID. Steps are independent unless after or bindings connect them; a step without device uses a saved operation. guide("steps") gives the form and guide("sweeps") its scan and recovery options. requires names conditions; upkeep=true keeps them. preview=true returns counts, condition states and estimated_s without running. Reuse request_id only for an identical submission."""
        if preview:
            return client.preview(steps, platform=platform, resources=resources or [], requirements=requirements,
                                  requires=requires or (), upkeep=upkeep or None)
        return client.submit(steps, name=name, project=project, platform=platform, resources=resources or [],
                             requirements=requirements, request_id=request_id, requires=requires or (),
                             upkeep=upkeep or None).info(details=False)

    @tool()
    def start_experiment(request_id: str, name: str = 'Experiment', project: str | None = None,
                         platform: str | None = None, resources: list[str] | None = None, parameters: dict | None = None,
                         requirements: dict[str, int] | None = None, requires: list[str] | None = None,
                         upkeep: bool = False) -> dict:
        """Start one experiment for successive decisions: then submit_operations or invoke_operation, and finish_experiment at the end. Reserve every instrument and shared resource the work depends on in resources. It stays open across connections until finished or cancelled. requires names the conditions it relies on and upkeep=true has the Runtime keep them between operations: guide("conditions")."""
        return client.start_experiment(request_id=request_id, name=name, project=project, platform=platform,
                                       resources=resources or [], parameters=parameters, requirements=requirements,
                                       requires=requires or (), upkeep=upkeep or None).info(details=False)

    @tool()
    def invoke_operation(run_id: str, device: str, operation: str, request_id: str,
                         arguments: dict | None = None, wait: float = 0) -> dict:
        """Submit one operation in an external experiment or running program. With wait up to 25 seconds, the reply includes its result if it finishes in that time; otherwise read operation_result before dependent actions. Reuse request_id for an identical retry."""
        return summary(compact(client.request('/v1/runs/' + quote(run_id, safe='') + '/operations' + waiting(wait),
                                              {'device':device, 'operation':operation, 'arguments':arguments or {},
                                               'request_id':request_id})))

    @tool()
    def finish_experiment(run_id: str) -> dict:
        """Finish an external experiment after its operations and file transfers complete. Retains records and releases its reserved resources."""
        return client.run(run_id).finish()

    @tool()
    def submit_operations(run_id: str, steps: list[dict], request_id: str, wait: float = 0) -> dict:
        """Submit a batch within an open experiment; all steps are accepted together. They run in order; after=[] starts a parallel branch and after=[IDs] joins branches. Step options: guide("sweeps"). Replies include progress and results within wait (up to 25 s); complete says whether all finished. read_array(run_id, field, request_id) reads gathered arrays."""
        batch = client.request('/v1/runs/' + quote(run_id, safe='') + '/batches' + waiting(wait),
                               {'steps':steps, 'request_id':request_id})
        batch = compact(batch)
        swept = [step['id'] for step in steps if isinstance(step, dict) and step.keys() & {'sweep', 'repeat', 'seek'}
                 and isinstance(step.get('id'), str)]
        points = {name: [op for op in batch.get('operations', []) if op['step'].startswith(name + '.')] for name in swept}
        inside = {op['id'] for ops in points.values() for op in ops}
        shown = {**batch, 'operations': [summary(op) for op in batch.get('operations', []) if op['id'] not in inside]}
        if swept:
            shown['sweeps'] = {}
            for name, ops in points.items():
                states, errors = [op['state'] for op in ops], [op['error'] for op in ops if op.get('error')]
                stopped = [op['gathers'][name]['stopped'] for op in ops if 'stopped' in op.get('gathers', {}).get(name, {})]
                doubtful = sum(1 for op in ops if op.get('in_doubt') and not op.get('retaken_by'))
                shown['sweeps'][name] = {'points': {state: states.count(state) for state in dict.fromkeys(states)},
                                         **({'stopped': stopped[0]} if stopped else {}),
                                         **({'in_doubt': doubtful} if doubtful else {}),
                                         **({'error': errors[0]} if errors else {})}
        return shown

    @tool()
    def save_acquisition(run_id: str, name: str, operation_ids: list[str],
                         conditions: dict | None = None, analysis: dict | None = None) -> dict:
        """Save completed operation results and their device configurations as JSON. Conditions are caller-supplied; include actual setting readbacks among operation_ids. Analysis must refer to these same acquired results. No instrument commands are sent."""
        return client.run(run_id).save_acquisition(name, operation_ids, conditions=conditions, analysis=analysis)

    @tool()
    def begin_episode(run_id: str, episode_id: str, task: dict, observation: Any,
                      policy: dict | None = None, seed: int | None = None, info: dict | None = None) -> dict:
        """Save a task's initial observation inside an experiment. task and optional policy need id/version. Does not reset instruments."""
        return client.run(run_id).begin_episode(episode_id, task=task, observation=observation,
                                                policy=policy, seed=seed, info=info).info()

    @tool()
    def record_transition(run_id: str, episode_id: str, transition: dict) -> dict:
        """Save sequence, action, observation, reward (or null), terminated, truncated, operation_ids and optional info. IDs must name completed operations in this run. Repeating an identical sequence returns its saved record."""
        return client.request('/v1/runs/' + quote(run_id, safe='') + '/episodes/' + quote(episode_id, safe='') + '/transitions', transition)

    @tool(annotations=read_only)
    def research_episodes(run_id: str, episode_id: str | None = None, after: int = -1, offset: int = 0) -> dict:
        """List research episodes, or read one episode and a page of transitions. Continue from the returned cursor."""
        run = client.run(run_id)
        if episode_id is None:
            return run.episodes(offset=offset)
        episode = run.episode(episode_id)
        return {'episode': episode.info(), **episode.transitions(after=after)}

    @tool(annotations=read_only)
    def available_adapters(adapter: str | None = None) -> dict:
        """Read installed adapters. Pass adapter to read the fields of its device record and operations with an example to adapt from the instrument's manual."""
        return client.adapters(adapter) if adapter else {'adapters':client.adapters()}

    @tool()
    def save_experiment_file(run_id: str, name: str, text: str, operation_ids: list[str] | None = None) -> dict:
        """Save a UTF-8 result or analysis file, optionally linked to completed operations in this experiment. Use the SDK's binary upload for large measurement arrays."""
        return client.run(run_id).save_file(name, text, operation_ids=operation_ids or [])

    def submit_program(path: str, request_id: str, name: str | None = None, project: str | None = None,
                       platform: str | None = None, resources: list[str] | None = None, parameters: dict | None = None,
                       requirements: dict[str, int] | None = None, requires: list[str] | None = None,
                       upkeep: bool = False) -> dict:
        """Upload an experiment directory or ZIP from this MCP host to the instrument service, which runs it independently of this connection. requires and upkeep are those of start_experiment."""
        return client.submit_package(Path(path).expanduser(), request_id=request_id, name=name, project=project,
                                     platform=platform, resources=resources or [], parameters=parameters,
                                     requirements=requirements, requires=requires or (),
                                     upkeep=upkeep or None).info(details=False)

    @tool(annotations=read_only)
    def experiment(run_id: str, after: int = 0, limit: int = 20, include_result: bool = False, in_doubt: bool = False) -> dict:
        """Read an experiment: its state and operation_states, a summary of the next operations (limit up to 100; pass the returned cursor as after) and the first 20 output files. include_result adds the result, partial while a step graph runs. in_doubt=true lists only the operations taken while a condition they relied on may not have held. operation_result returns one full record; experiment_files pages the files."""
        run = client.run(run_id)
        files = run.file_page(0, 20)
        result = {'run': run.info(details=False), 'operations': overview(run.operations(after, max(1, min(int(limit), 100)), in_doubt)),
                  'files': files['files'], 'files_cursor': files['cursor'], 'more_files': files['has_more']}
        if include_result:
            result['result'] = compact(client.request('/v1/runs/' + quote(run_id, safe='') + '/result'))
        if result['run'].get('can_resume'):
            # What can be done about an experiment that stopped, said where it is read.
            result['next'] = ('continue_program(run_id, request_id) continues it from where it stopped once the cause is '
                              'removed; guide("experiments")')
        return result

    @tool(annotations=read_only)
    def find_experiments(search: str | None = None, state: str | None = None, limit: int = 20,
                         before: str | None = None) -> dict:
        """Find experiments, newest first, by text in their name, project, experiment label, request_id or an instrument used, and by state: queued, running, completed, failed, cancelled, interrupted. Pass the returned before for older ones."""
        limit = max(1, min(int(limit), 100))
        found = client.runs(search, state=state, limit=limit, before=before)
        listed = [{key: run[key] for key in FOUND_FIELDS if run.get(key) not in (None, [], {})} for run in found]
        return {'experiments': listed, **({'before': found[-1]['id']} if len(found) == limit else {})}

    @tool()
    def cancel_experiment(run_id: str) -> dict:
        """Request cancellation. In-flight instrument operations finish or report their actual cancellation before their resources are released."""
        return client.run(run_id).cancel()

    @tool(annotations=read_only)
    def experiment_updates(run_id: str, after: int = 0, limit: int = 100, wait: float = 0) -> dict:
        """Read what changed in an experiment since a cursor: its state and the operations that started or ended. wait up to 25 seconds waits for a change. Continue at once while has_more is true; keep the cursor across reconnects."""
        return client.run(run_id).updates(after, limit, wait)

    @tool(annotations=read_only)
    def experiment_files(run_id: str, after: int = 0, limit: int = 100) -> dict:
        """Read one page of saved output files; retain cursor for the next page. Works while experiments are running."""
        return client.run(run_id).file_page(after, limit)

    @tool(annotations=read_only)
    def operation_result(operation_id: str, values: bool = False) -> dict:
        """Read one recorded operation and its result without repeating an instrument action. Arrays longer than 64 values show their description unless values is true; read_array summarizes them."""
        return compact(client.request('/v1/operations/' + quote(operation_id, safe='')), values)

    @tool(annotations=read_only)
    def read_array(operation_id: str | None = None, field: str | None = None,
                   along: str | int | list[str | int] | None = None, at: dict[str, str | int] | None = None,
                   start: int | list[int | None] | None = None, stop: int | list[int | None] | None = None,
                   low: float | list[float | None] | None = None, high: float | list[float | None] | None = None,
                   points: int | list[int] | None = None, peak: Literal['max', 'min'] | None = None,
                   prominence: float | None = None, level: float | None = None, fit: Literal['line', 'lorentzian', 'gaussian'] | None = None,
                   run_id: str | None = None, stream: str | None = None, request_id: str | None = None) -> dict:
        """Read arrays as summaries and bounded samples. Choose operation_id, run_id with field (and request_id for a batch), or run_id with stream. along selects one axis or two for a map; at fixes others. start/stop select indices, low/high coordinates. peak/prominence find peaks, level crossings, and fit line/Lorentzian/Gaussian models with errors. guide("arrays") explains results and limits."""
        if points is None:
            points = 16 if isinstance(along, list) and len(along) > 1 else 32
        options = dict(along=along, at=at, start=start, stop=stop, low=low, high=high, points=points, peak=peak,
                       prominence=prominence, level=level, fit=fit)
        if operation_id is not None:
            if run_id is not None or stream is not None or request_id is not None:
                raise ValueError('Read an operation array with operation_id alone, or an experiment array with run_id')
            return client.read_array(operation_id, field, **options)
        if run_id is None:
            raise ValueError('Give operation_id, or run_id with field or stream')
        if stream is not None:
            if field is not None or request_id is not None:
                raise ValueError('A stream holds one array; give run_id and stream only')
            return client.run(run_id).stream(stream).read_array(**options)
        return client.run(run_id).read_array(field, request_id=request_id, **options)

    @tool()
    def continue_program(run_id: str, request_id: str | None = None) -> dict:
        """Continue an experiment that failed, was cancelled or was interrupted, under the same run ID: pass request_id. A step graph keeps the operations that completed and runs the others again, first retaking points left in doubt; a program that declares resume:true continues from the progress it saved. Without request_id, read the run and its saved progress."""
        run = client.run(run_id)
        if request_id is None:
            return {'run': run.info(details=False), 'checkpoints': run.checkpoints()}
        run.resume(request_id)
        return run.info(details=False)

    @tool()
    def reconnect_instrument(device: str) -> dict:
        """Reconnect a registered instrument or SDK worker after its accepted operations have ended."""
        return client.reconnect(device)

    @tool()
    def release_instrument(device: str) -> dict:
        """Close an idle device connection for use by another application. Shared SDK workers release their devices together. Discovery leaves them released until reconnect_instrument."""
        return client.release(device)

    @tool(annotations=read_only)
    def manual_tasks() -> list:
        """List steps waiting for a person: instruction, arguments and the readings to report."""
        return client.manual_tasks()

    @tool()
    def report_manual_step(operation_id: str, status: Literal['done', 'failed', 'unsure'], readings: dict | None = None,
                           note: str = '', by: str = '') -> dict:
        """Report what a person did for a waiting step: done with its readings, failed, or unsure. Report only what the person stated; unsure holds the step's resources until an observation is recorded."""
        return client.report_manual(operation_id, status, readings, note, by)

    @tool()
    def resolve_resources(resources: list[str], observation: str) -> dict:
        """Return interrupted resources to use after recording the observed instrument state. Read resource_holds in lab_context for the exact resource names."""
        return client.resolve_resources(resources, observation)

    def download_file(artifact_id: str, destination: str) -> dict:
        """Download an experiment output to a new file on this MCP host. Use artifact IDs returned by experiment."""
        path = Path(destination).expanduser().resolve()
        if path.exists():
            raise ValueError('Choose a new output filename: ' + str(path))
        client.run('').download(artifact_id, path)
        return {'path': str(path), 'size': path.stat().st_size}

    def save_experiment(run_id: str, destination: str) -> dict:
        """Save a whole experiment as one ZIP on this MCP host: its records, files and an RO-Crate description that research data repositories read."""
        path = Path(destination).expanduser().resolve()
        if path.exists():
            raise ValueError('Choose a new output filename: ' + str(path))
        client.run(run_id).archive(path)
        return {'path': str(path), 'size': path.stat().st_size}

    if not http:
        tool()(submit_program)
        tool()(download_file)
        tool()(save_experiment)

    # Tool listings are read on every agent turn. Use the SDK's registered schemas when it exposes them.
    manager = getattr(server, '_tool_manager', None)
    for registered in (manager.list_tools() if hasattr(manager, 'list_tools') else []):
        drop_titles(getattr(registered, 'parameters', None))
        if isinstance(getattr(registered, 'parameters', None), dict):
            optional(registered.parameters)
        # An argument a tool does not take is refused. The SDK leaves it out without a word, so that
        # submit_experiment with "require" for requires would run without requiring anything.
        arguments = getattr(getattr(registered, 'fn_metadata', None), 'arg_model', None)
        if arguments is not None:
            arguments.model_config['extra'] = 'forbid'
            arguments.model_rebuild(force=True)
            registered.parameters['additionalProperties'] = False

    def prompt(toolset=None, **options):
        """Register a prompt a person starts work with, offered when the tools it names are."""
        return server.prompt(**options) if toolset is None or toolset in chosen else lambda function: function

    @prompt(title='Begin work in this laboratory')
    def begin() -> str:
        """Read the laboratory and say what is ready and what needs the person."""
        return ('Read `lab_context`. Tell me briefly which instruments and platforms are ready and which are not, which '
                'saved operations there are, which experiments are open or can continue, and what its next_steps ask, of '
                'you and of me. Continue work already authorized by the task. Ask for missing physical details or a '
                'decision that changes its scope, and preserve existing experiments and configuration.')

    @prompt('experiments', title='Plan and run a measurement')
    def measure(goal: str) -> str:
        """Choose a reusable method or program, acquire the data and report its scientific meaning."""
        return ('Goal: ' + goal + '\n\nWork as `guide` describes. Read `lab_context`, then `describe_instrument` for each '
                'instrument involved, and look in `saved_operations` for a method that already does this: use it rather '
                'than new steps. Choose a step graph for known steps, a continuous experiment for decisions between '
                'calls, or a submitted program for an algorithm. Name required conditions in requires. Preview a step '
                'graph before submitting; keep long loops and saved progress in a program. For repeated measurements, '
                'dark subtraction or reference normalization, read `guide(measurements)` and preserve shared calibration '
                'errors. Submit once with a request_id and follow it with `experiment_updates`. Read relevant arrays or '
                'saved results. Report the measured quantity with units and uncertainty, the assumptions of the '
                'reduction, any points in doubt, and what the goal still lacks.')

    @prompt('experiments', title='Report what an experiment observed')
    def report(run_id: str) -> str:
        """An account of one experiment from its record alone."""
        return ('Report on experiment ' + run_id + ' from its record alone. Read it with `experiment` (include_result=true, '
                'then in_doubt=true), `operation_result` for a failure, and `read_array` for each array: its summary, and '
                'lines and fits where they apply. Say what ran, on which instruments and relying on which conditions; '
                'what was observed, with units and errors, and where the files are; what failed or is in doubt, and why; '
                'and whether it can continue. Where the record does not say, say so.')

    @prompt('experiments', title='Save the method of an experiment for reuse')
    def keep(run_id: str) -> str:
        """Turn what an experiment did into a saved Standard or Workflow Operation."""
        spec = client.run(run_id).info().get('spec', {})
        steps = json.dumps(spec['steps'], ensure_ascii=False, separators=(',', ':')) if spec.get('steps') else ''
        return ('The method of experiment ' + run_id + ' is worth keeping. ' + (
                    'It was submitted as these steps:\n\n' + steps + '\n\n' if steps and len(steps) <= PROMPT_STEPS else
                    'Its submission is experiment.json among its files (`experiment_files`). ' if steps else
                    'Read what it did with `experiment`. ') +
                'Turn the values that will differ next time into parameters and save it with `saved_operations` '
                '(definition): a Standard Operation when it is one method built on device operations, a Workflow Operation '
                'when it joins several methods. `guide` with topic steps shows the form. Show me the definition saved, then '
                'read a step that uses it with `submit_experiment` and preview=true, to see that it expands to the same '
                'operations.')

    @server.resource('iop://guides/operations')
    def operating_guide() -> str:
        return guide_text('overview')

    @server.resource('iop://guides/{topic}')
    def topic_guide(topic: str) -> str:
        return guide_text(topic)

    @server.resource('iop://laboratory')
    def laboratory_resource() -> str:
        return json.dumps(client.context(), ensure_ascii=False)

    return server


def run(home, endpoint=None, selection=None):
    if importlib.util.find_spec('mcp') is None:
        raise RuntimeError('Install the Agent component: python -m pip install "ai4instruments[agent]"')
    toolsets(selection)
    from .desktop import ensure_service
    client = ensure_service(home, endpoint)
    create_server(client, selection=selection).run(transport='stdio')

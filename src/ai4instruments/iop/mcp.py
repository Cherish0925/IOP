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

# Hosts that cap or pay for every tool can load only the toolsets a laboratory needs; the three
# descriptions are always offered. Every tool belongs to exactly one toolset.
ALWAYS = ('lab_context', 'describe_instrument', 'describe_platform')
TOOLSETS = {
    'setup': ('configuration_schema', 'register_instrument', 'instrument_configuration', 'register_instruments',
              'discover_instruments', 'identify_instrument', 'instrument_profiles', 'save_instrument_profile',
              'retry_instrument_discovery', 'available_adapters', 'configure_platform', 'configure_resources'),
    'experiments': ('saved_operations', 'run_procedure', 'submit_experiment', 'start_experiment', 'invoke_operation',
                    'finish_experiment', 'submit_operations', 'save_acquisition', 'submit_program', 'continue_program',
                    'experiment', 'cancel_experiment', 'experiment_updates', 'experiment_files', 'operation_result',
                    'save_experiment_file', 'download_file'),
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
                   'calibration_expired', 'result', 'files')


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
        'Read lab_context first. Reuse saved instrument and platform descriptions. '
        'Continuous experiments, batches and acquisitions require service version 0.8.5; use workflows or packages on older services. '
        'Ask for physical wiring and sample details only when missing. '
        'Read describe_instrument before choosing operations and arguments. '
        'Device operations are Basic Operations; lab_context lists the Standard and Workflow Operations saved here. '
        'Use a saved one as a step instead of recomposing it, and save a method that worked with saved_operations. '
        'Saving platform configuration does not execute initialization. '
        'Submit experiments or programs once, retain the returned ID and inspect them later. '
        'For adaptive work, start_experiment, submit_operations within that run, then finish_experiment. '
        'Batch known sequences and parallel branches; pass wait=25 to receive results in the same reply. '
        'Save acquired results by operation ID without reading the instruments again. '
        'Reuse request_id after a connection failure; the runtime schedules devices and retains results. '
        'Follow next_steps in lab_context. A held resource returns to use only through resolve_resources with what was observed. '
        'Report a manual step with report_manual_step only from what the person stated.' +
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
        """Read a compact laboratory overview. Pass its cursor as after on subsequent reads to receive only changes. Device and platform descriptions are fetched when needed."""
        if device_offset is not None:
            return client.device_page(device_offset)
        return client.changes(after) if after is not None else client.context(refresh)

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
        """Read a platform's devices, connections, shared resources, procedures and revision. Trigger and clock wiring appears under timing, with arm_order listing receivers before their sources."""
        return client.platform(platform)

    @tool(annotations=read_only)
    def configuration_schema(name: Literal['device', 'platform', 'workflow', 'instrument-profile', 'operation']) -> dict:
        """Read the JSON schema before preparing a device, platform, workflow or saved operation definition."""
        return client.schema(name)

    @tool()
    def register_instrument(definition: dict) -> dict:
        """Register an installed driver and connection configuration. Repeating the identical definition reuses the device."""
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
        """Standard and Workflow Operations saved in this laboratory: reusable methods built from device (Basic) operations. Without arguments, list them. operation returns one with its parameters, steps, result and use record; with file, the text of that file of a saved program. definition saves a new revision: id, layer (standard | workflow), description, parameters, and steps with result, or, for a workflow that decides from results, program: a directory or ZIP on this MCP host whose experiment.py reads IOP_PARAMETERS_JSON and saves its result. Use one as a step without device: {"id": "s", "operation": name, "arguments": {...}}; a program runs as the only step."""
        if definition is not None:
            return client.save_operation(definition, program=program)
        if operation and file:
            return client.program_file(operation, file)
        return client.saved_operation(operation) if operation else client.saved_operations()

    @tool()
    def configure_platform(definition: dict, expected_revision: int = 0, preview: bool = False) -> dict:
        """Save devices, physical connections, shared resources and procedures. Use the current revision, or 0 for a new platform. Preview checks the definition without saving; neither option executes a procedure."""
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
    def submit_experiment(steps: list[dict], request_id: str, name: str = 'Experiment',
                          platform: str | None = None, resources: list[str] | None = None,
                          requirements: dict[str, int] | None = None) -> dict:
        """Submit instrument operations with after dependencies and optional result bindings. Returns immediately. Reuse request_id only for the identical submission."""
        return client.submit(steps, name=name, platform=platform, resources=resources or [],
                             requirements=requirements, request_id=request_id).info(details=False)

    @tool()
    def start_experiment(request_id: str, name: str = 'Experiment', platform: str | None = None,
                         resources: list[str] | None = None, parameters: dict | None = None,
                         requirements: dict[str, int] | None = None) -> dict:
        """Start one experiment for successive agent decisions. Reserve all mutually dependent devices up front. The run stays open across connections until finished or cancelled."""
        return client.start_experiment(request_id=request_id, name=name, platform=platform,
                                       resources=resources or [], parameters=parameters,
                                       requirements=requirements).info(details=False)

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
        """Submit a batch within an existing experiment. Steps run in order by default. after=[] starts an independent branch; after=[IDs] joins branches. All steps are accepted together. With wait up to 25 seconds, the reply includes results of steps that finish in that time; complete tells whether all did."""
        batch = client.request('/v1/runs/' + quote(run_id, safe='') + '/batches' + waiting(wait),
                               {'steps':steps, 'request_id':request_id})
        batch = compact(batch)
        return {**batch, 'operations': [summary(op) for op in batch.get('operations', [])]}

    @tool()
    def save_acquisition(run_id: str, name: str, operation_ids: list[str],
                         conditions: dict | None = None, analysis: dict | None = None) -> dict:
        """Save completed operation results and their device configurations as JSON. Conditions are caller-supplied; include actual setting readbacks among operation_ids. Analysis must refer to these same acquired results. No instrument commands are sent."""
        return client.run(run_id).save_acquisition(name, operation_ids, conditions=conditions, analysis=analysis)

    @tool()
    def begin_episode(run_id: str, episode_id: str, task: dict, observation: Any,
                      policy: dict | None = None, seed: int | None = None, info: dict | None = None) -> dict:
        """Save a task's initial observation inside an experiment. task and optional policy need id/version. Does not reset instruments. Requires IOP 0.8.6."""
        return client.run(run_id).begin_episode(episode_id, task=task, observation=observation,
                                                policy=policy, seed=seed, info=info).info()

    @tool()
    def record_transition(run_id: str, episode_id: str, transition: dict) -> dict:
        """Save sequence, action, observation, reward (or null), terminated, truncated, operation_ids and optional info. IDs must name completed operations in this run. Repeating an identical sequence returns its saved record. Requires IOP 0.8.6."""
        return client.request('/v1/runs/' + quote(run_id, safe='') + '/episodes/' + quote(episode_id, safe='') + '/transitions', transition)

    @tool(annotations=read_only)
    def research_episodes(run_id: str, episode_id: str | None = None, after: int = -1, offset: int = 0) -> dict:
        """List research episodes, or read one episode and a page of transitions. Continue from the returned cursor. Requires IOP 0.8.6."""
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

    def submit_program(path: str, request_id: str, platform: str | None = None,
                       resources: list[str] | None = None, parameters: dict | None = None,
                       requirements: dict[str, int] | None = None) -> dict:
        """Upload an experiment directory or ZIP from this MCP host to the instrument service. The service executes it independently of the agent connection."""
        return client.submit_package(Path(path).expanduser(), request_id=request_id, platform=platform,
                                     resources=resources or [], parameters=parameters,
                                     requirements=requirements).info(details=False)

    @tool(annotations=read_only)
    def experiment(run_id: str, after: int = 0, limit: int = 20, include_result: bool = False) -> dict:
        """Read experiment status, a summary of the next operations (limit up to 100) and the first 20 output files. Pass the returned cursor as after; page files with experiment_files. operation_result returns a full record; arrays longer than 64 values show their description unless values is true."""
        run = client.run(run_id)
        files = run.file_page(0, 20)
        result = {'run': run.info(details=False), 'operations': overview(run.operations(after, max(1, min(int(limit), 100)))),
                  'files': files['files'], 'files_cursor': files['cursor'], 'more_files': files['has_more']}
        if include_result:
            result['result'] = compact(client.request('/v1/runs/' + quote(run_id, safe='') + '/result'))
        return result

    @tool()
    def cancel_experiment(run_id: str) -> dict:
        """Request cancellation. In-flight instrument operations finish or report their actual cancellation before their resources are released."""
        return client.run(run_id).cancel()

    @tool(annotations=read_only)
    def experiment_updates(run_id: str, after: int = 0, limit: int = 100, wait: float = 0) -> dict:
        """Read changes and operation states since cursor, including completion of previously queued work. Pass wait up to 25 seconds to wait for a change. Arrays stay at operation/result URLs. Continue immediately while has_more is true; retain cursor across reconnects."""
        return client.run(run_id).updates(after, limit, wait)

    @tool(annotations=read_only)
    def experiment_files(run_id: str, after: int = 0, limit: int = 100) -> dict:
        """Read one page of saved output files; retain cursor for the next page. Works while experiments are running."""
        return client.run(run_id).file_page(after, limit)

    @tool(annotations=read_only)
    def operation_result(operation_id: str, values: bool = False) -> dict:
        """Read one recorded operation and its result without repeating an instrument action. Arrays longer than 64 values show their description unless values is true."""
        return compact(client.request('/v1/operations/' + quote(operation_id, safe='')), values)

    @tool()
    def continue_program(run_id: str, request_id: str | None = None) -> dict:
        """Read saved program progress, or pass request_id to continue a stopped program that declares resume:true. The program reads its progress and reuses completed operation IDs."""
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

    if not http:
        tool()(submit_program)
        tool()(download_file)

    # Tool listings are read on every agent turn. Use the SDK's registered schemas when it exposes them.
    manager = getattr(server, '_tool_manager', None)
    for registered in (manager.list_tools() if hasattr(manager, 'list_tools') else []):
        drop_titles(getattr(registered, 'parameters', None))

    @server.resource('iop://guides/operations')
    def operating_guide() -> str:
        from .connector import skill
        return skill().decode('utf-8')

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

# IOP · Agent integration

[Install a host plugin or connect a remote MCP client](connect/README.md)

[中文](agent-setup.zh-CN.md) · [Application and platforms](product.md) · [HTTP API](../docs/runtime/http-api.md) · [Platform schema](schema/platform.schema.json)

Codex, Claude Code, DeepSeek Harness, Qiushi Engine and other research software can connect through MCP, CLI, HTTP or the Python/TypeScript clients. All interfaces use the same service, instruments, platform configuration and experiment records.

## Tasks for an agent

Work with IOP passes through five stages. Each is one task for an agent session: Codex, Claude Code, DeepSeek Harness, Qiushi Engine or another host. Give the text of a stage as it stands, with the bracketed parts filled in. From the second stage on, hosts that list MCP prompts offer the same tasks as commands.

1. **Deploy**, on the computer connected to the instruments:

   > Install IOP from https://github.com/EnvFoundry/iop on this computer, from the release for this system or from source. Start the service with `iop init` and `iop start`, connect this agent host with `iop connect` and complete the installation it prints, then report what `iop context --start` returns.

2. **Connect the instruments**, prompt `connect`:

   > Connect the instruments of this laboratory. Find, register, constrain and verify each one by its class, and ask me only for what neither the instruments nor their manuals tell.

3. **Describe the platform**:

   > Describe the platform [name]: its instruments, wiring, shared resources and the conditions its experiments rely on, from the manuals and from what I tell you. Save it, maintain its conditions once and report their states.

4. **Run an experiment**, prompt `measure(goal)`:

   > [Goal: the quantity, its range, the uncertainty it needs.] Use a saved method where one exists, require the conditions the result relies on, preview, submit once and report the result with its uncertainty.

5. **Research**, prompt `research(question)`:

   > [Question.] Work in rounds of expectation, experiment, analysis and decision; record each decision; stop when the question is answered or the laboratory cannot go further, and report.

`begin` reads where the work stands; `report(run_id)` and `keep(run_id)` close an experiment. Each task follows a guide: `setup`; `conditions` and `priors`; `experiments`, `steps` and `sweeps`; `research`.

## First connection

The release installers include the Agent component. Print the MCP connection configuration using the installed `iop` command:

```sh
iop mcp-config
```

Use its `command` and `args` in your agent host's MCP server settings. The output uses the common `mcpServers` JSON structure; hosts using another configuration format use the same command and argument list. Paths refer to the current installation. No connection token is printed.

The host starts `iop mcp`. It connects to the local service, starting it if necessary. Closing the MCP process leaves that service and accepted experiments running. Call `lab_context` first: it returns saved platforms, instrument operation names, available drivers, visible connections, resource usage and remaining setup work.

Terminal-based agents can instead call:

```sh
iop context --start
```

Add `--refresh` for a new connection scan; otherwise IOP reuses its current inventory. The desktop/browser settings page exports the same laboratory description as JSON.

For source installations, install `.[agent]` in the IOP environment. Set `IOP_AGENT=0` in the release installer to omit MCP dependencies. Python 3.10 uses the maintained MCP SDK 1.x; Python 3.11+ uses SDK 2.x. Both paths are covered by the runtime workflow. SDK references: [v1](https://py.sdk.modelcontextprotocol.io/v1/) and [v2](https://py.sdk.modelcontextprotocol.io/).

## Configure once, then reuse

1. Read `lab_context`. Reuse existing instruments and platforms.
2. For a new instrument, read its documentation and choose an installed adapter. Read `configuration_schema("device")`, then call `register_instrument`. Repeating the identical record reuses the connection.
3. Save devices, physical connections, shared objects and named procedures with `configure_platform`. Use the current revision when editing. Ask only for physical details that cannot be obtained from existing descriptions or connected equipment.
4. Run the saved initialization procedure when the experiment needs it. Saving configuration alone performs no device action.
5. Submit an experiment or program; retain its run ID. Inspect progress and results later. Reuse `request_id` when retrying the same submission.

An individual instrument can operate without a platform. A platform records relationships among devices used together. Serial/parallel ordering comes from experiment dependencies; shared resources describe objects requiring coordinated use.

## MCP tools

| Tool | Purpose |
| --- | --- |
| `lab_context` | Current laboratory description; optional connection refresh |
| `describe_instrument` | Operations, arguments and units for one device |
| `describe_platform` | Devices, connections, shared resources, procedures and signal timing of one platform |
| `guide` | The working method, or one part of it with examples, read when a task needs it |
| `configuration_schema` | Device, platform or workflow JSON schema |
| `register_instrument` | Register a connection using an installed driver |
| `register_instruments` | Register multiple descriptions, returning each result; IDs may be omitted |
| `discover_instruments` | Start background discovery or read the cached inventory |
| `configure_platform` | Save or preview platform configuration |
| `configure_resources` | Set named resource capacities |
| `run_procedure` | Execute initialization or another saved procedure |
| `saved_operations` | List, read or save Standard and Workflow Operations; a step without `device` uses one |
| `operating_conditions` | What must hold on a platform, or on one instrument, for experiments: read the states, maintain them, or record a disturbance |
| `submit_experiment` | Submit a graph of instrument operations and dependencies |
| `submit_program` | Upload a directory or ZIP from the MCP host for service execution |
| `experiment` | Status, paged operations, output files and optional result |
| `find_experiments` | Experiments newest first, found again by name, project, request ID, instrument or state |
| `cancel_experiment` | Request cancellation |
| `reconnect_instrument` | Reconnect a device or a previously unavailable SDK |
| `resolve_resources` | Record observed instrument state and return interrupted resources to use |
| `manual_tasks` | Steps waiting for a person: instruction and readings to report |
| `report_manual_step` | Report a step done with its readings, failed or unsure, as the person stated |
| `download_file` | Save an experiment output on the MCP host |
| `save_experiment` | Save a whole experiment as one ZIP on the MCP host: records, files and an RO-Crate description |

An argument a tool does not take is refused, as is a field a step or a submission does not take: a misspelt `requires` or `retry` never runs an experiment without it.

`iop://laboratory` exposes the same context as an MCP resource. Submissions return immediately. `experiment` returns at most 100 operations per page; pass the returned cursor as `after`. Retrieve large data through output files.

### Guides

Tool descriptions are read on every turn, so they stay short and point to guides read when a task needs them. `guide()` returns the working method; `guide(topic)` returns one part with examples: `steps`, `sweeps`, `arrays`, `measurements`, `conditions`, `priors`, `experiments`, `research`, `programs`, `setup` or `episodes`. The same texts are the skill a host plugin installs (`SKILL.md` with its `references/`), the MCP resources `iop://guides/{topic}`, `iop guide [topic]` in a terminal, `GET /v1/guides/{topic}`, and `guide(topic)` in the Python and TypeScript clients. The texts are in [connect/skills/iop-lab](connect/skills/iop-lab/); a guide names only tools and options that exist, which the runtime cases verify.

### Prompts

Hosts that list MCP prompts offer six as commands a person starts work with; each names the tools to use and what to report.

| Prompt | Starts |
|---|---|
| `begin` | Reading the laboratory: what is ready, what can continue, what needs the person |
| `connect` | Connecting the instruments of this computer: find, register, constrain, and verify each by its class |
| `measure(goal)` | A measurement from a goal: descriptions, a saved method when one exists, a preview, one submission, the arrays |
| `research(question)` | Rounds of expectation, experiment, analysis and decision on one question, each decision recorded |
| `report(run_id)` | An account of one experiment from its record alone, with what failed or is in doubt |
| `keep(run_id)` | Saving an experiment's method as a Standard or Workflow Operation; the steps it was submitted as are placed in the prompt |

`begin` is always offered; `connect` comes with the `setup` and `experiments` toolsets, the others with `experiments`.

### Toolsets

Hosts that cap the number of tools, or carry every tool description in each request, can load part of them: `iop mcp --toolsets experiments,attention`, or `IOP_MCP_TOOLSETS=experiments,attention` in the host's environment (an SSH connection passes it on as the argument). `lab_context`, `describe_instrument`, `describe_platform` and `guide` are always offered. When a next step needs a tool that is left out, the server instructions tell the agent to ask for its toolset. `iop mcp-config --toolsets ...` prints the launcher with the choice.

| Toolset | Tools | For |
| --- | --- | --- |
| `setup` | 12 | Registering instruments, model descriptions, platforms and resources |
| `experiments` | 21 | Saved operations, operating conditions, experiments, batches, acquisitions, programs, results and files |
| `attention` | 5 | Reconnecting and releasing instruments, held resources and manual steps |
| `episodes` | 3 | Research episodes and transitions |

All four (45 tools, about 24 kB of descriptions and parameters) are the default. `experiments,attention` offers 30 tools in about 18 kB for a laboratory that is already set up.

## Remote service

```sh
iop --endpoint http://lab-computer:8765 mcp-config
```

Set `IOP_TOKEN` in the agent host's environment or secret configuration. The MCP process runs beside the agent and forwards work to the reachable instrument service. It does not start another local service when a remote endpoint is selected. See [server configuration](../docs/runtime/README.md).

## SDK access

```python
from ai4instruments.iop import Client
lab = Client()  # Reads environment variables or local connection settings.
print(lab.context()['platforms'])
print(lab.schema('workflow'))
```

```typescript
import { IOP } from '@ai4instruments/iop';
const lab = new IOP(process.env.IOP_ENDPOINT, process.env.IOP_TOKEN);
const context = await lab.context();
const schema = await lab.schema('workflow');
```

HTTP exposes `GET /v1/context` and `GET /v1/schemas/{name}`. Retrieve detailed operations at `GET /v1/devices/{id}` only for instruments needed by the current task.

## Multiple instruments and repeated calls

Start discovery with `discover_instruments()`, then inspect progress with `discover_instruments(refresh=false)`. Serial, VISA and USB inventory remain distinct; model commands come from instrument descriptions, installed drivers and manufacturer documentation. `register_instruments(definitions)` registers multiple devices and returns each generated ID or error. IDs do not require operator input.

Python and TypeScript `run.invoke()` wait for the target operation and return as soon as its result is available. Submit independent calls concurrently; the service schedules device, bus and platform resources. When all steps are known, submit one workflow using `after` and `bindings`. Put feedback-dependent loops inside experiment programs.

[Instrument setup](instrument-setup.md) · [Measurements](performance/README.md)

## Identity and automatic setup

Read `lab_context` first. `discover_instruments(refresh=false).instruments` retains identity, source, presence and required setup fields. Use automatically registered instruments directly. `complete_instrument_setup` entries in `next_steps` identify the remaining protocol, communication settings, driver or model operations.

- `identify_instrument(device)`: schedule an identity read and save its result; returns a run ID.
- `instrument_profiles()`: read bundled and local model descriptions.
- `save_instrument_profile(definition)`: save model operations and add new operations to identified devices.
- `retry_instrument_discovery(connection_key)`: retry a connection immediately; omit the key to retry all.

Python: `identify()`, `instrument_profiles()`, `save_instrument_profile()`, `retry_discovery()`. TypeScript: `identify()`, `instrumentProfiles()`, `saveInstrumentProfile()`, `retryDiscovery()`. Read `configuration_schema("instrument-profile")` for the description format.

## Continuing work

The default context links to full descriptions; use `lab_context(after=cursor)` for subsequent changes. `instrument_configuration` reads or edits versioned settings. `continue_program` continues an experiment that stopped: a step graph from the operations that completed, a program that declares resume support from the progress it saved. [Program progress and binary data](continuity.md).

## Installed model descriptions

`instrument_profiles(manufacturer=..., model=...)` returns short pages. Continue with `next_offset`; set `details=true` for operation definitions. Sources include package versions and original documentation. Equally applicable descriptions remain explicit choices applied through existing device configuration. [Instrument support packages](instrument-packages.md).

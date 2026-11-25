# Connect an agent to IOP

IOP keeps instruments, methods, experiments and results in one laboratory service. MCP, CLI, HTTP and the Python/TypeScript clients use that same service.

## First connection

Install IOP on the instrument computer. Follow the [connection guide](connect/README.md) for your agent host and for local, SSH or remote access. For a local MCP host:

```sh
iop mcp-config
```

Use the returned command and arguments in the host's MCP settings, then call `lab_context`. A terminal-based agent can use `iop context --start`. Once connected, begin with the current laboratory state and the [working method](connect/skills/iop-lab/SKILL.md).

## Tasks for an agent

Choose the task needed now. Replace known bracketed details; existing installations and experiments carry forward.

**Deploy**

> Connect this agent to IOP from https://github.com/EnvFoundry/IOP for [instrument computer]. Identify where your tools and the instruments run. Reuse the existing installation and data, or install IOP there. Follow the connection guide for this host. Complete a lab_context call and report the connection and data location. Tell me the exact local action if one is needed.

**Connect instruments** — MCP prompt `connect`

> Connect [devices] for [task]. Reuse existing descriptions and read guide("setup"). Take commands, units and ranges from the model manual or SDK. Ask only for missing wiring, sample or apparatus information and save it. Try the operations needed for the task and report their actual response.

**Describe a platform**

> Save or update [platform] for [task]: its instruments, wiring, shared resources, reusable methods and relevant operating conditions. Use existing records and the physical details I supply. Prepare only what the next experiment needs.

**Measure** — MCP prompt `measure(goal)`

> Measure [quantity, range and precision] on [platform]. Reuse a suitable method, prepare its conditions and run the experiment. Read the result with its units and relevant uncertainty. Save the data and a short explanation of what it means.

**Research** — MCP prompt `research(question)`

> Investigate [question] on [platform] within [budget, if specified]. Start from earlier results. Choose measurements or calculations that help answer the question, compare predictions with observations, and use the result to choose the next action. Keep the objective while changing an unproductive approach. Save the result and what to do next.

**Continue**

> Continue [project or run]. Find its saved records, follow work still running and reuse completed results. Resume interrupted work after resolving its cause. Ask only for changed or missing physical information. Keep useful methods and apparatus knowledge for later work.

The prompts `begin`, `report(run_id)` and `keep(run_id)` read the laboratory, explain a result and retain a method. Use `finish_experiment` to close a continuous experiment after its operations complete. Hosts without MCP prompts can use the task text through their CLI or client connection.

## Keep the context small

Read one instrument, guide or result when needed. Use cursors for changes and array slices for data. Details remain in the laboratory.

Connections can select toolsets with `iop mcp-config --toolsets experiments,attention`: 30 tools for an established laboratory. Add `setup` to register instruments and platforms; add `episodes` for learning trajectories. All 45 tools remain available when no selection is supplied.

The same on-demand guide is available through `guide(topic)`, `iop guide [topic]`, `iop://guides/{topic}` and the clients. Native HTTP MCP uses a bridge or SDK for local file transfers.

[Host connections](connect/README.md) · [Instrument setup](instrument-setup.md) · [Python, TypeScript and HTTP](../docs/runtime/http-api.md) · [Program continuation](continuity.md) · [Instrument packages](instrument-packages.md)

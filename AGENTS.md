# Working on IOP

To install IOP and work with instruments, rather than develop IOP, follow the
[tasks for an agent](iop/agent-setup.md#tasks-for-an-agent).

IOP runs instruments and experiment programs independently of their agent host.
Start with [README.md](README.md); the runtime's responsibilities are in
[architecture](docs/runtime/architecture.md). Operating guidance lives in
`iop/connect/skills/iop-lab/` and is also served through HTTP, MCP and the clients.

| Source | Responsibility |
| --- | --- |
| `src/ai4instruments/iop/engine.py`, `library.py`, `conditions.py` | Scheduling, reusable operations and maintained conditions |
| `drivers.py`, `*_driver.py`, `plugins.py` | Device semantics, transports and installed driver packages |
| `store.py`, `arrays.py`, `acquisitions.py`, `streams.py` | Persistent records and scientific data |
| `measurements.py` | Scientific reduction with explicit uncertainty assumptions |
| `client.py`, `server.py`, `cli.py`, `mcp.py` | Python, HTTP, command and agent interfaces |
| `iop/clients/typescript/` | Host and experiment client |
| `iop/daemon/` | Rust process ownership and file-backed array views |
| `examples/iop/` | Adaptable experiment programs |

Keep new device behavior in a description or driver, research algorithms in
programs, and common scheduling in the runtime. Preserve existing request IDs,
stored experiments and configuration when extending formats. An interrupted
connection does not mean an instrument action can be repeated.

Keep summaries small; return details and arrays on request. Add guide topics to
`mcp.GUIDES`, its tool annotation and the TypeScript guide type. Update English
and Chinese entry documents together. Change source guides, then regenerate
their packaged copies; generated output is not a second source.

Useful commands from the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests
cargo test --locked --manifest-path iop/daemon/Cargo.toml
npm test --prefix iop/clients/typescript
python scripts/build_iop_connect_guides.py
python scripts/build_iop_connector.py
```

Use focused cases while developing; run the relevant runtime/client checks
before merging. Preserve contributor commits when integrating work. Explain
the resulting behavior and any external equipment or SDK still required.

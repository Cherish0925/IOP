# IOP 0.8.6

IOP now has an independent product repository. Existing Python imports, CLI commands, TypeScript package names and laboratory data directories remain compatible with 0.8.5.

Research episodes retain versioned task and policy descriptions, initial observations and ordered action/observation/reward records. Every transition references completed operations from the same experiment. Identical retries reuse stored records; changed content at an existing sequence is rejected. Python can export a bounded-memory JSON Lines snapshot. The same records are available through TypeScript, HTTP and MCP.

Device execution, continuous experiments, resource scheduling, acquisition files and native desktop/browser entry points retain the existing implementation. Episode recording does not reset equipment or implement a learning algorithm.

The native workflow builds platform archives on Windows, macOS and Linux, for x86_64 and ARM64. Available assets are listed on this release page. Hardware support remains determined by installed drivers, model descriptions and the connected experimental platform.

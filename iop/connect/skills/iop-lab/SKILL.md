---
name: iop-lab
description: Use IOP to operate instruments or scientific software, submit experiments, and retrieve laboratory results.
---

# Operate a laboratory

Use the service version and tools reported by the selected connection. Continuous experiment, batch and acquisition APIs require IOP 0.8.5; older services use workflow and program submissions until upgraded while idle.

Read `lab_context` on the selected connection. Keep its name alongside the run ID: IDs belong to the service that issued them. Reuse the saved platform. Read `describe_instrument` for operation names, arguments and units. A discovered port alone does not identify an instrument, a sample or its wiring.

When building a platform, use the manufacturer's device description and the observed connections. Ask the operator only for information that cannot be read or a physical change that cannot be performed remotely. Preview and save the platform definition; run its initialization procedure separately if the experiment needs it.

For adaptive or repeated work, use `start_experiment` once and keep its run ID. Reserve all mutually dependent instruments and shared physical resources up front. Use `submit_operations` for each known sequence: missing `after` means input order, `after=[]` starts an independent branch, and `after=[IDs]` joins dependencies. On IOP 0.8.7, use `experiment_updates(run_id, after=cursor, wait=25)` to receive changed states. Keep its cursor, and read another page immediately while `has_more` is true. Use `operation_result` only for the measurements needed for the next decision. Older services use `experiment`. Finish with `finish_experiment` after operations and file transfers complete.

Save an acquisition with `save_acquisition` using the completed operation IDs. Analyze those same results; do not acquire again just to save. Include setting readbacks among the operation IDs and put sample or wiring information in `conditions`. Use the SDK for large arrays and long-running analysis.

Use `available_adapters` for connection parameters. To hand an idle instrument to other software, call `release_instrument`; call `reconnect_instrument` to use it again.

Submit an operation graph with `submit_experiment`, or a research program with `submit_program`. Declare dependencies for steps whose order matters and shared resources for operations that cannot overlap. Keep timing that requires a hardware clock or trigger on the instrument. Program paths and download destinations refer to the computer running the MCP bridge.

Choose a request ID before submitting. Save the returned run ID; a receipt means the service accepted the run. If the reply is lost, repeat the identical submission with its original request ID. Use `experiment_files` to page through output files while the experiment runs. The operation-list cursor enumerates newly submitted operations; the update cursor tracks later completion too. Do not use an operation-list cursor as a completion cursor. Closing the client leaves the laboratory service running. Use `cancel_experiment` only when cancellation is intended. Continue an interrupted program through its saved checkpoints when the program supports it.

For research trajectories or learning data on IOP 0.8.6, use `begin_episode` after preparing and observing the initial state. Supply a versioned task, optional policy and the initial observation. `record_transition` saves an explicit sequence, action, next observation, reward or null, terminated/truncated flags and completed operation IDs. Reuse the same sequence and content after a lost response. `research_episodes` reads saved progress. Beginning an episode does not reset a physical instrument. Keep uncertainty or failed operations in their original records rather than labeling them as completed transitions. For repeated numerical decisions, run the research program next to the Runtime and use its SDK.

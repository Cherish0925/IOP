# Instruments and platforms

## Connecting the instruments for a task

Read `lab_context` and the existing descriptions first. Apply the following steps to missing or changed devices needed for the task, on the computer the service runs on. Ask the person only for information absent from records, instruments and manuals: physical connections, sample state or setup-specific limits. Keep that information with the instrument or platform so later conversations can reuse it.

1. **Find.** `discover_instruments` lists serial, USB, VISA and network connections; instruments a discovery profile recognizes are registered already. A port is not yet an identified model. For a configured SCPI device, `identify_instrument` reads its identity and adds operations from a matching model description; `instrument_profiles` finds model descriptions for others. Use the documented identification method for other protocols.
2. **Register.** Choose the adapter by how open the instrument is (below), take operations, units and ranges from its manual, and register it with `register_instrument(definition)`, or several with `register_instruments`.
3. **Constrain.** Add the `limits` of this setup, the measuring interval and status of readings (`result`, `quality`), and the instrument's own conditions such as warm-up, lock or zero (`guide("priors")`).
4. **Verify.** Check the operations the task needs (below) in a short experiment within the established limits and conditions. Compare observations with the description; a successful command or a setpoint echo alone does not establish the physical effect. Report the device ID, supported operations, observed result and run ID, and anything still missing.

### By how open the instrument is

| The instrument offers | Connect it with |
| --- | --- |
| A documented protocol: SCPI or other text commands over a socket, serial or VISA; registers; OPC UA; EPICS; a web API | A device record for `scpi` (with `protocol` when the commands are not SCPI), `modbus`, `opcua`, `epics` or `http`, when that adapter expresses the interface |
| A vendor SDK, a Python package or a driver library | `python`: an object of the installed package with described operations; `qcodes` for its drivers; `process` for an SDK that needs its own process |
| Only vendor software, with a command line or files | `command` |
| No electrical interface: a knob, a manual valve, a manual stage | Use `manual`; if the task calls for automation, describe the physical actuator or sensor needed and connect it after installation |
| An action this setup cannot automate, such as loading a sample | `manual`: a step the person performs and reports |

Add a driver when no adapter can express the interface.

### By what the instrument does

| Class | Its operations | Verify by | Its first conditions |
| --- | --- | --- | --- |
| Sensor: detector, meter, camera, balance | Observe; configure acquisition | A reference appropriate to the quantity and range; a dark or blank checks the background, not the full calibration | Background, measuring interval and warm-up where relevant |
| Actuator: stage, valve, switch, source, modulator | Change; observe its effect | A bounded change confirmed by actual-state readback or an independent sensor | Position reference if needed, settling, approach direction |
| Controller: temperature, pressure, flow, field, power | Set a target; observe the controlled quantity | The measured quantity follows the target within the method's tolerance | Target agreement; stability or tracking for the chosen method |
| Computation: solver, simulation, analysis program | Run; observe its output | A known solution, limiting case or independent comparison | Convergence, residual or resolution where the algorithm needs it |

These are operation roles, not exclusive device types. A source meter acts and observes; a camera also changes exposure. Choose checks for the operation and scientific use, and keep settings distinct from measured state.

## The record

`available_adapters(adapter)` returns the fields of the device record and an example to adapt; `configuration_schema("device")` gives every field. `retry_instrument_discovery` looks again after a connection was corrected or a driver installed; `save_instrument_profile` saves a model description for every instrument of that model.

In the record:

- `allowed_operations` keeps only the operations whose names match;
- an operation's `result` states its measuring interval with `minimum` and `maximum`, and `saturation` where it clips; `quality` names the status read after a reading: for text commands `{"query": "STAT:QUES:COND?", "bits": 32767}` or `{"query": "RDGST? {input}", "equals": 0}`, for any adapter another observe operation, `{"operation": "status", "arguments": {"channel": "{channel}"}, "value": ["overrange"], "equals": false}`. A reading outside the interval, at saturation, or with a bad status is kept as uncertain and in doubt;
- `limits` bounds what this setup tolerates, within what the instrument takes: `{"set_current": {"current": {"maximum": 0.05, "note": "The diode is damaged above 50 mA"}}}`. Arguments beyond are refused before the instrument is called, with the note.

`instrument_configuration` reads earlier versions and saves a replacement while the instrument is idle. `release_instrument` hands an idle instrument to other software; `reconnect_instrument` takes it back.

## Platforms

A platform records the instruments of one setup, their physical connections, shared resources, procedures and operating conditions. `describe_platform` returns it; `timing.arm_order` is a suggestion derived from wiring, null for feedback connections. Build one from the manufacturers' descriptions and the connections observed; preview and save with `configure_platform`. Saving runs nothing: run `run_procedure(platform, request_id)` for its initialization when the experiment needs it. `configure_resources` sets the capacities of named pools such as GPUs or licenses.

Write what the next experiment relies on as conditions (`guide("conditions")`, `guide("priors")`). When preparing to run, maintain that selection with `operating_conditions` and report its states. Saving a platform alone does not require operating every device or establishing every condition.

For a physical step, give the person the required action, what to observe and how to report completion. Use the `manual` adapter when execution must wait for that action; `manual_tasks` and `report_manual_step` retain the handoff. A sample change or rewiring can invalidate earlier conditions: record the disturbance and recheck the affected observations.

## What needs attention

`lab_context.next_steps` names what stands in the way.

| Step | Do |
| --- | --- |
| `continue_experiments` | Experiments a stop of the service left interrupted: record what `resolve_first` names with `resolve_resources`, then `continue_program(run_id, request_id)`: `guide("experiments")` |
| `maintain_conditions` | `operating_conditions(maintain=true, ...)`: `guide("conditions")` |
| `manual_tasks` | Read `manual_tasks`, tell the person the instruction, report with `report_manual_step` only what they stated |
| `reconnect` | `reconnect_instrument` once the cause is removed |
| `inspect_monitors`, `renew_calibration` | Read the instrument's state; a held resource returns to use only through `resolve_resources` with what was observed |
| `complete_instrument_setup`, `select_instrument_profile` | Finish describing a discovered instrument |

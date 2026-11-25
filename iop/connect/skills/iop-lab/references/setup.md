# Instruments and platforms

## Connecting the instruments of a computer

Each instrument is connected in four steps. Do them with the tools below, and ask the person only for what neither the instrument nor its manual tells: the wiring, the sample, and what this setup tolerates.

1. **Find.** `discover_instruments` lists serial, USB, VISA and network connections; instruments a discovery profile recognizes are registered already. `identify_instrument` reads a SCPI identity and adds the operations of a matching model description; `instrument_profiles` finds model descriptions for others.
2. **Register.** Choose the adapter by how open the instrument is (below), take operations, units and ranges from its manual, and register it with `register_instrument(definition)`, or several with `register_instruments`.
3. **Constrain.** Add the `limits` of this setup, the measuring interval and status of readings (`result`, `quality`), and the instrument's own conditions such as warm-up, lock or zero (`guide("priors")`).
4. **Verify.** Exercise its operations as its class requires (below) in a short experiment, and compare what it did with what its description says. Correct the record before anything relies on it.

### By how open the instrument is

| The instrument offers | Connect it with |
| --- | --- |
| A documented protocol: SCPI or other text commands over a socket, serial or VISA; registers; OPC UA; EPICS; a web API | A device record for `scpi` (with `protocol` when the commands are not SCPI), `modbus`, `opcua`, `epics` or `http`; no code |
| A vendor SDK, a Python package or a driver library | `python`: an object of the installed package with described operations; `qcodes` for its drivers; `process` for an SDK that needs its own process |
| Only vendor software, with a command line or files | `command` |
| No electrical interface: a knob, a manual valve, a manual stage | Fit an actuator or a sensor that has one, such as a motor, a relay, a DAQ or a camera, and connect that |
| Nothing a machine can do: loading a sample, a judgment by eye | `manual`: a step the person performs and reports |

Add a driver when no adapter can express the interface.

### By what the instrument does

| Class | Its operations | Verify by | Its first conditions |
| --- | --- | --- | --- |
| Sensor: detector, meter, camera, balance | Observe | Reading a known reference, or the state without stimulus; units, range and noise as the manual states | Zero or dark level, measuring interval, warm-up |
| Actuator: stage, valve, switch, source, modulator | Change, with a readback | A small change inside its limits, confirmed by its readback or by a sensor that sees the effect | Position reached, reference position after power-on, settling, backlash |
| Controller: temperature, pressure, flow, field, power | Change a setpoint; observe the controlled quantity | A nearby setpoint that the controlled quantity reaches and holds within tolerance | At setpoint, steady, locked |
| Computation: solver, simulation, analysis program | Run; observe its output | A case with a known answer | Converged, residual below tolerance |

Many instruments combine classes: a source meter acts and observes, a laser with power stabilization is a controller. Verify each of its operations by the class it belongs to.

## The record

`available_adapters(adapter)` returns the fields of the device record and an example to adapt; `configuration_schema("device")` gives every field. `retry_instrument_discovery` looks again after a connection was corrected or a driver installed; `save_instrument_profile` saves a model description for every instrument of that model.

In the record:

- `allowed_operations` keeps only the operations whose names match;
- an operation's `result` states its measuring interval with `minimum` and `maximum`, and `saturation` where it clips; `quality` names the status read after a reading: for text commands `{"query": "STAT:QUES:COND?", "bits": 32767}` or `{"query": "RDGST? {input}", "equals": 0}`, for any adapter another observe operation, `{"operation": "status", "arguments": {"channel": "{channel}"}, "value": ["overrange"], "equals": false}`. A reading outside the interval, at saturation, or with a bad status is kept as uncertain and in doubt;
- `limits` bounds what this setup tolerates, within what the instrument takes: `{"set_current": {"current": {"maximum": 0.05, "note": "The diode is damaged above 50 mA"}}}`. Arguments beyond are refused before the instrument is called, with the note.

`instrument_configuration` reads earlier versions and saves a replacement while the instrument is idle. `release_instrument` hands an idle instrument to other software; `reconnect_instrument` takes it back.

## Platforms

A platform records the instruments of one setup, their physical connections, shared resources, procedures and operating conditions. `describe_platform` returns it; `timing.arm_order` is a suggestion derived from wiring, null for feedback connections. Build one from the manufacturers' descriptions and the connections observed; preview and save with `configure_platform`. Saving runs nothing: run `run_procedure(platform, request_id)` for its initialization when the experiment needs it. `configure_resources` sets the capacities of named pools such as GPUs or licenses.

Then write what the experiments on it rely on as conditions (`guide("conditions")`, `guide("priors")`), maintain them once with `operating_conditions`, and keep the states they reach as the platform's starting point.

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

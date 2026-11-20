# Instruments and platforms

## A new instrument

Describe an instrument with an installed adapter, taking commands, ranges and units from its manual. Add a driver when existing adapters cannot express its interface.

| Interface | Adapter |
| --- | --- |
| Text commands over a socket, serial or VISA | `scpi`; add `protocol` when they are not SCPI |
| Registers | `modbus` |
| A web API | `http` |
| Industrial and accelerator controls | `opcua`, `epics` |
| An installed Python package or vendor SDK | `python`, `qcodes`; `process` for an SDK that needs its own process |
| A program or command-line solver | `command` |
| A step a person performs | `manual` |

`available_adapters(adapter)` returns the fields of the device record and an example to adapt; `configuration_schema("device")` gives every field. `discover_instruments` lists connections, and `retry_instrument_discovery` looks again after a connection was corrected or a driver installed; `identify_instrument` reads a SCPI identity and adds the operations of a matching model description. `instrument_profiles` finds model descriptions; `save_instrument_profile` saves one for every instrument of that model.

Register with `register_instrument(definition)`, or several at once with `register_instruments`. In the record:

- `allowed_operations` keeps only the operations whose names match;
- `limits` bounds what this setup tolerates, within what the instrument takes: `{"set_current": {"current": {"maximum": 0.05, "note": "The diode is damaged above 50 mA"}}}`. Arguments beyond are refused before the instrument is called, with the note.

`instrument_configuration` reads earlier versions and saves a replacement while the instrument is idle. `release_instrument` hands an idle instrument to other software; `reconnect_instrument` takes it back.

## Platforms

A platform records the instruments of one setup, their physical connections, shared resources, procedures and operating conditions. `describe_platform` returns it; `timing.arm_order` is a suggestion derived from wiring, null for feedback connections. Build one from the manufacturers' descriptions and the connections observed; preview and save with `configure_platform`. Saving runs nothing: run `run_procedure(platform, request_id)` for its initialization when the experiment needs it. `configure_resources` sets the capacities of named pools such as GPUs or licenses.

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

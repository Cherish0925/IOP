# Instruments and platforms

Read `lab_context` and reuse existing descriptions. Work on missing or changed devices needed for this task, through the service on the instrument computer. Ask only for physical details absent from records, manuals and instrument readings; save those details with the apparatus.

## Connect a device

`discover_instruments` finds interfaces and devices. Use automatically registered devices directly. For a missing device, find its model with `instrument_profiles` or read its programming manual. `identify_instrument` reads a configured SCPI device's identity; other protocols use their documented identification command.

| Available interface | Adapter |
| --- | --- |
| SCPI or other text commands over serial, sockets or VISA | `scpi`; use `protocol` for non-SCPI framing |
| Registers, OPC UA, EPICS or a web API | `modbus`, `opcua`, `epics`, `http` |
| Vendor SDK or driver library | `python`, `qcodes`, or `process` for a separate SDK process |
| Vendor command line or file interface | `command` |
| A manual action, such as loading a sample or turning a knob | `manual`; connect an actuator later if automation is needed |

`available_adapters(adapter)` gives the fields and an example; `configuration_schema("device")` gives the complete format. Add a driver if the existing adapters cannot express the interface.

Use `register_instrument(definition)` or `register_instruments` to save the connection. Describe operations, arguments, units and ranges from the model documentation. Put apparatus-specific bounds in `limits`, reading range and saturation in `result`, and instrument status in `quality`. Read `guide("priors")` when the method depends on apparatus knowledge.

Try the operations needed for the task. Compare a sensor with an appropriate reference, an actuator with actual-position readback or another sensor, a controller with its measured response, and a computation with a known case. A setpoint echo describes the requested setting; it does not measure the physical response. Report what worked and what still needs attention.

## Describe a platform

An individual instrument can run on its own. A platform relates devices used together: wiring, shared resources, methods and operating conditions. Read `describe_platform`, update it with `configure_platform`, and run a saved initialization only when needed using `run_procedure`. `timing.arm_order` suggests receiver-before-source ordering from wiring; feedback wiring has no such order. `configure_resources` sets capacities such as GPU slots or licenses.

Add only conditions the experiment needs, using `guide("conditions")`. A physical task states what the person should do and report. The `manual` adapter waits for that action; read `manual_tasks` and use `report_manual_step` with the person's response. Sample changes or rewiring may require updating related conditions.

## Reuse and changes

- `save_instrument_profile` keeps model operations for other devices of that model; `configuration_schema("instrument-profile")` describes the format.
- `instrument_configuration` reads or updates a device's settings while it is idle.
- `retry_instrument_discovery` repeats discovery after installing a driver or correcting a connection.
- `release_instrument` gives an idle device to other software; `reconnect_instrument` takes it back.
- Handle `lab_context.next_steps` relevant to this task. For interrupted operations and continuation, read `guide("experiments")`.

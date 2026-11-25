# Apparatus knowledge

Use what is already known about the apparatus: its manual, wiring, sample, calibration and operating experience. Ask the operator only for missing information. Save connections in the platform, device bounds in `limits`, repeatable procedures in saved methods, and useful explanations in descriptions or `note`.

The experiment determines which conditions matter. Select them with `requires`; use `upkeep` when they need renewal. An equilibrium measurement and a transient experiment can use the same apparatus with different conditions. Read `guide("conditions")` for the format and maintenance tools.

## Turn experience into a method

For each relevant condition, identify the observation (`verify`), the range or behavior the method needs (`accept`), and a way to restore it (`correct`) if one exists. Use `samples` and `interval` when one reading is insufficient, `settle` for the response after a change, and `valid_for` for an observed period of applicability. Keep the source and circumstances in `note`.

A background or calibration coefficient belongs in `reference` for later analysis. It changes how data are reduced; an actuator correction changes the apparatus. Methods involving richer data or coupled adjustments can use a saved operation or program.

| Physical property | What to observe or retain |
| --- | --- |
| Warm-up and equilibrium | Drift after switch-on or a setting change: temperature, source output, pressure or baseline |
| Controlled state | Measured temperature, current, flow or position compared with the instrument's setpoint |
| Zero and background | Dark counts, tare, blank or baseline under the relevant settings |
| Calibration | Response to a known reference over the range used |
| Measuring range | Noise floor, saturation, overload, nonlinearity and the instrument's status |
| Environment | Temperature, vibration, ambient light or another influence that affects this measurement |
| Alignment and contact | Coupling, focus, probe contact or sample position; use a suitable bounded adjustment |
| Locks and synchronization | Reference, lock state and trigger timing; fast timing stays on the instruments |
| Coupled devices | Disturbances from a shared supply, vibration, heating or overlapping use |
| Hysteresis and settling | Approach direction, response time and relevant preparation history |
| Sample state | Identity, preparation, concentration, loading, degradation or irreversible changes |
| Position reference | Whether position remains known after restart; home incremental axes when required by the device and setup |

A mean at the setpoint can conceal oscillation: include spread or drift when stability matters. A dark reading measures background, not the whole calibration. Absolute-position devices may retain their reference; establish the applicable procedure and travel limits before moving an axis.

Use `after` only for actual dependencies, such as cooling before heating or a reference clock before synchronization. The order depends on the apparatus and method. Independent observations can run together, and a correction cannot depend on the condition it restores.

## Example

A cryostat reading can be compared with its current setpoint. Use this list in the platform's `conditions`. Its operations must be described for the actual controller and report compatible units.

```json
[
  {
    "id": "cryostat.at_setpoint",
    "description": "The sample stage is at its setpoint",
    "verify": {"device": "cryostat", "operation": "read_kelvin", "arguments": {"input": "A"}},
    "samples": 5,
    "interval": 2,
    "accept": {"target": {"device": "cryostat", "operation": "read_setpoint", "arguments": {"output": 1}}, "within": 0.02},
    "correct": {"device": "cryostat", "operation": "wait", "arguments": {"seconds": 120}}
  }
]
```

## Keep experience useful

Record a changed sample, cable, range or enclosure as a named platform `disturbance`, or use `operating_conditions(invalidate="what changed", conditions=[...])`. Renew the affected conditions and retain the rest. `operating_conditions(condition=...)` shows prior observations, drift and correction outcomes; use them to improve the method and its timing. Keep estimates and hypotheses distinguishable from measured state, and revise them when new observations disagree.

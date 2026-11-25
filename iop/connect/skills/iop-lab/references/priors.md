# Physical priors

A measurement method assumes particular apparatus and sample states. Depending on the method, these can include equilibrium, calibration, a measuring interval, alignment, synchronization or a controlled environment. Transient and driven experiments may require a changing state instead. Write the observable assumptions as conditions (`guide("conditions")`), select them with `requires`, and use `upkeep` where they need renewal. The Runtime then checks and maintains those declared conditions and keeps their observations with the experiment.

## From what the operator knows to a condition

Ask the operator, or read the manual, for each question; each answer is one field of the condition saved with `configure_platform(definition)`, or of an instrument's record when it belongs to that instrument wherever it is used.

| Question | Field |
| --- | --- |
| Which short reading shows that it holds? One operation or a saved one; a saved one can return a value from an array view, such as a fitted peak | `verify` |
| Is one reading enough, or does it take several to see that the quantity is steady? | `samples`, `interval` |
| Which values does this experiment accept? Derive them from the uncertainty the measurement can bear | `accept`: a range, a `target` with `within` and `relative`, or the `bits` of a status register that must be clear |
| Is it about the level, the scatter or the drift of the readings? | `statistic`: `mean`, `std`, `span`, `slope` |
| Below which value does the reading say nothing about it, such as no signal reaching the detector? | `usable` |
| What restores it, in order of preference? An operation, a saved procedure, `tune`, then a step for a person | `correct` |
| How long after a correction is the reading representative: thermal, mechanical, chemical, electronic settling? | `settle` |
| How long does an accepted reading stay true? Start short and lengthen it from the records | `valid_for` |
| What must hold first? | `after` |
| Which value does the analysis use: zero, blank, background, baseline, dark level, time zero? | `reference` |
| What should the next person know: what disturbs it, how fast it drifts, what to avoid? | `note` |

- One decision per condition. "At temperature" compares a mean with a target; "steady" constrains a standard deviation, span or slope. Use separate conditions when both matter: a mean near the target can conceal oscillation. Join conditions with `after` only when one observation depends on another. A condition that holds for every channel, input or axis is written once with `each`.
- Tolerances may be given with their unit, `"within": "20 mK"`, `"maximum": "1 K/min"` with `slope`, `"relative": "0.1 %"`, when the description states the unit of the reading; they are kept in that unit. Arguments of corrections may be given with theirs, such as `"5 min"`.
- A dynamic target with a declared unit is converted to the verification unit. Without declared units both readings must already use the same numeric convention; check configurable instrument units rather than assuming them.
- `usable` checks every sample unless it explicitly selects a statistic; one invalid reading must not be hidden by averaging.
- A correction acts on the system; a `reference` acts on the data. A zero, a blank or a dark level has no correction other than measuring it again.
- Settings adjusted against each other belong to one saved operation or to one `tune` over several settings; the graph stays acyclic.
- A correction never requires the condition it restores.
- Events the Runtime cannot observe, such as an opened enclosure, a reconnected cable, a changed sample or a power cut, are part of what the operator knows. Name each in the platform's `disturbances` with the conditions it acts on; it is then reported as `operating_conditions(disturbance="sample_changed", platform=...)`, or with `invalidate="what happened"` and `conditions`.

## The forms

The built-in forms cover common scalar observations. Saved methods and programs can reduce richer data, coupled behavior or a time-dependent model to an observation suitable for the decision:

| Form | Tolerance | Typical `kind` |
| --- | --- | --- |
| Steady after switch-on or a change | `samples` with `statistic` `std`, `span` or `slope` and a `maximum` | `equilibrium` |
| At its setpoint or commanded state | `target`: the reading of the setpoint, with `within` | `equilibrium`, `operating_point` |
| Agrees with a measurement standard | `target`: the certified value, with `within` or `relative` | `calibration` |
| Inside the measuring interval | `minimum` and `maximum` of the reading, such as a fraction of full scale | `operating_point` |
| Zero, blank or background known | a reading kept as `reference`, with `valid_for` | `reference` |
| Influence quantity in range | `minimum`, `maximum` or a steady `statistic` of an environment reading | `environment` |
| Locked or synchronized | a status reading `equals` its locked value | `lock` |
| No fault or error reported | `bits` of a status register that must be clear, such as 60 for `*ESR?` | `operating_point` |
| Signal sufficient | signal-to-noise, contrast or count rate above a `minimum` | `signal` |

## Priors by concept

Each concept names what to observe and gives cases from several fields; the values belong to the apparatus and the experiment.

- **Warm-up and equilibrium.** After switch-on or a change of setting a quantity settles with a time constant; measure once its drift is below what the result can bear. Furnaces, hot plates, thermal stages and cryostats; lamps, lasers and X-ray tubes; amplifiers and frequency references after power-on; chromatographic baselines and column ovens; incubator CO₂ and humidity; vacuum after pump-down; a balance after power-on.
- **Setpoint and commanded state.** A controlled quantity is at the value the experiment set: temperature, pressure, flow, voltage or current of a source, magnetic field, rotation speed of a centrifuge or stirrer, position of a stage or goniometer. Compare the reading with the setpoint the instrument reports, not with a number copied into the condition.
- **Zero, offset, blank, background.** What the instrument reads without the stimulus: dark counts, the tare of a balance, the zero of a pressure gauge or power meter, a solvent blank in spectroscopy, the baseline of a chromatogram, a background spectrum, the open/short of an impedance fixture. Keep it as a `reference` so the analysis subtracts the value that held when the data were taken.
- **Calibration against a standard.** Reading a reference whose value is known: reference weights, pH and conductivity buffers, wavelength lines of a calibration lamp, a frequency or time reference, a network-analysis calibration kit, thermometric fixed points, certified concentration standards. Records of how fast it drifts set `valid_for`.
- **Measuring interval and linearity.** A reading means what the method assumes only inside the interval where the response is linear: below saturation, overload or compression, above the noise floor and the limit of detection. Detectors and cameras, analog-to-digital converters, amplifiers and receivers, load cells, electrodes with a known slope. State the interval in the instrument's description (`minimum`, `maximum`, `saturation` of a result) and its status query (`quality`): every reading outside it, or with a bad status, is then kept as uncertain and in doubt without a condition; a condition keeps the operating point inside it, such as an exposure that leaves a margin below saturation.
- **Influence quantities.** Quantities that are not measured but change the result: ambient temperature, humidity, pressure, vibration, air currents, stray light, mains and radio-frequency pickup, magnetic fields, static charge. Balances and drafts, interferometers and vibration, electrophysiology and mains pickup, electron microscopes and stray fields, cell culture and humidity. A condition with a `reference` keeps their value with every experiment that requires it, so each dataset states the environment it was taken in.
- **Alignment, contact and position.** A path or a probe meets the sample where it should: optical paths and fiber or waveguide coupling, probe contact on a wafer, tip–sample distance, electrode placement, sample height in a diffractometer, focus. Use a bounded `tune` when a local scalar objective is appropriate; use a saved method or program for coupled, discontinuous or history-dependent adjustments.
- **Locks and synchronization.** Phase-locked loops, frequency and cavity locks, clocks on a shared reference, trigger timing, a lock-in amplifier's reference. Instruments that latch an unlock or an overload until it is read report it reliably between observations.
- **Order of bring-up.** Vacuum before high voltage, cooling before current, warm-up before calibration, calibration before alignment, alignment before signal: write the order the operator follows as `after`.
- **Interaction between instruments.** One instrument disturbs another: heater switching and ground loops, pump vibration, radio-frequency pickup, a shared supply. Write the disturbing state as a condition of the disturbed measurement, or keep conflicting operations apart with shared resources.
- **Sample state.** The sample is in the state the method assumes: equilibrated at temperature, degassed, not bleached or damaged by the measurement, at its concentration, viable. Use `kind` `sample_integrity`.
- **Reference position.** Stages, goniometers, filter wheels and valves that count steps lose their position at power-off or after a stall: home them before positioning, and make every alignment that depends on the position come `after` the homing.
- **History.** Backlash, magnetic and mechanical hysteresis, piezo creep: approach every position from the same side inside the saved operation that sets it, and say so in `note`.
- **Computation.** A solver or a simulation has converged, a residual is below its tolerance, a mesh or time step no longer changes the result: conditions of a computed platform are written the same way.

## Dependencies

Write `after` for physical or procedural dependencies: cooling before a heat-producing source, a stable blank before an absorption reference, or a reference clock before checking synchronization. Independent checks remain independent. A transient measurement need not wait for equilibrium, and a calibration may itself require alignment or lock. `kind` is an extensible label; only `after` defines dependencies.

## Example

A pH reference check, a settled chromatographic baseline and a cryostat setpoint check use the same forms. The named pH methods must be defined for the actual apparatus: `ph.check_buffer` presents a known buffer, waits, reads, and restores the sample path; `ph.two_point_calibration` performs the calibration. A reading of an unknown sample cannot substitute for the buffer check.

```json
[
  {"id": "ph.calibrated", "kind": "calibration", "description": "The electrode reads the pH 7.00 buffer",
   "verify": {"operation": "ph.check_buffer"}, "samples": 3, "interval": 10,
   "accept": {"target": 7.0, "within": 0.02},
   "correct": {"operation": "ph.two_point_calibration"}, "valid_for": 28800,
   "note": "The check method uses a certified buffer at 25 °C, rinses and restores the sample path"},
  {"id": "hplc.baseline", "kind": "equilibrium", "description": "The detector baseline has settled",
   "verify": {"device": "hplc", "operation": "read_absorbance"}, "samples": 30, "interval": 2,
   "accept": {"statistic": "slope", "maximum": 3e-8}, "reference": {"baseline": ["value"]},
   "correct": {"device": "hplc", "operation": "wait", "arguments": {"seconds": 600}},
   "note": "3e-8 AU/s is about 1e-4 AU per hour"},
  {"id": "cryostat.at_setpoint", "kind": "equilibrium", "description": "The sample stage is at its setpoint",
   "verify": {"device": "cryostat", "operation": "read_kelvin", "arguments": {"input": "A"}}, "samples": 5, "interval": 2,
   "accept": {"target": {"device": "cryostat", "operation": "read_setpoint", "arguments": {"output": 1}}, "within": 0.02},
   "correct": {"device": "cryostat", "operation": "wait", "arguments": {"seconds": 120}}}
]
```

For the cryostat example, `read_setpoint` must declare its actual unit or return the same numeric unit as `read_kelvin`. The Lake Shore 336 setpoint follows its control sensor's preferred units, which can differ from kelvin.

## What the records teach

`operating_conditions(condition="hplc.baseline")` returns how often it held, drifted or was unusable, the intervals it held, how often each correction restored it, and a drift trend with the time it is expected to reach the tolerance. Revise `valid_for`, tolerances and the order of corrections from these, and write the reason in `note`: they are this apparatus's own experience.

# Steps

A step is one operation. With `device` it is a Basic Operation of that instrument; without, it uses a saved Standard or Workflow Operation by name. A step holds `id`, `device`, `operation`, `arguments`, `after`, `bindings`, `resources`, `requirements` and the options `sweep`, `repeat`, `until`, `seek`, `retake`, `retry`; any other field is refused, and what is refused names the step and the nearest names that exist.

```json
[{"id": "set", "device": "source", "operation": "set_power", "arguments": {"dbm": -10}},
 {"id": "m", "operation": "bench.measure_at", "arguments": {"a": 10, "b": 20}, "after": ["set"]},
 {"id": "back", "device": "stage", "operation": "move_to", "after": ["m"],
  "bindings": {"angle_deg": {"step": "m", "path": ["angle"]}}}]
```

## Quantities with units

A numeric argument whose parameter declares a unit can be given with its own: `{"value": 250, "unit": "mV"}` or `"250 mV"`. It is converted to the declared unit before limits are applied, and the record holds the converted number. A unit of another quantity is refused, rad/s for Hz among them, as is a unit for a parameter that declares none. Units are SI symbols with prefixes and powers (`mK`, `µm`, `m/s^2`, `J/(mol K)`), `°C`, `°F`, `deg`, `min`, `h`, `bar`, `Torr`, `atm`, `psi`, `eV`, `L`, `G`, `%` and `ppm`; logarithmic units such as `dBm` are given as plain numbers in the declared unit. Sweep values and seek ranges take units the same way: positions convert with their offset, widths (`span`, `step`, `resolution`) as differences.

## Order

- `submit_experiment`: a step starts once the steps it names in `after` have completed and its instruments are free; steps without `after` are independent and may overlap. A binding also makes its source a predecessor.
- `submit_operations` and the steps of a saved operation: in the order written; `"after": []` starts an independent branch, `"after": [ids]` joins branches.
- An instrument runs one operation at a time. Steps that must not overlap for another reason name the same `resources`, e.g. `["sample"]`; `requirements`, e.g. `{"gpu": 1}`, take quantities of a pool.

## Bindings

A binding gives an argument from an earlier result: `{"step": id, "path": [keys or indices]}`. For a saved operation the path starts with one of its named results. With `view` it reads a number from an array the step returned, with the options of `read_array`:

```json
{"step": "trace", "view": {"at": {"component": "abs"}, "peak": "min"}, "path": ["peak", "center"]}
```

## Options of a step

| Option | Effect | Details |
| --- | --- | --- |
| `sweep` | Run at each value of the parameters named; numbers return as arrays over the sweep | `guide("sweeps")` |
| `repeat` | Run N times in turn, gathered over an axis named `repeat` | `guide("sweeps")` |
| `until` | Stop a sweep or repetition at a limit, or once what it gathered is known well enough | `guide("sweeps")` |
| `seek` | Find where a reading is largest or smallest, in place of `sweep` | `guide("sweeps")` |
| `retake` | Take again the points a lost condition put in doubt | `guide("conditions")` |
| `retry` | `{"times": 2, "after_s": 5}`: make a failed instrument call again; only for operations safe to repeat | `guide("sweeps")` |

## Options of a submission

| Option | Effect |
| --- | --- |
| `platform` | The platform whose devices the steps use |
| `resources`, `requirements` | Reserve instruments, shared objects and pool quantities for the whole experiment, so a scan keeps its setup between steps |
| `requires`, `upkeep` | Conditions that must hold, and whether the Runtime keeps them while it runs: `guide("conditions")` |
| `preview` | Run nothing; return operation counts, the state of the conditions and `estimated_s` |

## Results

The experiment result holds, under each step's id, what the operation returned; for a saved operation, its named result; for a swept step, what the sweep gathered. `experiment(run_id, include_result=true)` reads it, also while the graph runs.

## Saved operations

`saved_operations()` lists them, `saved_operations(operation)` returns one with its parameters, steps, named result and record of use. Save a method that worked:

```json
{"id": "resonance.locate", "layer": "standard", "description": "Sweep and locate the resonance dip",
 "parameters": {"points": {"type": "integer", "minimum": 11}},
 "steps": [{"id": "sweep", "device": "vna", "operation": "read_trace", "arguments": {"points": "${points}"}}],
 "result": {"f0": {"step": "sweep", "view": {"at": {"component": "abs"}, "peak": "min"}, "path": ["peak", "center"]}}}
```

`"${name}"` inserts a parameter. `layer` is `standard` for a method, `workflow` for an organization of methods across instruments. `configuration_schema("operation")` gives every field.

Parameter declarations are checked when saved: use `minimum` and `maximum`, not `min` or `max`. Descriptive fields and `x-*` annotations are retained, but annotations do not enforce constraints. The same contract applies to newly written local instrument and model parameters; older records and SDK descriptions remain usable.

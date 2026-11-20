# Operating conditions

A condition is something an experiment relies on: an alignment, a lock, a warm-up, a calibration, a reference. It has a verification, a tolerance, corrections and often a validity time. A platform states its own; an instrument's own are named `<device>.<id>`.

## State

`operating_conditions()` lists them with their state; `platform` or `device` narrows it, `condition` returns one with statistics and records.

| State | Meaning |
| --- | --- |
| `holds` | Verified within tolerance and still valid |
| `due` | Its validity ended or it was invalidated; verify again |
| `lost` | Verified outside tolerance and not restored |
| `unknown` | Never verified |

## Maintaining

`operating_conditions(maintain=true, platform=..., request_id=...)` verifies what is due and corrects what drifted, dependencies first; `conditions=[...]` or `device` narrows it, `force` verifies again what holds, `run_id` runs it inside an experiment that reserved the instruments. The reply returns within `wait`; read on with `maintenance=<id>`. After a physical disturbance, `invalidate="what happened"` with `conditions` marks them due.

## Relying on conditions

- `requires: [...]` on `submit_experiment`, `start_experiment` or `submit_program` names what must hold. The submission is refused with `conditions_not_held` while they do not; maintain, then submit again.
- `upkeep: true` has the Runtime keep them inside the experiment: before an operation starts it renews what is due or about to expire and corrects what drifted, then goes on. Use it for work longer than a validity time. A continuous experiment reserves the instruments maintenance uses; a step graph reserves them itself. A validity too short to last from a verification to the next operation cannot be kept: after three renewals in a row the operation fails and says so; review maintenance and acquisition timing and the `valid_for` setting; extend validity only when observations support it.
- A platform that declares `upkeep` is kept while idle; `lab_context.setup.upkeep` shows the next observation and what it waits for.

## Data in doubt

When a verification finds a condition lost, each operation in progress since it last held is marked `in_doubt`, with `since`, `found` and `outcome`; a disturbance reported while an operation ran marks it with the outcome `disturbed`. `experiment(run_id, in_doubt=true)` lists only those.

`"retake": true` on a sweep or repetition of a device operation in a step graph takes those points again once the condition is restored, before the next point; the sweep gathers what the retakes read, and the first readings stay with `retaken_by`. Declared bindings retain their input operation IDs; results that consumed doubtful inputs keep `in_doubt.source_operations`. Retakes do not clear those downstream records or replay actions whose arguments came from doubtful data.

## Defining

Conditions are part of a platform definition (`configure_platform`) or of an instrument's record. Add or revise them from what the experiment requires, the instrument's manual and what the operator knows; `configuration_schema("platform")` gives the fields. A correction can be an operation, a saved operation, or `tune`, a bounded local adjustment of settings against the verification value; it is not a global optimizer.

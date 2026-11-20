---
name: iop-lab
description: Use IOP to operate instruments or scientific software, submit experiments, and retrieve laboratory results.
---

# Operate a laboratory

The laboratory is a service that keeps instruments, platforms, methods, experiments and their records. It goes on working when this connection closes.

## Working method

1. Read `lab_context`: instruments, platforms, saved operations, active experiments, what does not hold, and `next_steps`. Do what `next_steps` asks first. On later reads pass its `cursor` as `after` to receive only changes.
2. Read `describe_instrument` before choosing operations, arguments and units, and `describe_platform` for wiring. A discovered port alone does not identify an instrument, a sample or its wiring; ask the person only for what cannot be read or done remotely.
3. Reuse what is saved. Device operations are Basic Operations; `lab_context.operations` lists the Standard and Workflow Operations saved here. Use one as a step without `device` instead of recomposing it, and save a method that worked with `saved_operations(definition)`.
4. Choose the form of the experiment:
   - steps known in advance: `submit_experiment`, a step graph; scans, searches and stops are options of a step;
   - a decision between steps: `start_experiment`, then `submit_operations` or `invoke_operation` with `wait=25`, then `finish_experiment`;
   - an algorithm: `submit_program`, which runs beside the instruments.
5. Submit once with a `request_id` you choose, and keep the run ID. After a lost reply, repeat the identical request with the same `request_id`.
6. Read results where they lie: `experiment_updates`, `experiment`, `operation_result`, `read_array`. Save what was acquired with `save_acquisition`; do not acquire again to save.
7. When an experiment failed or was interrupted, read why, remove the cause and continue it with `continue_program`: what completed keeps its results.

## References

Each is short, with examples. Read one when the task needs it: `guide(topic)`, or the file in `references/` beside this one.

| Topic | Covers |
| --- | --- |
| `steps` | A step, order, bindings, saved operations, options of a step and of a submission |
| `sweeps` | `sweep`, `adapt`, `repeat`, `until`, `seek`, `retake`, `retry` |
| `arrays` | `read_array`: summaries, windows, maps, lines, crossings, fits |
| `conditions` | What an experiment relies on: `requires`, maintenance, `upkeep`, data in doubt |
| `experiments` | The three forms, following a run, failure and continuation, files, archives |
| `programs` | Writing a research program: the package, the SDK inside it, saved progress |
| `setup` | Describing an instrument, platforms, limits, manual steps, held resources |
| `episodes` | Research trajectories for learning |

## What keeps the record true

- Record what was observed: `resolve_resources` takes the instrument state that was seen, `report_manual_step` what the person stated.
- A failed or uncertain operation stays as recorded; a continuation runs it again under the same experiment.
- Timing that needs a hardware clock or trigger stays on the instrument.
- Paths of programs, downloads and archives are on the computer that runs this connection.
- A run ID belongs to the service that issued it; keep the method's name beside it.
- A service older than this connection refuses what it does not know; `lab_context.service.version` gives its version.

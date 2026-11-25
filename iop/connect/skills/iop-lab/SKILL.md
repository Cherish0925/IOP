---
name: iop-lab
description: Use IOP to connect instruments and scientific software, run automated experiments and research on them, and retrieve laboratory results.
---

# Operate a laboratory

The laboratory is a service that keeps instruments, platforms, methods, experiments and their records. It goes on working when this connection closes. These guides describe IOP 0.16.0; check `lab_context.service.version` and upgrade an older service while idle before using newer options.

## Where the work stands

Work on a laboratory moves through these stages; `lab_context` shows how far it has come. Start at the first that is not done.

| Stage | Done when | Guide |
| --- | --- | --- |
| Instruments connected | Every instrument is registered with its limits and verified by its class | `setup` |
| Platform described | Wiring, shared resources and the conditions its experiments rely on are saved, and the conditions hold | `conditions`, `priors` |
| Automated experiments | An experiment submitted once runs to its result with the conditions it requires | `experiments`, `steps`, `sweeps` |
| Research | Rounds of question, experiment, analysis and decision, each recorded | `research` |
| Methods kept | A method used twice is a saved operation | `steps` |

## Working method

1. Read `lab_context`: instruments, platforms, saved operations, active experiments, what does not hold, and `next_steps`. Do what `next_steps` asks first. On later reads pass its `cursor` as `after` to receive only changes.
2. Read `describe_instrument` before choosing operations, arguments and units, and `describe_platform` for wiring. A discovered port alone does not identify an instrument, a sample or its wiring; ask the person only for what cannot be read or done remotely.
3. Reuse what is saved. Device operations are Basic Operations; `lab_context.operations` lists the Standard and Workflow Operations saved here. Use one as a step without `device` instead of recomposing it, and save a method that worked with `saved_operations(definition)`.
4. Choose the form of the experiment:
   - steps known in advance: `submit_experiment`, a step graph; scans, searches and stops are options of a step;
   - a decision between steps: `start_experiment`, then `submit_operations` or `invoke_operation` with `wait=25`, then `finish_experiment`;
   - an algorithm: `submit_program`, which runs beside the instruments.
5. Submit once with a `request_id` you choose and a `name` that says what it is, and keep the run ID. After a lost reply, repeat the identical request with the same `request_id`; in a later conversation, `find_experiments` finds the experiment again.
6. Read results where they lie: `experiment_updates`, `experiment`, `operation_result`, `read_array`. Save what was acquired with `save_acquisition`; do not acquire again to save.
   For averaging, calibration or normalization, read `guide(measurements)`: retain common error sources across readings and keep raw data with the reduced result.
7. When an experiment failed or was interrupted, read why, remove the cause and continue it with `continue_program`: what completed keeps its results.

## References

Each is short, with examples. Read one when the task needs it: `guide(topic)`, or the file in `references/` beside this one.

| Topic | Covers |
| --- | --- |
| `steps` | A step, order, bindings, saved operations, options of a step and of a submission |
| `sweeps` | `sweep`, `adapt`, `repeat`, `until`, `seek`, `retake`, `retry` |
| `arrays` | `read_array`: summaries, windows, maps, lines, crossings, fits |
| `measurements` | Streaming means, correlated uncertainty, dark/reference correction, precision stopping |
| `conditions` | What an experiment relies on: `requires`, maintenance, `upkeep`, disturbances, data in doubt, tolerance forms |
| `priors` | Writing what is known of an apparatus as conditions: the questions, the general forms, priors by concept across fields |
| `experiments` | The three forms, following a run, failure and continuation, files, archives |
| `research` | Rounds of question, design, run, analysis and decision; confirming, stopping, reporting |
| `programs` | Writing a research program: the package, the SDK inside it, saved progress |
| `setup` | Connecting an instrument in four steps, by how open it is and by what it does; platforms, limits, manual steps |
| `episodes` | Research trajectories for learning |

## What keeps the record true

- Record what was observed: `resolve_resources` takes the instrument state that was seen, `report_manual_step` what the person stated.
- A failed operation retains its record when continued under the same experiment. An unknown instrument outcome keeps its resources held until the instrument state is observed and resolved; it is not retried automatically.
- A reading the instrument reports as uncertain, or outside the measuring interval its description states, carries `quality` and is in doubt; a verification that reads it is unusable.
- A number given with its unit is converted to the unit the operation declares; a unit of another quantity is refused before the instrument is called.
- Nothing asked for is left out without a word: a tool argument, a submission field or a step field that does not exist is refused, and what is refused names the step and the nearest names that exist.
- Timing that needs a hardware clock or trigger stays on the instrument.
- Paths of programs, downloads and archives are on the computer that runs this connection.
- A run ID belongs to the service that issued it; keep the method's name beside it.
- A service older than this connection refuses what it does not know; `lab_context.service.version` gives its version.

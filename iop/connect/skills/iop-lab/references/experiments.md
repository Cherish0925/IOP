# Running and following experiments

## Three forms

| Form | Start | Use when |
| --- | --- | --- |
| Step graph | `submit_experiment(steps, request_id)` | The steps are known; scans, searches and stops are step options |
| Continuous experiment | `start_experiment`, then `submit_operations` or `invoke_operation`, then `finish_experiment` | You decide between steps from what was read |
| Program | `submit_program(path, request_id)` | An algorithm reads and acts many times; it runs beside the instruments through the SDK |

In a continuous experiment reserve every instrument and shared resource the work depends on when starting it; `wait=25` returns results in the same reply.

Use the tools this connection actually offers. Native HTTP MCP omits host-file uploads and exports; submit program packages or export archives through a stdio bridge or the HTTP/Python/TypeScript client. Existing saved programs can still be invoked through their saved methods.

## Requests and IDs

Choose `request_id` before submitting. A reply with a run ID means the service accepted the work. Submitted steps and programs continue when this connection closes; a continuous experiment waits for any future decisions the host has not yet submitted. After a lost reply repeat the identical request with the same `request_id`: the original record returns and nothing runs twice.

Give an experiment a `name` that says what it measures and a `project` for the line of work it belongs to. A run ID lost with the conversation is then found again: `find_experiments(search)` lists experiments newest first whose name, project, `request_id` or an instrument used contains the text; `state` narrows it, e.g. to `interrupted`.

## Following

- `experiment_updates(run_id, after=cursor, wait=25)` returns what changed, including completion of operations seen earlier; keep its cursor and read again while `has_more` is true.
- `experiment(run_id)` gives the state, `operation_states` (how many operations in each state), a page of operations and the first files; `include_result=true` adds the result, partial while a step graph runs. A running graph gives `estimated_end`.
- `operation_result(operation_id)` returns one full record. Read only what the next decision needs.
- The cursor of `experiment` enumerates operations; the cursor of `experiment_updates` tracks changes. Do not use one as the other.

## When it stops

A failed operation ends a step graph as `failed`; a service restart leaves started work `interrupted`. Read `error` and the operations' states, remove the cause, then `continue_program(run_id, request_id)`:

- a step graph keeps what completed and runs the rest again, each keeping how it ended under `attempts`;
- a program that declares `resume: true` continues from the progress it saved.

An operation whose outcome is unknown holds its instruments: observe their state with an operation described as `effect: "observe"`, record it with `resolve_resources`, then continue. Resubmitting a failed run under a new `request_id` starts from the beginning.

`cancel_experiment` only when stopping is intended.

A completed experiment is read, not resumed. A changed sample, question or acquisition setup normally needs a new run with a new request ID and a reference to the earlier work. Reuse methods and applicable calibration, but do not treat earlier sample data as newly acquired results.

## Saving

- `save_acquisition(run_id, name, operation_ids, conditions, analysis)` saves completed results with the instrument configurations they were taken under. Include setting readbacks among the IDs; put sample and wiring in `conditions`.
- `save_experiment_file` saves a text result or analysis; `experiment_files` pages through output files; `download_file` brings one to this computer.
- `save_experiment(run_id, destination)` saves the whole experiment as one ZIP: records, events, the verifications of its conditions, the saved operations it used, its files, and an RO-Crate description that research data repositories read.

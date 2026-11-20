# Research programs

A program is the form for an algorithm: fitting, optimization, a decision after each reading. It runs as its own process beside the instruments, goes on when this connection closes, and reaches the laboratory through the SDK; every instrument call it makes is scheduled and recorded like any other.

## Package

A directory or ZIP with the program and an optional `package.json`:

```json
{"name": "polarization-scan", "command": ["{python}", "experiment.py"], "resume": true}
```

`{python}` is the service's Python; install what the program imports there. Submit with `submit_program(path, request_id, platform, resources, parameters)`; list in `resources` every instrument it uses. To keep a workflow that decides from results as a method of the laboratory, save it with `saved_operations(definition, program=path)`; it then runs as the only step of an experiment.

## Inside the program

```python
import json, os
from ai4instruments.iop import Client

run = Client().current_run()
parameters = json.loads(os.environ.get("IOP_PARAMETERS_JSON", "{}"))
progress = run.checkpoints().get("main", {}).get("state")
if progress is None:
    progress = {"next": 0}
    run.checkpoint(progress)  # Establish continuation before the first instrument call.
for index in range(progress["next"], parameters.get("points", 10)):
    reading = run.invoke("meter", "measure", request_id=f"{run.id}-{index}")
    run.checkpoint({"next": index + 1})
run.save_result({"points": parameters.get("points", 10)})
```

| Call | Does |
| --- | --- |
| `run.invoke(device, operation, arguments, request_id=...)` | One operation; returns its result. From several threads, independent instruments work at once |
| `run.batch(steps)` | Steps with every option of a step (`guide("steps")`); returns their operation records |
| `run.call(operation, arguments, sweep=..., repeat=..., until=..., seek=..., retry=...)` | A saved operation; returns its named result, gathered over a sweep or a seek |
| `run.read_array(field, request_id=...)`, `Client().read_array(operation_id)` | A view of an array, with the options of `guide("arrays")` |
| `run.checkpoint(state)`, `run.checkpoints()` | Save and read progress, once the operations before it have returned |
| `run.save_result(value)`, `run.save_file(name, text)`, `run.stream(name, definition).append(data, sequence)` | The result, a file, a stream of batches. Files written under `IOP_RUN_DIR` are kept as output |
| `run.maintain(conditions)`, `run.batch(steps, maintain=True)` | Restore conditions the work relies on: `guide("conditions")` |

## Continuing

A stable `request_id` on each call makes a repeated call return its first result instead of acting twice. Save an initial progress point before the first instrument call, then update it after completed work; this also permits continuation when the first round is interrupted. A program that declares `"resume": true` and saves checkpoints continues after an interruption with `continue_program(run_id, request_id)`: it starts again, reads its progress and goes on from there. Output printed by each attempt is kept.

A failed operation also retains its response under that ID. A research method that decides to retry a known failed reading uses a new stable ID for that reading, while keeping the IDs of completed calls. The reference-measurement example in the `measurements` guide implements this choice. Preparation actions and unknown instrument outcomes need their own recovery; changing every ID on continuation would repeat completed work.

## Choosing this form

Use a step graph when the steps are known: `sweep`, `until` and `seek` cover scans, stops and bounded searches without a program. Use a program when the next action depends on a computation the options of a step do not express, or when many decisions must follow each other faster than a conversation can make them.

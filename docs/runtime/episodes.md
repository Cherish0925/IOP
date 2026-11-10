# Research episodes

An episode is an ordered record of decisions and observations inside an existing experiment. The Runtime does not assign scientific rewards or reset physical equipment. The research program provides those meanings.

```python
from ai4instruments.iop import Client

with Client() as client:
    with client.start_experiment(resources=['stage']) as run:
        initial = run.invoke_record('stage', 'position')
        episode = run.begin_episode(
            'episode-0', task={'id': 'positioning', 'version': '1'},
            policy={'id': 'controller', 'version': '1'},
            observation=initial['result'],
            info={'operation_ids': [initial['id']]})
        measured = run.invoke_record('stage', 'move_to', {'angle_deg': 30},
                                     request_id=run.id + '-move-0')
        episode.record(0, action={'angle_deg': 30},
                       observation=measured['result'], operation_ids=[measured['id']],
                       reward=None, terminated=True)
        episode.export('episode-0.jsonl')
```

This example uses the demonstration stage's operation names. A real task reads its registered instrument descriptions, prepares its actual initial state and computes feedback from measured results. `reward=None` means an unscored research record. A training dataset should select scored transitions explicitly.

## Record semantics

- Task and optional policy each have `id` and `version`. Store task parameters, sample identity and physical units in `info` or structured observations.
- `sequence` starts at zero. The server accepts only the next sequence, or an identical retry of an existing sequence. Changed content at an existing sequence is rejected.
- `operation_ids` contains distinct completed operations belonging to this experiment. Operation records retain their device configuration revisions and results. Files can be linked by their existing artifact IDs rather than embedded as arrays in JSON.
- `terminated` means the task has ended under its own definition. `truncated` means collection stopped for an external reason such as a time or step budget. Neither is inferred from an instrument disconnect or a failed operation.
- Episode creation and recording do not send instrument commands. An uncertain or failed physical action remains an operation record, not a fabricated successful transition.
- The Runtime stores records in the same SQLite database as experiments. All clients read the same sequence. It coordinates writes; research programs remain responsible for deciding which actor controls an episode.

Reconnect with `client.run(run_id).episode(episode_id)`. Its `info()` returns `next_sequence`; `transitions(after=cursor)` returns a bounded page. `export(path)` streams the episode metadata and transitions accepted at the start of export as JSON Lines. The first observation is in the episode record; each transition's observation is the result **after** its action. A truncated transition retains its final observation for the learner.

## Interfaces

| HTTP | Operation |
| --- | --- |
| `POST /v1/runs/{run}/episodes` | Create or retrieve an identical episode definition. |
| `GET /v1/runs/{run}/episodes?offset=0&limit=100` | List episodes. |
| `GET /v1/runs/{run}/episodes/{episode}` | Read metadata, next sequence and state. |
| `POST /v1/runs/{run}/episodes/{episode}/transitions` | Append one transition or retrieve an identical retry. |
| `GET /v1/runs/{run}/episodes/{episode}/transitions?after=-1&limit=100` | Read transitions. |

MCP exposes `begin_episode`, `record_transition` and `research_episodes`. TypeScript exposes `Run.beginEpisode`, `Run.episode` and `Episode.record/transitions`. These functions require Runtime 0.8.6. Older connection and operation APIs remain unchanged.

For many laboratory steps, execute a program package through the Runtime and use the SDK inside it. MCP can submit and inspect that program without a round trip to the language model for every operation. Physical parallelism is limited by independent devices and shared resources; queued tasks do not create additional apparatus.

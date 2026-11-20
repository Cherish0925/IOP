# Research episodes

An episode records a trajectory inside an experiment for later learning: an initial observation, then actions, observations and feedback, each linked to the operations that produced them.

1. Prepare the setup and observe its initial state with ordinary operations. Beginning an episode resets nothing.
2. `begin_episode(run_id, episode_id, task, observation, policy, seed, info)`: `task` and the optional `policy` carry `id` and `version`.
3. After each decision, `record_transition(run_id, episode_id, transition)` with `sequence`, `action`, `observation`, `reward` (or null), `terminated`, `truncated` and `operation_ids`, which name completed operations of this experiment.
4. `research_episodes(run_id)` lists episodes; with `episode_id` it returns one and a page of transitions from `after`.

After a lost reply repeat the same `sequence` with the same content: the saved record returns. Keep uncertain or failed operations in their own records; do not record them as completed transitions. Parallel episodes need independent instruments. For many numerical decisions run the research program beside the Runtime and use its SDK.

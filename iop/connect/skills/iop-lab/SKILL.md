---
name: iop-lab
description: Use IOP to connect instruments, run experiments and research, and continue work from saved laboratory results.
---

# Work with the laboratory

IOP runs experiments beside the instruments and keeps their results across conversations.

1. **Read the current state.** Start with `lab_context`. Reuse the instruments, platforms, methods and experiments already there. Handle only what the task needs; use `after=cursor` for later changes.
2. **Choose the next action.** Read the relevant `describe_instrument` or `describe_platform`. Reuse an applicable method from `saved_operations`. Ask for physical information only when it is missing, and save it with the apparatus. Read one guide below when needed.
3. **Run.** Use `submit_experiment` for known steps, `start_experiment` for decisions between operations, or `submit_program` for an algorithm. Include the conditions the measurement relies on. Choose a `request_id` and keep the run ID; after a lost reply, repeat the identical request with that same ID. Finish a continuous experiment with `finish_experiment`.
4. **Read and continue.** Follow `experiment_updates`; request relevant results or array slices. Record the result and next action with the experiment. Use `find_experiments` in a later conversation; continue interrupted work after resolving its cause. Reuse completed results and useful methods.

## Read on demand

Use `guide(topic)` or the matching file in `references/`.

| Task | Topic |
| --- | --- |
| Connect an instrument or describe a platform | `setup` |
| Apply apparatus knowledge | `priors` |
| Maintain experimental conditions | `conditions` |
| Choose, follow or continue an experiment | `experiments` |
| Compose steps and reusable methods | `steps` |
| Scan, repeat or optimize | `sweeps` |
| Read arrays and fit curves | `arrays` |
| Reduce measurements and uncertainty | `measurements` |
| Investigate a research question | `research` |
| Write a program with saved progress | `programs` |
| Export trajectories for learning | `episodes` |

Hardware timing stays on the instrument. Human steps record what the person actually reports. Program and download paths belong to the computer running this connection.

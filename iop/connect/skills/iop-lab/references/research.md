# Research in rounds

Organize research around the question, prior observations and the next informative action. A round may involve several measurements, computations or instruments. Keep a short account of its prediction, observations and decision beside the experiments so a later round or conversation can continue. Reuse existing records before acquiring more data.

## One round

1. **Question.** State what is unknown and which observable distinguishes the plausible explanations. For quantitative work, state the precision needed; for exploration, state which observation would change the next decision. Write the expected value, trend or model before measuring, and retain alternatives that remain plausible.
2. **Design.** Choose instruments and settings from `describe_instrument` and `describe_platform`, and look for an applicable method in `saved_operations`. Name required conditions in `requires`, with `upkeep` when they need renewal. Choose a step graph for known steps, a continuous experiment for decisions between readings, or a program for an algorithm (`guide("experiments")`). Plan within the task's time, sample and measurement budget.
3. **Run.** Preview a step graph with `submit_experiment(steps, request_id, preview=true)`, then submit it once with a descriptive `name` and the `project` of the line of work. Follow it with `experiment_updates`. Accepted steps and programs run independently of this conversation; future decisions that still need the host wait for its return. Finish an external experiment with `finish_experiment` after its operations and transfers complete.
4. **Analyze.** Read relevant arrays with `read_array`, and use `guide("measurements")` for repeated, dark-corrected or normalized readings. Read `experiment(run_id, in_doubt=true)` and the affected records: preserve the raw data, determine which conclusions depend on the missing condition, and repeat or qualify those measurements as needed. Compare observations with predictions and account for model assumptions and systematic effects, not only fitted standard errors.
5. **Decide.** Useful outcomes include:
   - *answered*: the result addresses the question within a stated scope and uncertainty where applicable;
   - *refine*: the same question with more points, a narrower range or longer averaging where the result is still open;
   - *revise*: the expectation failed; state the new model or the new question;
   - *repair*: the apparatus did not behave as its description says; restore conditions, settings or wiring before the next round.

   Record a concise decision and its reason with `save_experiment_file(run_id, name, text)`: the expectation, relevant run IDs and files, what was observed, the decision and the next step. These are notes for continuing the research, not a required classification of every task.

## Designing the experiment of a round

- Prefer measurements whose predicted differences can be resolved above noise and systematic effects. Shared predictions may still provide a useful calibration or control.
- Use a suitable reference: a blank, a known sample or the state without stimulus. Reuse one while its applicability holds, or measure it alongside the experiment when drift matters.
- Choose a design for the question: single-factor changes, factorial or blocked designs, space-filling samples or adaptive selection. Coupled factors need coverage that can distinguish their effects; the first parameter of a `sweep` changes slowest.
- Where drift can mimic the effect, interleave or randomize settings, block the design, or repeat reference points. Respect hysteresis, settling and irreversible changes; ordering alone does not remove drift. A `sweep` accepts an explicit list of values.
- Repeat where noise decides: `repeat` with `until` on the uncertainty the question needs (`guide("sweeps")`).
- Name required conditions in `requires`; their recorded checks and reported disturbances establish what was observed, not unmeasured behavior between checks.

## Across rounds

- One `project` names a line of work; `find_experiments` with the project's name lists its rounds, newest first. The notes of each round name the earlier rounds it builds on.
- When a consequential result remains ambiguous, choose a repeat, control or independent method that resolves the ambiguity.
- Save a method when reuse would help (`saved_operations(definition)`, `guide("steps")`); parameterize what varies and state its applicability. Keep research algorithms in programs.
- Keep what was acquired with `save_acquisition`, and a finished line of work with `save_experiment`.
- When the rounds are data for learning, record them as episodes as well: `guide("episodes")`.
- Keep the research objective while changing an unproductive model, method or experimental design. Finish when the question is answered within its scope or the agreed budget is spent. If progress requires unavailable physical action, information or equipment, preserve the work and name what enables continuation; a plateau alone does not establish the answer.

## Report

State the question; the answer with units and uncertainty; the experiments it rests on, by run ID; the conditions they relied on and whether they held; data in doubt and what was done with it; the assumptions of the analysis; and what remains open.

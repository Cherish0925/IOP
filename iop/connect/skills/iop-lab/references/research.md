# Research in rounds

Research on the instruments proceeds in rounds. Each round asks one question the apparatus can answer, runs one experiment, and ends with a decision. The laboratory keeps the experiments; keep the reasoning beside them, so that the next round, or the next conversation, starts from what is known.

## One round

1. **Question.** State what is unknown, the quantity that answers it and the uncertainty that would settle it. Write down what you expect before measuring: a value, a trend, a model with its parameters.
2. **Design.** Choose instruments and settings from `describe_instrument` and `describe_platform`, and look for a saved method in `saved_operations` first. Name the conditions the result relies on in `requires`, with `upkeep` for work longer than their validity. Choose the form: a step graph for known steps, with `sweep`, `adapt`, `seek`, `repeat` and `until` as options of a step; a continuous experiment for decisions between readings; a program for an algorithm (`guide("experiments")`). Spend measurements where they decide the question: a coarse scan, then points where the response changes.
3. **Run.** Preview a step graph with `submit_experiment(steps, request_id, preview=true)`, then submit it once with a `name` that states the question and the `project` of the line of work. Follow it with `experiment_updates`; it continues when this conversation ends.
4. **Analyze.** Read arrays with `read_array`: summaries, fits with standard errors, crossings. Reduce repeated, dark-corrected or normalized readings as `guide("measurements")` describes. Read `experiment(run_id, in_doubt=true)` before drawing conclusions: points taken while a condition they relied on was lost are not evidence. Compare the result with what you expected.
5. **Decide.** One of four:
   - *answered*: the result settles the question at the stated uncertainty;
   - *refine*: the same question with more points, a narrower range or longer averaging where the result is still open;
   - *revise*: the expectation failed; state the new model or the new question;
   - *repair*: the apparatus did not behave as its description says; restore conditions, settings or wiring before the next round.

   Record the decision and its reason with `save_experiment_file(run_id, name, text)`: the expectation, what was observed, the decision, and the round it leads to.

## Designing the experiment of a round

- Measure where the expectations differ most: a setting at which every candidate explanation predicts the same reading decides nothing.
- Include a reference in the same experiment: a blank, a known sample, the state without stimulus. The analysis compares with what held at the time, not with a value from another day.
- Vary one factor at a time unless the question is about how factors interact; then sweep them together, the first parameter of a `sweep` changing slowest.
- Order points so that drift cannot imitate the effect: give a `sweep` its values as a list that interleaves settings, and take the first point again at the end.
- Repeat where noise decides: `repeat` with `until` on the uncertainty the question needs (`guide("sweeps")`).
- Name the conditions the result relies on in `requires`; the record then says for every point whether they held.

## Across rounds

- One `project` names a line of work; `find_experiments` with the project's name lists its rounds, newest first. The notes of each round name the earlier rounds it builds on.
- Confirm a result that changes the direction of the work before building on it: take the decisive measurement again, or reach the same quantity with another method or instrument.
- A method used twice becomes a saved operation (`saved_operations(definition)`, `guide("steps")`); later rounds call it by name and its uses are recorded.
- Keep what was acquired with `save_acquisition`, and a finished line of work with `save_experiment`.
- When the rounds are data for learning, record them as episodes as well: `guide("episodes")`.
- Stop when the question is answered at the uncertainty it needs, when further rounds no longer change the answer, or when the next step needs what this laboratory cannot do; say which.

## Report

State the question; the answer with units and uncertainty; the experiments it rests on, by run ID; the conditions they relied on and whether they held; data in doubt and what was done with it; the assumptions of the analysis; and what remains open.

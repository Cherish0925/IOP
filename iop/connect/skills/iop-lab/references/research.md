# Research

Start from the question and previous results. A useful next action measures, calculates or changes something that helps answer it. One question may span several experiments and conversations.

1. **Choose.** State what to learn or improve and choose an observation that changes the next action. When explanations compete, measure where their predictions differ. Read prior work with `find_experiments`. Reuse suitable methods and plan within the available time, samples and equipment.
2. **Run.** Read the relevant instrument or platform description. Choose a step graph, continuous experiment or program using `guide("experiments")`; include its `requires` and `upkeep` where needed. Name the project and experiment, submit once, and follow `experiment_updates`.
3. **Learn.** Read the relevant arrays and compare observations with predictions. Account for uncertainty and model assumptions where they affect the answer. For `in_doubt` results, inspect the affected measurements and their effect on the conclusion; preserve the raw data.
4. **Continue.** Save a short result and next action with `save_experiment_file`: what was expected, what happened, the relevant run IDs and what to try next. Reuse what worked; change the model, method or design when the current route stops being informative.

Choose the experimental design for the question: a scan, factorial or blocked design, adaptive measurements, controls, or an independent method. When drift matters, interleave settings or reference readings; account for settling, hysteresis and irreversible changes. Reuse a calibration or reference while it applies. Read `guide("measurements")` for reductions and uncertainty, or `guide("sweeps")` for sampling options.

Finish when the question is answered within its scope or the agreed budget is spent. If progress needs an unavailable physical action or resource, retain the work and state what enables continuation. Report the answer, the observations it rests on and what remains open. `finish_experiment` closes a continuous experiment; `save_acquisition` retains measured data, `saved_operations` keeps a reusable method, and `save_experiment` exports the work.

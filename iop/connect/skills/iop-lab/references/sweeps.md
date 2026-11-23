# Sweeps, repetitions and seeks

Each point is an ordinary operation with its own record; what the points return is gathered under the step.

## sweep

```json
{"id": "scan", "operation": "axis.measure", "arguments": {"axis": "stage"},
 "sweep": {"angle": {"start": -40, "stop": 40, "points": 81}}}
```

- Values: a list (`[0, 10, 25]` or labels `["x", "y"]`), `start`/`stop`/`points`, or `center`/`span`/`points`. Several parameters give a grid, the first changing slowest. At most 10000 points.
- An end of a range may be read from an earlier step, written as a binding: `"center": {"step": "coarse", "view": {"field": "power", "peak": "max"}, "path": ["peak", "center"]}`.
- Gathered: numbers in the same place become one array with an axis per swept parameter; arrays of one shape combine with the sweep's axes in front (spectra over a bias read as a map). Read with `read_array(run_id, field)`, adding `request_id` for a batch; bind with `{"step": "scan", "view": {"field": "power"}, "path": ["summary", "max_at"]}`.
- `"adapt": ["power"]` inside a range places each point from what the earlier ones read, closer together where the reading changes most: one parameter, 2 to 1000 points, gathered in order of value. Use it to resolve a narrow line in a wide span.

## repeat

`"retake": true` takes again, before the next point, a point whose reading was uncertain or taken while a condition it relied on was lost: `guide("conditions")`.

`"repeat": 20` runs the step 20 times in turn, at each point of its sweep if it has one; the readings are gathered over a last axis named `repeat`, whose summary gives their mean, spread and error.

## until

Stops a sweep or repetition; the later points are skipped without reaching an instrument, and the steps after it run.

```json
{"id": "ramp", "device": "smu", "operation": "source",
 "sweep": {"volts": {"start": 0, "stop": 1, "points": 101}},
 "until": {"path": ["current"], "above": 0.001}}
```

- `path` is the number each point reads; `above`, `below` or both are the limits. A point that reads no number also stops it.
- With `view`, `path` is read in that view of what the sweep gathered so far: `{"view": {"field": "power"}, "path": ["summary", "error"], "below": 5e-6, "points": 5}` repeats until the mean is known to 5e-6; `{"view": {"field": "power", "fit": "lorentzian"}, "path": ["fit", "errors", "center"], "below": 2e4}` sweeps until a line's center is known. `points` is how many run first. At most 1000 points.
- Where it stopped is recorded as `stopped` (the point and its reading).

## seek

In place of `sweep`: find where a reading is largest (`maximize`) or smallest (`minimize`).

Seek moves continuous numeric parameters. Use a fixed `sweep` of allowed values for integer, categorical or enumerated parameters; the submission rejects them before any calls run.

```json
{"id": "align", "device": "fiber", "operation": "couple",
 "seek": {"maximize": ["power"], "points": 60,
          "over": {"x": {"minimum": -2, "maximum": 2, "step": 0.5, "start": -1.5},
                   "y": {"minimum": -2, "maximum": 2, "step": 0.5}}}}
```

- From `start` (the middle unless given) it moves one parameter at a time by `step`, keeps a move whose reading improves on the best by more than `gain`, and halves every step when no move helps. It ends once each step is below `resolution` (`step`/16 unless given) or after `points` places (40 unless given); the rest are skipped.
- No point leaves `minimum`..`maximum`, which must lie within what the operation takes. Any of these numbers may be read from an earlier step, such as `start` where a coarse sweep read most.
- Gathered: the readings over an axis named `evaluation`, the places under `positions`, and `best` with its `index`, place `at` and `reading`. Bind the result: `{"step": "align", "path": ["best", "at", "x"]}`.
- To seek on a number read from an array (the peak of a trace at each place), seek over a saved operation whose named result reads it through a `view`.
- It is a bounded local search: start it where a coarse sweep read most when the reading has several maxima.

## retry

`"retry": {"times": 2, "after_s": 5}` makes a failed instrument call again after the wait, up to `times` more times; failed calls stay in the record under `attempts`. A call whose outcome is unknown, a refusal before the call and a cancellation are not made again. Give it to readings and moves to a position, not to relative moves or doses.

## In a batch

`submit_operations` takes the same options; its reply counts a swept step's points by state, with `stopped` and `in_doubt` when they apply.

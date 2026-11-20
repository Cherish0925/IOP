# Reading arrays

Traces, spectra, images and what sweeps gather stay in the laboratory; `read_array` tells what they say. Arrays above 64 values reach you as `data` (dtype, shape, unit, axes) with `values_omitted`.

## Source

| Give | Reads |
| --- | --- |
| `operation_id` | An array an operation returned; `field` names it when the result holds several |
| `run_id`, `field` | An array of an experiment's result, such as what a sweep gathered; add `request_id` for a batch |
| `run_id`, `stream` | A numeric data stream of an experiment, samples by channels |

## What returns

`summary`: `count`, `min` and `max` with their index and coordinate (`max_index`, `max_at`), `mean`, `std` and `error`, the standard error of the mean. Then exact `values` when there are at most `points` positions, otherwise an `envelope`: the min, max and mean of `points` buckets (default 32).

## Choosing what to read

| Option | Effect |
| --- | --- |
| `along` | The axis followed (default the first); two axes, e.g. `["row", "column"]` with `points: [16, 16]`, read a map |
| `at` | Fix other axes by index or label: `{"channel": "x"}`. Complex values read as `abs` unless `at` chooses `re`, `im`, `arg` or `power` |
| `start`, `stop` | A window by position |
| `low`, `high` | A window by coordinate |

For a map, `start`, `stop`, `low` and `high` take one value per axis.

## Lines, thresholds, fits

| Option | Adds |
| --- | --- |
| `peak: "max"` or `"min"` | `peak`: `center` between samples, `width` halfway to the opposite extreme (`base`), `low`, `high`, `value` |
| `peak` with `prominence` | `peaks`: every line that stands out by that much, such as the modes of a spectrum |
| `level` | `crossings`: each place `at` which the trace crosses that value, `rising` or not: a threshold, a -3 dB point, a coercive field |
| `fit: "line"` | `slope`, `intercept`, `zero` (where it reaches 0, such as a laser threshold), `residual`, `errors` |
| `fit: "lorentzian"` or `"gaussian"` | `center`, `width` (full, at half height), `height`, `base`, `residual`, `errors` |

- A resonance's loaded Q is `center / width`; read a dip with `at: {"component": "power"}` and `peak: "min"`, or fit it through noise.
- `errors` are standard errors; they are `null` when the samples do not resolve the line, and then the fit should not be trusted.
- With one other axis left free, `peak`, `level` and `fit` read at each of its positions and return `rows`: the center of a line at each bias of a map.

## Using a view without reading it

A binding, a named result of a saved operation, an `until` and a condition's verification take the same options under `view`, so a later step uses the number without it passing through you: `{"step": "trace", "view": {"peak": "max"}, "path": ["peak", "center"]}`.

Analyze whole arrays in a program beside the Runtime; request `operation_result(values=true)` only when the values themselves are needed.

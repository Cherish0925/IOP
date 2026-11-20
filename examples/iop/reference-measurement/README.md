# Reference-normalized measurement · 参考归一化测量

This program repeatedly measures a signal, a reference and optionally a dark reading, then returns `(signal-dark)/(reference-dark)` with standard uncertainty. It is a method for any scalar instrument readings with compatible units: transmission, detector response, reference-normalized intensity or another ratio. All instrument calls still pass through IOP's normal scheduling and operation records.

The included configuration uses **IOP's ideal simulated polarization apparatus**, not physical equipment. Its half-wave-plate angles give a ratio of 0.5. A common relative gain contribution cancels from the ratio. This ideal simulation has no independent reading noise; real measurements must supply the relevant uncertainty model.

```sh
iop init --demo
iop start
```

From the repository root, submit with Python:

```python
import json
from pathlib import Path
from ai4instruments.iop import Client

folder = Path("examples/iop/reference-measurement")
parameters = json.loads((folder / "parameters.demo.json").read_text())
run = Client().submit_package(folder, resources=["stage", "meter"], parameters=parameters)
print(run.id)                 # Retain this before waiting.
print(run.result())
```

## Adapt to instruments

Replace each `signal`, `reference` and optional `dark` object:

| Field | Meaning |
| --- | --- |
| `device`, `operation`, `arguments` | A registered instrument operation |
| `path` | Keys/indices selecting the scalar in its result, e.g. `["power"]` |
| `unit` | Unit of that scalar; all three must agree. A reported result unit is checked |
| `prepare` | Optional step list before this reading, such as a shutter or sample change; uses the normal batch options |
| `components` | Signed shared standard-uncertainty contributions: a number in the reading's unit, or `{"relative": fraction}` times its estimated mean |

Reserve every device used for preparation or reading. A result without a `unit` is interpreted in the explicitly configured unit, according to the instrument description. Supply normalized units at the driver/program boundary; this method does not guess conversions. Calibration metadata on an instrument is not automatically added: a researcher must specify which contributions are shared and how they depend on the reading.

The three repeatability terms are estimated separately and treated as independent; jointly fluctuating readings require a covariance model. Names in `components` identify actual common errors across channels; use distinct names for independent errors. Reusing `meter-gain` in the demo expresses one shared gain error, not three independent calibrations. Expanded calibration uncertainties must first be divided by their stated coverage factor.

`min_samples` (default 10) and `max_samples` (default 100) bound the number of rounds; both are integers, at least two. `standard_uncertainty` is an absolute limit on the dimensionless ratio. `relative_uncertainty` is a fraction of its absolute value. When both are set, both must hold; without either, all requested rounds run. A relative target alone cannot certify a zero signal. Limits describe estimated standard uncertainty, not guaranteed confidence coverage under repeated stopping.

`reference_relative_uncertainty` (default 0.1) is the maximum standard uncertainty divided by the absolute dark-corrected reference for reporting a first-order ratio. If the corrected reference is zero or too uncertain, the program retains measurements and returns `estimate: null` with `reason: zero_reference` or `uncertain_reference`. It continues to the requested maximum if this does not improve; `stop: max_samples` is distinct from reaching precision. A threshold below one only limits this approximation's use; it does not establish the full ratio distribution.

## Results and continuation

The program returns the ratio, standard uncertainty and its signed contributions, the channel means and corrected reference, sample count, `precision_reached`, and `stop` (`precision` or `max_samples`). Each round saves an acquisition under `reference-measurement/` with its completed operation records and analysis, and a JSONL row in the `reference-measurement` stream. The statistical progress contains only count, mean and squared deviations per channel.

The package declares `resume: true` and saves its initial configuration and empty statistics before any preparation or measurement, so even the first round can be continued. If interrupted, use `run.resume(request_id=...)` or MCP `continue_program`; it rereads its checkpoint. Stable per-run request IDs and stream sequence numbers reuse completed readings and saved rows even when an earlier reply was lost. Changing the measurement configuration requires a new run. Physical preparation after an interruption remains the experimenter's responsibility; instrument outcomes recorded as unknown follow IOP's normal resolution process.

On explicit continuation after a **known failed reading**, the method keeps that operation's failure record and issues a replacement reading under an ID derived from the failed operation. A replacement that already completed is reused after another interruption, as are all other completed readings. There is no automatic retry within an attempt. Unknown outcomes are not replayed. Preparation is a separate step graph: a failed preparation may involve side effects and requires an explicit recovery method or a new experiment; this program does not rerun the whole preparation graph automatically.

The mathematical model is first-order propagation with shared sources, as in [JCGM 100, §5.2](https://www.iso.org/sites/JCGM/GUM/JCGM100/C045315e-html/C045315e_FILES/MAIN_C045315e/05_e.html); repeated-mean uncertainty assumes independent stationary observations. The generic API is described in the [measurement guide](../../../iop/connect/skills/iop-lab/references/measurements.md).

## 中文

这个程序完成信号、参考和可选暗信号的重复读取，计算扣除同一暗信号后的比值及标准不确定度，并根据精度目标或最大次数停止。示例参数用于 IOP 理想偏振模拟装置，理论比值为 0.5；没有接入真实硬件，也没有模拟独立读出噪声。

真实使用时，替换每个通道的 `device`、`operation`、`arguments`、结果 `path` 与 `unit`；`prepare` 可指定移动、快门、样品切换等前置步骤。提交时预留所有涉及的仪器。`components` 的数值是带符号的共同标准不确定度贡献；`{"relative": 0.01}` 表示均值的 1%。同名项表示同一误差源，不同项按独立误差处理。通道重复性分别估计并假定独立；相关漂移或其他共同波动需另建协方差模型。证书里的扩展不确定度要先按覆盖因子换算。

`standard_uncertainty` 是比值标准不确定度的绝对目标，`relative_uncertainty` 是相对目标；同时给出则同时满足。`min_samples` 与 `max_samples` 控制最少和最多轮数。校正参考为零或相对标准不确定度超出 `reference_relative_uncertainty` 时，不输出不可靠的一阶比值，原始读数仍保存。精度目标是采样停止条件，不是置信区间覆盖保证。

首次操作前先保存初始配置和空统计量，因此第一轮中断也能续跑。每轮保存带仪器记录的 acquisition、JSONL 结果和固定大小的统计进度。中断后可使用 `continue_program` 或 `run.resume`；稳定请求 ID 和流序号使已完成读数与写入不会重复。修改测量参数时新建实验。校正、单位、相关性、一阶近似的适用性和中断后的物理状态由研究方法明确规定。

读取明确失败后，显式续跑会保留失败记录，仅对该读数使用新的稳定请求 ID。替代读数若已完成，下一次续跑仍复用它；其他已完成读数也不重复。结果未知的操作不自动重做。前置操作是单独的步骤图，失败后需明确恢复方法或新建实验，不会自动重做整个前置流程。

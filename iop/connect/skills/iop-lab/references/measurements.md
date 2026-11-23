# Measurement reductions

Use a research program when acquired numbers need correction, normalization or an uncertainty calculation. `ai4instruments.iop.measurements` provides `Estimate`, `RunningMean`, `propagate` and `normalize`; it has no scientific-package dependency. Instrument calls, saved progress and output use the usual program SDK.

## Values and shared errors

```python
from ai4instruments.iop.measurements import Estimate, normalize

signal = Estimate(6, "W", {"signal-noise": 0.2, "common-gain": 0.06})
reference = Estimate(11, "W", {"reference-noise": 0.3, "common-gain": 0.11})
dark = Estimate(1, "W", {"dark-noise": 0.1, "common-gain": 0.01})
transmission = normalize(signal, reference, dark)
# (6 - 1) / (11 - 1) = 0.5; standard uncertainty ≈ 0.025495.
run.save_result(transmission.to_dict())
```

Each component is a **signed contribution to standard uncertainty**, in the estimate's unit. Different names represent independent sources. The same name represents the same underlying error, including after `Estimate.from_dict(...)`. Choose names meaningful across the results being combined; a certificate identifier alone does not establish the correlation model. A shared gain cancels in this ratio. The one dark reading is used twice, and its covariance is retained.

Addition/subtraction requires equal unit labels. `estimate.scaled(0.001, unit="V")` explicitly converts mV to V. Multiplication/division by an exact scalar preserves contributions. The Python function `measurements.propagate(value, [(quantity, derivative), ...], unit="...")` uses the supplied partial derivatives for another differentiable model; it does not infer physical units. Convert expanded uncertainties to standard uncertainties using their stated coverage factors before constructing components.

## Repeated readings and continuation

```python
from ai4instruments.iop.measurements import RunningMean

saved = run.checkpoints().get("mean", {}).get("state")
mean = RunningMean.from_dict(saved) if saved is not None else RunningMean()
if saved is None:
    run.checkpoint(mean.to_dict(), name="mean")  # Establish continuation before the first instrument call.
for index in range(mean.count, 20):
    record = run.invoke_record("meter", "measure", request_id=f"{run.id}:mean:{index}")
    mean.add(record["result"]["power"])
    run.checkpoint(mean.to_dict(), name="mean")
reading = mean.estimate(source=f"repeatability:{run.id}", unit="W", components={"meter-offset": 0.002})
run.save_result(reading.to_dict())
```

`RunningMean` keeps count, mean and squared deviations. Its repeatability contribution is the sample standard deviation divided by the square root of the count; fewer than two observations cannot establish it. Shared components supplied to `estimate` are not reduced by averaging. `merge` combines disjoint observation groups. Overlapping groups require covariance information beyond these summaries.

The [reference-measurement example](https://github.com/EnvFoundry/IOP/tree/main/examples/iop/reference-measurement) runs configurable signal/reference/dark operations, optional preparation steps and precision-based stopping. Every round saves its raw operation records, a reduction and progress. Lost replies can be repeated under stable request IDs; a resumed program retains completed measurements. On explicit continuation, this method replaces a known failed reading under a new stable ID while keeping its failure record. Unknown outcomes and failed preparation graphs require explicit method-specific recovery.

## Assumptions of the model

Nonlinear propagation uses first-order derivatives, following [JCGM 100, §5](https://www.iso.org/sites/JCGM/GUM/JCGM100/C045315e-html/C045315e_FILES/MAIN_C045315e/05_e.html). The mean's repeatability assumes independent observations of a stationary quantity. Drift, autocorrelation, detector saturation and model discrepancy need their own model or correction. Invalid/nonfinite data are refused, never silently dropped. `normalize` refuses a zero corrected reference; near-zero references may need a nonlinear uncertainty model. The example reports no ratio when the reference's relative standard uncertainty exceeds its configured limit. A precision target is a stopping rule for this estimated uncertainty, not a confidence-interval coverage guarantee.

## 中文

`measurements` 用于研究程序中的校正、归一化与不确定度传播。`Estimate` 保存数值、单位和具名误差贡献；每个贡献带符号，单位与数值相同，表示标准不确定度而非置信区间。同名项表示同一误差源，不同名称表示独立误差源。共同增益在比值中可以抵消，共用暗信号的相关性会被保留；不能仅因两个读数来自同一校准证书就假定它们完全相关。

`RunningMean` 以固定内存累计次数、均值和平方偏差，可序列化进 `run.checkpoint`。重复性项使用均值的标准误差，至少需要两个独立读数；额外指定的共同校准项不会因平均而缩小。`merge` 仅用于不重叠的数据组。首次操作前保存空进度，再用稳定 `request_id` 完成读取并更新进度，中断后相同请求会取回原记录。

`normalize` 计算共享暗信号校正后的信号/参考比值；其他模型用 `propagate` 显式给出偏导数。单位转换显式完成，非有限读数会报错。模型使用一阶传播；漂移、自相关、饱和及参考接近零时应使用适合的模型。精度目标用于停止采样，不代表置信区间覆盖率的保证。完整可运行方法和模拟参数见上面的实例链接。

# Operating conditions / 工作条件

[Operations](operations.md) · [Platforms](../../iop/platforms.md) · [HTTP API](http-api.md)

## The principle / 原理

A platform can state the operating conditions required by a particular measurement: what to observe, which values are acceptable, how to restore them, and when to observe again. The researcher or agent supplies this knowledge; the Runtime executes it using the existing Basic, Standard and Workflow Operations. / 平台可以写明一次测量需要的工作条件：观察什么、什么数值可接受、怎样恢复、何时重新观察。研究者或 Agent 提供这些知识，Runtime 用现有的基础、标准和工作流操作执行。

The cycle is **observe → compare → correct when needed → observe again**. `maintain` starts a pass. Without `upkeep` the Runtime never starts one by itself; a platform that declares [`upkeep`](#upkeep--自主保持) has passes started by the Runtime while its instruments are idle, each recorded with its reasons. / 循环是**观察 → 比较 → 必要时校正 → 再次观察**。调用 `maintain` 开始一轮。未声明 `upkeep` 时，运行服务不会自行开始；声明了 [`upkeep`](#upkeep--自主保持) 的平台，由运行服务在设备空闲时开始，每一轮连同原因一起记录。

- **Outcomes:** `holds` is within the stated tolerance; `drifted` is a usable reading outside it; `unusable` cannot support this decision. An unusable reading may arise from the measurement itself, a local fault or a dependency. / **结果：** `holds` 满足所设容差；`drifted` 是可用但超出容差的读数；`unusable` 无法支持这次判断，可能来自测量本身、本地故障或依赖项。
- **Dependencies:** `after` declares which conditions must hold first. The Runtime follows this graph and can revisit dependencies when a reading is unusable. It does not discover causal relationships from wiring or labels. / **依赖：** `after` 明确哪些条件需要先成立；读数不可用时可回看依赖。运行服务不会从接线或分类名称推断因果关系。
- **Validity:** optional `valid_for` limits the age of an accepted observation. Reported disturbances, corrections and relevant description changes require renewed observations. Without `valid_for`, there is no time-based expiry. / **有效性：** 可选的 `valid_for` 限制观察的有效时间；已报告的扰动、校正和相关说明变更要求重新观察。未设置时没有按时间到期的规则。
- **Records:** experiments retain the observations they relied on. Later loss of a condition can mark the relevant interval `in_doubt`; this is a reason to examine the data, not proof that every result in that interval is wrong. / **记录：** 实验保存其依赖的观察；后续发现条件失效，可将相关时间段标为 `in_doubt`，表示需要重新解释这段数据，并非断言其中所有结果都错了。

The dependency-graph approach draws on Kelly et al., *Physical qubit calibration on a directed acyclic graph* ([arXiv:1803.03226](https://arxiv.org/abs/1803.03226)). IOP's implementation and platform-specific choices are separate from that paper's qubit calibration procedure. / 依赖图方法参考 Kelly 等人的量子比特校准工作；IOP 的实现和各平台的配置，并不是该论文对所有实验领域作出的结论。

## Condition labels / 条件分类

`kind` is an optional, extensible label. These common examples impose no order and are not an exhaustive list. Only `after` defines dependencies. / `kind` 是可选、可扩展的标签。以下常用分类没有固定顺序，也不构成穷尽列表；依赖只由 `after` 定义。

| `kind` | Examples / 例子 |
| --- | --- |
| `environment` | Temperature, vibration, pressure / 温度、振动、压力 |
| `equilibrium` | Warm-up and settling / 预热、稳定过程 |
| `reference` | Dark counts, offset, background / 暗计数、零点、本底 |
| `calibration` | Frequency, power or wavelength calibration / 频率、功率、波长标定 |
| `alignment` | Coupling, contact, focus / 耦合、接触、聚焦 |
| `lock` | Frequency lock or clock synchronization / 锁频、时钟同步 |
| `operating_point` | The operating region specified by this experiment / 该实验要求的工作区域 |
| `signal` | Signal-to-noise ratio, visibility, count rate / 信噪比、可见度、计数率 |

Use additional labels such as `sample_integrity` where needed. A coupled adjustment can be one saved operation rather than a cycle in the condition graph. / 可按需要增加 `sample_integrity` 等标签。需要相互迭代的联合调节可以封装为一个保存的操作，条件依赖图本身保持无环。

## A condition / 一个条件

Conditions are part of the platform description (`conditions`) and are saved with `configure_platform`. Names are unique in the laboratory. / 条件是平台说明的一部分（`conditions`），随 `configure_platform` 保存；名称在实验室内唯一。

```json
{"id": "path.aligned", "kind": "alignment",
 "description": "The source reaches the detector",
 "after": ["source.stable"],
 "verify": {"device": "detector", "operation": "read"},
 "accept": {"value": ["signal"], "minimum": 0.8},
 "usable": {"value": ["signal"], "minimum": 0.05},
 "reference": {"dark": ["dark"]},
 "correct": [{"operation": "coupler.peak_up"},
             {"device": "bench", "operation": "align_by_hand"}],
 "settle": 2, "valid_for": 3600,
 "note": "Coupling drifts for about ten minutes after the enclosure was opened"}
```

| Field | Meaning / 含义 |
| --- | --- |
| `verify` | A short reference measurement: one device operation, or a saved operation without `device`, whose named results can come from [views of arrays](operations.md), such as the frequency of a resonance / 一次简短的基准测量：一个设备操作，或不带 `device` 的已保存操作，其具名结果可以取自[数组的视图](operations.md)，例如谐振频率 |
| `accept` | The tolerance: `value` is the path into the result, with `minimum`, `maximum` or `equals` / 容差：`value` 是结果中的路径，配 `minimum`、`maximum` 或 `equals` |
| `usable` | Whether the reading is usable for this decision; otherwise maintenance revisits declared dependencies / 读数是否适用于这次判断；否则保持过程会重新查看已声明的依赖 |
| `reference` | Values of the accepted verification kept with the experiments that rely on it, such as a dark level used in the analysis / 通过核对时保留的数值，随依赖它的实验保存，例如分析时要扣除的暗计数 |
| `correct` | What restores the condition, tried in order, each followed by a verification: an operation, or [`tune`](#tuning--调节). A step of a `manual` device asks a person / 恢复该条件的做法，按顺序尝试，每个之后核对一次：一个操作，或 [`tune`](#tuning--调节)；`manual` 设备的步骤交给人 |
| `settle` | Seconds between a correction and its verification / 校正之后到核对之前等待的秒数 |
| `valid_for` | Seconds an accepted verification is trusted; without it there is no time-based expiry / 一次通过的观察有效的秒数；不写则没有按时间到期的规则 |
| `after` | Conditions of this platform that must hold first / 必须先成立的本平台条件 |
| `kind`, `note` | An extensible category and contextual explanation / 可扩展的分类与背景说明 |

A correction acts on the system. A `reference` acts on the data: the condition "the dark level was measured within the last half hour" has no correction beyond measuring again, and its value travels with every experiment that requires it. / 校正作用于系统；`reference` 作用于数据：“半小时内测过暗计数”这个条件的校正就是再测一次，它的数值随每个依赖它的实验保存。

## Conditions of an instrument / 仪器自身的条件

Some conditions belong to one instrument wherever it is used: a laser diode at its temperature, a lock-in amplifier without overload or reference unlock, a source warmed up, a self-calibration that is current. They are written in the device record or in its [model description](../../iop/instrument-packages.md), observed and restored with that instrument's own operations, and named `<device>.<id>` in the laboratory. / 有些条件属于单台仪器，不论它用在哪里：激光二极管到达工作温度、锁相放大器没有过载或参考失锁、光源已预热、自校准仍然有效。它们写在设备记录或[型号说明](../../iop/instrument-packages.zh-CN.md)里，用这台仪器自己的操作观察和恢复，在实验室中命名为 `<device>.<id>`。

```json
{"id": "laser", "driver": "...", "config": {"...": "..."},
 "conditions": [
   {"id": "warm", "kind": "equilibrium", "description": "The diode is at its set temperature",
    "verify": {"operation": "temperature"}, "accept": {"minimum": 24.5, "maximum": 25.5},
    "correct": {"operation": "wait_warm", "arguments": {"minutes": 30}}},
   {"id": "power_ok", "description": "The output power is at its working value", "after": ["warm"],
    "verify": {"operation": "power"}, "accept": {"minimum": 0.9, "maximum": 1.1}}]}
```

- `device` may be omitted from their operations; another instrument is refused. `after` names conditions of the same instrument by their local names. / 操作中可以省略 `device`；用到其他仪器会被拒绝。`after` 用本地名称指向同一台仪器的条件。
- Registration and configuration check them against the instrument's operations and parameter ranges. A configuration that removes a condition a platform depends on is refused until the platform is revised. / 登记和修改配置时，按仪器的操作和参数范围核对。删除平台所依赖的条件时，配置被拒绝，先修改平台。
- A platform condition may depend on them: `"after": ["laser.power_ok"]`. A platform's listing, its next step in `lab_context` and `maintain(platform)` include the conditions of its instruments. An instrument outside every platform is maintained alone with `maintain(device=...)`. / 平台条件可以依赖它们。平台的列表、`lab_context` 中的下一步和 `maintain(platform)` 都包含其仪器的条件；不属于任何平台的仪器用 `maintain(device=...)` 单独保持。
- A model description can carry them. Every instrument that registers with that description, or is identified as that model, gets those conditions unless its record defines the same name; a condition is left out when an operation it needs is not among the instrument's operations or `allowed_operations`. / 型号说明可以携带它们。按该说明登记或被识别为该型号的每台仪器都会得到这些条件，记录中同名的条件优先；所需操作不在仪器操作或 `allowed_operations` 中的条件不加入。

The built-in SR830 description carries four: `reference_locked`, `input_in_range`, `filter_in_range` and `output_in_range`, each read from one LIA status bit. These bits stay set until they are read, so an observation reports whether an unlock or overload occurred since the previous one; data taken in between by experiments that relied on the condition are marked `in_doubt`. / 内置的 SR830 说明带有四个条件，各读一个 LIA 状态位。这些位读取前保持置位，所以一次观察说明的是自上次观察以来是否发生过失锁或过载；依赖该条件的实验在这期间采集的数据被标为 `in_doubt`。

## Tuning / 调节

Most corrections an experimentalist makes by hand have one form: change a setting a little, look at the reading, keep the change if it helped. `tune` lets the Runtime do this itself, for one setting or for several adjusted jointly. / 实验者手动做的校正大多是同一种形式：把某个设定改一点，看读数，变好就保留。`tune` 让运行服务自己做这件事，可以是一个设定，也可以是多个设定联合调节。

```json
"correct": {"tune": [
   {"device": "mirror", "operation": "set_x", "argument": "position",
    "read": {"device": "mirror", "operation": "where", "value": ["x"]},
    "step": 0.1, "resolution": 0.01, "minimum": -1, "maximum": 1},
   {"device": "mirror", "operation": "set_y", "argument": "position",
    "read": {"device": "mirror", "operation": "where", "value": ["y"]},
    "step": 0.1, "resolution": 0.01, "minimum": -1, "maximum": 1}],
  "until": "optimum", "evaluations": 40}
```

- The value that is improved is the one `accept` looks at. `goal` is `maximize` when `accept` has only a minimum, `minimize` with only a maximum, and `center` with both. / 被优化的就是 `accept` 所看的数值。`accept` 只有下限时 `goal` 为 `maximize`，只有上限时为 `minimize`，两者都有时为 `center`。
- The search steps one setting in the direction that improves the value, steps back when it does not, goes on to the next setting, and halves the step when no move helps, down to `resolution`. It needs no model of the system. / 搜索沿着变好的方向逐步改变一个设定，没有变好就退回，再换下一个设定；所有方向都无改善时步长减半，直到 `resolution`。不需要系统的模型。
- Requested settings stay within `[minimum, maximum]`; an initial out-of-range readback is first moved into range. The local coordinate search uses at most `evaluations` trial observations and, on normal completion, retains the best usable setting observed. It does not establish a global optimum; cancellation or a failed physical action stops the pass without assuming a rollback succeeded. `until: "accept"` stops as soon as the tolerance is met. `gain` is the smallest improvement that counts when the reading is noisy. / 请求的设定在 `[minimum, maximum]` 内；初始读回超出范围时先移动到范围内。这是局部坐标搜索，最多进行 `evaluations` 次试探观察；正常结束时保留观察到的最佳可用设定，不保证全局最优。取消或物理动作失败时停止，不假定已经恢复到先前位置。`until: "accept"` 在满足容差后立即停止；读数有噪声时用 `gain` 给出算数的最小改善。
- The record of the correction keeps where each setting started and ended and how many settings were tried. / 校正记录保留每个设定的起点、终点和尝试次数。

`tune` and operations can be listed together: tune first, then a `manual` step when tuning cannot restore the condition. / `tune` 可以和操作一起列出：先调节，调不回来再交给 `manual` 步骤。

On the demonstration devices: / 在演示设备上：

```sh
iop init --demo && iop start
iop platform apply examples/iop/conditions.json
iop conditions --platform demo-bench --maintain   # verifies, corrects, reports
iop conditions                                     # states now
```

## Maintaining / 保持

`maintain` runs the loop for a platform or for named conditions. For each condition, after its dependencies: / `maintain` 对一个平台或指定的条件运行这个循环。对每个条件，在它的依赖之后：

1. If its state holds, nothing is measured. / 状态成立则不测量。
2. Otherwise it is verified. *Holds*: recorded. / 否则核对。*成立*：记录。
3. *Unusable*: its dependencies are verified whatever their state says and corrected where needed; then it is verified again. / *不可用*：不管状态怎么说，先核对它依赖的条件并在需要时校正，然后再核对它。
4. *Drifted*, or still unusable: its corrections are tried in order until a verification holds. / *漂移*，或仍然不可用：按顺序尝试它的校正，直到核对成立。
5. If none restores it the condition is **lost**, and what depends on it is **blocked**. / 都无法恢复则该条件**不成立**，依赖它的条件**受阻**。
6. A correction makes what depends on that condition due; those are verified before the pass ends. / 一次校正使依赖该条件的各项待核对；它们在本轮结束前被重新核对。

The pass is an experiment of its own, so every verification and correction is an ordinary operation with its record, reservation and monitors. The pass reserves the devices and shared resources used by all its dependencies and corrections together. Independent resources remain available to other experiments. Inside an existing experiment, reserve these resources when starting it and finish its current batch before maintenance. / 一轮保持本身是一次实验，每次观察和校正都是普通操作。整轮共同占用观察、依赖和校正所需的设备与共享资源，其他独立资源仍可并行使用。在已有实验中执行时，启动实验就要预留这些资源，并在当前批次完成后调用。

Saved operations used for observations or corrections keep their own `requires`. Declare those prerequisites in the condition graph where appropriate, or establish them before maintenance. A correction should not require the condition it is meant to restore. / 观察或校正采用的已保存操作，仍保留其自身的 `requires`。把实际前提写入条件依赖图，或在保持前建立这些条件；校正方法不应要求其所要恢复的条件已经成立。

## State / 状态

The state of a condition is known without touching an instrument: / 条件的状态不需要触碰仪器就能得出：

| State | When / 何时 |
| --- | --- |
| `holds` | The last verification was accepted, is within `valid_for`, nothing it depends on was corrected since, and its dependencies hold / 上次核对通过、在有效期内、其依赖此后没有被校正过，且依赖都成立 |
| `due` | It held, and one of those is no longer true; `reason` says which / 曾经成立，但上述某一点已不满足；`reason` 说明是哪一点 |
| `lost` | The last verification did not hold and no correction restored it / 上次核对不成立，且校正未能恢复 |
| `unknown` | Never observed, or its measurement, dependency definition, referenced device or saved-operation revision changed / 从未观察，或测量方式、依赖定义、所用设备或保存操作的版本改变 |

`lab_context` lists only the conditions that do not hold, with the next step `maintain_conditions`. / `lab_context` 只列出不成立的条件，并给出下一步 `maintain_conditions`。

## Experiments rely on conditions / 实验依赖条件

A submission names what it relies on with `requires`; a saved Standard or Workflow Operation can name it too, and then every use requires it. / 提交时用 `requires` 写明依赖的条件；已保存的标准操作和工作流操作也可以写，之后每次使用都要求它。

- The experiment is accepted while every required condition holds. Otherwise it is refused with `conditions_not_held`, naming each condition and why; after `maintain` the same submission is accepted. / 所依赖的条件都成立时才受理；否则以 `conditions_not_held` 拒绝并逐条说明原因，`maintain` 之后同一提交即被受理。
- The run records `conditions`: when each was accepted, its value and its `reference`. / 实验记录 `conditions`：每个条件何时通过、数值和 `reference`。
- In a continuous experiment every batch and operation is accepted on the same terms, so `valid_for` sets how often the work stops to verify. `run.maintain()` does it inside the experiment, and `run.batch(steps, maintain=True)` or `run.call(name, arguments, maintain=True)` do it only when needed. An experiment that declares [`upkeep`](#inside-an-experiment--实验内部) has the Runtime do it. / 连续实验中的每个批次和操作按同样的规则受理，因此 `valid_for` 决定了多久停下来核对一次。`run.maintain()` 在实验内部完成；`maintain=True` 只在需要时才做。
- When a later verification finds a condition lost, each run that relied on it since the last accepted verification gets `in_doubt[condition] = {since, found, outcome}` for the first such interval, and each of its operations in progress since then gets the same entry in its own `in_doubt`. A disturbance reported while an operation runs marks that operation, with outcome `disturbed`. `GET /v1/runs/{id}/operations?in_doubt=true`, `run.operations(in_doubt=True)` and `experiment(in_doubt=true)` in MCP list only these, such as the points of a sweep to take again, and `lab_context` names the conditions of an active experiment's data in doubt. Accepted observations describe their sampling instants; they do not prove uninterrupted validity between samples. / 之后的观察发现条件不成立时，自上次通过以来依赖它的每个实验得到 `in_doubt`（记第一个这样的时段），其间进行过的每个操作在自己的 `in_doubt` 中得到同样的记录；操作运行时报告的扰动以 `disturbed` 标记该操作。`GET /v1/runs/{id}/operations?in_doubt=true`、`run.operations(in_doubt=True)` 和 MCP 的 `experiment(in_doubt=true)` 只列出这些操作，例如需要重测的扫描点；`lab_context` 给出进行中实验存疑数据所涉及的条件。通过的读数只说明采样时刻符合要求，不能证明两次采样之间始终成立。

Before each dependent step starts, the Runtime checks the recorded condition state again and records the observations used by that step. An expired or disturbed condition stops the step before its device call. Without `upkeep` no readings or corrections are inserted into a graph; with it, what is due is renewed inside the experiment before the step starts. / 每个依赖条件的步骤启动前，Runtime 再看一次已有条件状态，并记录该步骤采用的观察依据。过期或受扰动的条件会阻止设备调用。不声明 `upkeep` 时，步骤图中不会插入读数或校正；声明后，到期的条件在步骤启动前于实验内部更新。

## Disturbances / 扰动

`invalidate(conditions, reason)` records that something happened (the enclosure was opened, a cable was changed) and makes those conditions and their dependents due. Re-observing only the upstream condition does not silently restore downstream validity. A monitor reading that leaves its limits does the same for the conditions verified or corrected with that device. / `invalidate(conditions, reason)` 记录发生了什么（开过罩子、换过线缆），使这些条件及下游依赖待确认。只重新观察上游，不会自动恢复下游条件。监视读数越限时，用到该设备的条件同样变为待核对。

## What the records say / 记录能说明什么

Every verification and correction is recorded. `GET /v1/conditions/{id}` returns the latest records and: / 每次核对和校正都有记录。`GET /v1/conditions/{id}` 返回最近的记录以及：

| Statistic | Meaning / 含义 |
| --- | --- |
| `verifications`, `holds`, `drifted`, `unusable` | How often each outcome occurred / 各结果出现的次数 |
| `held` | Intervals from an accepted observation to a later failed observation: count, median and shortest; these do not locate the instant of failure / 从通过的观察到后续失败观察的间隔：次数、中位数、最短；它们不能确定实际失效时刻 |
| `corrections` | For each correction, how often it was tried and how often it restored the condition / 每种校正尝试了几次、恢复了几次 |
| `trend` | A straight line through the accepted values since the last correction or reported disturbance: `per_hour`, the number of `points`, the `residual`, and `expected_until`, when the line reaches the tolerance / 自上次校正或已报告扰动以来通过值的直线拟合：每小时变化、点数、残差，以及到达容差的时间 `expected_until` |

Statistics use the latest 1,000 records. A linear extrapolation does not change expiry times or settings; with upkeep it only decides when to observe again. The state of a held condition shows it as `forecast`. This is where experience accumulates: which conditions drift, how fast, and what restores them, measured on this system. / 统计使用最近 1,000 条记录；直线外推不会改变有效期或设备设定，在自主保持中只决定何时再次观察；成立状态中以 `forecast` 给出。经验在这里积累：哪些条件会漂、漂多快、什么能恢复，都是在这套系统上测出来的。

## Upkeep / 自主保持

A platform can declare that the Runtime keeps its conditions. / 平台可以声明由运行服务来保持它的条件。

```json
"upkeep": {"ahead_s": 300, "retry_s": 1800, "correct": true}
```

The Runtime plans every `upkeep_interval_s` (2 s by default, in the service configuration). When a condition needs a pass and the instruments that pass would use are idle, it starts one for the conditions concerned. / 运行服务每隔 `upkeep_interval_s`（默认 2 秒，在服务配置中设置）安排一次：有条件需要处理、且这一轮要用的设备空闲时，就为这些条件开始一轮。

| A pass starts for a condition that is / 何时开始 | Reason recorded / 记录的原因 |
| --- | --- |
| Never observed, or its basis changed / 从未观察或依据已变 | `unknown` |
| Due: validity elapsed, a dependency corrected, a disturbance / 待核对：过期、依赖被校正、受扰动 | `due: <reason>` |
| Still held, `ahead_s` before its validity ends / 仍成立，有效期结束前 `ahead_s` | `validity ends` |
| Still held, `ahead_s` before the drift forecast reaches the tolerance, and again when it does / 仍成立，漂移预测到达容差前 `ahead_s`，以及到达时 | `forecast reaches the tolerance` |
| Lost / 不成立 | `lost` |

- **Idle.** No active experiment uses or reserves a device or shared resource the pass needs, and none of them is held. Otherwise upkeep waits; its status names the experiment or resource. A queued experiment goes first. / **空闲。** 没有进行中的实验使用或预留这一轮所需的设备和共享资源，也没有处于挂起状态的。否则等待，状态中写明在等哪个实验或资源；排队中的实验优先。
- **One observation per moment.** A foreseen moment triggers once, because the observation moves the accepted time past it. The forecast decides when to look again, never what to change. / **每个时刻观察一次。** 观察之后通过时间越过该时刻，所以不会重复。预测只决定何时再看，不决定改变什么。
- **Retry.** An attempt that does not end with the condition held is repeated only after `retry_s`. / **重试。** 未能使条件成立的尝试，`retry_s` 之后才再进行。
- **No steps for a person.** An automatic pass skips corrections that ask a person; such a condition ends `lost` with `left_for_a_person`, for an explicit `maintain` or the operator. / **不安排人工步骤。** 自动的一轮跳过需要人来做的校正，该条件以 `lost` 结束并给出 `left_for_a_person`，留给显式的 `maintain` 或操作者。
- **Observe only.** With `correct: false`, drifted conditions are recorded as lost with `not_corrected`, and nothing is changed. / **只观察。** `correct: false` 时，漂移的条件记为不成立并注明 `not_corrected`，不做任何改变。
- **Disclosed.** Each automatic pass is an ordinary maintenance in its own experiment, named `Maintain <platform> (upkeep)`, with `automatic: true` and `reasons` in its record. `conditions` limits upkeep to some of the platform's conditions and its instruments'. Remove `upkeep` from the platform to stop. / **公开记录。** 每一轮都是普通的保持，在自己的实验中进行，名称为 `Maintain <platform> (upkeep)`，记录中有 `automatic: true` 和 `reasons`。`conditions` 可把自主保持限定在部分条件上；从平台中删除 `upkeep` 即停止。

Platform upkeep does not interrupt an experiment in progress. The status is in `GET /v1/upkeep`, `lab_context.setup.upkeep` and on the platform card: the next foreseen observation, pending reasons, what it waits for and the last pass. / 平台的自主保持不打断进行中的实验。状态见 `GET /v1/upkeep`、`lab_context.setup.upkeep` 和平台卡片：下一次预计观察、待处理的原因、正在等待什么，以及上一轮。

### Inside an experiment / 实验内部

An experiment that names its conditions in `requires` can declare `upkeep` too, `True` or `{"ahead_s": ..., "retry_s": ..., "correct": ...}`: a step graph, `submit(steps, requires=[...], upkeep=True)`; a continuous experiment, `start_experiment(requires=[...], upkeep=True)`; or a program. The Runtime then keeps those conditions, and the conditions they depend on, inside the experiment: / 用 `requires` 写明条件的实验也可以声明 `upkeep`（`True` 或 `{"ahead_s": ..., "retry_s": ..., "correct": ...}`）：步骤图 `submit(steps, requires=[...], upkeep=True)`、连续实验 `start_experiment(requires=[...], upkeep=True)` 或程序。运行服务随后在实验内部保持这些条件及其依赖：

Program upkeep starts after its resources are reserved and the program is running. Conditions from several instruments can be kept in one experiment, within its declared platform and resource reservation. If a new batch adds conditions not covered by a maintenance pass already in progress, it returns `maintenance_active` before acceptance; wait for the named pass and retry the same request ID. / 程序取得预留资源并进入运行状态后才开始维护。同一实验可在声明的平台与预留资源范围内保持多个仪器的条件。若新批次增加了当前维护尚未覆盖的条件，会在受理前返回 `maintenance_active`；等待所指的维护结束，再用同一请求 ID 重试。

- **Before an operation.** When an operation is submitted or about to start and a required condition is unknown, due, lost (after `retry_s`) or about to expire, the operation waits: a pass starts inside the experiment once none of its operations is running, and the operations continue after it. Each operation still checks its conditions when it starts; one that could not be restored fails with `conditions_not_held`. A validity shorter than the time from a verification to the next operation cannot be kept: after three passes in a row that an operation free to start waited for, with none started between them, that operation fails with `conditions_not_held` and says so, rather than renewing without end; review maintenance and acquisition timing and the `valid_for` setting; extend validity only when observations support it. Passes that keep an experiment with nothing to run, or whose operations wait for an instrument, do not count: it is kept for as long as it stays so. / **操作之前。** 提交操作或操作即将开始时，若所需条件未观察、待核对、不成立（`retry_s` 之后）或即将过期，操作先等待：实验中没有操作在运行时，就在实验内部开始一轮，结束后操作继续。每个操作启动时仍会核对条件；无法恢复时以 `conditions_not_held` 失败。有效期短于“核对完成到下一个操作开始”所需时间的条件无法保持：可以启动的操作连续等待了三轮、其间没有任何操作启动时，该操作以 `conditions_not_held` 失败并说明原因，而不是无休止地更新；应检查维护、采集时序与 `valid_for` 设置；只有观测依据支持时才延长有效期。实验暂时没有操作可运行，或其操作在等待仪器时，为保持条件而进行的各轮不计入：这种状态持续多久，条件就保持多久。
- **Between operations.** While none of its operations is running, the periodic plan renews what is about to expire, `ahead_s` early. / **操作之间。** 实验中没有运行中的操作时，周期性的安排会提前 `ahead_s` 更新即将过期的条件。
- **At the start.** An experiment that keeps its conditions is accepted while they do not hold yet; they are brought up before its first operation. Starting it requires reserving the devices and resources that maintenance uses; otherwise it is refused with `maintenance_resources`, naming them. A step graph that reserves what its steps use reserves those too; one without a reservation claims each instrument when maintenance uses it, as its steps do. / **开始时。** 保持自身条件的实验在条件尚未成立时也会被受理，第一个操作之前先把条件建立起来。启动时需要预留保持过程会用到的设备和资源，否则以 `maintenance_resources` 拒绝并列出它们。预留了所用设备的步骤图也一并预留这些；不预留的步骤图在保持过程用到某台设备时才占用它，与其步骤相同。

A sweep of several hours is then kept for its whole length: an alignment trusted for ten minutes is verified again between its points, and a drift found then is corrected before the next point. Each operation records the verifications it relied on in `conditions`, with their time, value and `reference` values such as a dark level, and a sweep with [`retake`](operations.md#sweeps--扫描) takes its points in doubt again once the condition is restored. The passes are recorded in the experiment with `automatic: true` and their reasons, and the points taken before a condition was found lost are marked `in_doubt`, each in its own record. A program no longer needs to handle `conditions_not_held` itself. / 长达数小时的扫描因此全程得到保持：有效期十分钟的对准在两点之间重新核对，此时发现的漂移在下一点之前校正。每个操作在 `conditions` 中记录它依赖的核对，包括时间、读数和 `reference` 中的数值，例如暗计数；带 [`retake`](operations.md#sweeps--扫描) 的扫描在条件恢复后重测存疑的点。这些轮次记录在实验中，带 `automatic: true` 和原因；条件被发现不成立之前采集的各点在各自的记录中标为 `in_doubt`。程序无需再自己处理 `conditions_not_held`。

## Writing the conditions of a platform / 为一个平台写条件

Record the knowledge needed by the intended experiment, then revise it as the platform and methods change. Notes, manuals and human steps can accompany executable observations and corrections. / 记录目标实验需要的知识，随平台和方法变化持续修订。解释、手册和人工步骤可以与可执行的观察、校正一起保存。

1. Before a measurement counts, what must be true? Use the labels relevant to this platform. / 测量算数之前什么必须成立？采用与本平台有关的分类。
2. For each, which short measurement tells, and which value is acceptable? / 每一项用哪个简短的测量判断，什么数值可以接受？
3. When that measurement shows nothing at all, what do you look at first? Which are actual prerequisites? Put those in `after`. / 测量没有有效结果时先看哪里？其中哪些确实是前提？把前提写入 `after`。
4. How do you restore it, and what do you try when that does not work? / 怎么恢复，不行时再试什么？
5. How long does it stay true, and what disturbs it? / 能维持多久，什么会扰动它？

Settling times, warm-up times and safe ranges come from the instrument manuals; tolerances and `valid_for` are then corrected from the records. / 稳定时间、预热时间和安全范围来自仪器手册；容差和 `valid_for` 之后根据记录修正。

## Interfaces / 接口

| | Read / 读取 | Maintain / 保持 | Disturbance / 扰动 |
| --- | --- | --- | --- |
| HTTP | `GET /v1/conditions?platform=&device=`, `GET /v1/conditions/{id}`, `GET /v1/upkeep` | `POST /v1/conditions/maintain?wait=`, `GET /v1/maintenance/{id}?wait=` | `POST /v1/conditions/invalidate` |
| Python | `client.conditions()`, `client.condition(id)` | `client.maintain(platform)`, `client.maintain(device=...)`, `run.maintain()` | `client.invalidate(names, reason)` |
| TypeScript | `conditions()`, `condition(id)` | `maintain({platform})`, `run.maintain()` | `invalidate(names, reason)` |
| MCP | `operating_conditions()` | `operating_conditions(maintain=true, ...)` | `operating_conditions(invalidate=..., conditions=[...])` |
| CLI | `iop conditions [name]` | `iop conditions --platform P --maintain`, `--device D --maintain` | |

`maintain` accepts `platform` or `conditions`, `run_id` to run inside that experiment, `request_id` so a repeated request returns the same pass, and `force` to verify what still holds. Its report lists each condition with `held`, `verified`, `corrected` (and `by`), `lost` or `blocked`. / `maintain` 接受 `platform` 或 `conditions`，`run_id` 表示在该实验内部运行，`request_id` 使重复请求返回同一轮，`force` 连仍然成立的也核对。报告逐条给出 `held`、`verified`、`corrected`（及 `by`）、`lost` 或 `blocked`。

Platform conditions belong to platform descriptions; an instrument's own conditions belong to its record and can come with its model description. / 平台条件保存在平台说明中；仪器自身的条件保存在设备记录中，并可随型号说明一起提供。

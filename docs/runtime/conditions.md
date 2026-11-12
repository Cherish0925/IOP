# Operating conditions / 工作条件

[Operations](operations.md) · [Platforms](../../iop/platforms.md) · [HTTP API](http-api.md)

## The principle / 原理

A platform can state the operating conditions required by a particular measurement: what to observe, which values are acceptable, how to restore them, and when to observe again. The researcher or agent supplies this knowledge; the Runtime executes it using the existing Basic, Standard and Workflow Operations. / 平台可以写明一次测量需要的工作条件：观察什么、什么数值可接受、怎样恢复、何时重新观察。研究者或 Agent 提供这些知识，Runtime 用现有的基础、标准和工作流操作执行。

The cycle is **observe → compare → correct when needed → observe again**. `maintain` starts a pass; it does not run an undisclosed background optimizer. / 循环是**观察 → 比较 → 必要时校正 → 再次观察**。调用 `maintain` 才开始一轮，不会在后台自行改变实验设定。

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
| `verify` | A short reference measurement: one device operation, or a saved operation without `device` / 一次简短的基准测量：一个设备操作，或不带 `device` 的已保存操作 |
| `accept` | The tolerance: `value` is the path into the result, with `minimum`, `maximum` or `equals` / 容差：`value` 是结果中的路径，配 `minimum`、`maximum` 或 `equals` |
| `usable` | Whether the reading is usable for this decision; otherwise maintenance revisits declared dependencies / 读数是否适用于这次判断；否则保持过程会重新查看已声明的依赖 |
| `reference` | Values of the accepted verification kept with the experiments that rely on it, such as a dark level used in the analysis / 通过核对时保留的数值，随依赖它的实验保存，例如分析时要扣除的暗计数 |
| `correct` | What restores the condition, tried in order, each followed by a verification: an operation, or [`tune`](#tuning--调节). A step of a `manual` device asks a person / 恢复该条件的做法，按顺序尝试，每个之后核对一次：一个操作，或 [`tune`](#tuning--调节)；`manual` 设备的步骤交给人 |
| `settle` | Seconds between a correction and its verification / 校正之后到核对之前等待的秒数 |
| `valid_for` | Seconds an accepted verification is trusted; without it there is no time-based expiry / 一次通过的观察有效的秒数；不写则没有按时间到期的规则 |
| `after` | Conditions of this platform that must hold first / 必须先成立的本平台条件 |
| `kind`, `note` | An extensible category and contextual explanation / 可扩展的分类与背景说明 |

A correction acts on the system. A `reference` acts on the data: the condition "the dark level was measured within the last half hour" has no correction beyond measuring again, and its value travels with every experiment that requires it. / 校正作用于系统；`reference` 作用于数据：“半小时内测过暗计数”这个条件的校正就是再测一次，它的数值随每个依赖它的实验保存。

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
- In a continuous experiment every batch and operation is accepted on the same terms, so `valid_for` sets how often the work stops to verify. `run.maintain()` does it inside the experiment, and `run.batch(steps, maintain=True)` or `run.call(name, arguments, maintain=True)` do it only when needed. / 连续实验中的每个批次和操作按同样的规则受理，因此 `valid_for` 决定了多久停下来核对一次。`run.maintain()` 在实验内部完成；`maintain=True` 只在需要时才做。
- When a later verification finds a condition lost, each run that relied on it since the last accepted verification gets `in_doubt[condition] = {since, found, outcome}`. Accepted observations describe their sampling instants; they do not prove uninterrupted validity between samples. / 之后的观察发现条件不成立时，自上次通过以来依赖它的每个实验得到 `in_doubt`。通过的读数只说明采样时刻符合要求，不能证明两次采样之间始终成立。

Before each dependent step starts, the Runtime checks the recorded condition state again and records the observations used by that step. An expired or disturbed condition stops the step before its device call. This does not insert extra readings or corrections into a graph. For long work, use a program or continuous experiment that calls maintenance between completed batches. / 每个依赖条件的步骤启动前，Runtime 再看一次已有条件状态，并记录该步骤采用的观察依据。过期或受扰动的条件会阻止设备调用；步骤图中不会擅自插入读数或校正。长时间工作通过程序或连续实验，在已完成的批次之间调用保持。

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

Statistics use the latest 1,000 records. A linear extrapolation is descriptive and does not change expiry times or settings automatically. This is where experience accumulates: which conditions drift, how fast, and what restores them, measured on this system. / 统计使用最近 1,000 条记录；直线外推不会自动改变有效期或设备设定。经验在这里积累：哪些条件会漂、漂多快、什么能恢复，都是在这套系统上测出来的。

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
| HTTP | `GET /v1/conditions?platform=`, `GET /v1/conditions/{id}` | `POST /v1/conditions/maintain?wait=`, `GET /v1/maintenance/{id}?wait=` | `POST /v1/conditions/invalidate` |
| Python | `client.conditions()`, `client.condition(id)` | `client.maintain(platform)`, `run.maintain()` | `client.invalidate(names, reason)` |
| TypeScript | `conditions()`, `condition(id)` | `maintain({platform})`, `run.maintain()` | `invalidate(names, reason)` |
| MCP | `operating_conditions()` | `operating_conditions(maintain=true, ...)` | `operating_conditions(invalidate=..., conditions=[...])` |
| CLI | `iop conditions [name]` | `iop conditions --platform P --maintain` | |

`maintain` accepts `platform` or `conditions`, `run_id` to run inside that experiment, `request_id` so a repeated request returns the same pass, and `force` to verify what still holds. Its report lists each condition with `held`, `verified`, `corrected` (and `by`), `lost` or `blocked`. / `maintain` 接受 `platform` 或 `conditions`，`run_id` 表示在该实验内部运行，`request_id` 使重复请求返回同一轮，`force` 连仍然成立的也核对。报告逐条给出 `held`、`verified`、`corrected`（及 `by`）、`lost` 或 `blocked`。

Conditions currently belong to platform descriptions. A reusable instrument profile does not yet install platform conditions automatically. / 条件目前保存在平台说明中；安装可复用的仪器型号说明，还不会自动安装平台条件。

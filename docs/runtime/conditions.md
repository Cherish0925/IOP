# Operating conditions / 工作条件

[Operations](operations.md) · [Platforms](../../iop/platforms.md) · [HTTP API](http-api.md)

## The principle / 原理

An experiment has its meaning only while the system is in its working state. An experienced experimentalist carries that state in mind as a short list: what must be true, how to tell, how to restore it, and how long it stays true. IOP keeps this list on the platform as **conditions**, and the Runtime, not the person or the agent, keeps them true. / 实验只有在系统处于工作状态时才有意义。有经验的实验者心里有一张不长的表：什么必须成立、怎么判断、怎么恢复、能维持多久。IOP 把这张表作为**条件**保存在平台上，由运行服务来保持它们成立，而不是靠人或 Agent 记着。

Keeping any experimental system in its working state is one loop: / 让任何实验系统保持在工作状态，是同一个循环：

```text
verify  →  compare with the tolerance  →  correct  →  verify again
基准读数 →  与容差比较               →  校正     →  再次核对
```

Four facts make the loop general. / 四点使这个循环通用。

1. **A verification has three outcomes.** *Holds*: within tolerance. *Drifted*: measurable but out of tolerance, so this condition is corrected. *Unusable*: the measurement itself means nothing (no signal, a failed read), so the cause is in what this condition depends on. / **核对有三种结果。** *成立*：在容差内。*漂移*：可以测但超出容差，校正这一项。*不可用*：测量本身没有意义（没有信号、读数失败），原因在它依赖的上游条件。
2. **Conditions are ordered by cause.** Each names the conditions it stands on (`after`). Bringing a system up walks this order forward; finding a fault walks it backward to the first condition that does not hold. / **条件按因果排序。** 每个条件写明它依赖的条件（`after`）。把系统调起来是顺着这个顺序走；找故障是逆着走到第一个不成立的条件。
3. **Every assurance expires.** An accepted verification is trusted for `valid_for`, and no longer once something it depends on was corrected or the system was disturbed. / **每一次确认都会过期。** 一次通过的核对在 `valid_for` 内可信；它依赖的条件被校正过，或系统受到扰动，就不再可信。
4. **Data carry their assurance.** An experiment records what it relied on; when a later verification finds a condition lost, the data taken since the last accepted one are marked in doubt. / **数据带着它的依据。** 实验记录它依赖了什么；之后的核对发现某个条件已经不成立时，自上次通过以来采集的数据被标记为存疑。

The loop runs on three occasions: when a platform is brought up, when a verification has expired, and when something disturbed the system. The procedure is the calibration graph of Kelly et al., *Physical qubit calibration on a directed acyclic graph* ([arXiv:1803.03226](https://arxiv.org/abs/1803.03226)), stated for any platform. / 这个循环在三种时机运行：平台启用时、核对过期时、系统受扰动时。做法来自 Kelly 等人的校准有向无环图，这里推广到任何平台。

## What the conditions of a system are / 一个系统有哪些条件

They are finite. From the outside in, each kind presupposes the ones before it. / 它们是有限的。由外到内，每一类以前面各类为前提。

| `kind` | What must hold / 什么必须成立 | Examples / 例 |
| --- | --- | --- |
| `environment` | The surroundings are within bounds / 环境在允许范围内 | Room temperature, humidity, vibration, background field, vacuum, cryostat base temperature |
| `equilibrium` | Instruments and samples have settled / 仪器和样品已经稳定 | Source warmed up, oven at temperature, amplifier bias stable |
| `reference` | The zero is known / 零点已知 | Dark counts, background, offset, tare, noise floor, baseline |
| `calibration` | Scales agree with a standard / 标度与标准一致 | Wavelength or frequency axis, power, network-analyzer error terms, delay and phase |
| `alignment` | The signal path transmits / 信号通路是通的 | Beam pointing, fiber coupling, probe contact, focus, sample position |
| `lock` | Loops are locked and timing is synchronous / 环路锁定、时序同步 | Frequency and phase locks, reference clock, trigger delays |
| `operating_point` | The system works where it is linear and sensitive / 工作点在线性且灵敏的位置 | Bias, gain, level below compression, on resonance, matched |
| `signal` | The end-to-end figure of merit is good / 端到端的品质指标达标 | Signal-to-noise ratio, visibility, linewidth, count rate, stability |

Three ordering rules hold across fields and decide `after`: upstream before downstream, slow before fast, coarse before fine. / 三条排序规则在各领域通用，决定 `after`：先上游后下游，先慢后快，先粗后细。

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
| `usable` | When this is not met the measurement means nothing and the search goes upstream / 不满足时测量本身无意义，转向上游 |
| `reference` | Values of the accepted verification kept with the experiments that rely on it, such as a dark level used in the analysis / 通过核对时保留的数值，随依赖它的实验保存，例如分析时要扣除的暗计数 |
| `correct` | What restores the condition, tried in order, each followed by a verification: an operation, or [`tune`](#tuning--调节). A step of a `manual` device asks a person / 恢复该条件的做法，按顺序尝试，每个之后核对一次：一个操作，或 [`tune`](#tuning--调节)；`manual` 设备的步骤交给人 |
| `settle` | Seconds between a correction and its verification / 校正之后到核对之前等待的秒数 |
| `valid_for` | Seconds an accepted verification is trusted; without it only corrections and disturbances make it due / 一次通过的核对可信的秒数；不写则只有校正和扰动会使它待核对 |
| `after` | Conditions of this platform that must hold first / 必须先成立的本平台条件 |
| `kind`, `note` | The class above, and what a person would say in passing / 上表的类别，以及人会顺口交代的话 |

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
- It never leaves `[minimum, maximum]`, tries at most `evaluations` settings, and leaves the system at the best setting it saw. `until: "accept"` stops as soon as the tolerance is met. `gain` is the smallest improvement that counts when the reading is noisy. / 不会离开 `[minimum, maximum]`，最多尝试 `evaluations` 个设定，结束时停在见过的最好位置。`until: "accept"` 在满足容差后立即停止；读数有噪声时用 `gain` 给出算数的最小改善。
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

The pass is an experiment of its own, so every verification and correction is an ordinary operation with its record, reservation and monitors. Steps take their devices one at a time. / 一轮保持本身是一次实验，每次核对和校正都是普通的操作，有记录、占用和监视。各步骤逐个占用设备。

## State / 状态

The state of a condition is known without touching an instrument: / 条件的状态不需要触碰仪器就能得出：

| State | When / 何时 |
| --- | --- |
| `holds` | The last verification was accepted, is within `valid_for`, nothing it depends on was corrected since, and its dependencies hold / 上次核对通过、在有效期内、其依赖此后没有被校正过，且依赖都成立 |
| `due` | It held, and one of those is no longer true; `reason` says which / 曾经成立，但上述某一点已不满足；`reason` 说明是哪一点 |
| `lost` | The last verification did not hold and no correction restored it / 上次核对不成立，且校正未能恢复 |
| `unknown` | Never verified, or its verification or tolerance was changed / 从未核对，或核对方式、容差已被修改 |

`lab_context` lists only the conditions that do not hold, with the next step `maintain_conditions`. / `lab_context` 只列出不成立的条件，并给出下一步 `maintain_conditions`。

## Experiments rely on conditions / 实验依赖条件

A submission names what it relies on with `requires`; a saved Standard or Workflow Operation can name it too, and then every use requires it. / 提交时用 `requires` 写明依赖的条件；已保存的标准操作和工作流操作也可以写，之后每次使用都要求它。

- The experiment is accepted while every required condition holds. Otherwise it is refused with `conditions_not_held`, naming each condition and why; after `maintain` the same submission is accepted. / 所依赖的条件都成立时才受理；否则以 `conditions_not_held` 拒绝并逐条说明原因，`maintain` 之后同一提交即被受理。
- The run records `conditions`: when each was accepted, its value and its `reference`. / 实验记录 `conditions`：每个条件何时通过、数值和 `reference`。
- In a continuous experiment every batch and operation is accepted on the same terms, so `valid_for` sets how often the work stops to verify. `run.maintain()` does it inside the experiment, and `run.batch(steps, maintain=True)` or `run.call(name, arguments, maintain=True)` do it only when needed. / 连续实验中的每个批次和操作按同样的规则受理，因此 `valid_for` 决定了多久停下来核对一次。`run.maintain()` 在实验内部完成；`maintain=True` 只在需要时才做。
- When a later verification finds a condition lost, each run that relied on it since the last accepted verification gets `in_doubt[condition] = {since, found, outcome}`. Two accepted verifications are taken to mean the condition held in between. / 之后的核对发现条件不成立时，自上次通过以来依赖它的每个实验得到 `in_doubt`。相邻两次核对都通过，视为其间一直成立。

A graph of steps is checked when it is accepted, not between its steps; for long work use a continuous experiment or a program and maintain as it goes. / 步骤图只在受理时核对，不在步骤之间核对；长时间的工作请用连续实验或程序，边做边保持。

## Disturbances / 扰动

`invalidate(conditions, reason)` records that something happened (the enclosure was opened, a cable was changed) and makes those conditions due. A monitor reading that leaves its limits does the same for the conditions verified or corrected with that device. / `invalidate(conditions, reason)` 记录发生了什么（开过罩子、换过线缆），使这些条件待核对。监视读数越限时，用到该设备的条件同样变为待核对。

## What the records say / 记录能说明什么

Every verification and correction is recorded. `GET /v1/conditions/{id}` returns the latest records and: / 每次核对和校正都有记录。`GET /v1/conditions/{id}` 返回最近的记录以及：

| Statistic | Meaning / 含义 |
| --- | --- |
| `verifications`, `holds`, `drifted`, `unusable` | How often each outcome occurred / 各结果出现的次数 |
| `held` | How long the condition stayed accepted before it was found lost: count, median and shortest. Choose `valid_for` below the shortest / 条件从通过到被发现不成立维持了多久：次数、中位数、最短；`valid_for` 应小于最短值 |
| `corrections` | For each correction, how often it was tried and how often it restored the condition / 每种校正尝试了几次、恢复了几次 |
| `trend` | A straight line through the accepted values since the last correction: `per_hour`, the number of `points`, the `residual`, and `expected_until`, when the line reaches the tolerance / 自上次校正以来通过值的直线拟合：每小时变化、点数、残差，以及到达容差的时间 `expected_until` |

This is where experience accumulates: which conditions drift, how fast, and what restores them, measured on this system. / 经验在这里积累：哪些条件会漂、漂多快、什么能恢复，都是在这套系统上测出来的。

## Writing the conditions of a platform / 为一个平台写条件

The questions are the same for every system; an agent asks them once and writes the answers down. / 对任何系统，要问的问题都一样；Agent 问一次并写下来。

1. Before a measurement counts, what must be true? Go through the eight kinds. / 测量算数之前什么必须成立？按八类过一遍。
2. For each, which short measurement tells, and which value is acceptable? / 每一项用哪个简短的测量判断，什么数值可以接受？
3. When that measurement shows nothing at all, what do you look at first? That is `after`. / 这个测量什么都看不到时，先看哪里？这就是 `after`。
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

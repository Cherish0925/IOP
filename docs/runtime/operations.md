# Operations / 操作

[English](README.md) · [中文](README.zh-CN.md) · [Drivers](drivers.md) · [HTTP](http-api.md)

IOP keeps the three roles of operations defined by AI4Instruments as three things the Runtime stores and executes. / IOP 保留 AI4Instruments 定义的三类操作，它们是运行服务中保存和执行的三种对象。

| Role / 角色 | In the Runtime / 在运行服务中 | Addressed by / 调用方式 |
| --- | --- | --- |
| **Basic Operations** | The operations of a registered device: one control or observation each, with parameters, units, ranges and readbacks / 已登记设备的操作：每个是一项控制或观察，带参数、单位、范围和回读 | `device` + `operation` |
| **Standard Operations** | Saved methods built from Basic and Standard Operations, with parameters, conditions, a named result and a record of use / 由基础操作和标准操作组成并保存的方法，带参数、适用条件、具名结果和使用记录 | `operation` |
| **Workflow Operations** | Saved organizations of methods across devices: a step graph in sequence and in parallel, or a program that decides from results / 跨设备组织方法的保存流程：串并行步骤图，或根据结果决定的程序 | `operation` |

A step with `device` is a Basic Operation. A step without `device` uses a saved operation by name. The Runtime replaces it by its Basic Operations before scheduling, so reservation, calibration, monitors and holds apply to every one of them, and records each use. / 带 `device` 的步骤是基础操作；不带 `device` 的步骤按名称使用已保存的操作。运行服务在调度前把它展开为基础操作，预留、标定、监测和挂起对其中每一步照常生效，并记录每次使用。

An Episode records an experiment trajectory; it does not replace a Standard Operation. / Episode 记录实验轨迹，不替代标准操作。

A Workflow Operation that decides from results is a saved program and runs as its own experiment. Agents that decide interactively use a continuous experiment and call operations of all three roles there. / 根据结果决定的工作流操作以程序保存，作为独立实验运行；交互决策的 Agent 在连续实验中调用三类操作。

## Save an operation / 保存操作

```json
{
  "id": "axis.position",
  "layer": "standard",
  "description": "Move one axis and read where it arrived",
  "conditions": "The axis is free to rotate",
  "parameters": {
    "axis": {"type": "string", "enum": ["stage", "aux"]},
    "angle": {"type": "number", "minimum": -90, "maximum": 90, "unit": "deg"}
  },
  "steps": [
    {"id": "move", "device": "${axis}", "operation": "move_to", "arguments": {"angle_deg": "${angle}"}},
    {"id": "read", "device": "${axis}", "operation": "position"}
  ],
  "result": {"angle": {"step": "read", "path": ["angle_actual"]}}
}
```

| Field / 字段 | Meaning / 含义 |
| --- | --- |
| `layer` | `standard` or `workflow`. A Standard Operation uses Basic and Standard Operations; a Workflow Operation may use all three / 标准操作使用基础操作和标准操作；工作流操作三类都可使用 |
| `description`, `conditions` | What it achieves, and when the result has that meaning / 作用，以及结果成立的条件 |
| `requires` | Names of [conditions](conditions.md) that must hold when the operation is used, a platform's or an instrument's (`<device>.<id>`); an experiment using it is accepted while they hold / 使用该操作时必须成立的[条件](conditions.md)名称，平台的或仪器的（`<device>.<id>`）；条件成立时使用它的实验才被受理 |
| `parameters` | Described like operation parameters; arguments are checked before anything runs / 与操作参数格式相同，执行前校验 |
| `steps` | Run in the order written; `after: []` starts a parallel branch and `after: [ids]` joins branches. `"${name}"` in `arguments`, `device`, `sweep`, `repeat` or `until` inserts a parameter with its type; `sweep` runs a step at each value of some of its parameters (see [Sweeps](#sweeps--扫描)) / 按书写顺序执行；`after: []` 开始并行分支，`after: [ids]` 汇合。`"${name}"` 在 `arguments`、`device`、`sweep`、`repeat` 或 `until` 中按类型代入参数；`sweep` 让一步按参数的各个取值依次运行（见[扫描](#sweeps--扫描)） |
| `result` | Names the values returned: a step and an optional path into its result, or into a `view` of an array it returned / 为返回值命名：取自某一步结果中的路径，或该步所返回数组的 `view` 中的路径 |

Save with `POST /v1/library` `{"definition": {...}}`, Python `save_operation`, MCP `saved_operations(definition)`, or `iop operations --save file.json`. Saving the same name creates a new revision; earlier revisions stay readable at `/v1/library/{id}/history`, and each experiment records the revision it used. [operations.json](../../examples/iop/operations.json) holds the three operations of this page for the demonstration devices. / 同名保存产生新版本，旧版本仍可读取，每次实验记录所用版本。

## Workflow Operations that decide / 根据结果决定的工作流操作

A Workflow Operation that branches or repeats from results is saved as a program: a directory or ZIP with `experiment.py`, or `package.json` naming its command. It is described like any saved operation, without `steps` and `result`. / 根据结果分支或循环的工作流操作以程序保存：目录或 ZIP，含 `experiment.py` 或在 `package.json` 中给出启动命令；其余描述与其他已保存操作相同，不写 `steps` 和 `result`。

```python
import json, os
from ai4instruments.iop import Client

arguments = json.loads(os.environ['IOP_PARAMETERS_JSON'])
run = Client().current_run()
angle, best = arguments['start'], None
while angle <= arguments['stop']:
    run.call('axis.position', {'axis': 'stage', 'angle': angle})
    power = run.call('meter.capture')['power']
    if best is None or power > best['power']:
        best = {'angle': angle, 'power': power}
    if power >= arguments['threshold']:
        break
    angle += arguments['step']
run.save_result({'best': best})
```

Save it with `save_operation(definition, program="path/to/folder")`, MCP `saved_operations(definition, program)` or `POST /v1/library` with the ZIP as base64 `archive`. The Runtime records its command and files; `GET /v1/library/{id}/files/{name}` returns the text of a file, so the method can be read before it is used or revised. A program runs as the only step of an experiment (`{"steps": [{"id": "scan", "operation": "stage.find_power", "arguments": {...}}]}`): its arguments are checked against `parameters` and passed in `IOP_PARAMETERS_JSON`, and its saved result appears under the step. Inside, `run.call(name, arguments)` uses a saved Standard or Workflow Operation and returns its named result, so every use inside the program is recorded as well. A program cannot be a step of a step graph. / 程序作为实验的唯一步骤运行：参数按 `parameters` 校验后经 `IOP_PARAMETERS_JSON` 传入，保存的结果出现在该步骤名下。程序内用 `run.call(名称, 参数)` 调用已保存的操作并取得其具名结果，这些调用同样计入使用记录。程序不能作为步骤图中的一步。

## Use and read / 使用与查看

```json
{"steps": [
  {"id": "m", "operation": "bench.measure_at", "arguments": {"a": 10, "b": 20}},
  {"id": "back", "operation": "axis.position", "arguments": {"axis": "aux"},
   "bindings": {"angle": {"step": "m", "path": ["first"]}}, "after": ["m"]}
]}
```

The same form works in workflows, batches of a continuous experiment and platform procedures. The experiment result holds the named result of each use under its step (`m`), beside every Basic Operation it ran (`m.first.move`, `m.capture.measure`, …). A binding takes one of the named results of an earlier use, and a parameter can itself be bound to an earlier result. / 工作流、连续实验的批量操作和平台过程都用同一写法。实验结果在该步骤名下给出具名结果，同时保留它执行的每个基础操作。后续步骤可以绑定其具名结果，参数本身也可以绑定到之前的结果。

A named result or a binding can read through a `view` of an array the step returned, with the options of [reading arrays](drivers.md#reading-arrays--读取数组); `path` then selects inside the view. A trace becomes a number that later steps, other saved operations and [operating conditions](conditions.md) use: / 具名结果和绑定可以经由该步返回数组的 `view` 取值（选项同[读取数组](drivers.md#reading-arrays--读取数组)），`path` 在视图中选取。一条迹线由此成为后续步骤、其他已保存操作和[工作条件](conditions.md)可用的数值：

```json
{"id": "resonance.locate", "layer": "standard", "description": "Sweep and locate the resonance dip",
 "steps": [{"id": "sweep", "device": "vna", "operation": "read_trace"}],
 "result": {"f0": {"step": "sweep", "view": {"at": {"component": "abs"}, "peak": "min"}, "path": ["peak", "center"]},
            "linewidth": {"step": "sweep", "view": {"at": {"component": "abs"}, "peak": "min"}, "path": ["peak", "width"]}}}
```

These views are computed once, when the step that returned the array completes, and kept in its operation record as `derived`; the value does not change when it is read again. A view reads an array a device operation returned, so a binding to a saved operation takes one of its named results. / 这些视图在返回数组的那一步完成时计算一次，记入其操作记录的 `derived`，之后再读也不会变。视图只作用于设备操作返回的数组；绑定已保存操作时取其具名结果。

`GET /v1/library` lists the saved operations with their layer, description, parameters and use counts; `GET /v1/library/{id}` returns one with its steps, so a method can be read, changed and saved again. The laboratory overview lists the names by role, which is what an agent sees first. / 列表给出每个操作的角色、说明、参数和使用次数；单个查询返回其步骤，方法可以读出、修改后再保存。实验室概况按角色列出名称，Agent 连接后首先看到的就是这些。

## Sweeps / 扫描

A step with `sweep` runs once at each value of the parameters it names, one point after another; the first name changes slowest. A list gives the values (numbers, or strings such as channel names); `start`, `stop` and `points`, or `center`, `span` and `points`, give evenly spaced numbers. A sweep has at most 10,000 points, and each point is an ordinary operation, or use of a saved operation, with its own record. / 带 `sweep` 的步骤按所列参数的每个取值依次运行一次，第一个参数变化最慢。列表给出取值（数值，或通道名等字符串）；`start`、`stop`、`points`，或 `center`、`span`、`points`，给出等间隔的数值。每次扫描最多 10,000 个点，每个点都是普通的操作或已保存操作的一次使用，有自己的记录。

```json
{"steps": [
  {"id": "scan", "operation": "axis.measure", "arguments": {"axis": "stage"},
   "sweep": {"angle": {"start": -40, "stop": 40, "points": 81}}},
  {"id": "best", "device": "stage", "operation": "move_to", "after": ["scan"],
   "bindings": {"angle_deg": {"step": "scan", "view": {"field": "power"}, "path": ["summary", "max_at"]}}}
]}
```

What the points return is gathered under the step. Numbers in the same place become an array over the sweep, with one axis for each swept parameter carrying its values and unit; a `unit` beside a single number, as in `{"value": 0.25, "unit": "V"}`, becomes the array's unit. Arrays of one shape in the same place combine into one array with the sweep's axes in front: from the values the records hold, or, when every point saved its array in a file, from those files read in order without copying them. Spectra swept over a bias thus read as a map, and so does a sweep of a saved operation that sweeps. Other values are kept when they are the same at every point; the rest stay in the record of each point. The experiment result holds what the sweep gathered in place of the points; while a step graph runs, `GET /v1/runs/{id}/result` gives what its completed operations give so far, so a map fills in as it is measured. / 各点的返回值汇集在该步骤名下。同一位置的数值成为随扫描变化的数组，每个扫描参数一个坐标轴，带取值和单位；与单个数值并列的 `unit`（如 `{"value": 0.25, "unit": "V"}`）成为该数组的单位。同一位置、形状相同的数组合并为一个数组，扫描的轴在前：数值在记录中时直接合并；各点都把数组存成文件时，按顺序读取这些文件，不复制。随偏压扫描的谱线因此读作二维图，扫描一个本身含扫描的已保存操作也是如此。其他值在各点相同时保留；其余的值留在各点的记录里。实验结果中给出汇集后的内容，不再逐点列出；步骤图运行期间，`GET /v1/runs/{id}/result` 给出已完成操作得到的部分，二维图因此边测边显示。

Bindings and named results read what a sweep gathered by path or through a `view`, so a saved operation can scan and then go to the best point; `"points": "${points}"` takes the number of points from a parameter. `read_array` with `run_id` and `field`, `Run.read_array` and `readArray` view these arrays, with `request_id` for a batch of a continuous experiment; there `run.call(operation, arguments, sweep=...)` returns the named results gathered. / 绑定和具名结果可按路径或经 `view` 读取扫描汇集的内容，已保存的操作因此可以先扫描、再转到最佳点；`"points": "${points}"` 从参数取点数。`read_array`（`run_id` 与 `field`）、`Run.read_array` 和 `readArray` 读取这些数组，连续实验中的批次另加 `request_id`；在连续实验中，`run.call(操作, 参数, sweep=...)` 返回汇集后的具名结果。

An end of a range can come from an earlier step. `start`, `stop`, `center` and `span` each take a number or a source written as a binding is (`step`, `path`, `view`); the number of points stays fixed, and each point reads its value when it runs, after the steps it reads have finished. Here the fine sweep spans 2 degrees around the line the coarse sweep found, its center placed between the coarse points: / 范围的端点可以取自之前的步骤。`start`、`stop`、`center`、`span` 各自可以是数值，或像绑定那样写出的来源（`step`、`path`、`view`）；点数固定，每个点在运行时、在所读的步骤完成之后取得自己的值。下例中，细扫以粗扫得到的谱线中心为中心（中心位于粗扫的点之间），跨 2 度：

```json
{"steps": [
  {"id": "coarse", "operation": "axis.measure", "arguments": {"axis": "stage"},
   "sweep": {"angle": {"start": -40, "stop": 40, "points": 17}}},
  {"id": "fine", "operation": "axis.measure", "arguments": {"axis": "stage"},
   "sweep": {"angle": {"center": {"step": "coarse", "view": {"field": "power", "peak": "max"}, "path": ["peak", "center"]},
                       "span": 2, "points": 21}}}
]}
```

Each point records the range it read and the value it took, and what the fine sweep gathered has its axis from the ends that were read. In a saved operation an end can name one of its steps, or be `"${name}"` for a parameter the caller binds, so a coarse-to-fine search is saved as one method. / 每个点记录所读的范围和取得的值，细扫汇集的数组以读到的两端为坐标轴。已保存的操作中，端点可以指向本操作的某一步，也可以写作 `"${name}"`，由调用方绑定该参数；先粗扫、再细扫因此可以保存为一个方法。

A range with `adapt` places its points from what the earlier ones read. `adapt` is the path to the number each point reads, in what its operation returns or in the named result of its use, such as `["power"]`. The first two points are the ends; each next one is the middle of the interval whose ends differ most, in spacing and in reading, so points come closer together where the reading changes most and stay apart where it is flat. A difference in reading counts by how far it exceeds twice the typical one, the median between neighbouring points, so that noise does not draw the points while a line is still to be found. Intervals narrower than 1/1024 of the even spacing are not divided further, so an edge does not take every point, and integer ends give integers. An adaptive range varies one parameter, has 2 to 1000 points, and may itself be centered on an earlier result. What it gathers is in order of value, the values being the coordinates of its axis; while it runs, it holds the points finished so far. / 带 `adapt` 的范围根据前面各点的读数安排后面的点。`adapt` 是各点读数的路径，取自操作的返回值或已保存操作的具名结果，例如 `["power"]`。前两个点取两端；之后每个点取两端相差最大的区间（间距和读数一并计）的中点，于是读数变化大的地方点密，平坦处点疏。读数之差按超出典型差值（相邻点之差的中位数）两倍的部分计入，谱线尚未找到时噪声不会把点吸走。窄于均匀间距 1/1024 的区间不再细分，台阶不会吸走所有点；整数两端给出整数。自适应范围只改变一个参数，2 到 1000 个点，本身也可以以之前的结果为中心。汇集的结果按取值排序，取值即坐标轴的坐标；运行期间给出已完成的点。

```json
{"id": "line", "device": "analyzer", "operation": "probe",
 "sweep": {"hz": {"start": 1.0e9, "stop": 1.1e9, "points": 40, "adapt": ["power"]}}}
```

On a line 3 MHz wide in this 100 MHz span, 16 of the 40 points fall within its width; through `peak` they give its center within 1.5 kHz and its width within 1%, where 40 evenly spaced points place 3 on the line and give a width 16% too large. Under noise of ±0.02 on the line's height of 1, the 40 points find the line from each of 40 noise sequences, at least 8 of them within its width at half height; counting every difference in reading in full, they missed it in 13. / 在 100 MHz 范围内一条 3 MHz 宽的谱线上，40 个点中有 16 个落在线宽以内；经 `peak` 读出的中心误差在 1.5 kHz 以内、线宽误差在 1% 以内；均匀的 40 个点只有 3 个落在线上，线宽大 16%。谱线高 1、噪声 ±0.02 时，40 组噪声序列下这 40 个点每次都能找到谱线，且至少 8 个落在半高全宽以内；若读数之差全部计入，有 13 次错过谱线。

A step with `repeat` runs that many times in turn, at each point of its sweep when it has one; what it returns is gathered over a last axis named `repeat`. Twenty readings of a meter thus read as one array whose view gives their mean and spread, and a sweep of a bias with `"repeat": 10` takes ten readings at each bias. In a saved operation the number can be `"${name}"`. / 带 `repeat` 的步骤依次运行这么多次，有 `sweep` 时在每个扫描点各运行这么多次；返回值汇集在最后一个名为 `repeat` 的轴上。电表读 20 次即成为一个数组，其视图给出均值和离散程度；扫描偏压并带 `"repeat": 10` 时每个偏压读 10 次。已保存的操作中次数可以写作 `"${name}"`。

A sweep or a repetition stops early with `until` beside it. `path` names the number each point reads, as `adapt` does, and `above`, `below` or both give the limits it stays within. After a point that reads beyond a limit, or reads no number, so that the limit is not known to hold, the later points are skipped: they never reach an instrument and end as `skipped`, and the steps after the sweep run as when it completes. A bias ramp thus stops at compliance and still returns to zero, a search stops once the signal is found, and a repeated reading waits, at most that many times, until it passes a limit. The first point always runs. What the sweep gathers keeps its planned shape, the skipped points empty. Where it stopped, the point's index and its reading, is recorded with the sweep as `stopped` and in the experiment's events as `sweep.stopped`; MCP replies for a batch show it beside the sweep's points. In a saved operation a limit can be `"${name}"`, given by the caller. / 扫描或重复旁加 `until` 可以提前停止。`path` 指各点读取的数值（与 `adapt` 相同），`above`、`below` 或两者给出读数应保持的界限。某一点的读数越过界限，或读不到数值（无法确认界限仍成立）时，其后的点全部跳过：不会到达仪器，状态为 `skipped`；扫描之后的步骤照常运行，与扫描完成时一样。偏压斜坡因此在达到限流时停止，随后仍回到零；搜索在找到信号后停止；重复的读取最多那么多次，等到读数越过界限为止。第一个点总会运行。汇集结果保持原定形状，跳过的点为空。停在哪一点（序号）、读数多少，随扫描记为 `stopped`，并作为 `sweep.stopped` 记入实验事件；MCP 对批次的回复在扫描的点数旁给出。已保存的操作中，界限可以写作 `"${name}"`，由调用方给出。

```json
[{"id": "ramp", "device": "smu", "operation": "source",
  "sweep": {"volts": {"start": 0, "stop": 1, "points": 101}},
  "until": {"path": ["current"], "above": 0.001}},
 {"id": "zero", "device": "smu", "operation": "source", "arguments": {"volts": 0}, "after": ["ramp"]}]
```

On a diode, the ramp stops after 0.54 V, the first point above 1 mA; the source is given none of the 46 higher voltages and then returns to 0 V. / 在二极管上，斜坡在第一个超过 1 mA 的点 0.54 V 之后停止；更高的 46 个电压都没有发给电源，随后回到 0 V。

With `view`, `path` is read in that [view](drivers.md) of what the sweep has gathered so far, before each point, and the sweep stops once it gives a number beyond a limit; while it gives none, from too few points or a fit that finds no line its samples resolve, the sweep goes on. A sweep then stops when its answer is known well enough: a repeated reading once the `error` of its mean is small enough, an adaptive sweep once a line's fitted center is. `points` sets how many points run before `until` is read, since an error from a few values can come out small by chance. Such a sweep has at most 1000 points. / 带 `view` 时，在每个点之前于扫描至此汇集结果的该[视图](drivers.md)中读取 `path`，给出越过界限的数值即停止；还给不出数值时（点太少，或拟合找不到采样能分辨的谱线），扫描继续。扫描因此在答案足够确定时停止：重复读取在均值的 `error` 足够小时停止，自适应扫描在拟合出的谱线中心足够确定时停止。`points` 设定读取 `until` 之前先运行的点数，因为只有几个数值时误差可能偶然偏小。这样的扫描最多 1000 个点。

```json
[{"id": "mean", "device": "meter", "operation": "read", "repeat": 1000,
  "until": {"view": {"field": "power"}, "path": ["summary", "error"], "below": 5e-6, "points": 5}},
 {"id": "find", "device": "vna", "operation": "probe",
  "sweep": {"hz": {"start": 1.0e9, "stop": 1.1e9, "points": 200, "adapt": ["power"]}},
  "until": {"view": {"field": "power", "fit": "lorentzian"}, "path": ["fit", "errors", "center"], "below": 2e4, "points": 10}}]
```

A power read with a spread of 29 µW is known to 5 µW after 37 of the 1000 readings allowed; a line 3 MHz wide under noise of 0.02 is found and its center known to 16 kHz after 18 of the 200 points, 1.3 errors from where it lies. / 离散 29 µW 的功率读到第 37 次（允许 1000 次）时均值已确定到 5 µW；噪声 0.02 下 3 MHz 宽的谱线在 200 个允许点中的第 18 个点时被找到，中心确定到 16 kHz，与真实位置相差 1.3 个误差。

## Record of use / 使用记录

Every use is recorded when its last step ends: `uses` counts `calls`, `completed` and `failed`, and `last` names the experiment, state, time and revision. Each Basic Operation run for a use carries `calls`, the chain of uses it belongs to, so the evidence of a method is the set of experiments in which it ran. A failed or cancelled step ends the use as failed or cancelled; operations already executed are not undone. A use whose steps were all skipped, because the sweep it is a point of stopped, is not recorded. / 每次使用在其最后一步结束时记录：次数、完成数、失败数，以及最近一次的实验、状态、时间和版本。为某次使用执行的每个基础操作都带有所属的调用链，方法的证据就是它运行过的那些实验。所在扫描已停止、步骤全部跳过的使用不记录。

## When the laboratory changes / 实验室变化之后

A saved operation keeps the device names, operations and parameters it was written with. When one of them is no longer there (the device is not registered, its description no longer has the operation, or the operation lost a parameter), the listing and `GET /v1/library/{id}` carry `unavailable`, for example `["lamp: not registered", "meter.configure: no such operation"]`, and `lab_context` adds `setup.unavailable_operations` and the next step `repair_saved_operations`. Register the device again or save a revision. A device given by a parameter, and the devices a program uses, are known only when the operation runs. / 已保存的操作沿用保存时的设备名、操作和参数。其中任何一项不在了（设备未登记、设备描述中已无该操作、操作少了某个参数），列表和 `GET /v1/library/{id}` 会带 `unavailable`，`lab_context` 增加 `setup.unavailable_operations` 和下一步 `repair_saved_operations`。重新登记设备或保存新版本即可。由参数给出的设备、程序内部使用的设备，只有在运行时才能确定。

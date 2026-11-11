# Operations / 操作

[English](README.md) · [中文](README.zh-CN.md) · [Drivers](drivers.md) · [HTTP](http-api.md)

IOP keeps the three roles of operations defined by AI4Instruments as three things the Runtime stores and executes. / IOP 保留 AI4Instruments 定义的三类操作，它们是运行服务中保存和执行的三种对象。

| Role / 角色 | In the Runtime / 在运行服务中 | Addressed by / 调用方式 |
| --- | --- | --- |
| **Basic Operations** | The operations of a registered device: one control or observation each, with parameters, units, ranges and readbacks / 已登记设备的操作：每个是一项控制或观察，带参数、单位、范围和回读 | `device` + `operation` |
| **Standard Operations** | Saved methods built from Basic and Standard Operations, with parameters, conditions, a named result and a record of use / 由基础操作和标准操作组成并保存的方法，带参数、适用条件、具名结果和使用记录 | `operation` |
| **Workflow Operations** | Saved organizations of methods across devices, in sequence and in parallel; they may use all three roles / 跨设备组织方法的保存流程，可串行、并行，可使用三类操作 | `operation` |

A step with `device` is a Basic Operation. A step without `device` uses a saved operation by name. The Runtime replaces it by its Basic Operations before scheduling, so reservation, calibration, monitors and holds apply to every one of them, and records each use. / 带 `device` 的步骤是基础操作；不带 `device` 的步骤按名称使用已保存的操作。运行服务在调度前把它展开为基础操作，预留、标定、监测和挂起对其中每一步照常生效，并记录每次使用。

Research programs and agent-driven experiments, which decide the next step from results, remain programs; they call operations of all three roles. / 根据结果决定下一步的研究程序和 Agent 实验仍是程序，它们调用三类操作。

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
| `parameters` | Described like operation parameters; arguments are checked before anything runs / 与操作参数格式相同，执行前校验 |
| `steps` | Run in the order written; `after: []` starts a parallel branch and `after: [ids]` joins branches. `"${name}"` in `arguments` or `device` inserts a parameter with its type / 按书写顺序执行；`after: []` 开始并行分支，`after: [ids]` 汇合。`"${name}"` 按类型代入参数 |
| `result` | Names the values returned: a step and an optional path into its result / 为返回值命名：取自某一步结果中的路径 |

Save with `POST /v1/library` `{"definition": {...}}`, Python `save_operation`, MCP `saved_operations(definition)`, or `iop operations --save file.json`. Saving the same name creates a new revision; earlier revisions stay readable at `/v1/library/{id}/history`, and each experiment records the revision it used. [operations.json](../../examples/iop/operations.json) holds the three operations of this page for the demonstration devices. / 同名保存产生新版本，旧版本仍可读取，每次实验记录所用版本。

## Use and read / 使用与查看

```json
{"steps": [
  {"id": "m", "operation": "bench.measure_at", "arguments": {"a": 10, "b": 20}},
  {"id": "back", "operation": "axis.position", "arguments": {"axis": "aux"},
   "bindings": {"angle": {"step": "m", "path": ["first"]}}, "after": ["m"]}
]}
```

The same form works in workflows, batches of a continuous experiment and platform procedures. The experiment result holds the named result of each use under its step (`m`), beside every Basic Operation it ran (`m.first.move`, `m.capture.measure`, …). A binding takes one of the named results of an earlier use, and a parameter can itself be bound to an earlier result. / 工作流、连续实验的批量操作和平台过程都用同一写法。实验结果在该步骤名下给出具名结果，同时保留它执行的每个基础操作。后续步骤可以绑定其具名结果，参数本身也可以绑定到之前的结果。

`GET /v1/library` lists the saved operations with their layer, description, parameters and use counts; `GET /v1/library/{id}` returns one with its steps, so a method can be read, changed and saved again. The laboratory overview lists the names by role, which is what an agent sees first. / 列表给出每个操作的角色、说明、参数和使用次数；单个查询返回其步骤，方法可以读出、修改后再保存。实验室概况按角色列出名称，Agent 连接后首先看到的就是这些。

## Record of use / 使用记录

Every use is recorded when its last step ends: `uses` counts `calls`, `completed` and `failed`, and `last` names the experiment, state, time and revision. Each Basic Operation run for a use carries `calls`, the chain of uses it belongs to, so the evidence of a method is the set of experiments in which it ran. A failed or cancelled step ends the use as failed or cancelled; operations already executed are not undone. / 每次使用在其最后一步结束时记录：次数、完成数、失败数，以及最近一次的实验、状态、时间和版本。为某次使用执行的每个基础操作都带有所属的调用链，方法的证据就是它运行过的那些实验。

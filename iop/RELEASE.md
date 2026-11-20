# IOP 0.13.0

Experiments can be found again by name, project, label, request ID, run ID or instrument, with state filters and a cursor for older records. HTTP, Python, TypeScript, the CLI and MCP use the same search. The SDKs, CLI and MCP return summaries; detailed records and arrays remain available on demand. Interrupted experiments remain visible in the laboratory overview for a week, including when the service stays running.

New submissions, calls, batches and saved steps reject unknown fields with the relevant step and accepted names. Locally written instrument, model and saved-method parameter declarations are checked before acceptance: misspelled bounds, invalid defaults and incompatible types are refused. Descriptive annotations and `x-*` extensions remain supported. Previously stored definitions and identical request retries retain their accepted format; loading an old revision does not rewrite it. SDK descriptions retain their existing contract.

Upkeep continues while an experiment is idle or waiting for instruments, retry backoff or a pending retake. Only maintenance that blocks an otherwise eligible operation counts toward the three-pass limit. Retry timing and retake gates use the scheduler's existing rules; unknown instrument outcomes keep their resource holds. Text-command spacing uses a high-resolution monotonic clock, including on older Windows Python versions.

MCP adds four optional starting prompts for inspecting the laboratory, planning a measurement, reporting observations and saving a reusable method. Program submissions can name their project and required conditions and request upkeep, like other experiments. Tool schemas expose the same strict argument boundary used at execution; task guides provide the details.

IOP Connect is **1.7.0**. The service, native executor, TypeScript SDK and DeepSeek connector are **0.13.0**. Existing configurations, imports, experiment IDs and data directories remain valid. Upgrade the service while idle and update clients to use the new capabilities.

[Operations](https://github.com/Cherish0925/IOP/blob/main/docs/runtime/operations.md) · [Operating conditions](https://github.com/Cherish0925/IOP/blob/main/docs/runtime/conditions.md) · [Install and connect](https://github.com/Cherish0925/IOP/blob/main/iop/connect/README.md)

---

## 中文

IOP 0.13.0 可以按名称、项目、标签、请求 ID、实验 ID 或仪器找回实验，并按状态筛选、用游标读取更早的记录。HTTP、Python、TypeScript、命令行和 MCP 共用同一查询。SDK、命令行和 MCP 列表提供摘要，详细记录与数组按需读取。中断实验会在实验室概况中保留一周，服务持续运行时也适用。

新提交、单次调用、批次与保存的步骤会拒绝未知字段，并指出相关步骤与可用名称。新写入的本地仪器配置、型号描述及保存方法均校验参数定义，受理前拒绝拼错的界限、无效默认值与不匹配的类型，同时保留描述字段和 `x-*` 扩展注释。已存定义与相同请求的重试沿用原有格式，读取旧版本不会改写它。SDK 返回的描述保持原有契约。

实验空闲、等待仪器、等待重试间隔或先前测量重测时，自动维护持续保持所需条件。只有阻挡了本来可以启动的操作的维护轮次才计入三轮上限。重试时序与重测门控复用原调度规则，仪器执行结果未知时仍保留资源占用。文本命令间隔采用高分辨率单调时钟，兼顾较旧的 Windows Python 版本。

MCP 新增四个可选起始提示，用于查看实验室、规划测量、报告观测和保存可复用方法。程序提交与其他实验一样，可以指定项目、所需条件和自动维护。工具 schema 与执行时的严格参数边界一致，具体方法由任务指南按需提供。

IOP Connect 为 **1.7.0**；运行服务、原生执行器、TypeScript SDK 和 DeepSeek 连接包统一为 **0.13.0**。已有配置、导入路径、实验 ID 和数据目录保持兼容。服务空闲时升级，并更新客户端以使用新增能力。

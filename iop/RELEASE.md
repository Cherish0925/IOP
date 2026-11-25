# IOP 0.17.0

## Agent guidance and research continuity

- English and Chinese task prompts cover deployment, instrument and platform setup, measurements, research and continuation. They distinguish the instrument computer from the agent execution host and complete connection setup with a host tool call.
- The operating guide starts from the task and existing laboratory state. Setup checks apply to the needed operations; physical knowledge retains its source and scope. Position-reference procedures follow the model and setup rather than prescribing homing for every device.
- MCP adds `connect` and `research(question)` prompts. The new research guide supports several experiment forms, controls and experimental designs, retains doubtful observations for qualified analysis, and records decisions for later sessions. Reporting and saving a method are separate from finishing an experiment.
- Tool descriptions are shorter while all 45 stdio tools retain their parameter schemas. Native HTTP MCP rejects gateway-local program paths in `saved_operations`, consistently with its existing omission of host-file upload and export tools; use a stdio bridge or the SDK for package transfers.
- The simulated optical bench includes a complete acquisition submission, named project, idempotent retry and cross-session retrieval instructions. It uses a separate laboratory directory and keeps simulation distinct from physical calibration.

The runtime, Rust executor, TypeScript client and DeepSeek package are 0.17.0; IOP Connect and source plugin manifests are 1.11.0. Existing experiment and configuration formats remain supported. Update the runtime for the new MCP prompts and guide topic, and regenerate or update the host connection for the matching instructions.

## 中文

- 中英文任务提示覆盖部署、仪器与平台配置、测量、研究和续接，区分仪器电脑与 Agent 执行端，并以宿主实际调用成功确认连接。
- 从当前任务和已有实验室状态开始，只准备相关设备与条件。现场知识保留来源和适用范围；位置参考按型号与装置处理，不再笼统要求所有设备回原点。
- MCP 新增 `connect`、`research(question)`。研究指南支持不同实验形式、对照与设计，保留存疑观测并分析其影响，将研究决定留给下一次会话。报告、保存方法和结束实验分别说明。
- 精简工具说明，保留全部 45 个 stdio 工具的参数结构。原生 HTTP MCP 的 `saved_operations` 不再接受网关本地程序路径，与已有文件通路限制保持一致；程序包传输使用 stdio 桥接或 SDK。
- 模拟光学平台补齐采集提交、项目命名、重复请求与跨会话找回示例，使用独立数据目录，明确模拟与实机标定的区别。

运行时、Rust 执行器、TypeScript 客户端和 DeepSeek 包为 0.17.0；IOP Connect 与源码插件清单为 1.11.0。保留已有实验与配置格式。新增 MCP 提示和指南主题随运行时更新；宿主连接需重新生成或升级，以取得对应说明。

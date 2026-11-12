# IOP 0.9.0

IOP now stores and runs the three operation roles explicitly:

- **Basic Operations** control or observe a registered instrument.
- **Standard Operations** save reusable methods with parameters, stated conditions and named results.
- **Workflow Operations** organize methods as dependency graphs or saved research programs. Every use retains the operation revision and its underlying instrument actions. Episodes remain experiment trajectories, separate from reusable operations.

Instrument access adds SCPI binary arrays, non-SCPI text protocols, multi-register Modbus values, reusable Modbus/HTTP model descriptions, QCoDeS, OPC UA, EPICS Channel Access, and Python library or vendor SDK objects. QCoDeS, OPC UA and EPICS dependencies are optional and installed where those interfaces are used.

Calibration records, idle observations, manual steps and per-instrument operation selection share the existing scheduler and experiment records. With `unconfirmed_change: "hold"`, cancelling a wait after a command was sent retains the unknown outcome. Python SDK cancellation releases the action only when its configured stop and completion readback establish that it stopped. Feedback wiring is retained; a wiring-derived arming order is offered only when one exists.

Agent calls can return completed results in the same request. Large arrays remain in files, instrument descriptions can be selected by operation, and MCP toolsets can be selected for each connection. HTTP, Python, TypeScript, CLI and the browser expose the new capabilities. IOP Connect 1.3.0 includes the updated operating guide. HTTP MCP returns a complete credential error even when the request body is still arriving, including on Windows.

Existing laboratory configurations and records are retained. Finish accepted experiments before updating a running service. Saved-operation `conditions` describe applicability in words; they are not executable conditions. The added adapters have been exercised with software instruments and protocol servers; physical instrument coverage is recorded separately.

---

IOP 0.9.0 将**基础操作、标准操作、工作流操作**作为明确的可执行对象：基础操作连接设备；标准操作保存可复用的方法；工作流操作以步骤图或研究程序组织实验。每次使用记录版本、底层动作和结果。Episode 记录实验轨迹，不替代标准操作。

新增 SCPI 二进制数组、通用文本指令、多寄存器 Modbus 数值、Modbus/HTTP 型号说明，以及 QCoDeS、OPC UA、EPICS 和 Python 仪器库接入。标定、空闲监测、人工步骤和设备操作选择复用同一套调度与记录。Agent 可按需读取说明、在同次调用中等待结果，并通过文件取得大型数组。

本次保留原有配置和实验记录。更新前结束已接收的实验。新增接口已运行软件仪器及协议服务器；真实设备的运行范围单独记录。

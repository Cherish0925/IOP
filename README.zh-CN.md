# IOP

**面向真实仪器设备和实验平台的研究环境。**

[English](README.md) · [安装](docs/runtime/README.md) · [Agent 接入](iop/connect/README.zh-CN.md) · [研究任务记录](docs/runtime/episodes.md) · [下载](https://github.com/Cherish0925/IOP/releases)

IOP 包含常驻 Runtime、仪器说明、驱动、实验平台配置、实验程序、数据记录和 Agent 连接。研究程序向同一个服务提交操作；服务协调仪器、执行串行与并行任务，并在 Agent 断开后保留实验及结果。

IOP 独立于 Agent 宿主运行。人、Agentic Research OS、Codex、Claude Code、Qiushi Engine 等通过相应的 SDK、HTTP 或 MCP 入口使用同一套实验能力。

## 安装与连接

使用 Python 3.10 或更新版本从源码安装：

```sh
python -m pip install "ai4instruments[agent] @ git+https://github.com/Cherish0925/IOP.git"
iop init
iop start
iop open
iop connect --name bench --local
```

安装桌面依赖后，`iop desktop` 可以打开原生桌面窗口。平台发行包包含 Python、Rust 执行器及 Runtime/Agent 核心依赖；已完成的构建列在[下载页面](https://github.com/Cherish0925/IOP/releases)。[安装说明](docs/runtime/README.md)覆盖 Windows、macOS、Linux、WSL 和远程实验室。

Agent 宿主可以安装 `iop connect` 生成的连接目录，或在兼容的 MCP 客户端中配置 `iop mcp`。支持本机、SSH 和远程 HTTP 服务。[连接说明](iop/connect/README.zh-CN.md)给出各宿主的具体方法。插件连接实验室服务，驱动和实验平台配置由该服务保留。

## 各部分负责什么

| 部分 | 职责 |
| --- | --- |
| 仪器 | 驱动或厂商 SDK、设备身份、单位、操作参数和实际读数。 |
| 实验平台 | 研究者或 Agent 配置真实连接、共享设备、样品信息和初始化流程。 |
| Runtime | 协调设备占用、依赖关系、并行分支、常驻程序、取消操作和结果存储。 |
| 研究程序 | 决定实验方法、算法、初始化条件、分析过程和任务反馈。 |
| 研究任务记录 | 保存初始观测、动作、后续观测和反馈，并关联已经完成的仪器操作。 |

设备发现不能代替样品准备和物理连接说明。独立的平台可以同时运行；共享设备由同一调度器协调。初始化需要明确执行，保存配置本身不会操作仪器。

## 用于研究数据和学习

0.8.7 新增持久化 episode 记录。Python、TypeScript 和 MCP 都可以保存任务与策略版本、观测、奖励、任务结束或外部中止信息。每条记录关联同一次实验中已完成的操作；重复提交相同序号返回原记录，重新连接后可以继续读取或导出 JSON Lines。

创建 episode 不会重置设备。实验程序应明确准备步骤并记录实际初始状态；并行物理任务需要相应的独立资源。IOP 提供执行和数据能力，研究程序定义科学目标、反馈方式和学习算法。

## 工程结构

- 服务、调度器、驱动和 Python SDK：[`src/ai4instruments/iop/`](src/ai4instruments/iop/)。
- Rust 进程执行器：[`iop/daemon/`](iop/daemon/)。
- TypeScript SDK：[`iop/clients/typescript/`](iop/clients/typescript/)。
- 桌面与网页：[`iop/interface/`](iop/interface/)。
- 仪器说明、平台格式与插件：[`iop/`](iop/)。
- 可运行实验程序：[`examples/iop/`](examples/iop/)。

保留现有 Python 导入名、命令和数据目录。[来源记录](ORIGIN.md)连接原 AI4Instruments 实现及实验室反馈。软件采用 MIT，文档和原创图采用 CC BY 4.0。

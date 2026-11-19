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

## 操作

操作保留 [AI4Instruments](https://github.com/Cherish0925/AI4Instruments/blob/main/docs/operations.md) 定义的三类角色，每一类都是运行服务保存和执行的对象。

| 角色 | 在 IOP 中 |
| --- | --- |
| **Basic Operations** | 已登记设备的操作：每个是一项控制或观察，带参数、单位、范围和回读。 |
| **Standard Operations** | 由基础操作和标准操作组成并保存的方法，带参数、适用条件、具名结果和使用记录。 |
| **Workflow Operations** | 跨设备组织方法的保存流程：串并行的步骤图，或根据结果决定的程序。 |

带 `device` 的步骤是基础操作；不带的步骤按名称使用已保存的操作。运行服务把它展开为基础操作并记录每次使用，有效的方法因此在实验室里积累下来，并且随时可以查看和修改。任何步骤都可以用 `sweep` 扫描参数：按各个取值依次运行，返回的数值成为随扫描变化的数组，后续步骤和已保存的操作可以读取，例如转到功率最大的角度，或围绕它再细扫；自适应扫描根据之前的读数安排每个点，读数变化大的地方点更密；带 `until` 的扫描在读数越过界限后停止，例如偏压斜坡达到限流时；`repeat` 让一步重复运行，给出读数的离散程度，或等待读数越过界限。见[操作](docs/runtime/operations.md)。

## 工作条件

平台写明目标实验需要的条件：观察、容差、依赖、校正方法与可选的有效期。`maintain` 用实验共用的操作和调度器执行这些方法。校正期间共同占用相互关联的设备，独立平台仍可继续工作。

实验用 `requires` 声明所需条件。运行服务在每个相关操作开始前查看已有条件状态，随结果保留当时的观察，并在后续发现漂移时标明相关时间段。`tune` 提供有范围限制的局部数值搜索，其他方法通过保存的操作和程序表达。分类和依赖均由具体平台配置。

仪器自身的条件（预热、过载、参考锁定等）写在设备记录里，也可以随型号说明提供，该型号的每台仪器接入时就带着这些条件；平台条件建立在它们之上。

声明了 `upkeep` 的平台由运行服务保持：设备空闲时，对未观察、待核对、即将过期或预计漂出容差的条件进行观察，并校正已漂移的条件。每一轮自动保持都连同原因一起记录，且不会安排需要人来做的步骤。任何实验（步骤图、连续实验或程序）都可以为其所需条件声明 `upkeep`：运行服务在实验内部，于操作之前恢复、在操作之间更新这些条件，数小时的扫描因此能一直保持只在几分钟内可信的对准。见[工作条件](docs/runtime/conditions.md)。

## 测量数组

波形、谱线、图像、缓冲数据和数值数据流连同类型、形状、单位和坐标轴一起保存，坐标可以由仪器给出，例如扫频的起止频率。`read_array` 和各客户端不搬运数据即可读出数组的内容：极值及其位置、均值和离散程度、沿某轴的包络或沿两轴的二维图、指定窗口内的精确值，或峰与凹陷处的谱线：采样点之间的中心位置和半高全宽，光谱中突出程度达到给定值的每一条谱线、迹线穿过某一数值的位置，以及拟合的直线、洛伦兹线或高斯线，可对单条迹线或二维图的每一行读取；复数可取模、相位或单个分量。这些视图由 Rust `iop-execd` 在服务进程之外计算；整个数组也可以 NumPy 文件取得。已保存操作可以把经视图读出的数值命名为结果，例如谐振频率；后续步骤可以绑定它，工作条件可以据此核对和调节。见[读取数组](docs/runtime/drivers.md#reading-arrays--读取数组)。

## 用于研究数据和学习

Episode 记录实验轨迹，与三类操作并存。Python、TypeScript 和 MCP 都可以保存任务与策略版本、观测、奖励、任务结束或外部中止信息。每条记录关联同一次实验中已完成的操作；重复提交相同序号返回原记录，重新连接后可以继续读取或导出 JSON Lines。

创建 episode 不会重置设备。实验程序应明确准备步骤并记录实际初始状态；并行物理任务需要相应的独立资源。IOP 提供执行和数据能力，研究程序定义科学目标、反馈方式和学习算法。

## 工程结构

- 服务、调度器、驱动和 Python SDK：[`src/ai4instruments/iop/`](src/ai4instruments/iop/)。
- Rust 进程执行器与数组视图：[`iop/daemon/`](iop/daemon/)。
- TypeScript SDK：[`iop/clients/typescript/`](iop/clients/typescript/)。
- 桌面与网页：[`iop/interface/`](iop/interface/)。
- 仪器说明、平台格式与插件：[`iop/`](iop/)。
- 可运行实验程序：[`examples/iop/`](examples/iop/)。

保留现有 Python 导入名、命令和数据目录。[来源记录](ORIGIN.md)连接原 AI4Instruments 实现及实验室反馈。软件采用 MIT，文档和原创图采用 CC BY 4.0。

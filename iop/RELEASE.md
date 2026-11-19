# IOP 0.11.0

IOP now reads recorded arrays and streams as bounded summaries, traces and maps. Exact windows, coordinate axes, complex modulus/phase/power, peak or dip centers and half-height widths are available through the browser, HTTP, Python, TypeScript, CLI and MCP. Whole arrays download as NumPy files. The native executor reads large files; the Python implementation provides the same view within its documented size limit. Peak refinement uses physical coordinates, including unevenly spaced samples.

A step can sweep one or several parameters, including ranges derived from earlier results. The runtime gathers scalar and array results into arrays with sweep axes, preserving file-backed data across nested saved operations. Later steps can bind to those results and refine a scan. Array views and downloads also work while an experiment or stream is still producing results. Bounded sweep expansion rejects oversized requests before allocating their points, and batch waiting reads each completed operation once.

Instrument records and model descriptions can supply their own operating conditions. Platforms can maintain conditions while their instruments are idle; continuous experiments and programs can declare `upkeep` to maintain required conditions inside their existing reservation. Program upkeep starts after its reservation is acquired. A batch that adds conditions outside an active maintenance pass receives `maintenance_active` before acceptance and can retry with the same request ID after that pass completes. Invalid operations cannot start maintenance.

IOP Connect **1.5.0** includes the updated agent instructions and guides. The runtime, native executor, TypeScript SDK and DeepSeek connector are **0.11.0**. Existing configurations, run IDs, operation records and storage directories remain valid. Upgrade the service while idle, then update its clients; ongoing work continues to use the existing scheduler, resource ownership and saved-operation revisions.

Validation covers simulated instruments, local protocol servers, native/Python array parity, client APIs and browser workflows. Instrument-specific maintenance still requires commissioning on the intended hardware. A fitted peak and observed condition describe the acquired data; they do not establish continuous physical validity or a global optimum.

[Operating conditions](https://github.com/Cherish0925/IOP/blob/main/docs/runtime/conditions.md) · [Install and connect](https://github.com/Cherish0925/IOP/blob/main/iop/connect/README.md)

---

## 中文

IOP 0.11.0 将记录中的数组与数据流展示为有大小限制的摘要、曲线和二维图，支持精确区间、物理坐标、复数幅值/相位/功率、峰或谷的位置及半高宽，完整数组可下载为 NumPy 文件。网页、HTTP、Python、TypeScript、命令行和 MCP 共用这些能力。原生执行器读取大文件，Python 在规定大小范围内提供一致结果；非均匀采样的峰值拟合使用实际物理坐标。

步骤可扫描一个或多个参数，也可用前序结果确定后续扫描区间。运行服务把各点的标量和数组汇集成带扫描轴的数组；嵌套方法保留文件分段及各段长度，共同坐标轴不会因其他轴变化而丢失。后续操作可绑定这些结果继续细化扫描，运行中的实验和数据流也可读取已有结果。扫描在分配点之前检查数量限制，批次等待按完成顺序读取记录。

仪器记录和型号说明可以携带工作条件；平台空闲时可自动维护，连续实验与程序可用 `upkeep` 在自身预留资源内保持所需条件。程序取得资源后才开始维护。已有维护进行时，新增尚未覆盖条件的批次会在受理前返回 `maintenance_active`，结束后可用同一请求 ID 重试；无效操作不会提前触发设备维护。

IOP Connect 升级为 **1.5.0**，运行服务、原生执行器、TypeScript SDK 与 DeepSeek 连接包统一为 **0.11.0**。已有配置、实验和操作 ID、数据目录保持兼容。服务空闲时升级，再更新客户端。验证覆盖模拟仪器、协议服务器、原生与 Python 数组结果一致性、接口及网页流程；具体仪器的维护方法仍需在目标设备上调试确认。

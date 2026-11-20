# IOP 0.12.0

IOP adds result-driven scans and recovery to its existing operation model. Adaptive sweeps refine the sampled range, `repeat` gathers repeated readings, `until` stops on a reading or an estimated uncertainty, and `seek` searches for a local optimum within declared bounds. Nested saved operations retain their gathered results when an inner sweep stops early.

Step graphs can preview their operations and estimated duration, retry explicitly selected failed calls and continue after interruption while retaining completed work. Unknown instrument outcomes keep their resource holds and are not retried automatically. Step-graph upkeep uses the existing condition and resource scheduler; optional retakes replace doubtful sweep readings while preserving the originals. Declared bindings record their inputs, so doubtful measurements also mark results that consumed them. Those downstream physical actions are not replayed automatically.

Array views add multiple spectral peaks, threshold crossings, straight-line and Lorentzian/Gaussian fits, standard errors and analysis along rows of a map. Python and the native executor share the result format. The browser draws fitted curves and correctly displays scans that revisit coordinates. Task-specific guides are available through MCP, HTTP, both SDKs and the CLI, with the same references included in connector packages. Upkeep stops after three consecutive maintenance passes without an experiment operation starting, and continuation starts a fresh attempt. Whole experiments can be exported as streamed ZIP archives containing records, saved-operation revisions, files and RO-Crate metadata.

IOP Connect is **1.6.0**. The service, native executor, TypeScript SDK and DeepSeek connector are **0.12.0**. Existing configurations, imports, experiment IDs and data directories remain valid. Upgrade the service while idle and update clients to use the new capabilities.

[Operations](https://github.com/Cherish0925/IOP/blob/main/docs/runtime/operations.md) · [Operating conditions](https://github.com/Cherish0925/IOP/blob/main/docs/runtime/conditions.md) · [Install and connect](https://github.com/Cherish0925/IOP/blob/main/iop/connect/README.md)

---

## 中文

IOP 0.12.0 在原有操作模型中加入根据结果推进的扫描与恢复能力。自适应扫描细化取样区间，`repeat` 汇集重复测量，`until` 按读数或估计的不确定度停止，`seek` 在声明的范围内搜索局部极值。嵌套方法中的内层扫描提前停止后，外层仍可读取其汇集结果。

步骤图可以预览操作数量和预计耗时，对显式指定的失败调用重试，并在中断后保留已完成的工作继续运行。仪器执行结果未知时仍保留资源占用，不自动重试。步骤图的自动维护复用已有条件系统和资源调度；可选重测以新读数替换扫描中存疑的点，同时保留原记录。声明的绑定记录数据来源，存疑测量也会标记已经消费其结果的下游操作，不会自动重放下游物理动作。

数组视图新增多谱线、阈值穿越、直线与洛伦兹/高斯拟合、标准误和逐行分析，Python 与原生执行器共用结果格式。网页可绘制拟合曲线，并正确显示往返扫描。MCP、HTTP、两种 SDK 和命令行按需提供同一套任务指南，连接包也携带这些参考资料。连续三轮维护仍无法启动实验操作时会停止并说明原因，续跑从新的尝试开始。完整实验可流式导出为 ZIP，包含记录、已使用方法的版本、文件和 RO-Crate 元数据。

IOP Connect 为 **1.6.0**；运行服务、原生执行器、TypeScript SDK 和 DeepSeek 连接包统一为 **0.12.0**。已有配置、导入路径、实验 ID 和数据目录保持兼容。服务空闲时升级，并更新客户端以使用新增能力。

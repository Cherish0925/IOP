# IOP 0.15.0

## Physical conditions and measurements

- Conditions can judge repeated readings by mean, standard deviation, span or slope, or compare a reading with a fixed reference or the instrument's current setpoint. Agreement and stability are separate decisions; each experiment selects the conditions it needs.
- Arguments and tolerances accept unit-bearing strings or quantity objects. Conversion precedes parameter limits, including array items. Dynamic targets convert between declared units; absolute temperatures and temperature differences use their respective conversions. Incompatible dimensions are refused.
- Keep instrument-reported quality with readings from SCPI status queries, status operations, EPICS alarms and OPC UA status codes. Existing non-good quality is preserved when interval or saturation checks add a reason. Native snapshots retain affected channel names and status information.
- Verification, target and tuning readbacks retain the quality of the data they use, including selected results of saved operations. Unrelated diagnostic branches do not invalidate a usable result. A per-sample usability check cannot be hidden by averaging; an explicit statistic still selects an aggregate.
- Revisions to a dynamic target's device or method make prior observations due. Cancelling maintenance interrupts sampling and settling waits. Tuning keeps an acceptable trial even when its gain is smaller than the search threshold.

## Platforms and agent interfaces

- Platforms name physical disturbances and the conditions each affects. MCP can invalidate a named condition, a list, a platform or a device; dependent conditions become due through the existing dependency graph.
- Add the on-demand `priors` guide across HTTP, CLI, MCP and clients. It explains reference/sample context, calibration, equilibrium, transient experiments, measurement intervals and apparatus-specific dependencies.
- Add a simulated fiber-coupled optical bench with warm-up, mechanical and thermal drift, coupling, saturation and dark references. Its device models remain separate from the runtime's general scheduling and condition mechanisms.
- Add a Lake Shore Model 336 description for temperature, input validity, setpoints and heater state. Setpoints follow the control sensor's preferred units; the description does not assume kelvin. The profile covers the listed commands and is exercised with protocol fixtures.

IOP Connect and the source plugin manifests are 1.9.0. The runtime, native executor, TypeScript client and DeepSeek package are 0.15.0. Existing stored experiments and numeric result shapes are retained. Unit-bearing inputs are optional. Sampled `usable` guards now check each sample by default; use an explicit `statistic` for an aggregate guard.

## 中文

工作条件新增重复读数的均值、标准差、极差和斜率判断，以及对固定参考值或仪器当前设定值的比较。达到目标与保持稳定分别判断；各实验自行选择依赖条件。参数和容差支持带单位字符串及数量对象，数组元素同样先换算再判断范围，动态目标按已声明单位换算，绝对温度与温差分别处理。

读数保留 SCPI、EPICS、OPC UA 和状态操作提供的质量信息。核对、目标和调节回读使用实际数据来源的质量，封装方法不会因提取标量而丢掉它，无关的诊断分支也不会使有效结果失效。默认逐样本检查可用性，避免平均掩盖无效样本；显式指定统计量时仍可判断整体。目标方法或设备修订后重新观察，取消保持过程可中断采样和稳定等待。

平台可以声明扰动及其影响的条件。新增按需读取的物理先验指南、包含预热与漂移等机制的光纤耦合模拟平台，以及 Lake Shore 336 部分命令的型号说明。指南区分参考物与未知样品，按具体方法定义依赖，不规定所有领域共用一套实验顺序。

IOP Connect 与源码插件清单统一为 1.9.0，其余发行组件为 0.15.0，保留已有实验记录和数值结果结构。新增仪器型号以协议模拟验证，未在本次开发环境中连接实物。

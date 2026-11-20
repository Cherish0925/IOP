# IOP 0.14.0

## Research methods

- Add `measurements.Estimate`, a scalar result with units and named signed standard-uncertainty contributions. Arithmetic and first-order propagation preserve covariance from a shared dark reading, reference or calibration. Shared calibration terms do not shrink with repeated sampling.
- Add serializable, mergeable `RunningMean` for constant-memory mean and repeatability estimates. Nonlinear reduction states its first-order assumptions; unit conversion remains explicit.
- Add a configurable reference-normalized measurement program: signal/reference/optional dark, preparation steps, raw acquisitions, result stream, uncertainty targets and resumable statistics. The included configuration runs against the ideal simulated polarization apparatus.
- Save initial progress before the first instrument call in both resumable example programs and the program guide. An interruption during the first round can now use the existing resume operation and reuse completed readings.
- On explicit continuation, the reference method replaces a recorded failed reading with a stable new request while reusing completed readings, including replacements. Unknown instrument outcomes and failed preparation steps require method-specific recovery.

## Execution and clients

- Keep native SDK request deadlines and cancellation responsive when a driver stops reading its input pipe. Large requests no longer block the control loop.
- Refuse invalid or overflowing program deadlines before opening output files or launching the program.
- Compose TypeScript caller cancellation with the connection timeout for JSON, uploads, downloads and stream writes. Providing an AbortSignal no longer removes the timeout; older hosts without AbortSignal.any retain the same behavior. Archive streaming remains caller-controlled.
- Add the on-demand `measurements` guide across MCP, HTTP, CLI, Python, TypeScript and host packages. Measurement prompts choose between step graphs, continuous experiments and full programs. Refresh the architecture and contributor entry points.

IOP Connect is 1.8.0; runtime, native executor, TypeScript client and DeepSeek package are 0.14.0. Existing records and interfaces are retained.

## 中文

新增保留共享误差的测量归约：带单位和具名误差分量的估计值、协方差与一阶传播、可保存和合并的流式均值。参考归一化程序可配置仪器和准备步骤，保存原始采集、结果流与统计进度，并按标准不确定度目标或最大次数停止。示例使用理想偏振模拟装置。

两个可续跑示例都在第一次仪器操作前保存初始进度，首次采集期间中断也可恢复。Rust 执行器在驱动不读取大请求时继续响应时限与控制消息；无效时限在启动和写文件前拒绝。TypeScript 同时保留调用方取消与请求时限。新增测量指南并更新宿主入口、研究提示和架构说明，保留已有数据与接口。

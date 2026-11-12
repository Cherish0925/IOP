# IOP 0.10.0

Platforms can now save **operating conditions**: observations, tolerances, declared dependencies, correction procedures and optional validity periods. `maintain` uses the existing operations and scheduler to observe, correct where needed and observe again. Bounded coordinate tuning is available for numerical settings; other methods remain saved operations or research programs.

Experiments declare required conditions with `requires`. Their recorded state is checked at admission and again before each dependent device call. Each step retains the observations it used. Reported disturbances propagate to dependent conditions, and later observations can mark related experimental intervals `in_doubt` without rewriting the original measurements.

A maintenance pass reserves its coupled devices and shared resources together. Independent experiments remain concurrent. Inside an existing experiment, reserve the maintenance resources at startup and finish its current batch before calling maintenance. Saved methods in use by maintenance retain their revision until the pass ends.

HTTP, Python, TypeScript, CLI, the browser and MCP expose the same condition records and maintenance jobs. IOP Connect **1.4.0** includes the updated agent instructions. Existing configurations without conditions continue to work; Basic, Standard and Workflow Operations retain their roles. Upgrade the service after active experiments finish, then update the connector or SDK used by its clients.

Condition categories are extensible. Dependencies describe the configured platform; they are not inferred physical causality. Observations and local search results do not establish uninterrupted validity or global optimality. Conditions are currently platform-level; instrument profiles do not yet install them automatically. This release exercises simulated physical changes, protocol servers and client/service workflows; new maintenance procedures still need commissioning on their intended instruments.

[Operating conditions and examples](https://github.com/Cherish0925/IOP/blob/main/docs/runtime/conditions.md) · [Install and connect](https://github.com/Cherish0925/IOP/blob/main/iop/connect/README.md)

---

## 中文

IOP 0.10.0 新增**工作条件与自动调节**。平台保存观察方式、容差、依赖、校正方法和可选的有效期；调用 `maintain` 后，运行服务通过已有的操作与调度执行“观察—必要时校正—再次观察”。数值设定可使用有范围限制的局部搜索，其他方法继续保存为标准操作或研究程序。

实验用 `requires` 声明所需条件，在受理及每个相关设备调用开始前查看状态，随步骤保留观察依据。已报告的扰动传递到下游条件；后续发现漂移时标明相关数据的时间段，原始测量保持原样。

每轮校正共同占用相关设备和共享资源，独立实验仍可并行。在已有实验内进行保持时，启动实验就预留相关资源，在当前批次完成后调用。正在被保持过程使用的方法不会中途换版本。

HTTP、Python、TypeScript、命令行、网页与 MCP 共用同一实现。IOP Connect 1.4.0 同步更新操作说明。原有配置可以继续使用，基础、标准和工作流操作的职责保持清晰。结束正在运行的实验后更新服务，再更新连接插件或客户端。

条件分类可以扩展；依赖由平台配置明确提供。条件目前保存在平台说明中，仪器型号说明尚不自动携带条件。本次运行覆盖模拟状态变化、协议服务器及客户端流程；具体仪器的保持方法需在目标平台上实际运行确认。

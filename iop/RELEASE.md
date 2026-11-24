# IOP 0.16.0

## Unit-bearing ranges and channel conditions

- Sweep values and seek ranges accept quantity objects and strings in any compatible unit of the parameter. Positions convert with temperature offsets; spans, steps and resolutions convert as differences. Conversion applies before parameter limits, through saved methods and platform procedures. Categorical string labels remain unchanged, and quantities are distinguished from references to earlier results.
- Conditions can require selected status bits to be clear. Samples are combined by bitwise OR, with exact integer parsing shared by conditions and reading quality. Fractional and nonfinite registers are refused; bit masks cannot be mixed with numeric tolerances or statistics.
- `each` defines one condition per channel, input, output or axis. Typed substitution covers arguments, dependencies, targets and result paths. Each expansion has a checked size; local conditions take precedence over model conditions with the same expanded name. The Lake Shore 336 description uses this form for its two heater outputs.
- Guides distinguish current instrument states from event registers or error queues consumed by a query. They keep experiment-specific recovery in the method, and retain the independent roles of stability, target agreement and reading quality.

The runtime, Rust executor, TypeScript client and DeepSeek package are 0.16.0; IOP Connect and source plugin manifests are 1.10.0. Existing numeric requests, saved experiments and explicit condition definitions remain supported. New ranges and channel templates require IOP 0.16.0.

## 中文

- 扫描值和搜索范围支持带单位字符串及数量对象。位置按温度偏移换算，跨度、步长与分辨率按差值换算；封装方法和平台流程使用同一条转换路径，转换后再检查参数范围。字符串档位保留原值，数量对象与前序结果引用分别处理。
- 条件支持状态位掩码。多次读数按位或合并，与读数质量共用精确整数解析；小数和非有限值被拒绝，位掩码不与数值容差或统计量混用。
- `each` 为通道、输入、输出或轴分别展开条件，保留参数、依赖、目标及结果路径中的值类型。展开数量在分配前检查，本地同名条件优先于型号说明。Lake Shore 336 的两个加热输出使用这一形式。
- 指南区分当前物理状态与读取后清除的事件、错误队列；恢复步骤由具体实验方法定义，稳定性、目标一致性和读数质量仍分别表达。

运行时、Rust 执行器、TypeScript 客户端和 DeepSeek 包为 0.16.0；IOP Connect 与源码插件清单为 1.10.0。保留已有数值请求、实验记录和显式条件定义。新增范围写法与通道模板要求服务端为 IOP 0.16.0。

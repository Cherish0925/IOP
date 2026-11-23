# Instrument descriptions and drivers · 仪器说明与驱动

[中文安装](README.zh-CN.md) · [Installation](README.md)

A description names an instrument's operations and their inputs. A driver performs those operations and reports when they have finished. The runtime keeps connections and schedules calls. / 仪器说明列出操作与输入，驱动执行并报告完成，常驻服务保持连接并安排调用。

## SCPI

`iop adapters`, Python `client.adapters()` and MCP `available_adapters` list the adapters. With an adapter name (`iop adapters python`, `client.adapters("python")`, `available_adapters(adapter)`) they return the fields of its device record and operations with an example, which is what an agent reads before describing a new instrument. TCP `source_address` selects a local IP when the computer has several network interfaces; omit it to use system routing. Serial configuration includes `baudrate`, `bytesize`, `parity`, `stopbits`, `xonxoff`, `rtscts` and `dsrdtr`. Use settings from the instrument manual. / 连接参数可直接查询；多网卡可指定本机 IP，串口参数按仪器手册填写。

A change operation can time out after its command has reached the instrument. By default the operation simply fails. Set `"unconfirmed_change": "hold"` to record such an outcome as unknown instead: once the command or its completion query has been sent and no reply or completion arrives, the operation keeps the device and the experiment's reserved resources unavailable, like an interrupted SDK worker. Operations described with `effect: "observe"` can still read the instrument. Record what they show through `/v1/resources/resolve`, then continue. A failure before anything was sent, and a timeout of an observation, remain ordinary failures. / 设定类操作的命令可能已经到达仪器，回复或完成确认却超时。默认记为失败；设置 `"unconfirmed_change": "hold"` 后，命令发出后未得到回复或完成确认的设定类操作记为结果不明，设备和本实验预留的资源保持挂起，只允许 `effect: "observe"` 的读取操作；读回实际状态并通过 `/v1/resources/resolve` 登记后继续。命令发出前的失败和读取操作超时仍按普通失败处理。

Use `release` to hand an idle connection to another program, then `reconnect` to restore it. Shared SDK workers release all their devices together. / 使用 `release` 释放空闲连接，其他软件用完后通过 `reconnect` 接回。

Copy [scpi-meter.json](../../examples/iop/scpi-meter.json), replace the address and model-specific commands, then register it with `iop register device.json`. / 复制示例，按型号手册修改地址与命令后登记。

```json
{
  "id": "power-meter",
  "driver": "scpi",
  "resources": ["measurement-channel"],
  "config": {
    "name": "Optical power meter",
    "transport": "tcp",
    "host": "192.168.1.50",
    "port": 5025,
    "timeout": 5,
    "termination": "\n",
    "operations": {
      "read_power": {
        "description": "Read optical power",
        "parameters": {},
        "query": "MEAS:POW?",
        "result": {"type": "number", "unit": "W"}
      },
      "set_wavelength": {
        "parameters": {"nm": {"type":"number","minimum":400,"maximum":1100,"unit":"nm"}},
        "write": "SENS:CORR:WAV {nm}",
        "completion": {"query":"*OPC?","equals":"1","timeout":30}
      }
    }
  }
}
```

New local operation parameters follow the [parameter contract](operations.md): bounds use `minimum` and `maximum`, and unknown constraint fields are refused before a new configuration opens or replaces a driver. Existing stored configurations and SDK descriptions keep their compatibility. / 新写入的本地操作参数遵循[参数格式](operations.md)：界限使用 `minimum` 和 `maximum`，未知约束字段在新配置打开或替换驱动之前被拒绝；已存配置与 SDK 返回的描述保持兼容。

These commands and ranges illustrate the format; use those specified for the actual model. The driver formats named parameters into a command, reads query responses and optionally polls completion. Numeric results include their configured unit and acquisition timestamp. / 示例中的命令和范围需要替换为实际型号的定义。

### Validity of readings / 读数的有效性

An instrument often says whether a reading is valid: an overload, an overrange, an unlocked reference, an invalid sensor input. IOP keeps that word with the reading. / 仪器通常会说明读数是否有效：过载、超量程、参考失锁、传感器输入无效。IOP 把这一说明随读数保存。

- **Status query** (`scpi` and text protocols): `"quality": {"query": "STAT:QUES:COND?", "bits": 32767}` reads the status after the query; any of the bits set marks the reading uncertain. `"equals"` names the reply of a valid reading instead, as `{"query": "RDGST? {input}", "equals": 0}`; parameters of the operation fill the command. / **状态查询**：读数后再读状态，置位任一指定位、或回复不等于 `equals` 时，读数记为不确定。
- **Status operation** (any adapter): `"quality": {"operation": "status", "arguments": {"channel": "{channel}"}, "value": ["overrange"], "equals": false}` calls another observe operation of the same instrument after the reading, such as an SDK status call or a status register; `"{channel}"` passes the reading's own argument. / **状态操作**（任何适配器）：读数后调用同一仪器的另一个观测操作，如 SDK 的状态函数或状态寄存器；`"{channel}"` 传入读数本身的参数。
- **Measuring interval** (any adapter): `minimum` and `maximum` in an operation's `result` bound the readings that mean what the method assumes; `saturation` is the value at which the instrument clips, such as 4095 counts of a 12-bit converter. A value outside the interval, or at saturation, is uncertain. / **测量区间**（任何适配器）：`result` 中的 `minimum`、`maximum` 给出有意义读数的范围，`saturation` 为削顶值，如 12 位转换器的 4095；区间之外或达到饱和的读数为不确定。
- **EPICS** alarms that concern the value itself (READ, COMM, TIMEOUT, HWLIMIT, LINK, UDF, SIMM, CALC, BAD_SUB, DISABLE, READ_ACCESS) make it uncertain; limit alarms (HIHI, HIGH, LOW, LOLO, STATE) describe the process and stay in `alarm`; INVALID fails the operation. **OPC UA** Uncertain status codes make the value uncertain; Bad fails the operation. / **EPICS** 中涉及数值本身的告警使读数不确定，限值告警描述过程状态，记入 `alarm`；INVALID 使操作失败。**OPC UA** 的 Uncertain 状态码使读数不确定，Bad 使操作失败。

An uncertain reading carries `quality` with its reason and is marked `in_doubt.quality`, as data taken while a condition did not hold; results computed from it inherit the doubt, a sweep or repetition with `retake` takes it again, and a condition whose verification reads it finds the verification unusable. / 不确定的读数带有 `quality` 及原因，并像条件失效期间的数据一样标为 `in_doubt.quality`；由它计算的结果继承存疑，带 `retake` 的扫描或重复会重新测量，用它核对的条件判为不可用。

EPICS and OPC UA `snapshot` apply the same validity rules as individual reads. Values remain in `variables`; EPICS alarms are keyed by PV name in `alarms`, and OPC UA uncertain statuses by variable path in `statuses`. An uncertain value adds `quality` naming the affected variables; INVALID or Bad fails the snapshot. / EPICS 和 OPC UA 的 `snapshot` 与逐项读取采用相同的有效性规则。数值仍在 `variables` 中；EPICS 的 `alarms` 按 PV 名保存告警，OPC UA 的 `statuses` 按变量路径保存 Uncertain 状态。不确定数值使快照带上 `quality`，注明受影响的变量；INVALID 或 Bad 使快照失败。

### Units / 单位

A parameter's `unit` is the unit the instrument takes. A request may give the number with its own unit, `{"value": 250, "unit": "mV"}` or `"250 mV"`: it is converted to the declared unit before `minimum`, `maximum` and the record's `limits` apply, and the operation record holds the converted number. A unit of another quantity is refused before the instrument is called, as is a unit for a parameter without one. Dimensions follow the SI base quantities, with plane angle kept apart so that rad/s is not taken for Hz; `°C` and `°F` convert with their offset when they stand alone. Units the table does not know, and logarithmic units such as `dBm`, are accepted only as the declared unit itself. Sweep values and seek ranges convert the same way, positions with their offset and widths (`span`, `step`, `resolution`) as differences. Declare units with SI symbols so that conversions apply. / 参数的 `unit` 是仪器接受的单位。请求可带上自己的单位给出数值，如 `{"value": 250, "unit": "mV"}` 或 `"250 mV"`：先换算到声明的单位，再按 `minimum`、`maximum` 与设备记录的 `limits` 核对，操作记录保存换算后的数值。量纲不同的单位在调用仪器之前即被拒绝；未声明单位的参数不接受带单位的数值。量纲按 SI 基本量，平面角单独计，因而 rad/s 不会被当作 Hz；`°C`、`°F` 单独出现时按偏移换算。表中没有的单位以及 `dBm` 等对数单位，只接受与声明完全相同的写法。声明单位时请用 SI 符号，以便换算。

An array's numeric items accept quantities too: `["250 mV", "1 V"]` becomes `[0.25, 1]` for an array in V. Items inherit the array's `unit` unless `items` declares its own; conversion and bounds apply recursively before the driver receives the values. / 数组的数值元素同样可以带单位：单位为 V 的数组接收 `["250 mV", "1 V"]` 后得到 `[0.25, 1]`。元素继承数组的 `unit`，除非 `items` 自行声明单位；换算和范围检查递归应用，驱动收到的是换算后的数值。

| Transport / 连接 | `config` fields / 字段 |
| --- | --- |
| TCP | `transport: "tcp"`, `host`, `port` (default 5025) |
| Serial / 串口 | `transport: "serial"`, `port: "COM4"` or `/dev/ttyUSB0`, `baudrate` (default 9600) |
| VISA | `transport: "visa"`, `resource: "USB0::...::INSTR"`; optional `backend: "@py"` |

All support `timeout` in seconds and `termination`. Text encoding defaults to ASCII. VISA support depends on the installed vendor library or pyvisa-py backend and its transport dependencies.

Built-in `identify`, `query` and `write` operations provide `*IDN?`, a raw text query and a raw text write. A write result with `sent: true` means the command was sent. A named operation with `completion` also waits for its specified device response. Motion or acquisition completion must follow the actual device's documented status mechanism.

内置 `identify`、`query`、`write` 分别读取身份、查询文本和写入文本。`sent: true` 表示命令已发送；定义 `completion` 的操作还会等待相应设备回复。运动、稳定和采集完成的含义按仪器手册定义。

### Arrays and binary blocks / 数组与二进制块

Traces, spectra and other numerical arrays are read with `result.type: "array"`. `encoding: "block"` (default) reads an IEEE 488.2 definite-length block, `#<digits><length><bytes>`, and needs `dtype` (`int8` … `uint64`, `float32`, `float64`) and `byte_order` (`little` or `big`). `encoding: "ascii"` reads separated numbers as `float64`. Select the matching data format on the instrument first, for example with a `FORM` command from its manual. / 波形、谱线等数组用 `result.type: "array"` 读取：`block` 读取 IEEE 488.2 定长块，需写明元素类型和字节序；`ascii` 读取分隔的数值。仪器端的数据格式按手册先设定。

```json
"read_trace": {
  "query": "CALC1:DATA:SDAT?", "effect": "observe", "parameters": {},
  "result": {"type": "array", "dtype": "float64", "byte_order": "little", "shape": [-1, 2],
             "axes": [{"name": "point"}, {"name": "component", "labels": ["re", "im"]}]}
}
```

The result carries `data`: `dtype`, `byte_order`, `shape`, `count`, `bytes`, `sha256`, and `unit` and `axes` when described. The payload is saved unchanged as `<operation>.bin` (or `result.file`) in the operation's output directory. Arrays up to `inline_limit` elements (default 1024) are also returned as nested lists in `values`. The operation record lists its files in `files` as soon as it ends, so a running experiment can download them. Drivers for cameras, digitizers or other SDKs can produce the same description with `ai4instruments.iop.arrays.result(payload, arrays.check_result(spec), context, name)`. / 结果中的 `data` 描述元素类型、字节序、形状、数量、字节数、校验值以及单位和坐标轴。原始数据原样保存在操作输出目录，小数组同时以 `values` 返回；操作结束时文件即列入操作记录，实验进行中即可下载。相机、采集卡等 SDK 驱动可用同一函数生成相同描述。

An axis can carry coordinates and `unit`: `start` with either `step` or `stop` when they are evenly spaced, or `coordinates`, a list with one number for each position, such as the frequencies of a logarithmic sweep. For SCPI, `start`, `step` and `stop` can also be `{"query": command}`, read after the array, because sweep settings change between acquisitions; the result then holds the numbers the instrument reported. A last axis labelled `re`, `im` holds complex values. / 坐标轴可带坐标和 `unit`：等间隔时写 `start` 加 `step` 或 `stop`，否则写 `coordinates`，每个位置一个数，例如对数扫频的各个频率。SCPI 中 `start`、`step`、`stop` 也可写成 `{"query": 命令}`，在读完数组后向仪器查询，结果中记录仪器报告的数值。最后一维标为 `re`、`im` 时表示复数。

```json
"axes": [{"name": "frequency", "unit": "Hz", "start": {"query": "SENS1:FREQ:STAR?"}, "stop": {"query": "SENS1:FREQ:STOP?"}},
         {"name": "component", "labels": ["re", "im"]}]
```

A write with `data` sends one block after its command. `data.parameter` names an array parameter; `dtype` and `byte_order` encode its values. / 带 `data` 的写操作在命令后发送一个二进制块，用于任意波形表等大数组。

```json
"load_waveform": {
  "write": "SOUR1:DATA:ARB {name},",
  "parameters": {"name": {"type": "string"}, "samples": {"type": "array", "items": {"type": "integer"}, "maxItems": 65536}},
  "data": {"parameter": "samples", "dtype": "int16", "byte_order": "big"},
  "completion": {"query": "*OPC?"}
}
```

`max_block_bytes` (default 256 MiB) limits a received block. `block_terminated: false` is for instruments that send no terminator after a block. Indefinite-length blocks (`#0`) are refused; select a definite-length format. A block read ends with an error after `timeout` seconds without new bytes, so long transfers continue while data arrives. / `max_block_bytes` 限制单个接收块大小；块后不发终止符的仪器设 `block_terminated: false`；不支持 `#0` 不定长块。读取块时，`timeout` 指连续无新数据的时长。

### Reading arrays / 读取数组

`GET /v1/operations/{id}/array`, `read_array` in MCP, `Client.read_array` and `readArray` in TypeScript tell what an array says without moving all of its values. One axis is followed (`along`, default the first); `at` fixes the others by index or label, and an axis not fixed is summarized. Complex values read as `abs` unless `at` chooses `re`, `im`, `arg` (radians) or `power`, the squared modulus: the width of a resonance dip in power, read with `peak`, gives its loaded Q as center over width. `start`/`stop` select positions, `low`/`high` coordinates. Two axes, such as `"along": ["row", "column"]` with `"points": [16, 16]`, read an image or a sweep over two parameters as a map; `start`, `stop`, `low` and `high` then give one value per axis, `null` for none, and in a URL these lists are JSON. / 不搬运全部数值即可读出数组的内容：沿一个轴读取，`at` 按序号或标签固定其他轴，未固定的轴被汇总；复数默认取模，也可取 `re`、`im`、`arg`（弧度）或 `power`（模的平方：谐振凹陷在功率上的 `peak` 宽度给出有载 Q，即中心除以宽度）；`start`/`stop` 按位置、`low`/`high` 按坐标选取范围。沿两个轴读取（如 `"along": ["row", "column"]`、`"points": [16, 16]`）即把图像或双参数扫描读成二维图；此时 `start`、`stop`、`low`、`high` 每个轴给一个值，不限定写 `null`，在 URL 中以 JSON 列表传递。

The reply holds `summary` (count, minimum and maximum with their index and coordinate, mean, standard deviation, the standard `error` of the mean, and how many values are not finite), then `values` when each of at most `points` positions holds one element, otherwise `envelope`: the minimum, maximum and mean of `points` consecutive buckets. Zooming into the bucket that matters gives its exact values. Along two axes, `along` lists both, coordinates come as pairs and `values` and the `envelope` statistics are rows of the first axis by columns of the second. `along` gives the coordinates of the selection, `low` to `high`, with `step` when evenly spaced; along listed coordinates, exact values come with `along.coordinates`, and `low`/`high` select where those coordinates increase or decrease. / 返回 `summary`（数量、极值及其位置和坐标、均值、标准差、均值的标准误差 `error`、非有限值个数）；位置不超过 `points` 且每个位置一个元素时返回精确的 `values`，否则返回 `points` 段的最小、最大和平均值 `envelope`。在关心的一段上缩小范围，即得到精确值。沿两个轴读取时，`along` 列出两个轴，坐标成对给出，`values` 和 `envelope` 的各项按第一个轴分行、第二个轴分列。`along` 给出所选范围的坐标 `low` 到 `high`，等间隔时带 `step`；坐标逐个列出时，精确值附带 `along.coordinates`，坐标单调时可用 `low`/`high` 选取。

```json
{"along": {"axis": "frequency", "start": 0, "stop": 1001, "low": 1.0e9, "high": 1.1e9, "step": 1.0e5, "unit": "Hz"},
 "at": {"component": "abs"},
 "summary": {"count": 1001, "min": 0.2, "min_index": [600], "min_at": 1.06e9, "max": 0.999, "mean": 0.951, "std": 0.133, "error": 0.0042},
 "envelope": {"position": [0, 16, ...], "coordinate": [1.0e9, 1.0016e9, ...], "min": [...], "max": [...], "mean": [...]}}
```

`peak` finds the line at the highest value along one axis, the others fixed with `at`, or with `min` the dip at the lowest. `center` lies between samples, at the vertex of the parabola through the extreme and its two neighbours; `base` is the opposite extreme of the selection; `low` and `high` are where the trace, going out from the line, first comes halfway from its `value` to the base, interpolated between samples, and `width` is the distance between them: the full width at half maximum, of a dip measured from its base. Positions are in the unit of the axis, `value` and `base` in that of the array. When a trace holds several lines, select one with the view's `low` and `high`. / `peak` 沿一个轴（其余的轴用 `at` 固定）找出最高处的谱线，`min` 则找最低处的凹陷。`center` 位于采样点之间，是极值点与两侧相邻点所定抛物线的顶点；`base` 是所选范围内另一端的极值；`low`、`high` 是从谱线向两侧、数值第一次到达 `value` 与 `base` 中点的位置（在采样点间插值），`width` 是两者之差，即半高全宽，凹陷从其基线算起。位置以该轴的单位给出，`value`、`base` 以数组的单位给出。一条迹线有多条谱线时，用视图的 `low`、`high` 选出一条。

Listed coordinates are used directly for the fit and half-height interpolation, including uneven spacing; for a peak they must strictly increase or decrease over the selected trace. An extreme at an endpoint stays at that sample. / 显式坐标直接参与拟合与半高位置插值，支持非均匀间距；读取谱线时，所选迹线的坐标必须严格递增或递减。极值在端点时保留该采样点的位置。

```json
{"peak": {"center": 1.06e9, "width": 3.996e6, "low": 1.058e9, "high": 1.062e9, "value": 0.2, "base": 0.999, "index": 600}}
```

With `prominence`, `peaks` lists every line that stands out by at least that much, in order of position: the modes of a resonator chain, the lines of a spectrum. A line's prominence is its height above the higher of the lowest values on either side before a higher line or the end of the selection; that value is its `base`, and its `width` is taken where it comes halfway from its `value` to that base. Ripple and noise stand out by little, so a prominence above their height leaves only the lines; at most 1000 are read. / 加 `prominence` 时，`peaks` 按位置列出突出程度不小于该值的每一条谱线，例如谐振器链的各个模式、光谱中的各条谱线。一条谱线的突出程度是它高出两侧最低值中较高者的高度（两侧各自取到更高的谱线或所选范围的端点为止）；这个较高的最低值就是它的 `base`，`width` 在从 `value` 到该基线一半处取得。纹波和噪声的突出程度小，取高于它们的 `prominence` 就只剩下谱线；一次最多读出 1000 条。

```json
{"peaks": [{"center": 1.021297e9, "width": 2.098e6, "low": 1.020244e9, "high": 1.022341e9, "value": 0.225, "base": 1.001, "prominence": 0.776, "index": 213}, ...]}
```

`level` reads where the trace crosses a value: `crossings` lists each place, in order of position, between the two neighbouring samples on either side of the level, on the straight line through them, with `rising` true when the trace goes up through it and `index` the sample before. This gives the voltage at which a diode reaches a current, the frequencies where a filter is 3 dB down, the edges of a pulse at 10% and 90% of its height, or the coercive field of a magnetization loop. Listed coordinates need not increase, so a loop swept up and back crosses zero once on each branch; missing values are passed over, and at most 1000 crossings are read. / `level` 读取迹线穿过某个数值的位置：`crossings` 按位置列出每一处，取该数值两侧相邻两个采样点所连直线上的位置，`rising` 为真表示向上穿过，`index` 是穿越之前的采样点。由此可得二极管达到某一电流时的电压、滤波器下降 3 dB 处的频率、脉冲在 10% 和 90% 高度处的边沿、磁滞回线的矫顽场。显式坐标不必递增，往返扫描的回线在每一支上各穿过零一次；缺失值跳过，一次最多读出 1000 处。

```json
{"crossings": [{"at": 1.058e9, "rising": false, "index": 580}, {"at": 1.062e9, "rising": true, "index": 619}]}
```

`fit: "line"` gives the straight line through the selection by least squares, in the coordinates of its axis: its `slope`, its `intercept` at coordinate 0, `zero`, the coordinate where it reaches 0, the `residual`, the root mean square of what it leaves, and the `count` of values it went through. With `low` and `high` around the straight part of a curve, this is a laser's threshold current, where the line through its light above threshold reaches zero, with its slope efficiency; the resistance of an I–V curve; or the drift of a signal over time. With the summary's `std` of the same selection, 1 − (residual/std)² is the coefficient of determination. / `fit: "line"` 用最小二乘给出所选范围的直线，以该轴的坐标计：`slope` 斜率、坐标 0 处的 `intercept`、直线到达 0 处的坐标 `zero`、直线拟合后剩余部分的均方根 `residual`，以及参与拟合的数值个数 `count`。用 `low`、`high` 选出曲线的直线段，即可得到激光器的阈值电流（阈值以上的出光直线到达零的位置）和斜率效率、I–V 曲线的电阻、信号随时间的漂移。与同一范围摘要中的 `std` 一起，1 − (residual/std)² 即决定系数。

```json
{"fit": {"slope": 0.401, "intercept": -0.01, "zero": 0.024938, "residual": 0.0, "count": 31}}
```

`fit: "lorentzian"` and `fit: "gaussian"` fit a line of that shape over a base by least squares (Levenberg–Marquardt), starting from the value farthest from the median: its `center`, its `width`, full at half its height, its `height` above the base (negative for a dip), the `base`, the `residual` and the `count`. Through noise these use every point of the line, where `peak` uses the few around its half height: on a resonance of Q 2120 under noise of up to 0.02 on a dip 0.8 deep, the Lorentzian gives Q within 0.5% and the width at half height is 2% off. A Gaussian gives the width of a beam profile or of an inhomogeneously broadened line. Each step of the fit visits every value, so it takes at most 1,000,000 over all rows; select a window around the line. Both kinds of fit give `errors`, standard errors from what the fit leaves with the values taken as independent: of the slope and of where the line reaches zero, or of the center, width, height and base; on the resonance above, the true center and width lie within them, and the width's is 0.9%. A line gets its errors when at least two values lie within its width at half height; one drawn through a single noisy value, narrower than the samples resolve, gives `errors: null`. / `fit: "lorentzian"`、`fit: "gaussian"` 用最小二乘（Levenberg–Marquardt）在基线上拟合该形状的谱线，从离中位数最远的数值出发：给出 `center` 中心、半高全宽 `width`、高出基线的 `height`（凹陷为负）、`base` 基线、`residual` 和 `count`。有噪声时它们用到谱线上的每个点，而 `peak` 只用半高附近的几个点：Q 为 2120 的谐振，凹陷深 0.8、噪声至多 0.02 时，洛伦兹拟合的 Q 误差在 0.5% 以内，半高宽读法偏 2%。高斯拟合给出光束截面或非均匀展宽谱线的宽度。拟合的每一步都要遍历所有数值，因此所有行合计最多 1,000,000 个；请选出谱线附近的范围。两种拟合都给出 `errors`：按拟合剩余部分、把各数值视为独立算出的标准误差，直线为斜率和到零处的，线形为中心、宽度、高度和基线的；上面那个谐振的真实中心和宽度都落在其中，宽度的标准误差为 0.9%。半高全宽内至少有两个数值时，线形才给出误差；穿过单个噪声值、窄于采样所能分辨的谱线给出 `errors: null`。

```json
{"fit": {"center": 1.0600014e9, "width": 499518.1, "height": -0.795, "base": 0.999, "residual": 0.0117, "count": 201,
         "errors": {"center": 1324.4, "width": 4438.9, "height": 0.0042, "base": 0.0012}}}
```

`peak`, `level` and `fit` read along one axis with the others fixed. With one other axis left free, they are read at each of its positions, at most 1000: `rows` gives that axis's coordinates or labels and, for each position, the line, crossings and fit of the trace there. A map of spectra against a bias thus gives the line's center at each bias in one view, and a sweep gathered from its points' files is read from them. / `peak`、`level`、`fit` 沿一个轴读取，其余的轴固定；留下另一个轴不固定时，在它的每个位置各读一次（最多 1000 个）：`rows` 给出该轴的坐标或标签，以及每个位置上那条迹线的谱线、穿越位置和直线。随偏压变化的光谱二维图因此一次视图就给出各偏压下谱线的中心；由扫描各点文件汇集的数组直接从这些文件读取。

```json
{"rows": {"axis": "volts", "unit": "V", "coordinates": [0, 1, 2, 3],
          "peak": [{"center": 500.0, "width": 4.0, ...}, {"center": 510.0, ...}, ...]}}
```

Saved operations and step bindings can take a value through a view, such as the frequency of a resonance; see [operations](operations.md). A data stream of a numeric format reads the same way, as samples by channels; see [continuous data](../../iop/continuity.md#continuous-data). `GET /v1/runs/{id}/array` reads an array an experiment's result holds, such as what a [sweep](operations.md#sweeps--扫描) gathered, and with `request_id` one in the results of a batch. / 已保存操作和步骤绑定也可以经视图取值，例如谐振频率，见[操作](operations.md)。数值格式的数据流同样可读，形状为“采样 × 通道”，见[连续数据](../../iop/continuity.zh-CN.md#连续数据)。`GET /v1/runs/{id}/array` 读取实验结果中的数组，例如[扫描](operations.md#sweeps--扫描)汇集的数组；加 `request_id` 则读取某个批次结果中的数组。

The whole array comes as a NumPy `.npy` file, which `numpy.load` reads: `GET /v1/operations/{id}/array.npy`, `/v1/runs/{id}/array.npy` and `/v1/runs/{id}/streams/{name}/array.npy`, `array_file` in Python and `arrayFile` in TypeScript. A last axis of re/im pairs becomes complex values, a stream gives its whole samples by channels, and a map a sweep gathered from its points' files is read from them in order. / 整个数组以 NumPy `.npy` 文件取得，`numpy.load` 可直接读取：上述三个路径加 `.npy`，Python 的 `array_file`，TypeScript 的 `arrayFile`。最后一维为 re/im 时成为复数；数据流给出完整的“采样 × 通道”；扫描由各点文件汇集的二维图按顺序从这些文件读出。

`iop-execd --array-view` computes views in its own process and reads only the slabs that hold the selected positions; the service stays responsive while it reads large files. The browser draws an array of two axes, other than re/im components, as a map in a perceptually uniform colour scale, keeping the proportions of two lengths in the same unit. Without it, Python computes the same view when it reads at most 4,000,000 elements. / 视图由 `iop-execd` 在独立进程中计算，只读取所选位置所在的数据段，读取大文件时不占用运行服务；浏览器把两个轴的数组（re/im 分量轴除外）画成色阶均匀的二维图，单位相同的两个长度保持比例；没有它时，Python 在读取不超过 400 万个元素时给出相同结果。

### Other text command protocols / 其他文本指令协议

Many motion controllers, lasers, pumps, flow controllers and gauges exchange text lines without following SCPI. The same driver reads them from a description; no code is needed. `config.protocol` states how the command language differs, and an operation's `result` takes its value out of the reply. / 许多位移台控制器、激光器、泵、流量计和真空计用文本行通信但不遵循 SCPI。同一驱动按说明即可接入，不需要写代码：`config.protocol` 说明指令语言的差异，操作的 `result` 说明如何从回复中取值。

```json
{
  "id": "delay-stage",
  "driver": "scpi",
  "config": {
    "transport": "serial", "port": "COM5", "baudrate": 57600,
    "protocol": {"write_termination": "\r\n", "read_termination": "\r\n", "identify": false, "error_reply": "^ERR"},
    "operations": {
      "move": {
        "write": "1PA{mm}",
        "parameters": {"mm": {"type": "number", "minimum": 0, "maximum": 25, "unit": "mm"}},
        "completion": {"query": "1TS", "match": "(32|33|34)$", "timeout": 60}
      },
      "position": {"query": "1TP", "effect": "observe", "result": {"type": "number", "match": "^1TP(.+)$", "unit": "mm"}},
      "shutter_open": {"query": "s?", "effect": "observe", "result": {"map": {"ON": true, "OFF": false}}},
      "set_power": {"write": "p {mw}", "parameters": {"mw": {"type": "number", "unit": "mW"}}, "reply": {"match": "^OK$"}}
    }
  }
}
```

| `protocol` field / 字段 | Meaning / 含义 |
| --- | --- |
| `write_termination`, `read_termination` | Line endings of commands and replies when they differ; default `termination` / 命令与回复的结束符不同时分别指定 |
| `identify` | `false` when the instrument has no `*IDN?`; the `identify` operation is then absent / 仪器没有 `*IDN?` 时设为 `false` |
| `error_reply` | Pattern of replies that report an error. Such a reply fails the operation with its text; the instrument did not act, so nothing is held / 表示出错的回复模式；匹配时操作失败并给出回复原文，仪器并未执行，不挂起 |
| `write_reply` | `true` or `{"match": pattern}` when every write is answered with one line / 每条写入都有一行应答时设置 |
| `echo` | `true` when the instrument repeats each command before its reply / 仪器先回显命令时设为 `true` |
| `min_interval` | Shortest time between commands in seconds / 两条命令之间的最短间隔 |

| Operation field / 操作字段 | Meaning / 含义 |
| --- | --- |
| `result.match` | Pattern applied to the reply; the value is its first group, or the whole match / 作用于回复的模式，取第一个分组或整个匹配 |
| `result.type` | `number`, `integer`, `boolean` or `string` / 数值、整数、布尔或文本 |
| `result.scale`, `result.offset` | The value is reply × scale + offset, for counts and raw units / 换算：回复 × scale + offset |
| `result.map` | Value for each reply text, such as `{"ON": true, "OFF": false}` / 回复文本到取值的对应 |
| `reply` | On a write: `true`, `false` or `{"match": pattern}`, overriding `write_reply`; the line is returned as `reply` / 写入操作的应答，覆盖 `write_reply` |
| `completion.match` | Pattern the completion reply must match, in place of `equals` / 完成状态回复需匹配的模式，可替代 `equals` |

Patterns are Python regular expressions, searched within the reply. A model description can carry `protocol` beside its operations, so one description serves every unit of a model. Checksummed or binary framing needs a Python driver. / 模式为 Python 正则表达式，在回复中查找。型号说明可同时携带 `protocol`，同一型号的每台设备共用一份说明。带校验和或二进制帧的协议仍需 Python 驱动。

## Modbus TCP and RTU

Use `driver: "modbus"`. TCP configuration specifies `host`, `port` (default 502) and `device_id` (default 1). RTU specifies `transport: "serial"`, `port`, `baudrate`, `parity` and `stopbits`. Logical devices on the same bus share a connection and an automatically named scheduling resource. Install the `devices` extra for PyModbus.

使用 `modbus` 驱动，可接入寄存器与线圈接口的温控器、PLC、电源、传感器和执行器。同一总线上的逻辑设备共用连接，并自动按总线顺序通信。地址使用协议中的零起始地址；寄存器表中的编号需要按型号手册转换。

Built-ins: `read_holding_registers`, `read_input_registers`, `read_coils`, `read_discrete_inputs`, `write_register`, `write_registers`, `write_coil`. Named operations select a `function`, fixed `arguments`, a `scale`, `offset` and optional `unit`. `type` states how the value is held: `uint16` (default), `int16`, `uint32`, `int32`, `float32`, `uint64`, `int64` or `float64`; values wider than one register are read from consecutive registers, with `word_order` (`big`: high word first, the default) and `byte_order` as the model's register table specifies. A read of several values returns them in `decoded`. Named writes map a physical `value_parameter` back to the register value; values wider than one register use `write_registers`. / 具名操作用 `type` 说明数值在寄存器中的存放方式，跨寄存器的 32 位、64 位整数和浮点数按 `word_order`、`byte_order` 读写，以型号的寄存器表为准。

See [modbus-temperature.json](../../examples/iop/modbus-temperature.json). A write acknowledgement reports receipt by the device; reaching a temperature, position or flow target requires subsequent measurements in the experiment program or a device-specific operation. The adapter sets communication retries to zero so an unanswered write is not automatically repeated.

写入应答表示设备已收到请求。达到温度、位置或流量目标，需要实验程序继续读取或由专用操作等待完成。说明文件中的地址、比例和单位均需与真实型号对应。

## HTTP JSON

Use `driver: "http"` with `config.base_url`, optional `headers` and `timeout`. Each operation specifies `method`, `path`, optional `query` and `body`, and optional `result_path` as a list of JSON keys or indices.

`${parameter}` in JSON bodies preserves the input's JSON type. `{parameter}` in a URL path is URL-encoded. An operation returns the response together with the selected value, time and optional unit. See [http-sensor.json](../../examples/iop/http-sensor.json). Asynchronous HTTP jobs require an experiment to poll the device's status operation; a successful request alone does not imply physical completion.

HTTP 驱动适用于已有 JSON 接口的仪器和设备服务器。参数、路径和返回字段写在仪器说明中。设备返回异步任务时，由实验程序继续查询其状态。

## OPC UA

Use `driver: "opcua"` for controllers, process equipment and instruments with an OPC UA server, including LADS analytical instruments. Install the `opcua` extra. / 带 OPC UA 服务的控制器、过程设备和仪器（含 LADS 分析仪器）以 `driver: "opcua"` 登记，需安装 `opcua` 扩展。

```json
{
  "id": "reactor",
  "driver": "opcua",
  "config": {
    "endpoint": "opc.tcp://192.168.1.50:4840",
    "nodes": ["ns=2;s=Reactor"],
    "username": "iop",
    "password_env": "REACTOR_OPCUA_PASSWORD",
    "unconfirmed_change": "hold"
  }
}
```

| Field / 字段 | Meaning / 含义 |
| --- | --- |
| `endpoint` | Server address / 服务地址 |
| `nodes` | NodeIds of the objects to use; default: every object the vendor placed under Objects / 使用的对象节点，默认取 Objects 下全部厂商对象 |
| `depth` | Object levels walked below each node, default 6 / 向下遍历的对象层数 |
| `username`, `password_env` | User name, and the environment variable of the service that holds the password; the password is not stored in the configuration / 用户名，以及服务环境中保存密码的变量名；密码不写入配置 |
| `security` | Security string such as `Basic256Sha256,SignAndEncrypt,client.der,client.pem` / 安全策略、模式与证书 |
| `timeout` | Request timeout in seconds, default 5 / 请求超时 |
| `unconfirmed_change` | `hold`: a write or method call sent without a reply holds the device, as for SCPI / 写入或方法调用发出后无应答时挂起设备 |

Each readable variable becomes `get.<path>` (observe), each writable variable `set.<path>` with one `value` argument, and each method `call.<path>` with its input arguments; `snapshot` reads every scalar variable together. Paths are browse names joined with dots, starting at the node. Types come from the data type, `minimum` and `maximum` from EURange, `unit` from EngineeringUnits and `enum` from EnumStrings or EnumValues, so out-of-range values are refused before a request is sent. Numeric arrays return the shared array description and are saved as files. A Bad status fails the read; an Uncertain status is returned in `status`, and `time` is the server's source timestamp. `identity` comes from the Device Integration properties Manufacturer, Model and SerialNumber of a single node, otherwise from the server's build information. Structures and other values IOP cannot write stay readable; their set and call operations are listed in `unsupported`. A device holds at most 2000 variables and methods; choose narrower nodes on larger servers and use `allowed_operations` to expose a subset. / 可读变量生成 `get.<路径>`，可写变量生成 `set.<路径>`，方法生成 `call.<路径>`；`snapshot` 一次读取全部标量变量。类型、范围、单位和枚举取自服务的地址空间，超出范围的值在发送前即被拒绝。数值数组使用统一的数组说明并保存为文件。状态为 Bad 时读取失败，Uncertain 记在 `status` 中；`time` 是服务给出的采样时间。

## EPICS Channel Access

Use `driver: "epics"` for process variables served by EPICS IOCs: motors, detectors, shutters, vacuum and power supplies at beamlines, accelerators, laser facilities and laboratories. Install the `epics` extra. / EPICS IOC 提供的过程变量（电机、探测器、快门、真空、电源等）以 `driver: "epics"` 登记，需安装 `epics` 扩展。

```json
{
  "id": "sample-stage",
  "driver": "epics",
  "config": {
    "prefix": "BL7:SMP:",
    "pvs": ["X.VAL", "X.RBV", "Y.VAL", "Y.RBV", {"name": "Acquire", "wait": false}],
    "unconfirmed_change": "hold"
  }
}
```

| Field / 字段 | Meaning / 含义 |
| --- | --- |
| `pvs` | PV names, or objects with `name`, `wait` and `put_timeout` / PV 名称或带选项的对象 |
| `prefix` | Prepended to each name; operation names omit it / 名称前缀，操作名不含前缀 |
| `timeout` | Connection and read timeout in seconds, default 5 / 连接与读取超时 |
| `put_timeout` | Longest wait for put completion in seconds, default 300 / 等待写入完成的最长时间 |
| `unconfirmed_change` | `hold`: a put that does not complete in time, or whose wait is cancelled, holds the device / 写入未在时限内完成或等待被取消时挂起设备 |

Each PV becomes `get.<name>` (observe) and, with write access, `set.<name>`; `snapshot` reads every scalar PV together. Units, control limits and enumeration strings come from the IOC, so values outside the control limits are refused before a put. A put waits for put-completion, so moving a motor record returns when the move has finished; `"wait": false` sends without waiting. Reads return the IOC timestamp in `time`, the enumeration `label`, and `alarm` with severity and status when not NO_ALARM; an INVALID value fails the read. Waveforms return the shared array description and are saved as files. Cancelling stops waiting; the record keeps processing, so stop a motor through its stop field. The service finds IOCs through the usual `EPICS_CA_ADDR_LIST` and related environment variables. / 每个 PV 生成 `get.<名称>`，可写的再生成 `set.<名称>`；单位、控制限和枚举取自 IOC，超出控制限的值在写入前即被拒绝。写入等待 put-completion，电机运动结束后才返回。读取结果带 IOC 时间戳、枚举名称和报警状态，INVALID 时读取失败。取消只停止等待，记录仍在处理，停止电机需写其停止字段。

## QCoDeS drivers / QCoDeS 驱动

[QCoDeS](https://microsoft.github.io/Qcodes/) provides drivers for many source-measure units, multimeters, network and spectrum analyzers, waveform generators, lock-in amplifiers, magnet supplies and temperature controllers. Install the `qcodes` extra and register a driver class with `driver: "qcodes"`. / QCoDeS 已有大量源表、万用表、网络与频谱分析仪、波形发生器、锁相、磁体电源和温控驱动；安装 `qcodes` 扩展后以 `driver: "qcodes"` 登记。

```json
{
  "id": "source",
  "driver": "qcodes",
  "execution": {"mode": "process"},
  "config": {
    "instrument": "qcodes.instrument_drivers.yokogawa:YokogawaGS200",
    "address": "TCPIP0::192.168.1.80::inst0::INSTR",
    "parameters": ["voltage", "current", "output", "source_mode", "*_range"],
    "exclude": ["*_register"]
  }
}
```

| Field / 字段 | Meaning / 含义 |
| --- | --- |
| `instrument` | Driver class as `module:Class` / 驱动类 |
| `address` | VISA address passed to the driver / 传给驱动的 VISA 地址 |
| `arguments` | Other keyword arguments of the driver class / 驱动类的其他关键字参数 |
| `parameters`, `exclude` | Patterns over dotted parameter paths such as `ch1.voltage`; default all / 按参数路径筛选，默认全部 |

Each gettable parameter becomes `get.<path>` with `effect: "observe"`; each settable parameter becomes `set.<path>` with one `value` argument. Types, `minimum`, `maximum`, `enum` and `unit` come from the parameter's QCoDeS validator, so the Runtime rejects out-of-range values before calling the driver. Parameters without a validator accept any JSON value. Array parameters return the shared array description; setpoints of a `ParameterWithSetpoints` become axes and are saved as their own arrays; setpoints also give their axis coordinates, `start` and `step` when evenly spaced and otherwise `coordinates` when there are at most 10,000, so views report them. Complex values are returned as `re` and `im`. `snapshot` returns the driver's cached snapshot without querying the instrument. The identity read by VISA drivers at connection appears in the description. / 可读参数生成 `get.<路径>`（观测），可设参数生成 `set.<路径>`；类型、范围、枚举和单位取自 QCoDeS 校验器，超出范围的值在调用驱动前即被拒绝。数组参数使用统一的数组说明，setpoints 作为坐标轴另存；setpoints 同时给出坐标轴的坐标：等间隔时为 `start` 和 `step`，否则在不超过 10,000 个时逐个列为 `coordinates`，视图因此带坐标。`snapshot` 读取缓存的状态快照。

`execution.mode: "process"` runs the driver in its own process, so a stalled VISA session affects only that instrument. Driver methods and `Function` objects are not exposed as operations; write a Python driver or an experiment program for them. A `set` result reports the requested value; read the parameter for the instrument's value. / 建议以独立进程运行。驱动的方法和 `Function` 不生成操作；`set` 返回的是请求值，实际值以读回为准。

## Automatic registration / 自动登记

`config.json` can contain discovery `profiles`. Each profile requires a transport and exact metadata matches. Placeholders in its device record are filled from the discovered connection. / 发现规则按连接类型及字段精确匹配，用发现的地址与序列号生成设备记录。

```json
{
  "match": {"transport":"serial","vid":1234,"pid":5678},
  "device": {
    "id":"meter-{serial_number}",
    "driver":"scpi",
    "config":{"transport":"serial","port":"{address}","baudrate":9600}
  }
}
```

Use values actually returned by `iop discover`; the numbers above are placeholders. A scan runs every `discovery_interval` seconds (default 30), and `iop discover` requests a scan immediately. A matching installed profile registers a new device once and retains it in the configuration. A discovery entry describes a connection, not necessarily an instrument. Existing device IDs are not automatically rebound to a changed address; update their configuration and restart.

使用 `iop discover` 返回的真实字段值。服务默认每 30 秒发现一次，已登记的设备不重复添加。设备地址发生变化时，修改配置并重启。

## Limit operations and their arguments / 限定可用操作与参数范围

A device record can list `allowed_operations`: name patterns such as `["read_*", "set_voltage"]` or `["get.*", "set.ch1.*"]`. Other operations disappear from the description and are refused on submission, for every driver and entry point. Use it to remove the generic SCPI `query` and `write` or Modbus register writes from production instruments, so the ranges of named operations cannot be bypassed. `identify` stays available for discovery. Patterns that match no operation are refused when the device is saved. / 设备记录可用 `allowed_operations` 列出允许的操作名模式；其余操作从说明中移除，提交时被拒绝，对所有驱动和入口一致生效。可用来去掉通用 `query`、`write` 等底层命令，使具名操作的参数范围不被绕过。`identify` 保留用于识别。

A device record can also set `limits` on the numeric arguments of its operations, for what this setup tolerates rather than what the instrument can do: `{"source": {"volts": {"maximum": 0.6, "note": "The diode overheats above 0.6 V"}}}`. The description then carries those bounds, the note under `limit`, so an agent reading it sees them before planning. Every operation is checked against them when it is submitted and again when a value read from earlier results is given at its start, so a sweep that would reach 0.61 V is refused before anything runs, with `volts is 0.61 V, above 0.6 V: The diode overheats above 0.6 V`, and so is a bound or adaptive value that would. A limit stays within what the instrument takes, on a numeric parameter of one of its operations; otherwise the device is not saved. With `allowed_operations` removing generic writes, no entry point goes beyond them. / 设备记录还可以用 `limits` 给操作的数值参数设定界限，表达本装置能承受的范围，而非仪器本身的能力，例如 `{"source": {"volts": {"maximum": 0.6, "note": "二极管超过 0.6 V 会过热"}}}`。说明随之带上这些界限，说明文字放在 `limit` 下，Agent 规划之前即可看到。每个操作在提交时、以及在开始时取得由先前结果给出的数值时都按界限核对：会到达 0.61 V 的扫描在任何操作运行之前就被拒绝，并给出 `volts is 0.61 V, above 0.6 V` 及说明；绑定或自适应给出的越界数值同样被拒绝。界限必须在仪器可接受的范围之内，且针对其某个操作的数值参数，否则设备不予保存。配合 `allowed_operations` 去掉通用写入，任何入口都无法越过这些界限。

## Calibration / 标定

A device record can carry `calibration`: `id` (required), `date`, `valid_until`, `certificate`, `by`, `notes`, `uncertainty` and `enforce`. Every operation records the calibration `id`; operations listed in `uncertainty` also record their entry, for example `{"value": 0.02, "unit": "V", "coverage_factor": 2}`. Acquisitions keep the full record through the device configuration version. After `valid_until`, operations are marked `calibration_expired` and the laboratory overview lists `renew_calibration`; with `enforce: true` new operations are refused with `calibration_expired`. Validity is evaluated when an operation is submitted. A date without a time ends at 23:59:59 UTC. Update the record through the device configuration; earlier operations keep the version they used. / 设备记录可附 `calibration`：证书编号、日期、有效期、不确定度等。每次操作记录所依据的标定编号及该操作的不确定度，采集记录通过设备配置版本保留完整标定信息。过期后操作被标记，实验室概况提示更新标定；设 `enforce: true` 则拒绝新的操作。有效期在提交操作时判断。

## Monitors / 状态监测

A device record can list `monitors`. Each reads one `effect: "observe"` operation every `interval_s` seconds (at least 0.1) and compares the value at `value` (a result path, default `["value"]`) with `minimum`, `maximum` or `equals`. A missing, non-numeric or non-finite value under a numeric limit, or a failed read, counts as `error`. / 设备记录可列出 `monitors`：按周期读取一个观测操作，将结果中的指定值与上下限或期望值比较；读不到、类型不符或为 NaN、无穷大时视为 `error`。

```json
"monitors": [
  {"id": "laser-power", "operation": "read_power", "interval_s": 1, "maximum": 0.05,
   "on_violation": "hold", "hold": ["laser", "beam-path"]}
]
```

State changes are recorded as `monitor.ok`, `monitor.violation` and `monitor.error` events, and the laboratory overview lists every monitor with its last value and `inspect_monitors` when one is not ok. With `on_violation: "hold"`, entering a violation or error holds the device and the devices or shared resources in `hold`. Experiments using them stop, as after an unconfirmed instrument action. Record what was observed through `/v1/resources/resolve`; the resources are then usable so the condition can be corrected, and a new violation after a return to ok holds again. The default `record` only records events. / 状态变化记为事件，实验室概况列出各监测的最新值；`hold` 模式下进入超限或读取失败时挂起该设备及 `hold` 中列出的设备和共享资源，使用它们的实验随即停止；登记观测结果后资源恢复可用，以便纠正；恢复正常后再次超限会再次挂起。

A monitor reads a device only while no operation or experiment reservation uses it; an experiment that reserves the device reads it itself. Monitors run in the service and are a software stop. Laser shutters, door interlocks and other protective functions remain in hardware. / 监测只在设备空闲、未被实验预留时读取；预留设备的实验自行读取。这是服务中的软件停止，激光快门、门联锁等保护仍由硬件执行。

## Manual steps / 人工步骤

Steps a person performs, such as mounting a sample, aligning by eye, reading an analog gauge or refilling a dewar, are operations of a `manual` device. They are scheduled, reserved and recorded with instrument operations, so an experiment waits for the person and then continues. / 由人完成的步骤（装样、目视对准、读指针表、补液氮等）是 `manual` 设备的操作，与仪器操作一起排程、预留和记录；实验在此等待，人完成后继续。

```json
{
  "id": "bench",
  "driver": "manual",
  "config": {
    "name": "Bench operator",
    "operations": {
      "load_sample": {
        "instruction": "Mount sample {sample} in holder A and close the lid",
        "parameters": {"sample": {"type": "string"}},
        "readings": {"mass_g": {"type": "number", "minimum": 0, "unit": "g"}}
      },
      "beam_on_target": {
        "instruction": "Is the beam centred on target T2?",
        "effect": "observe",
        "readings": {"centred": {"type": "boolean"}}
      }
    }
  }
}
```

`instruction` is what the person sees; `{name}` takes the step's argument of that name, described in `parameters`. `readings` describe the values to report, with the same fields as operation parameters. A running step appears in `GET /v1/manual-tasks`, MCP `manual_tasks`, Python `manual_tasks()` and the desktop and browser interface. Report it with `POST /v1/manual-tasks/{operation_id}` and `{"status": "done", "readings": {...}, "note": "", "by": ""}`, MCP `report_manual_step` or Python `report_manual`. / `instruction` 是给人看的说明，`{名称}` 取同名参数；`readings` 描述需要报告的读数，字段与操作参数相同。步骤运行时出现在待办列表中（HTTP、MCP、Python 和桌面与网页界面），由人报告结果。

| Status / 状态 | Recorded / 记录 |
| --- | --- |
| `done` | Readings are validated; the result holds `instruction`, `readings`, `note`, `by`, `time` and `source: "person"` / 读数按 `readings` 校验后写入结果 |
| `failed` | The operation and its experiment fail / 操作和实验失败 |
| `unsure` | A change step is treated like an unconfirmed instrument action: the device and the experiment's reserved resources are held until the observed state is recorded through `/v1/resources/resolve`. An `effect: "observe"` step only fails. / 改变状态的步骤与仪器动作未确认相同，挂起设备和实验预留的资源，登记观测结果后恢复；观测步骤只记为失败 |

Waiting does not occupy instrument workers. Cancelling the experiment withdraws the task. A report records what the reporter stated; an agent relays only what the person said. Manual devices run in the service, without `execution`. / 等待不占用仪器工作线程；取消实验即撤回待办。报告内容即报告人的陈述，Agent 只转述人的原话。

## Python libraries and vendor SDKs / Python 库与厂商 SDK

Cameras, spectrometers, motion stages and many other devices are controlled through Python packages: instrument libraries such as pylablib, PyMeasure and InstrumentKit, or a vendor's SDK. `driver: "python"` creates one object of such a package and binds named operations to its methods and attributes, so the package is used from a description without writing a driver class. / 相机、光谱仪、位移台等设备多通过 Python 包控制：pylablib、PyMeasure、InstrumentKit 等仪器库，或厂商 SDK。`driver: "python"` 创建该包的一个对象，把具名操作对应到它的方法和属性，只写说明，不写驱动类。

```json
{
  "id": "delay-stage",
  "driver": "python",
  "execution": {"mode": "process"},
  "config": {
    "object": "pylablib.devices.Thorlabs:KinesisMotor",
    "arguments": ["27000001"],
    "keywords": {"scale": "stage"},
    "operations": {
      "move_to": {
        "call": "move_to",
        "parameters": {"position": {"type": "number", "minimum": 0, "maximum": 0.025, "unit": "m"}},
        "completion": {"call": "is_moving", "equals": false, "timeout": 60},
        "on_cancel": {"call": "stop"}
      },
      "position": {"call": "get_position", "effect": "observe", "result": {"unit": "m"}}
    }
  }
}
```

The names above illustrate the format; use those documented by the package and its version. / 示例中的名称仅说明格式，以所用包及其版本的文档为准。

| Field / 字段 | Meaning / 含义 |
| --- | --- |
| `object` | `module:Class` or a factory function; called with `arguments` and `keywords` / 类或工厂函数，以 `arguments`、`keywords` 调用 |
| `context` | `true` to enter the object as a context manager and exit it on close / 以上下文管理器方式进入和退出 |
| `close` | Method called when the device closes; default `close` when present / 关闭时调用的方法 |
| `call` | Method called with the operation's parameters as keyword arguments. `positional` lists parameters passed by position; `fixed` gives constant keyword arguments / 调用的方法；`positional` 指定按位置传入的参数，`fixed` 给出固定参数 |
| `get`, `set` | Attribute read, or attribute written with the operation's single parameter / 读取属性，或用唯一的参数写入属性 |
| `completion` | A `call` or `get` read every `interval` seconds until it `equals` a value, within `timeout` / 周期读取直到等于指定值 |
| `on_cancel` | Call made when the operation is cancelled while waiting, such as `stop` / 等待中被取消时调用，如 `stop` |
| `result` | `unit`; `path` selects from the returned value; `items` names several returned values, each with its `path` and `unit` / 单位；`path` 从返回值中选取；`items` 为多个返回值分别命名 |

Names are dotted paths from the object, such as `channels.1.voltage`; private names are not reachable. NumPy arrays, including camera frames, return the shared array description and are saved as files; tuples, named tuples, dataclasses, enumerations and NumPy scalars become JSON values. All calls to the object, including its creation, run on one thread, as COM and DLL based SDKs require. `execution.mode: "process"` keeps a faulting SDK away from other instruments. With `unconfirmed_change: "hold"`, an exception raised by a change operation holds the device, including `TypeError` or `ValueError` raised inside the SDK; Python argument binding is checked before calling the method. Cancellation releases a change only after the configured stop call and completion readback confirm it stopped. / `hold` 模式下，SDK 调用后抛出的异常保留结果不明状态；参数绑定错误在调用前返回。取消后，通过停止调用和完成读回确认设备停止，才释放该操作。 / 名称是从对象出发的点分路径，私有名称不可访问。NumPy 数组（含相机图像）使用统一的数组说明并保存为文件。对该对象的全部调用（含创建）在同一线程执行，满足 COM 和 DLL 类 SDK 的要求。建议以独立进程运行。

## Python driver / Python 驱动

Implement these methods in an installed Python module:

```python
from ai4instruments.iop.drivers import Driver

class TemperatureProbe(Driver):
    def describe(self):
        return {
            "name": "Temperature probe",
            "mode": "hardware",
            "driver_version": "1.0",
            "operations": {
                "read_temperature": {
                    "description": "Read the probe temperature in kelvin",
                    "parameters": {}
                }
            }
        }

    def invoke(self, operation, arguments, cancelled):
        # Call your SDK here and return a JSON-compatible result.
        # Return only when this operation's documented completion occurs.
        raise NotImplementedError("Connect the probe SDK")

    def close(self):
        # Release the connection held by this driver.
        pass
```

Register with `{"id":"probe","driver":"my_instruments:TemperatureProbe","config":{}}`. The example intentionally leaves the SDK call to the device implementation. A complete runnable simulated driver is provided in [custom_driver.py](../../examples/iop/custom_driver.py). Install or place that module on the service's Python import path before registering it.

`self.id` identifies the instrument, `self.config` contains driver settings, and `self.registry` provides registered driver objects. Calls to the same device are serialized; different devices may execute on different threads. Driver methods must return JSON-compatible values with units and acquisition context where appropriate. NaN and infinite numbers, which instruments report for missing or overranged readings, are recorded as `"NaN"`, `"Infinity"` and `"-Infinity"`; a monitor treats them as a failed read. Use bounded communication timeouts and implement the `cancelled` event according to the device. `close()` releases persistent connections.

将驱动安装在服务使用的 Python 环境中，再以 `module:Class` 登记。同一设备不会同时执行两次调用，不同设备可以在不同线程中执行。返回值使用可序列化为 JSON 的内容，并保留单位与采集信息。仪器在无读数或超量程时给出的 NaN 和无穷大记为 `"NaN"`、`"Infinity"`、`"-Infinity"`，状态监测将其视为读取失败。通信设置明确的超时时间，按设备行为响应取消请求，`close()` 关闭连接。

The parameter model supports `number`, `integer`, `string`, `boolean`, `array`, `object`, `minimum`, `maximum`, `enum`, `default`, `required` and descriptive `unit`. Required defaults to true; fields with a default may be omitted. Undeclared arguments are rejected. This is the runtime's current parameter format, not a complete JSON Schema implementation.

参数格式支持以上字段；有默认值的参数可以省略，未声明的参数不传入设备。数组可定义 `items`、`minItems` 和 `maxItems`；复杂数据的含义由驱动说明。

## Sources / 依赖文档

- [pySerial port discovery](https://pyserial.readthedocs.io/en/latest/tools.html)
- [PyVISA resource discovery and communication](https://pyvisa.readthedocs.io/en/latest/introduction/communication.html)
- [PyModbus client](https://pymodbus.readthedocs.io/en/v3.11.1/source/client.html)
- [Python SQLite](https://docs.python.org/3/library/sqlite3.html)

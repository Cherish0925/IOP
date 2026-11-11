# Instrument descriptions and drivers · 仪器说明与驱动

[中文安装](README.zh-CN.md) · [Installation](README.md)

A description names an instrument's operations and their inputs. A driver performs those operations and reports when they have finished. The runtime keeps connections and schedules calls. / 仪器说明列出操作与输入，驱动执行并报告完成，常驻服务保持连接并安排调用。

## SCPI

`iop adapters`, Python `client.adapters()` and MCP `available_adapters` expose supported connection fields. TCP `source_address` selects a local IP when the computer has several network interfaces; omit it to use system routing. Serial configuration includes `baudrate`, `bytesize`, `parity`, `stopbits`, `xonxoff`, `rtscts` and `dsrdtr`. Use settings from the instrument manual. / 连接参数可直接查询；多网卡可指定本机 IP，串口参数按仪器手册填写。

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

These commands and ranges illustrate the format; use those specified for the actual model. The driver formats named parameters into a command, reads query responses and optionally polls completion. Numeric results include their configured unit and acquisition timestamp. / 示例中的命令和范围需要替换为实际型号的定义。

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

## Modbus TCP and RTU

Use `driver: "modbus"`. TCP configuration specifies `host`, `port` (default 502) and `device_id` (default 1). RTU specifies `transport: "serial"`, `port`, `baudrate`, `parity` and `stopbits`. Logical devices on the same bus share a connection and an automatically named scheduling resource. Install the `devices` extra for PyModbus.

使用 `modbus` 驱动，可接入寄存器与线圈接口的温控器、PLC、电源、传感器和执行器。同一总线上的逻辑设备共用连接，并自动按总线顺序通信。地址使用协议中的零起始地址；寄存器表中的编号需要按型号手册转换。

Built-ins: `read_holding_registers`, `read_input_registers`, `read_coils`, `read_discrete_inputs`, `write_register`, `write_registers`, `write_coil`. Named operations select a `function`, fixed `arguments`, a `scale`, `offset` and optional `unit`. Scalar reads can use signed 16-bit interpretation. Named writes can map a physical `value_parameter` back to a register value. Multi-register floating-point formats require a model-specific driver.

See [modbus-temperature.json](../../examples/iop/modbus-temperature.json). A write acknowledgement reports receipt by the device; reaching a temperature, position or flow target requires subsequent measurements in the experiment program or a device-specific operation. The adapter sets communication retries to zero so an unanswered write is not automatically repeated.

写入应答表示设备已收到请求。达到温度、位置或流量目标，需要实验程序继续读取或由专用操作等待完成。说明文件中的地址、比例和单位均需与真实型号对应。

## HTTP JSON

Use `driver: "http"` with `config.base_url`, optional `headers` and `timeout`. Each operation specifies `method`, `path`, optional `query` and `body`, and optional `result_path` as a list of JSON keys or indices.

`${parameter}` in JSON bodies preserves the input's JSON type. `{parameter}` in a URL path is URL-encoded. An operation returns the response together with the selected value, time and optional unit. See [http-sensor.json](../../examples/iop/http-sensor.json). Asynchronous HTTP jobs require an experiment to poll the device's status operation; a successful request alone does not imply physical completion.

HTTP 驱动适用于已有 JSON 接口的仪器和设备服务器。参数、路径和返回字段写在仪器说明中。设备返回异步任务时，由实验程序继续查询其状态。

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

Each gettable parameter becomes `get.<path>` with `effect: "observe"`; each settable parameter becomes `set.<path>` with one `value` argument. Types, `minimum`, `maximum`, `enum` and `unit` come from the parameter's QCoDeS validator, so the Runtime rejects out-of-range values before calling the driver. Parameters without a validator accept any JSON value. Array parameters return the shared array description; setpoints of a `ParameterWithSetpoints` become axes and are saved as their own arrays. Complex values are returned as `re` and `im`. `snapshot` returns the driver's cached snapshot without querying the instrument. The identity read by VISA drivers at connection appears in the description. / 可读参数生成 `get.<路径>`（观测），可设参数生成 `set.<路径>`；类型、范围、枚举和单位取自 QCoDeS 校验器，超出范围的值在调用驱动前即被拒绝。数组参数使用统一的数组说明，setpoints 作为坐标轴另存。`snapshot` 读取缓存的状态快照。

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

## Limit available operations / 限定可用操作

A device record can list `allowed_operations`: name patterns such as `["read_*", "set_voltage"]` or `["get.*", "set.ch1.*"]`. Other operations disappear from the description and are refused on submission, for every driver and entry point. Use it to remove the generic SCPI `query` and `write` or Modbus register writes from production instruments, so the ranges of named operations cannot be bypassed. `identify` stays available for discovery. Patterns that match no operation are refused when the device is saved. / 设备记录可用 `allowed_operations` 列出允许的操作名模式；其余操作从说明中移除，提交时被拒绝，对所有驱动和入口一致生效。可用来去掉通用 `query`、`write` 等底层命令，使具名操作的参数范围不被绕过。`identify` 保留用于识别。

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

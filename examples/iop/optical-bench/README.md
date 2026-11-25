# Fiber-coupled laser bench · 光纤耦合激光平台

A laser is steered by a two-axis mirror into a fiber; a power meter reads the fiber output and a camera images it. The bench is **simulated** by the `simulated-optics` driver, but its readings follow the physics an experimentalist works against every day:

| Effect | Model | Kept as |
| --- | --- | --- |
| Diode warm-up | Temperature and power approach their set values with one time constant (`warmup_s`, 300 s) after switch-on | `laser.warm`, an instrument condition: wait about five time constants |
| Thermal drift of the table | The beam's best position walks by `drift_mrad_per_h` (0.3 mrad/h) | `fiber.coupled`: coupled power above 8 mW, restored by `tune` of the mirror's two tilts |
| Mechanical disturbance | `table.disturb` rings the table down with `quiet_s` (5 s) and displaces the beam | `table.quiet` before the coupling; the operator reports a knock |
| Fiber coupling | Gaussian in the mirror's distance from the best position, 0.25 mrad acceptance, 85 % at best | — |
| Camera saturation | 12-bit pixels clip at 4095 counts; 2 ms saturates at the start | `image.linear`: peak between 1500 and 3700 counts, restored by `tune` of the exposure |
| Dark current | Doubles every 6 K; the sensor warms after start | `image.dark`: a reference kept with each experiment, renewed after a new exposure |
| Power meter zero | A zero taken with light on offsets every later reading | The operation's description; block the beam first |

`after` puts them in the order the bench is brought up: warm-up and stillness, then coupling, then exposure, then its dark level. `disturbances` names what an operator reports at this bench, a knock, an opened enclosure, a reconnected fiber, a restarted laser, and the conditions each acts on; what depends on them follows.

## Run it

From the repository root, use a separate demonstration directory and an automatically assigned port. Keep using this directory when returning to the example. / 在仓库根目录运行，使用独立的演示目录和自动分配的端口；下次继续时仍使用这个目录。

```sh
iop --home ./runs/iop-demo init --port 0
iop --home ./runs/iop-demo start
iop --home ./runs/iop-demo register examples/iop/optical-bench/devices.json
iop --home ./runs/iop-demo platform apply examples/iop/optical-bench/platform.json
iop --home ./runs/iop-demo conditions --platform fiber-bench --maintain
```

The default simulated warm-up takes 25 minutes; for a quick try, add `"warmed": true` to the laser's config, or set `warmup_s` to a few seconds and the wait in `laser.warm` to five times that, before registering. Then:

```sh
iop --home ./runs/iop-demo conditions --platform fiber-bench
iop --home ./runs/iop-demo call table disturb --arguments '{"severity": 1}'
iop --home ./runs/iop-demo conditions --platform fiber-bench --disturbance knock
iop --home ./runs/iop-demo conditions --platform fiber-bench --maintain
iop --home ./runs/iop-demo conditions fiber.coupled
```

An experiment that names `requires: ["image.linear", "image.dark"]` is accepted only while they hold, and records the dark level it relied on; with `upkeep` the Runtime keeps them between its operations.

## Acquire and return later · 测量与跨会话读取

The included submission names the platform, conditions, measurement, project and request. Run it after the conditions hold:

```sh
iop --home ./runs/iop-demo run examples/iop/optical-bench/acquire.json
iop --home ./runs/iop-demo runs --search fiber-bench-introduction
iop --home ./runs/iop-demo result RUN_ID
```

Replace `RUN_ID` with the returned ID. The image result includes the peak, saturated-pixel count and sensor temperature; the run retains the dark reference used. Repeating the unchanged submission returns the same run. For a new acquisition, copy the file and choose a new `request_id`; changes to the sample or settings belong to a new measurement. / 将 `RUN_ID` 换成返回的编号。图像结果含峰值、饱和像素数与传感器温度，实验保留当时的暗背景。相同提交返回原实验；重新测量时复制文件并更换 `request_id`。

For an agent, connect it to this demonstration directory with `iop connect --name fiber-demo --local --lab-home ABSOLUTE_DEMO_PATH`, install the generated host package, then:

| Task / 任务 | MCP calls / 调用 |
| --- | --- |
| Read the setup / 读取平台 | `lab_context`, `describe_platform(platform="fiber-bench")`, `describe_instrument(device="camera")` |
| Take an image / 采集图像 | `submit_experiment` with the fields of `acquire.json`, first with `preview=true`, then submit |
| Follow the result / 跟进结果 | `experiment_updates(run_id, wait=25)`, `experiment(run_id, include_result=true)` |
| Return in another conversation / 新会话找回 | `find_experiments(search="fiber-bench-introduction")`, then read the returned run |

The `keep` prompt can turn this acquisition into a saved method. This example uses simulated readings; the scattering transmission-matrix experiment needs its own instrument descriptions, pattern sequence, calibration and reconstruction program. / `keep` 提示可以将采集过程保存成方法。本例使用模拟读数；散射传输矩阵实验还需要相应设备说明、图案序列、标定和重建程序。

When finished with the demonstration service: `iop --home ./runs/iop-demo stop`. Its data remain available for the next session.

## Adapt to a real bench · 用于实际平台

Replace the device records with the bench's instruments; keep the conditions and change their operations, tolerances and times. Take tolerances from the error the measurement can bear, and validity times from the records `iop conditions NAME` accumulates. `iop guide priors` gives the questions that fill a condition and the conditions common to other apparatus.

将设备记录换成实际仪器，保留工作条件，按实际仪器修改其操作、容差与时间。容差由测量允许的误差确定，有效期依据 `iop conditions NAME` 积累的记录确定。`iop guide priors` 给出填写各字段时要问的问题，以及其他类型平台常见的工作条件。

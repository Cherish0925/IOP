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

```sh
iop init
iop start
iop register examples/iop/optical-bench/devices.json
iop platform apply examples/iop/optical-bench/platform.json
iop conditions --platform fiber-bench --maintain
```

The first pass waits 25 minutes for the laser, as a real diode would; for a quick try, add `"warmed": true` to the laser's config, or set `warmup_s` to a few seconds and the wait in `laser.warm` to five times that, before registering. Then:

```sh
iop conditions --platform fiber-bench                       # all hold
iop call table disturb --arguments '{"severity": 1}'        # someone knocks the table
iop conditions --platform fiber-bench --disturbance knock   # the operator reports it
iop conditions --platform fiber-bench --maintain            # waits it out, tunes the mirror again
iop conditions fiber.coupled                                # records, corrections that worked, drift trend
```

An experiment that names `requires: ["image.linear", "image.dark"]` is accepted only while they hold, and records the dark level it relied on; with `upkeep` the Runtime keeps them between its operations.

## Adapt to a real bench · 用于实际平台

Replace the device records with the bench's instruments; keep the conditions and change their operations, tolerances and times. Take tolerances from the error the measurement can bear, and validity times from the records `iop conditions NAME` accumulates. `iop guide priors` gives the questions that fill a condition and the conditions common to other apparatus.

将设备记录换成实际仪器，保留工作条件，按实际仪器修改其操作、容差与时间。容差由测量允许的误差确定，有效期依据 `iop conditions NAME` 积累的记录确定。`iop guide priors` 给出填写各字段时要问的问题，以及其他类型平台常见的工作条件。

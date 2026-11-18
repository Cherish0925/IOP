# IOP TypeScript client

Requires Node.js 20+ or a browser with Fetch, AbortSignal.timeout and crypto.randomUUID. Build from source with `npm ci && npm run build`; install a release archive with `npm install ./ai4instruments-iop-0.8.5.tgz`.

```typescript
import { IOP } from '@ai4instruments/iop';
const lab = new IOP('http://127.0.0.1:8765', process.env.IOP_TOKEN);
console.log(await lab.platforms());
const run = await lab.submit([
  {id:'read', device:'meter', operation:'measure'}
], {platform:'materials-bench'});
console.log(await run.result());
```

`Run.wait({signal})` stops waiting when its signal is aborted. Use `Run.cancel()` to request cancellation of the experiment itself. `Run.watch(cursor)` resumes ordered event retrieval. Both clients support platform setup, operations, experiment packages, events, results and files. `lab.submitPackage(bytes, options)` uploads a ZIP; `run.invoke()` issues an operation from an already-running experiment package; `run.saveResult(value)` saves its result. `run.upload(name, blob)` streams a file and `run.download(id)` returns a Fetch response whose body can be streamed. `run.operations(cursor, limit)` pages through persistent history.

`lab.configureResources({cpu:16, solver:2})` sets capacities; a workflow step or submission can declare `requirements`. Steps can use `bindings` to transfer a previous result into an argument. `lab.adapters()` lists installed drivers, `lab.adapterReference(name)` returns the record fields and an example for one, and `lab.reconnect(device)` reconnects an idle device.

The integration test uploads a Node.js experiment package to an actual IOP service, calls an instrument operation, uploads a binary file and reads its result and events.

For adaptive experiments, use `startExperiment`, `run.batch`, `run.saveAcquisition`, then `run.finish`. See [continuous experiments](../../../docs/runtime/external-experiments.md). `lab.release(device)` hands an idle connection to another application; `lab.reconnect(device)` restores it.

`lab.readArray(operationId, {points: 64})` tells what an array an operation returned says: extremes with their index and coordinate, mean, spread, and an envelope or exact values; `along`, `at`, `start`/`stop` and `low`/`high` choose what to read; `stream.readArray(options)` reads a numeric data stream the same way, and `run.readArray({field})` an array the experiment's result holds. A step with `sweep` runs at each value of its parameters; `run.call(operation, args, {sweep})` returns the named results gathered into arrays. See [reading arrays](../../../docs/runtime/drivers.md#reading-arrays--读取数组) and [sweeps](../../../docs/runtime/operations.md#sweeps--扫描).

`lab.manualTasks()` lists steps of `manual` devices waiting for a person; `lab.reportManual(operationId, 'done', readings)` records one (`failed` and `unsure` also). `lab.resolveResources(resources, observation)` returns held resources to use after recording what was observed. See [manual steps](../../../docs/runtime/drivers.md#manual-steps--人工步骤).

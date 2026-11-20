import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const home = await mkdtemp(join(tmpdir(), "iop-interface-"));
const images = process.env.IOP_UI_SCREENSHOTS;
if (images) await mkdir(images, { recursive: true });
const child = spawn(
  process.env.PYTHON || "python",
  [
    "-u",
    "-c",
    `import sys
from ai4instruments.iop.config import initialize,save_json,home_path
from ai4instruments.iop.engine import Engine
from ai4instruments.iop.cli import serve
import socketserver, threading
import ai4instruments.iop.discovery as discovery
import array, math
from ai4instruments.iop import Driver, arrays
class Analyzer(Driver):
    def describe(self): return {'operations':{'sweep':{'parameters':{},'effect':'observe'},'image':{'parameters':{},'effect':'observe'}}}
    def invoke(self, operation, arguments, context):
        if operation == 'image':
            spot = array.array('f', [math.exp(-((x - 40) ** 2 + (y - 20) ** 2) / 60) for y in range(48) for x in range(64)])
            axes = [{'name':'y','unit':'m','start':0,'step':2e-6},{'name':'x','unit':'m','start':0,'step':2e-6}]
            return arrays.result(spot.tobytes(), arrays.check_result({'dtype':'float32','shape':[48,64],'axes':axes,'unit':'W'}), context, 'image')
        dip = array.array('d', [1 - 0.8 / (1 + ((i - 600) / 20) ** 2) for i in range(1001)])
        axis = {'name':'frequency','unit':'Hz','start':1e9,'step':1e5}
        return arrays.result(dip.tobytes(), arrays.check_result({'dtype':'float64','axes':[axis]}), context, 'trace')
p=home_path(sys.argv[1]); c=initialize(p,False,0);c['discovery_interval']=0;c['resource_pools']={'cpu':8,'gpu':2};save_json(p/'config.json',c)
class Instrument(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            response='Stanford_Research_Systems,SR830,SIM-830,1.0' if line.strip()==b'*IDN?' else '1.25'
            self.wfile.write((response+chr(10)).encode());self.wfile.flush()
instrument=socketserver.ThreadingTCPServer(('127.0.0.1',0),Instrument);instrument.daemon_threads=True
threading.Thread(target=instrument.serve_forever,daemon=True).start()
def inventory(*_, **__):
    return discovery.normalize({'connections':[{'transport':'serial','address':'COM7','name':'Simulated serial connection','manufacturer':'Simulation','serial_number':'SIM-007','status':'discovered','provider':'serial'}, {'transport':'tcp','address':'127.0.0.1:'+str(instrument.server_address[1]),'name':'SR830 protocol simulator','protocol':'scpi','provider':'lxi-network','connection':{'transport':'tcp','host':'127.0.0.1','port':instrument.server_address[1]}}],'providers':[{'name':'serial','available':True},{'name':'lxi-network','available':True}]})
discovery.discover=inventory
serve(p)
`,
    home,
  ],
  {
    cwd: root,
    env: { ...process.env, PYTHONPATH: join(root, "src") },
    stdio: ["ignore", "pipe", "pipe"],
  },
);
let browser, endpoint, token;
let stderr = "";
child.stderr.on("data", (data) => (stderr += data.toString()));
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
try {
  endpoint = await new Promise((resolve, reject) => {
    let out = "";
    child.stdout.on("data", (data) => {
      out += data;
      const match = out.match(/http:\/\/127\.0\.0\.1:\d+/);
      if (match) resolve(match[0]);
    });
    child.once("exit", (code) =>
      reject(Error("Service exited " + code + " " + stderr)),
    );
  });
  token = JSON.parse(await readFile(join(home, "config.json"), "utf8")).token;
  const api = async (path, body) => {
    const r = await fetch(endpoint + "/v1/" + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: "Bearer " + token,
        "Content-Type": "application/json",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await r.json();
    assert.ok(r.ok, JSON.stringify(data));
    return data;
  };
  browser = await chromium.launch({
    ...(process.env.IOP_CHROMIUM
      ? { executablePath: process.env.IOP_CHROMIUM }
      : {}),
    args: ["--no-sandbox", "--no-proxy-server"],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1050 },
    colorScheme: "light",
  });
  let errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(
    (value) => sessionStorage.setItem("iop-token", value),
    token,
  );
  await page.goto(endpoint);
  await page.waitForFunction(
    () =>
      document.querySelector("#service-caption")?.textContent ===
      "运行服务在线",
  );
  assert.equal(await page.locator("#nav-devices").innerText(), "0");
  assert.ok(
    !(await page.locator("body").innerText()).includes("[object Object]"),
  );
  if (images) await page.mouse.move(0, 0);
  if (images)
    await page.screenshot({
      path: join(images, "overview-empty.png"),
      fullPage: true,
      style: "#toast{visibility:hidden}",
    });
  await page.click("#overview-discover");
  await page.waitForSelector('[data-register="0"]');
  assert.equal(await page.locator("#discovery-section pre").count(), 0);
  // The connections can be listed before identification of the SR830 has finished.
  const identifying = performance.now() + 10000;
  let identified;
  do {
    identified = (await api("devices")).find(
      (d) => d.identity?.model === "SR830",
    );
    if (identified) break;
    await pause(50);
  } while (performance.now() < identifying);
  assert.ok(identified);
  await page.click(`[data-device="${identified.id}"]`);
  assert.ok(
    (await page.locator(".instrument-identity").innerText()).includes(
      "SIM-830",
    ),
  );
  await page.selectOption("#device-operation", "read_x");
  await page.click("#device-invoke");
  await page.waitForSelector("#device-response .badge.completed");
  assert.ok(
    (await page.locator("#device-response").innerText()).includes("1.25"),
  );
  await page.click("#device-release");
  await page.waitForFunction(
    () => document.querySelector("#device-invoke")?.disabled,
  );
  assert.equal(
    (await api("devices/" + identified.id)).status.state,
    "released",
  );
  await page.click("#device-reconnect");
  await page.waitForFunction(
    () => !document.querySelector("#device-invoke")?.disabled,
  );
  assert.equal((await api("devices/" + identified.id)).status.state, "ready");
  if (images)
    await page.screenshot({
      path: join(images, "instrument-identified.png"),
      fullPage: true,
      style: "#toast{visibility:hidden}",
    });
  await page.click("#model-description");
  await page.waitForSelector("#model-choice");
  assert.ok(
    (await page.locator("#model-choice").innerText()).includes("srs-sr830"),
  );
  if (images)
    await page.screenshot({
      path: join(images, "installed-models.png"),
      fullPage: true,
    });
  await page.click("#model-apply");
  await page.waitForSelector("#model-choice", { state: "hidden" });
  await page.click(`[data-device="${identified.id}"]`);
  await page.click("#device-edit");
  await page.waitForSelector("#configuration-json");
  await page.fill('[data-config-field="name"]', "Laboratory lock-in");
  if (images)
    await page.screenshot({
      path: join(images, "instrument-configuration.png"),
      fullPage: true,
      style: "#toast{visibility:hidden}",
    });
  await page.click("#configuration-save");
  await page.waitForSelector("#modal", { state: "hidden" });
  assert.equal(
    (await api("devices/" + identified.id)).name,
    "Laboratory lock-in",
  );
  assert.ok((await api("devices/" + identified.id + "/history")).length >= 2);
  await page.click('[data-connection="0"]');
  assert.ok((await page.locator("#modal").innerText()).includes("通信协议"));
  await page.click("#connection-configure");
  assert.equal(await page.locator("#reg-driver").inputValue(), "scpi-serial");
  assert.equal(await page.locator("#reg-port").inputValue(), "COM7");
  const generatedID = await page.locator("#reg-id").inputValue();
  assert.ok(generatedID);
  await page.fill("#reg-name", "模拟串口设备");
  await page.click("#reg-save");
  await page.waitForSelector(`[data-device="${generatedID}"]`);
  await api("devices", {
    id: "sim-axis",
    driver: "simulated",
    config: { kind: "stage" },
  });
  await api("devices", {
    id: "sim-sensor",
    driver: "simulated",
    config: { kind: "meter", stage: "sim-axis" },
  });
  await page.click("#refresh-button");
  await page.waitForSelector('[data-device="sim-axis"]');
  if (images) await page.mouse.move(0, 0);
  if (images)
    await page.screenshot({
      path: join(images, "instruments.png"),
      fullPage: true,
      style: "#toast{visibility:hidden}",
    });
  await page.click('[data-view="platforms"]');
  await page.click("#platform-new");
  await page.fill("#platform-name", "仪器协同平台（模拟）");
  await page.fill("#platform-id", "bench-01");
  await page.fill("#platform-description", "用于界面与执行流程的模拟平台");
  await page.check('[name="platform-device"][value="sim-axis"]');
  await page.check('[name="platform-device"][value="sim-sensor"]');
  await page.click("#connection-add");
  await page.fill('[data-port="from"]', "trigger-out");
  await page.fill('[data-port="to"]', "trigger-in");
  await page.fill("[data-kind]", "trigger");
  await page.click("#platform-save");
  await page.waitForSelector('[data-platform-edit="bench-01"]');
  assert.equal(
    (await api("platforms"))[0].definition.connections[0].kind,
    "trigger",
  );
  if (images) await page.mouse.move(0, 0);
  if (images)
    await page.screenshot({
      path: join(images, "platform.png"),
      fullPage: true,
      style: "#toast{visibility:hidden}",
    });
  await page.click("#new-run");
  await page.fill("#experiment-name", "串行与并行协同（模拟）");
  await page.selectOption("#experiment-platform", "bench-01");
  await page.selectOption('[data-step="step-1"] [data-step-operation]', "wait");
  await page.fill('[data-step="step-1"] [data-parameter="duration"]', "0.1");
  await page.click("#experiment-add");
  await page.selectOption(
    '[data-step="step-2"] [data-step-device]',
    "sim-sensor",
  );
  await page.selectOption('[data-step="step-2"] [data-step-operation]', "wait");
  await page.fill('[data-step="step-2"] [data-parameter="duration"]', "0.1");
  await page.click("#experiment-submit");
  await page.waitForSelector("#run-detail-summary .badge.completed");
  assert.equal(await page.locator(".operation-row").count(), 2);
  await page.click('[data-detail-tab="results"]');
  await page.waitForSelector("#detail-results .result-grid");
  if (images) await page.mouse.move(0, 0);
  if (images)
    await page.screenshot({
      path: join(images, "experiment.png"),
      fullPage: true,
      style: "#toast{visibility:hidden}",
    });
  await page.click("#modal-close");
  await page.click('[data-view="resources"]');
  await page.click("#resources-edit");
  await page.locator("[data-resource-count]").first().fill("12");
  await page.click("#resource-save");
  await page.waitForFunction(
    () => document.querySelector("#toast")?.textContent === "资源数量已保存",
  );
  assert.equal((await api("resources")).capacities.cpu, 12);
  await page.click('[data-view="overview"]');
  if (images) await page.mouse.move(0, 0);
  if (images)
    await page.screenshot({
      path: join(images, "overview.png"),
      fullPage: true,
      style: "#toast{visibility:hidden}",
    });
  await page.keyboard.press("Control+k");
  await page.fill("#palette-input", "sim-axis");
  await page.click('[data-palette="0"]');
  await page.waitForSelector("#device-operation");
  const deviceDownload = page.waitForEvent("download");
  await page.click("#device-export");
  const exportedDevice = await deviceDownload;
  const description = JSON.parse(
    await readFile(await exportedDevice.path(), "utf8"),
  );
  assert.equal(description.driver, "simulated");
  assert.equal(description.config.kind, "stage");
  await page.selectOption("#device-operation", "position");
  await page.click("#device-invoke");
  await page.waitForSelector("#device-response .badge.completed");
  assert.ok(
    (await page.locator("#device-response").innerText()).includes(
      "angle_actual",
    ),
  );
  if (images)
    await page.screenshot({
      path: join(images, "instrument-response.png"),
      fullPage: true,
      style: "#toast{visibility:hidden}",
    });
  await page.click("[data-device-run]");
  await page.waitForSelector("#run-detail-summary .badge.completed");
  await page.click("#modal-close");
  await page.click('[data-view="settings"]');
  const contextDownload = page.waitForEvent("download");
  await page.click("#agent-context-export");
  const exportedContext = await contextDownload;
  const labContext = JSON.parse(
    await readFile(await exportedContext.path(), "utf8"),
  );
  assert.equal(labContext.service.name, "IOP Runtime");
  assert.equal(labContext.platforms[0].id, "bench-01");
  assert.ok(!JSON.stringify(labContext).includes(token));
  await page.selectOption("#theme-select", "dark");
  await page.selectOption("#language-select", "en");
  assert.equal(
    await page.locator("#view-title").innerText(),
    "Connection & settings",
  );
  assert.equal(
    await page.locator("#service-caption").innerText(),
    "Service online",
  );
  await page.click('[data-view="overview"]');
  if (images) await page.mouse.move(0, 0);
  if (images)
    await page.screenshot({
      path: join(images, "overview-dark.png"),
      fullPage: true,
      style: "#toast{visibility:hidden}",
    });
  await page.setViewportSize({ width: 390, height: 844 });
  if (images) await page.mouse.move(0, 0);
  if (images)
    await page.screenshot({
      path: join(images, "mobile.png"),
      fullPage: true,
      style: "#toast{visibility:hidden}",
    });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
    false,
  );
  assert.ok(
    await page
      .locator("#sidebar")
      .evaluate((e) => e.getBoundingClientRect().right <= 0),
  );
  await page.click("#menu-toggle");
  await page.click("[data-view=devices]");
  await page.waitForSelector("[data-device=sim-axis]");
  assert.ok(
    await page
      .locator("#sidebar")
      .evaluate((e) => e.getBoundingClientRect().right <= 0),
  );
  await page.setViewportSize({ width: 1440, height: 1050 });
  const archive = execFileSync(process.env.PYTHON || "python", [
    "-c",
    String.raw`import io,sys,zipfile,json
b=io.BytesIO()
with zipfile.ZipFile(b,'w') as z:
 z.writestr('package.json',json.dumps({'name':'interface-program','command':['{python}','experiment.py'],'resume':True}))
 z.writestr('experiment.py',"from ai4instruments.iop import Client\nrun=Client().current_run()\nif not run.checkpoints():\n run.checkpoint({'next':0})\n raise SystemExit(17)\nrun.save_file('data.csv', b'time,value\\n0,12\\n')\nrun.save_result({'samples':1})\n")
sys.stdout.buffer.write(b.getvalue())`,
  ]);
  await page.click("#new-run");
  await page.fill("#experiment-name", "Program upload and file download");
  await page.click("[data-compose=package]");
  await page.setInputFiles("#experiment-archive", {
    name: "experiment.zip",
    mimeType: "application/zip",
    buffer: archive,
  });
  await page.click("#experiment-submit");
  await page.waitForSelector("#run-resume");
  await page.click("#run-resume");
  await page.waitForSelector("#run-detail-summary .badge.completed");
  await page.click("[data-detail-tab=results]");
  await page.waitForSelector("#detail-results .result-grid");
  assert.match(await page.locator("#detail-results").innerText(), /samples/);
  await page.click("[data-detail-tab=files]");
  const row = page.locator("#detail-files tr").filter({ hasText: "data.csv" });
  await row.waitFor();
  await page.evaluate(() => delete window.showSaveFilePicker);
  const downloadPromise = page.waitForEvent("download");
  await row.locator("button").click();
  const downloaded = await downloadPromise;
  assert.equal(downloaded.suggestedFilename(), "data.csv");
  assert.equal(
    await readFile(await downloaded.path(), "utf8"),
    "time,value\n0,12\n",
  );
  await page.click("#modal-close");
  await api("devices", {
    id: "bench",
    driver: "manual",
    config: {
      operations: {
        mount: {
          instruction: "Mount sample {sample}",
          parameters: { sample: { type: "string" } },
          readings: { mass_g: { type: "number", minimum: 0, unit: "g" } },
        },
      },
    },
  });
  const manual = (sample) =>
    api("runs", {
      name: "Manual step " + sample,
      steps: [
        {
          id: "mount",
          device: "bench",
          operation: "mount",
          arguments: { sample },
        },
      ],
    });
  const mounted = await manual("S-1");
  await page.click("#refresh-button");
  await page.click("#manual-open");
  assert.equal(
    await page.textContent(".manual-instruction"),
    "Mount sample S-1",
  );
  await page.fill("#reading-mass_g", "1.5");
  await page.click("[data-report=done]");
  await page.waitForSelector("#attention", { state: "hidden" });
  await pause(300);
  assert.deepEqual((await api("runs/" + mounted.id)).result.mount.readings, {
    mass_g: 1.5,
  });
  await manual("S-2");
  await page.click("#refresh-button");
  await page.click("#manual-open");
  await page.click("[data-report=unsure]");
  await pause(300);
  await page.click("#refresh-button");
  await page.click("#holds-open");
  await page.fill("#hold-observation", "S-2 seated in the holder");
  await page.click("#hold-save");
  await page.waitForSelector("#attention", { state: "hidden" });
  assert.deepEqual((await api("status")).resource_holds, []);
  await api("library", {
    definition: {
      id: "axis.settle",
      layer: "standard",
      description: "Wait for the axis to settle",
      steps: [
        {
          id: "wait",
          device: "sim-axis",
          operation: "wait",
          arguments: { duration: 0.01 },
        },
        { id: "shutter", device: "shutter", operation: "close" },
      ],
    },
  });
  await page.click("[data-view=overview]");
  await page.click("#refresh-button");
  await page.click('[data-operation="axis.settle"]');
  await page.waitForSelector("#modal[open]");
  const settle = await page.locator("#modal-body").innerText();
  assert.match(settle, /sim-axis/);
  assert.match(settle, /shutter: not registered/);
  await page.click("#modal-close");
  const bench = (await api("platforms")).find((p) => p.id === "bench-01");
  await api("platforms", {
    definition: {
      ...bench.definition,
      conditions: [
        {
          id: "axis.in_range",
          kind: "operating_point",
          description: "The axis reads a position inside its range",
          verify: { device: "sim-axis", operation: "position" },
          accept: { value: ["angle_actual"], minimum: -90, maximum: 90 },
        },
      ],
    },
    expected_revision: bench.revision,
  });
  await page.click('[data-view="platforms"]');
  await page.click("#refresh-button");
  await page.waitForSelector('[data-condition="axis.in_range"] .error-text');
  await page.click('[data-maintain="bench-01"]');
  await page.waitForFunction(
    () =>
      document.querySelector('[data-condition="axis.in_range"]') &&
      !document.querySelector('[data-condition="axis.in_range"] .error-text'),
  );
  // The identified SR830 brings the conditions of its model; the platform pass covers only its own instruments.
  const conditions = await api("conditions");
  assert.equal(conditions.find((c) => c.id === "axis.in_range").state, "holds");
  assert.ok(conditions.some((c) => c.id.endsWith(".reference_locked")));
  // Declared upkeep: the card says the Runtime keeps these conditions.
  const kept = (await api("platforms")).find((p) => p.id === "bench-01");
  await api("platforms", {
    definition: { ...kept.definition, upkeep: { ahead_s: 60 } },
    expected_revision: kept.revision,
  });
  await page.click("#refresh-button");
  await page.waitForFunction(() =>
    /自动保持|Upkeep/.test(
      document.querySelector('[data-maintain="bench-01"]')?.parentElement
        ?.textContent || "",
    ),
  );
  // An array result is drawn from its view: bucket extremes, the mean and coordinates.
  await api("devices", { id: "analyzer", driver: "__main__:Analyzer" });
  const sweep = await api("runs", {
    name: "Resonance sweep",
    steps: [{ id: "sweep", device: "analyzer", operation: "sweep" }],
  });
  await page.evaluate(
    (id) => document.dispatchEvent(new CustomEvent("iop:run", { detail: id })),
    sweep.id,
  );
  await page.waitForSelector("#detail-operation-list .array-plot svg polyline");
  assert.match(
    await page.locator("#detail-operation-list .array-plot").innerText(),
    /frequency 1 GHz[\s\S]*1\.1 GHz[\s\S]*0\.2 @ 1\.06 GHz/,
  );
  // The dip of the trace: the Lorentzian that fits it best, drawn over it, with its center and its width at
  // half height marked on the chart.
  await page.click('#detail-operation-list [data-array-line="min"]');
  await page.waitForSelector(
    "#detail-operation-list .array-plot polyline.fit",
    { state: "attached" },
  );
  assert.match(
    await page.locator("#detail-operation-list .array-plot").innerText(),
    /(洛伦兹|Lorentzian) 1\.06 GHz[^·]*· (半高宽|width) 4 MHz/,
  );
  assert.equal(
    await page
      .locator("#detail-operation-list .array-plot line.line-mark")
      .count(),
    2,
  );
  if (images)
    await page.screenshot({
      path: join(images, "array-view.png"),
      fullPage: true,
      style: "#toast{visibility:hidden}",
    });
  await page.click("#modal-close");
  // An image is drawn as a map of its two axes.
  const imaged = await api("runs", {
    name: "Beam image",
    steps: [{ id: "image", device: "analyzer", operation: "image" }],
  });
  await page.evaluate(
    (id) => document.dispatchEvent(new CustomEvent("iop:run", { detail: id })),
    imaged.id,
  );
  await page.waitForSelector("#detail-operation-list .array-map img");
  assert.match(
    await page.locator("#detail-operation-list .array-plot").innerText(),
    /y[\s\S]*x 0 m[\s\S]*126 µm[\s\S]*1 W @ 40 µm, 80 µm/,
  );
  if (images)
    await page.screenshot({
      path: join(images, "array-map.png"),
      fullPage: true,
      style: "#toast{visibility:hidden}",
    });
  // Peak on a map draws the line found in each row: the beam's center across, at 80 µm in each of 48 rows.
  await page.click('#detail-operation-list [data-array-line="max"]');
  // A straight line down the map has no width, so it is waited for as attached rather than visible.
  await page.waitForSelector("#detail-operation-list .array-ridge polyline", {
    state: "attached",
  });
  const ridge = (
    await page
      .locator("#detail-operation-list .array-ridge polyline")
      .getAttribute("points")
  )
    .split(" ")
    .map((point) => point.split(",").map(Number));
  assert.equal(ridge.length, 48);
  for (const [x] of ridge) assert.ok(Math.abs(x - 63.28125) < 0.01, String(x));
  // The whole array is saved as a NumPy file.
  const saving = page.waitForEvent("download");
  await page.click("#detail-operation-list [data-array-save]");
  const saved = await saving;
  assert.equal(saved.suggestedFilename(), "image.npy");
  assert.match(
    (await readFile(await saved.path())).subarray(0, 128).toString("latin1"),
    /^\x93NUMPY[\s\S]*'descr': '<f4'[\s\S]*'shape': \(48, 64\)/,
  );
  await page.click("#modal-close");
  // The experiment form sweeps a numeric parameter; the result gathers what each point returned.
  await page.click("#new-run");
  await page.fill("#experiment-name", "Turned axis");
  await page.selectOption(
    '[data-step="step-1"] [data-step-device]',
    "sim-axis",
  );
  await page.selectOption(
    '[data-step="step-1"] [data-step-operation]',
    "move_to",
  );
  await page.fill('[data-step="step-1"] [data-parameter="duration"]', "0");
  await page.click('[data-step="step-1"] [data-sweep-toggle="angle_deg"]');
  for (const [part, value] of [
    ["start", "-45"],
    ["stop", "45"],
    ["points", "19"],
  ])
    await page.fill(
      `[data-step="step-1"] [data-sweep="angle_deg"] [data-sweep-${part}]`,
      value,
    );
  await page.click("#experiment-submit");
  await page.waitForSelector("#run-detail-summary .badge.completed");
  await page.click('[data-detail-tab="results"]');
  await page.waitForSelector("#detail-results .array-plot svg polyline");
  assert.match(
    await page.locator("#detail-results .array-plot").innerText(),
    /step-1\.angle_actual · 19 · deg[\s\S]*angle_deg/,
  );
  await page.click("#modal-close");
  // An adaptive sweep is drawn at its coordinates: each angle read at itself gives a straight line.
  const adaptive = await api("runs", {
    name: "Adaptive turn",
    steps: [
      {
        id: "turn",
        device: "sim-axis",
        operation: "move_to",
        arguments: { duration: 0 },
        sweep: {
          angle_deg: {
            start: -45,
            stop: 45,
            points: 7,
            adapt: ["angle_actual"],
          },
        },
      },
    ],
  });
  await page.evaluate(
    (id) => document.dispatchEvent(new CustomEvent("iop:run", { detail: id })),
    adaptive.id,
  );
  await page.waitForSelector("#run-detail-summary .badge.completed");
  await page.click('[data-detail-tab="results"]');
  await page.waitForSelector("#detail-results .array-plot svg polyline");
  const drawn = (
    await page
      .locator("#detail-results .array-plot svg polyline")
      .first()
      .getAttribute("points")
  )
    .split(" ")
    .map((point) => point.split(",").map(Number));
  assert.equal(drawn.length, 7);
  for (const [x, y] of drawn)
    assert.ok(Math.abs(y - (146 - (x / 600) * 142)) < 0.01, `${x},${y}`);
  await page.click("#modal-close");
  // Returning and hysteresis scans use their whole coordinate range, even when their endpoints coincide.
  for (const angles of [
    [0, 1, 0],
    [0, 2, 1],
  ]) {
    const returning = await api("runs", {
      name: "Returning turn",
      steps: [
        {
          id: "turn",
          device: "sim-axis",
          operation: "move_to",
          arguments: { duration: 0 },
          sweep: { angle_deg: angles },
        },
      ],
    });
    await page.evaluate(
      (id) =>
        document.dispatchEvent(new CustomEvent("iop:run", { detail: id })),
      returning.id,
    );
    await page.waitForSelector("#run-detail-summary .badge.completed");
    await page.click('[data-detail-tab="results"]');
    await page.waitForSelector("#detail-results .array-plot svg polyline");
    const points = (
      await page
        .locator("#detail-results .array-plot svg polyline")
        .first()
        .getAttribute("points")
    )
      .split(" ")
      .map((point) => point.split(",").map(Number));
    assert.deepEqual(
      points.map(([x]) => x),
      angles.map((a) => (a * 600) / Math.max(...angles)),
    );
    assert.ok(points.flat().every(Number.isFinite));
    await page.click("#modal-close");
  }
  // A sweep stopped by its until shows how many of its points were skipped.
  const stopped = await api("runs", {
    name: "Stopped turn",
    steps: [
      {
        id: "turn",
        device: "sim-axis",
        operation: "move_to",
        arguments: { duration: 0 },
        sweep: { angle_deg: { start: 0, stop: 30, points: 4 } },
        until: { path: ["angle_actual"], above: 15 },
      },
    ],
  });
  await page.evaluate(
    (id) => document.dispatchEvent(new CustomEvent("iop:run", { detail: id })),
    stopped.id,
  );
  await page.waitForSelector("#run-detail-summary .badge.completed");
  assert.equal(
    await page.textContent("#run-detail-summary .run-states"),
    "Completed 3 · Skipped 1",
  );
  await page.click("#modal-close");
  // A numeric data stream is drawn per channel from its view.
  const streamed = await api("runs", {
    kind: "external",
    name: "Streamed voltages",
    request_id: "streamed-voltages",
  });
  await api("runs/" + streamed.id + "/streams/voltage", {
    definition: {
      format: "float32-le",
      sample_rate_hz: 1000,
      channels: [
        { name: "x", unit: "V" },
        { name: "y", unit: "V" },
      ],
    },
  });
  const samples = Float32Array.from({ length: 4000 }, (_, i) =>
    Math.sin(i / 80),
  );
  const appended = await fetch(
    endpoint + `/v1/runs/${streamed.id}/streams/voltage/chunks?sequence=0`,
    {
      method: "POST",
      headers: {
        Authorization: "Bearer " + token,
        "Content-Type": "application/octet-stream",
      },
      body: Buffer.from(samples.buffer),
    },
  );
  assert.ok(appended.ok);
  await page.evaluate(
    (id) => document.dispatchEvent(new CustomEvent("iop:run", { detail: id })),
    streamed.id,
  );
  await page.click('[data-detail-tab="files"]');
  await page.waitForFunction(
    () =>
      document.querySelectorAll("#detail-files .array-plot svg polyline")
        .length === 2,
  );
  assert.match(
    await page.locator("#detail-files .array-plot").first().innerText(),
    /voltage · x · float32-le[\s\S]*time 0 s[\s\S]*1\.999 s/,
  );
  await page.click("#modal-close");
  await api("runs/" + streamed.id + "/finish", {});
  const lasting = await api("runs", {
    name: "Continues after browser closes",
    steps: [
      {
        id: "wait",
        device: "sim-axis",
        operation: "wait",
        arguments: { duration: 1 },
      },
    ],
  });
  await page.close();
  const until = performance.now() + 10000;
  let continued;
  do {
    continued = await api("runs/" + lasting.id + "?details=false");
    if (!["queued", "running"].includes(continued.state)) break;
    await pause(50);
  } while (performance.now() < until);
  assert.equal(continued.state, "completed", JSON.stringify(continued));
  assert.deepEqual(errors, []);
  console.log(
    "Interface passed: discovery, registration, topology, workflow, results, resources, search, language, themes, mobile, program ZIP, file download, manual steps, held resources, saved operations, operating conditions, array and stream views, maps, sweeps and service continuity.",
  );
} finally {
  if (browser) await browser.close();
  if (endpoint)
    await fetch(endpoint + "/v1/shutdown", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + token,
        "Content-Type": "application/json",
      },
      body: "{}",
    }).catch(() => {});
  if (child.exitCode === null)
    await new Promise((resolve) => {
      const timer = setTimeout(() => child.kill(), 4000);
      child.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
    });
  await rm(home, { recursive: true, force: true });
}

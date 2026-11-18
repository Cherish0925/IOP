export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [
  ...root.querySelectorAll(selector),
];
const initial = new URLSearchParams(location.hash.slice(1));
export const state = {
  token: initial.get("token") || sessionStorage.getItem("iop-token") || "",
  view:
    initial.get("view") ||
    (location.hash.includes("=") ? "overview" : location.hash.slice(1)) ||
    "overview",
  lang: localStorage.getItem("iop-language") || "zh",
  desktop: initial.get("desktop") === "1",
  status: null,
  devices: [],
  platforms: [],
  runs: [],
  manual: [],
  library: [],
  conditions: [],
  resources: { capacities: {}, allocation: [] },
  inventory: null,
  ui: null,
  connected: false,
};
if (state.token) sessionStorage.setItem("iop-token", state.token);
if (initial.has("token")) history.replaceState(null, "", "#" + state.view);
export const t = (zh, en) => (state.lang === "en" ? en : zh);
export const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const paths = {
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  device: "M5 3h14v18H5z M8 7h8 M8 11h3 M8 16h.01 M12 16h.01 M16 16h.01",
  network: "M9 2h6v5H9z M3 17h6v5H3z M15 17h6v5h-6z M12 7v5 M6 17v-5h12v5",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  layers: "m3 7 9-5 9 5-9 5z M3 12l9 5 9-5 M3 17l9 5 9-5",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z",
  search: "M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15 M16 16l5 5",
  plus: "M12 5v14 M5 12h14",
  close: "m6 6 12 12 M18 6 6 18",
  refresh: "M20 8a8 8 0 1 0 0 8 M20 3v5h-5",
  monitor: "M3 4h18v13H3z M8 21h8 M12 17v4",
  "arrow-up-right": "M6 18 18 6 M6 6h12v12",
  arrow: "M4 12h16 m-6-6 6 6-6 6",
  play: "m8 4 12 8-12 8z",
  check: "m5 12 4 4L19 6",
  file: "M6 2h8l5 5v15H6z M14 2v6h5 M9 12h7 M9 16h7",
  download: "M12 3v12 m-5-5 5 5 5-5 M4 17v4h16v-4",
  upload: "M12 16V4 m-5 5 5-5 5 5 M4 17v4h16v-4",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2",
  code: "m8 5-6 7 6 7 M16 5l6 7-6 7 M14 3l-4 18",
  menu: "M4 6h16 M4 12h16 M4 18h16",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1 1 M18 18l1 1 M5 19l1-1 M18 6l1-1",
  moon: "M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11",
  box: "m3 7 9-5 9 5v11l-9 4-9-4z M3 7l9 5 9-5 M12 12v10",
  link: "m10 13 4-4 M8 15l-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0 M16 9l1-1a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0",
  info: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 10v7 M12 7h.01",
  chevron: "m9 5 7 7-7 7",
  stop: "M6 6h12v12H6z",
};
export const icon = (name) =>
  `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.box}"/></svg>`;
export function icons(root = document) {
  $$("[data-icon]", root).forEach((n) => {
    n.innerHTML = icon(n.dataset.icon);
  });
}
export function translate(root = document) {
  $$("[data-en]", root).forEach((n) => {
    n.dataset.zh ??= n.textContent;
    n.textContent = state.lang === "en" ? n.dataset.en : n.dataset.zh;
  });
  $$("[data-en-placeholder]", root).forEach((n) => {
    n.dataset.zhPlaceholder ??= n.placeholder;
    n.placeholder =
      state.lang === "en" ? n.dataset.enPlaceholder : n.dataset.zhPlaceholder;
  });
  document.documentElement.lang = state.lang === "en" ? "en" : "zh-CN";
}
export async function api(path, body, options = {}) {
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), options.timeout || 30000);
  try {
    const r = await fetch("/v1/" + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: "Bearer " + state.token,
        "Content-Type": "application/json",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    let v;
    try {
      v = await r.json();
    } catch {
      throw Error(
        t(
          "服务返回了无法读取的响应",
          "The service returned an unreadable response",
        ),
      );
    }
    if (!r.ok) {
      const error = Error(v.error || r.statusText);
      error.status = r.status;
      throw error;
    }
    return v;
  } catch (error) {
    if (error.name === "AbortError")
      throw Error(
        t(
          "请求尚未返回，请刷新查看当前状态",
          "The request timed out. Refresh to check its current state.",
        ),
      );
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
export function toast(text, error = false) {
  const el = $("#toast");
  el.textContent = text;
  el.dataset.error = error;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), error ? 8000 : 4000);
}
export async function busy(button, fn) {
  if (button?.disabled) return;
  const prior = button?.innerHTML;
  if (button) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  }
  try {
    return await fn();
  } catch (e) {
    toast(e.message, true);
    return null;
  } finally {
    if (button) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      if (prior !== undefined) button.innerHTML = prior;
    }
  }
}
export function modal(title, html, eyebrow = "") {
  const el = $("#modal");
  delete el.dataset.runDetail;
  el.classList.remove("wide", "inspector");
  $("#modal-title").textContent = title;
  $("#modal-eyebrow").textContent = eyebrow;
  $("#modal-body").innerHTML = html;
  icons(el);
  if (!el.open) el.showModal();
  return $("#modal-body");
}
export const closeModal = () => $("#modal").close();
// Arrays inside a result: [path, description, holder] with path "" for the result itself, as the service names them.
export function arraysIn(value, path = "") {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  const own =
    value.data &&
    typeof value.data === "object" &&
    typeof value.data.dtype === "string" &&
    Array.isArray(value.data.shape)
      ? [[path, value.data, value]]
      : [];
  return own.concat(
    ...Object.entries(value)
      .filter(([k]) => k !== "data" && k !== "values")
      .map(([k, v]) => arraysIn(v, path ? path + "." + k : k)),
  );
}
const SI = [
  "Hz",
  "V",
  "A",
  "s",
  "W",
  "m",
  "F",
  "H",
  "Ω",
  "T",
  "K",
  "J",
  "N",
  "Pa",
  "g",
];
export function quantity(value, unit = "") {
  if (value === null || value === undefined) return "—";
  const size = Math.abs(value);
  if (SI.includes(unit) && value !== 0 && (size >= 1e3 || size < 1)) {
    for (const [scale, prefix] of [
      [1e12, "T"],
      [1e9, "G"],
      [1e6, "M"],
      [1e3, "k"],
      [1, ""],
      [1e-3, "m"],
      [1e-6, "µ"],
      [1e-9, "n"],
      [1e-12, "p"],
    ])
      if (size >= scale)
        return `${Number((value / scale).toPrecision(5))} ${prefix}${unit}`;
  }
  return `${Number(value.toPrecision(5))}${unit ? " " + unit : ""}`;
}
// Viridis, a perceptually uniform scale that stays readable in grey and for colour-blind readers.
const SCALE = [
  [68, 1, 84],
  [59, 82, 139],
  [33, 145, 140],
  [94, 201, 98],
  [253, 231, 37],
];
function shade(t) {
  const x = Math.max(0, Math.min(1, t)) * (SCALE.length - 1),
    i = Math.min(SCALE.length - 2, Math.floor(x)),
    f = x - i;
  return SCALE[i].map((c, k) => Math.round(c + f * (SCALE[i + 1][k] - c)));
}
// The two ends of an axis followed, its first position first.
function ends(along, listed = along.coordinates) {
  if (along.low === undefined)
    return ["#" + along.start, "#" + (along.stop - 1)];
  const both = [along.low, along.high].map((v) =>
    quantity(v, along.unit || ""),
  );
  const falling =
    along.step < 0 ||
    (along.step === undefined &&
      listed?.length > 1 &&
      listed[0] > listed.at(-1));
  return falling ? both.reverse() : both;
}
// Two axes followed: rows down the first, columns across the second, as an image is read.
function map(view) {
  const s = view.summary;
  if (s.min === null)
    return `<small class="muted">${t("没有有限数值", "No finite values")}</small>`;
  const grid = view.values || view.envelope.mean,
    rows = grid.length,
    columns = grid[0].length,
    span = s.max - s.min || 1;
  const canvas = document.createElement("canvas");
  canvas.width = columns;
  canvas.height = rows;
  const context = canvas.getContext("2d"),
    image = context.createImageData(columns, rows);
  grid.forEach((row, r) =>
    row.forEach((v, c) => {
      if (v !== null)
        image.data.set(
          [...shade((v - s.min) / span), 255],
          4 * (r * columns + c),
        );
    }),
  );
  context.putImageData(image, 0, 0);
  const [down, across] = view.along,
    unit = view.unit || "",
    // Where an extreme lies: its coordinates when both axes have them, otherwise its index.
    place = (index, at) =>
      at?.every((c) => c !== null)
        ? at.map((c, k) => quantity(c, view.along[k].unit || "")).join(", ")
        : "[" + index.join(", ") + "]";
  const [top, bottom] = ends(down, view.envelope?.coordinate?.[0]),
    [left, right] = ends(across, view.envelope?.coordinate?.[1]);
  // Two lengths in the same unit keep their proportions, as an image of a beam does.
  const width = across.high - across.low,
    height = down.high - down.low,
    proportion =
      down.unit && down.unit === across.unit && width > 0 && height > 0
        ? ` style="max-width:calc(280px * ${width / height})"`
        : "";
  return `<div class="array-map"><div class="array-rows"><span>${esc(top)}</span><span>${esc(String(down.axis))}</span><span>${esc(bottom)}</span></div><div class="array-image"${proportion}><img src="${canvas.toDataURL()}" alt="${esc(String(down.axis))} × ${esc(String(across.axis))}"${proportion ? ` style="height:auto;aspect-ratio:${width / height}"` : ""}><div class="array-axis"><span>${esc(String(across.axis))} ${esc(left)}</span><span>${esc(right)}</span></div></div></div><div class="array-scale"><small>${esc(quantity(s.min, unit))}</small><i></i><small>${esc(quantity(s.max, unit))}</small></div><small class="muted">${t("最小", "min")} ${esc(quantity(s.min, unit))} @ ${esc(place(s.min_index, s.min_at))} · ${t("最大", "max")} ${esc(quantity(s.max, unit))} @ ${esc(place(s.max_index, s.max_at))} · ${t("均值", "mean")} ${esc(quantity(s.mean, unit))}${s.nonfinite ? " · " + s.nonfinite + t(" 个非有限值", " not finite") : ""}${
    view.at
      ? " · " +
        esc(
          Object.entries(view.at)
            .map(([k, v]) => k + "=" + v)
            .join(", "),
        )
      : ""
  }</small>`;
}
// Where a coordinate of the axis followed lies across a chart of n values or buckets, through its position;
// null when the axis lists its coordinates.
function chartX(view, n, width) {
  const along = view.along,
    position = along.step
      ? (c) =>
          along.start +
          (c - (along.step > 0 ? along.low : along.high)) / along.step
      : along.low === undefined
        ? (c) => c
        : null,
    starts = view.envelope?.position;
  if (!position) return null;
  return (c) => {
    const p = position(c);
    let i = p - along.start;
    if (starts) {
      const k = Math.max(
        0,
        starts.findLastIndex((s) => s <= p),
      );
      i = k + (p - starts[k]) / ((starts[k + 1] ?? along.stop) - starts[k]);
    }
    return n === 1 ? width / 2 : (i * width) / (n - 1);
  };
}
// One chart per array: a map along two axes, otherwise a line with the band between bucket minima and
// maxima and the mean or exact values.
function chart(view) {
  if (Array.isArray(view.along)) return map(view);
  const s = view.summary,
    width = 600,
    height = 150;
  if (s.min === null)
    return `<small class="muted">${t("没有有限数值", "No finite values")}</small>`;
  const series = view.values
    ? { min: view.values, max: view.values, mean: view.values }
    : view.envelope;
  const n = series.mean.length,
    span = s.max - s.min || 1;
  const x = (i) => (n === 1 ? width / 2 : (i * width) / (n - 1));
  const y = (v) => 4 + (1 - (v - s.min) / span) * (height - 8);
  const runs = [];
  series.mean.forEach((v, i) => {
    if (v === null) return runs.push(null);
    if (!runs.length || runs[runs.length - 1] === null) runs.push([]);
    runs[runs.length - 1].push(i);
  });
  const shapes = runs
    .filter(Boolean)
    .map((r) => {
      const band = view.values
        ? ""
        : `<polygon class="band" points="${r.map((i) => x(i) + "," + y(series.max[i])).join(" ")} ${r
            .slice()
            .reverse()
            .map((i) => x(i) + "," + y(series.min[i]))
            .join(" ")}"/>`;
      return `${band}<polyline points="${r.map((i) => x(i) + "," + y(series.mean[i])).join(" ")}"/>`;
    })
    .join("");
  const along = view.along,
    unit = view.unit || "",
    // Where an extreme lies: its coordinate, otherwise its position, or its index in an array of several axes.
    place = (index, at) =>
      at !== undefined
        ? quantity(at, along.unit || "")
        : index.length > 1
          ? "[" + index.join(", ") + "]"
          : "#" + index[0];
  const [first, last] = ends(
    along,
    along.coordinates || view.envelope?.coordinate,
  );
  // A line read at a peak or a dip: its center, and its width at half height, where the axis places them.
  const line = view.peak,
    lineX = line && chartX(view, n, width),
    half = line && y((line.value + line.base) / 2),
    marks = lineX
      ? `<line class="line-mark" x1="${lineX(line.center)}" x2="${lineX(line.center)}" y1="0" y2="${height}"/>${line.width !== undefined ? `<line class="line-mark" x1="${lineX(line.low)}" x2="${lineX(line.high)}" y1="${half}" y2="${half}"/>` : ""}`
      : "",
    lineText = line
      ? ` · ${line.value >= line.base ? t("峰", "peak") : t("谷", "dip")} ${esc(quantity(line.center, along.unit || ""))}${line.width !== undefined ? ` · ${t("半高宽", "width")} ${esc(quantity(line.width, along.unit || ""))}` : ""}`
      : "";
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${esc(String(along.axis))}">${shapes}${marks}</svg><div class="array-axis"><span>${esc(String(along.axis))} ${esc(first)}</span><span>${esc(last)}</span></div><small class="muted">${t("最小", "min")} ${esc(quantity(s.min, unit))} @ ${esc(place(s.min_index, s.min_at))} · ${t("最大", "max")} ${esc(quantity(s.max, unit))} @ ${esc(place(s.max_index, s.max_at))} · ${t("均值", "mean")} ${esc(quantity(s.mean, unit))} · σ ${esc(quantity(s.std, unit))}${s.nonfinite ? " · " + s.nonfinite + t(" 个非有限值", " not finite") : ""}${lineText}${
    view.at
      ? " · " +
        esc(
          Object.entries(view.at)
            .map(([k, v]) => k + "=" + v)
            .join(", "),
        )
      : ""
  }</small>`;
}
// One figure per array: source is the view's path under /v1, field the array in a result, at fixes axes
// such as a stream's channel, map names two axes to draw as a map, line offers the peak or dip of a line
// along the first axis, keep marks arrays that never change and name is what the whole array is saved as.
export function arrayFigure({
  source,
  field = "",
  caption,
  keep,
  at,
  map,
  line,
  name,
}) {
  const peak =
    (line && lines.get([source, field, at && JSON.stringify(at)].join("|"))) ||
    "";
  const toggles = line
    ? [
        [
          "max",
          t("峰", "Peak"),
          t("读出峰的中心和半高宽", "Read the peak's center and width"),
        ],
        [
          "min",
          t("谷", "Dip"),
          t("读出谷的中心和半高宽", "Read the dip's center and width"),
        ],
      ]
        .map(
          ([kind, label, title]) =>
            `<button class="array-tool" data-array-line="${kind}" aria-pressed="${peak === kind}" title="${title}">${label}</button>`,
        )
        .join("")
    : "";
  return `<figure class="array-plot" data-array-source="${esc(source)}" data-array-field="${esc(field)}" data-array-name="${esc(name || field || "array")}"${at ? ` data-array-at="${esc(JSON.stringify(at))}"` : ""}${map ? ` data-array-map="${esc(JSON.stringify(map))}"` : ""}${peak ? ` data-array-peak="${peak}"` : ""}${keep ? ' data-array-keep="1"' : ""}><figcaption><span>${caption}</span><span class="array-tools">${toggles}<button class="array-tool" data-array-save title="${t("保存整个数组，NumPy 可直接读取", "Save the whole array for NumPy")}">${icon("download")}.npy</button></span></figcaption><div class="array-chart"><small class="muted">${t("正在读取…", "Reading…")}</small></div></figure>`;
}
// Save a file the service sends, such as an artifact or an array as .npy, where the person chooses.
export async function save(path, name) {
  let handle;
  if ("showSaveFilePicker" in window)
    handle = await window.showSaveFilePicker({ suggestedName: name });
  const response = await fetch("/v1/" + path, {
    headers: { Authorization: "Bearer " + state.token },
  });
  if (!response.ok)
    throw Error(
      (await response.json().catch(() => ({}))).error ||
        t("文件读取失败", "Could not read the file"),
    );
  if (handle) {
    const writer = await handle.createWritable();
    await response.body.pipeTo(writer);
  } else {
    const url = URL.createObjectURL(await response.blob()),
      a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }
}
document.addEventListener("click", (e) => {
  const button = e.target.closest?.("[data-array-save]"),
    figure = button?.closest("figure");
  if (!figure) return;
  const field = figure.dataset.arrayField;
  busy(button, () =>
    save(
      figure.dataset.arraySource +
        ".npy" +
        (field ? "?" + new URLSearchParams({ field }) : ""),
      figure.dataset.arrayName + ".npy",
    ),
  );
});
// Peak or dip shown on a figure, until it is chosen again.
document.addEventListener("click", (e) => {
  const button = e.target.closest?.("[data-array-line]"),
    figure = button?.closest("figure");
  if (!figure) return;
  const kind =
    figure.dataset.arrayPeak === button.dataset.arrayLine
      ? ""
      : button.dataset.arrayLine;
  remember(lines, figureKey(figure), kind);
  if (kind) figure.dataset.arrayPeak = kind;
  else delete figure.dataset.arrayPeak;
  for (const toggle of figure.querySelectorAll("[data-array-line]"))
    toggle.setAttribute(
      "aria-pressed",
      String(toggle.dataset.arrayLine === kind),
    );
  drawArray(figure);
});
// The axes an array is drawn along: those of more than one position, other than re/im components. Two make a
// map, an image or a sweep over two parameters; one, the first, a line where a peak or a dip can be read.
function drawnAxes(data) {
  const named = (data.axes || []).length === data.shape.length;
  return data.shape
    .map((size, i) => [size, named ? data.axes[i].name : i, i])
    .filter(
      ([size], i) =>
        size > 1 &&
        !(
          named &&
          i === data.shape.length - 1 &&
          JSON.stringify(data.axes[i].labels) === '["re","im"]'
        ),
    );
}
export function mapAxes(data) {
  const axes = drawnAxes(data);
  return axes.length === 2 ? axes.map(([, name]) => name) : null;
}
export function lineAxis(data) {
  const axes = drawnAxes(data);
  return axes.length === 1 && axes[0][2] === 0;
}
// A completed operation's array is read once; a growing stream shows its last chart while one
// reading at a time renews it.
const charts = new Map(),
  recent = new Map(),
  reading = new Map(),
  lines = new Map();
function remember(map, key, value) {
  if (!map.has(key) && map.size >= 200) map.delete(map.keys().next().value);
  map.set(key, value);
}
const figureKey = (figure) =>
  [
    figure.dataset.arraySource,
    figure.dataset.arrayField,
    figure.dataset.arrayAt,
  ].join("|");
const chartKey = (figure) =>
  figureKey(figure) + "|" + (figure.dataset.arrayPeak || "");
function readChart(figure) {
  const width = figure.clientWidth || 600,
    query = new URLSearchParams(
      figure.dataset.arrayMap
        ? {
            along: figure.dataset.arrayMap,
            points: JSON.stringify([
              60,
              Math.max(16, Math.min(300, Math.round(width / 3))),
            ]),
          }
        : {
            points: String(Math.max(32, Math.min(600, Math.round(width / 2)))),
          },
    );
  if (figure.dataset.arrayField) query.set("field", figure.dataset.arrayField);
  if (figure.dataset.arrayAt) query.set("at", figure.dataset.arrayAt);
  if (figure.dataset.arrayPeak) query.set("peak", figure.dataset.arrayPeak);
  return api(figure.dataset.arraySource + "?" + query).then(chart);
}
function show(html, target) {
  html.then(
    (value) => (target.innerHTML = value),
    (error) =>
      (target.innerHTML = `<small class="muted">${esc(error.message)}</small>`),
  );
}
function drawArray(figure) {
  const key = chartKey(figure),
    target = figure.querySelector(".array-chart");
  if (figure.dataset.arrayKeep === "1") {
    if (!charts.has(key)) {
      const read = readChart(figure);
      read.catch(() => charts.delete(key));
      remember(charts, key, read);
    }
    return show(charts.get(key), target);
  }
  if (recent.has(key)) target.innerHTML = recent.get(key);
  if (!reading.has(key)) {
    const read = readChart(figure);
    read
      .then(
        (html) => remember(recent, key, html),
        () => {},
      )
      .finally(() => reading.delete(key));
    reading.set(key, read);
  }
  show(reading.get(key), target);
}
const observed = new Set();
const visible =
  "IntersectionObserver" in window
    ? new IntersectionObserver((entries) => {
        for (const entry of entries)
          if (entry.isIntersecting) {
            visible.unobserve(entry.target);
            observed.delete(entry.target);
            drawArray(entry.target);
          }
      })
    : null;
// Draw the array figures inside root once they are on screen. Views are rendered again every few
// seconds, so figures no longer in the page stop being observed.
export function drawArrays(root) {
  for (const figure of observed)
    if (!figure.isConnected) {
      visible.unobserve(figure);
      observed.delete(figure);
    }
  for (const figure of root.querySelectorAll("[data-array-source]"))
    if (
      !visible ||
      charts.has(chartKey(figure)) ||
      recent.has(chartKey(figure))
    )
      drawArray(figure);
    else {
      visible.observe(figure);
      observed.add(figure);
    }
}
export function operationResult(value, operationId) {
  if (value === undefined || value === null)
    return `<p class="inline-note">${t("等待仪器返回", "Waiting for the instrument")}</p>`;
  const found = operationId ? arraysIn(value) : [];
  if (found.length)
    return found
      .map(([path, data]) =>
        arrayFigure({
          source: "operations/" + encodeURIComponent(operationId) + "/array",
          field: path,
          caption: `${esc(path || t("数组", "Array"))} · ${esc(data.dtype)} ${esc(data.shape.join(" × "))}${data.unit ? " · " + esc(data.unit) : ""}`,
          keep: true,
          map: mapAxes(data),
          line: lineAxis(data),
          name: data.file?.replace(/\.[^./]*$/, "") || path,
        }),
      )
      .join("");
  const labels = {
    manufacturer: t("制造商", "Manufacturer"),
    model: t("型号", "Model"),
    serial_number: t("序列号", "Serial number"),
    firmware: t("固件", "Firmware"),
  };
  if (value.identity)
    return `<div class="identity-grid">${Object.entries(value.identity)
      .map(
        ([k, v]) =>
          `<div><small>${labels[k] || esc(k)}</small><strong>${esc(v)}</strong></div>`,
      )
      .join("")}</div><pre class="response-text">${esc(value.value)}</pre>`;
  if (value.value !== undefined)
    return `<pre class="response-text">${esc((typeof value.value === "object" ? JSON.stringify(value.value, null, 2) : String(value.value)).slice(0, 6000))}${value.unit ? " " + esc(value.unit) : ""}</pre>${String(value.value).length > 6000 ? `<small>${t("显示前 6000 字符，完整内容保存在实验记录中。", "Showing the first 6000 characters. The complete response is in the experiment record.")}</small>` : ""}`;
  if (value.sent === true)
    return `<p class="response-text">${t("命令已发送", "Command sent")} · <code>${esc(value.command)}</code></p>`;
  if (typeof value === "object" && !Array.isArray(value)) {
    const fields = Object.entries(value).filter(
      ([k]) => !["time", "source", "unit"].includes(k),
    );
    if (
      fields.length &&
      fields.every(([, v]) => v === null || typeof v !== "object")
    )
      return `<div class="identity-grid">${fields
        .slice(0, 12)
        .map(
          ([k, v]) =>
            `<div><small>${esc(k)}</small><strong>${esc(v)}${fields.length === 1 && value.unit ? " " + esc(value.unit) : ""}</strong></div>`,
        )
        .join("")}</div>`;
  }
  const text = JSON.stringify(value, null, 2);
  return `<pre class="response-text">${esc(text.slice(0, 6000))}${text.length > 6000 ? "…" : ""}</pre>`;
}
export const field = (label, input, note = "") =>
  `<label class="field"><span>${label}</span>${input}${note ? `<small>${note}</small>` : ""}</label>`;
export const empty = (glyph, title, description, action = "") =>
  `<div class="empty"><span class="empty-icon">${icon(glyph)}</span><h3>${title}</h3><p>${description}</p>${action}</div>`;
export function statusName(value) {
  return (
    {
      queued: t("等待中", "Queued"),
      running: t("运行中", "Running"),
      cancelling: t("正在停止", "Stopping"),
      completed: t("已完成", "Completed"),
      failed: t("未完成", "Failed"),
      cancelled: t("已停止", "Stopped"),
      interrupted: t("已中断", "Interrupted"),
      skipped: t("未执行", "Skipped"),
    }[value] || value
  );
}
export const badge = (value) =>
  `<span class="badge ${esc(value)}"><i></i>${esc(statusName(value))}</span>`;
export function date(value) {
  return value
    ? new Date(value).toLocaleString(state.lang === "en" ? "en-US" : "zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
}
export function duration(seconds) {
  if (seconds == null) return "—";
  seconds = Math.max(0, Math.floor(seconds));
  return seconds < 60
    ? `${seconds}s`
    : seconds < 3600
      ? `${Math.floor(seconds / 60)}m ${seconds % 60}s`
      : `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}
export const size = (bytes) =>
  bytes < 1024
    ? `${bytes} B`
    : bytes < 1048576
      ? `${(bytes / 1024).toFixed(1)} KB`
      : `${(bytes / 1048576).toFixed(1)} MB`;
export function download(name, data, type = "application/json") {
  const url = URL.createObjectURL(
    new Blob(
      [typeof data === "string" ? data : JSON.stringify(data, null, 2) + "\n"],
      { type },
    ),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export const emit = (name, detail) =>
  document.dispatchEvent(new CustomEvent("iop:" + name, { detail }));
export const go = (view) => emit("navigate", view);
export function jsonDetails(value, label = t("查看原始数据", "Raw data")) {
  return `<details class="raw"><summary>${icon("code")}${label}</summary><pre>${esc(JSON.stringify(value, null, 2))}</pre></details>`;
}
export function readJSON(id) {
  try {
    return JSON.parse($(id).value || "{}");
  } catch {
    throw Error(
      t(
        "JSON 格式不正确，请检查逗号与引号",
        "Invalid JSON. Check commas and quotation marks.",
      ),
    );
  }
}
export const uniqueID = () =>
  globalThis.crypto?.randomUUID?.() ||
  "ui-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);

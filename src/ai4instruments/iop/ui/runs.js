import {
  $,
  $$,
  state,
  t,
  esc,
  icon,
  api,
  busy,
  toast,
  modal,
  closeModal,
  field,
  empty,
  badge,
  statusName,
  date,
  duration,
  size,
  jsonDetails,
  readJSON,
  emit,
  uniqueID,
  operationResult,
  drawArrays,
  arrayFigure,
  arraysIn,
  mapAxes,
  lineAxis,
  save,
} from "./core.js";
import {
  parametersHTML,
  parameterValues,
  setSweep,
  sweepValues,
} from "./devices.js";
let query = "",
  category = "all",
  detailTimer = null;
export function runRows(runs, compact = false) {
  return runs
    .map(
      (run) =>
        `<tr class="clickable" data-run="${esc(run.id)}" tabindex="0"><td><strong>${esc(run.name || run.id)}</strong><small>${esc(run.platform?.definition?.name || run.platform?.id || (run.kind === "package" ? t("实验程序", "Experiment program") : run.kind === "external" ? t("外部实验", "External experiment") : t("操作流程", "Workflow")))}</small></td><td>${badge(run.state)}</td>${compact ? "" : `<td>${run.operation_count ?? run.operations?.length ?? "—"}</td>`}<td class="nowrap">${date(run.started || run.created)}</td><td>${icon("chevron")}</td></tr>`,
    )
    .join("");
}
export function bindRunRows(root = document) {
  $$("[data-run]", root).forEach((row) => {
    row.onclick = () => inspectRun(row.dataset.run);
    row.onkeydown = (e) => {
      if (e.key === "Enter") inspectRun(row.dataset.run);
    };
  });
}
export function renderRuns() {
  return `<div class="page-heading"><div><span class="eyebrow">EXPERIMENTS</span><h1>${t("实验任务", "Experiments")}</h1><p>${t("查看正在执行的实验、步骤进度、结果与文件。", "Follow running experiments, operation progress, results and files.")}</p></div><button class="button primary" id="run-create">${icon("plus")}${t("新建实验", "New experiment")}</button></div><div class="toolbar"><label class="search-field">${icon("search")}<input id="run-search" value="${esc(query)}" placeholder="${t("搜索实验名称或 ID", "Search experiment name or ID")}"></label><div class="segmented">${[
    ["all", t("全部", "All")],
    ["active", t("运行与等待", "Active")],
    ["completed", t("已完成", "Completed")],
    ["other", t("其他记录", "Other")],
  ]
    .map(
      ([key, label]) =>
        `<button data-run-filter="${key}" class="${category === key ? "active" : ""}">${label}</button>`,
    )
    .join(
      "",
    )}</div></div><div class="panel" id="run-table"></div><p class="inline-note" style="margin-top:14px">${t("显示最近 200 个实验；完整记录保存在运行服务中。", "Showing the latest 200 experiments. Full history is retained by the service.")}</p>`;
}
function renderTable() {
  const runs = state.runs.filter(
    (r) =>
      (r.name + " " + r.id).toLowerCase().includes(query.toLowerCase()) &&
      (category === "all" ||
        (category === "active" &&
          ["queued", "running", "cancelling"].includes(r.state)) ||
        (category === "completed" && r.state === "completed") ||
        (category === "other" &&
          ["cancelled", "failed", "interrupted"].includes(r.state))),
  );
  $("#run-table").innerHTML = runs.length
    ? `<div class="table-wrap"><table><thead><tr><th>${t("实验", "Experiment")}</th><th>${t("状态", "State")}</th><th>${t("操作数", "Operations")}</th><th>${t("时间", "Time")}</th><th></th></tr></thead><tbody>${runRows(runs)}</tbody></table></div>`
    : empty(
        "activity",
        t("这里还没有实验记录", "No experiments here"),
        t(
          "提交操作流程或实验程序包，运行过程和结果会保存在这里。",
          "Submit an operation workflow or an experiment package. Its progress and results will appear here.",
        ),
        `<button class="button" id="run-empty-create">${icon("plus")}${t("新建实验", "New experiment")}</button>`,
      );
  bindRunRows($("#run-table"));
  $("#run-empty-create")?.addEventListener("click", () => newExperiment());
}
export function bindRuns() {
  renderTable();
  $("#run-create").onclick = () => newExperiment();
  $("#run-search").oninput = (e) => {
    query = e.target.value;
    renderTable();
  };
  $$("[data-run-filter]").forEach(
    (b) =>
      (b.onclick = () => {
        category = b.dataset.runFilter;
        $$("[data-run-filter]").forEach((n) =>
          n.classList.toggle("active", n === b),
        );
        renderTable();
      }),
  );
}
// How many operations are in each state, so how far a long sweep got shows without paging through it.
function operationStates(counts = {}) {
  const parts = Object.entries(counts)
    .filter(
      ([state]) => state !== "completed" || Object.keys(counts).length > 1,
    )
    .map(([state, count]) => `${esc(statusName(state))} ${count}`);
  return parts.length
    ? `<span class="run-states">${parts.join(" · ")}</span>`
    : "";
}
// Streams of a numeric format, such as float32-le, are drawn per channel from their views.
const NUMERIC = /^(u?int8(-(le|be))?|(u?int(16|32|64)|float(32|64))-(le|be))$/;
function streamFigures(id, streams) {
  return streams
    .filter((s) => s.bytes && NUMERIC.test(s.definition?.format || ""))
    .flatMap((s) => {
      const source = `runs/${encodeURIComponent(id)}/streams/${encodeURIComponent(s.name)}/array`,
        channels = (s.definition.channels || []).filter(
          (c) => c && typeof c === "object" && !Array.isArray(c),
        ),
        caption = (channel) =>
          `${esc(s.name)}${channel === undefined ? "" : " · " + esc(channel)} · ${esc(s.definition.format)} · ${size(s.bytes)}`;
      return channels.length > 1
        ? channels.map((c, i) =>
            arrayFigure({
              source,
              caption: caption(String(c.name ?? i)),
              at: { channel: i },
              line: true,
              name: s.name,
            }),
          )
        : [
            arrayFigure({
              source,
              caption: caption(channels[0]?.name),
              line: true,
              name: s.name,
            }),
          ];
    })
    .join("");
}
// Arrays the result holds, such as what a sweep gathered from its points' values or files, are drawn from their views.
function heldFigures(id, value, keep) {
  return arraysIn(value)
    .filter(
      ([, data, holder]) =>
        holder.values !== undefined || Array.isArray(data.files),
    )
    .map(([path, data]) =>
      arrayFigure({
        source: `runs/${encodeURIComponent(id)}/array`,
        field: path,
        caption: `${esc(path)} · ${esc(data.shape.join(" × "))}${data.unit ? " · " + esc(data.unit) : ""}`,
        keep,
        map: mapAxes(data),
        line: lineAxis(data),
      }),
    )
    .join("");
}
// A finished experiment's result is final; while a step graph runs, it is what its completed operations give so far.
function resultView(value, id, final) {
  if (value === null || value === undefined)
    return `<p class="inline-note">${t("此实验没有单独保存汇总结果，可查看每个操作的返回值和输出文件。", "No separate summary was saved. Operation results and output files are available below.")}</p>`;
  const entries =
    typeof value === "object" && !Array.isArray(value)
      ? Object.entries(value)
      : [["result", value]];
  return `${heldFigures(id, value, final)}<div class="result-grid">${entries
    .slice(0, 24)
    .map(
      ([key, v]) =>
        `<div class="result-item"><small>${esc(key)}</small>${v !== null && typeof v === "object" ? `<pre>${esc(JSON.stringify(v, null, 2).slice(0, 1600))}</pre>` : `<strong>${esc(v)}</strong>`}</div>`,
    )
    .join("")}</div>${jsonDetails(value)}`;
}
const downloadFile = (file) =>
  save("artifacts/" + encodeURIComponent(file.id), file.name.split("/").pop());
export async function inspectRun(id) {
  clearTimeout(detailTimer);
  const body = modal(
    t("实验详情", "Experiment details"),
    `<div id="run-detail-summary"></div><div class="tabs"><button data-detail-tab="steps" class="active">${t("操作步骤", "Operations")}</button><button data-detail-tab="results">${t("结果", "Results")}</button><button data-detail-tab="files">${t("文件", "Files")}</button></div><div id="detail-steps"><div id="detail-operation-list"></div><div class="actions" style="margin-top:15px"><button class="button small" id="ops-prev">${t("上一页", "Previous")}</button><span id="ops-page" class="muted"></span><button class="button small" id="ops-next">${t("下一页", "Next")}</button></div></div><div id="detail-results" hidden></div><div id="detail-files" hidden></div>`,
    "EXPERIMENT",
  );
  $("#modal").classList.add("wide");
  $("#modal").dataset.runDetail = id;
  let cursor = 0,
    next = 0,
    pages = [],
    terminalLoaded = false,
    resultLoaded = false,
    running = true,
    activeTab = "steps";
  $$("[data-detail-tab]", body).forEach(
    (b) =>
      (b.onclick = () => {
        activeTab = b.dataset.detailTab;
        loadResult();
        for (const n of $$("[data-detail-tab]", body)) {
          n.classList.toggle("active", n === b);
          $("#detail-" + n.dataset.detailTab).hidden = n !== b;
        }
      }),
  );
  async function loadResult() {
    if (activeTab !== "results" || resultLoaded) return;
    if (!running) resultLoaded = true;
    if (!$("#detail-results").childElementCount)
      $("#detail-results").innerHTML =
        `<p class="inline-note">${t("正在读取结果…", "Loading results…")}</p>`;
    try {
      const value = await api("runs/" + encodeURIComponent(id) + "/result");
      if (body.isConnected && $("#modal").dataset.runDetail === id) {
        $("#detail-results").innerHTML =
          running && value === null
            ? `<p class="inline-note">${t("实验正在进行，完成的操作给出结果后显示在此。", "The experiment is running. Results appear here as its operations complete.")}</p>`
            : resultView(value, id, !running);
        drawArrays($("#detail-results"));
      }
    } catch (error) {
      resultLoaded = false;
      if (body.isConnected) toast(error.message, true);
    }
  }
  async function load() {
    if (
      !$("#modal").open ||
      $("#modal").dataset.runDetail !== id ||
      !body.isConnected
    )
      return;
    try {
      const [run, page] = await Promise.all([
        api("runs/" + encodeURIComponent(id) + "?details=false"),
        api(
          "runs/" +
            encodeURIComponent(id) +
            "/operations?after=" +
            cursor +
            "&limit=100",
        ),
      ]);
      if ($("#modal").dataset.runDetail !== id || !body.isConnected) return;
      $("#modal-title").textContent = run.name || run.id;
      const active = ["running", "queued", "cancelling"].includes(run.state);
      $("#run-detail-summary").innerHTML =
        `<div class="run-summary"><div><small>${t("状态", "State")}</small>${badge(run.state)}</div><div><small>${t("开始时间", "Started")}</small>${date(run.started || run.created)}</div><div><small>${t("用时", "Elapsed")}</small>${duration(run.started ? (new Date(run.ended || Date.now()) - new Date(run.started)) / 1000 : null)}</div><div><small>${t("操作数", "Operations")}</small>${run.operation_count ?? page.operations.length}${operationStates(run.operation_states)}</div>${active ? `<button class="button small" id="run-cancel">${icon("stop")}${t("停止实验", "Stop experiment")}</button>` : ""}</div>${
          run.can_resume
            ? `<div class="actions"><button class="button small" id="run-resume">${icon("play")}${t("从保存的进度继续", "Continue from saved progress")}</button><small>${t("程序进度", "Saved progress")} · ${Object.keys(
                run.progress || {},
              )
                .map(esc)
                .join(", ")}</small></div>`
            : ""
        }${run.error ? `<p class="run-error">${esc(typeof run.error === "object" ? JSON.stringify(run.error) : run.error)}</p>` : ""}`;
      $("#run-resume")?.addEventListener("click", (e) =>
        busy(e.currentTarget, async () => {
          await api("runs/" + encodeURIComponent(id) + "/resume", {
            request_id: crypto.randomUUID(),
          });
          terminalLoaded = false;
          resultLoaded = false;
          emit("refresh");
          await load();
        }),
      );
      $("#run-cancel")?.addEventListener("click", (e) =>
        busy(e.currentTarget, async () => {
          await api("runs/" + encodeURIComponent(id) + "/cancel", {});
          emit("refresh");
          await load();
        }),
      );
      $("#detail-operation-list").innerHTML = page.operations.length
        ? page.operations
            .map(
              (o, i) =>
                `<div class="operation-row"><span class="operation-index">${String(pages.length * 100 + i + 1).padStart(2, "0")}</span><div><h3>${esc(o.step || o.id)} <span class="muted">· ${esc(o.device)} / ${esc(o.operation)}</span></h3><small>${date(o.started || o.created)}${o.ended ? " · " + duration((new Date(o.ended) - new Date(o.started)) / 1000) : ""}${o.progress?.message ? " · " + esc(o.progress.message) : ""}${o.maintenance ? " · " + t("保持条件", "Keeping conditions") : ""}${o.in_doubt ? ` · <span class="doubt">${t("存疑", "In doubt")}: ${esc(Object.keys(o.in_doubt).join(", "))}</span>` : ""}</small>${o.progress?.fraction != null ? `<div class="progress-track"><i style="width:${Math.max(0, Math.min(100, o.progress.fraction * 100))}%"></i></div>` : ""}${o.error ? `<p class="error-text">${esc(typeof o.error === "string" ? o.error : JSON.stringify(o.error))}</p>` : ""}${o.result != null ? operationResult(o.result, o.state === "completed" ? o.id : undefined) : ""}<details class="raw"><summary>${t("参数与完整返回值", "Arguments & full response")}</summary><pre>${esc(JSON.stringify({ arguments: o.arguments, result: o.result }, null, 2))}</pre></details></div>${badge(o.state)}</div>`,
            )
            .join("")
        : empty(
            "activity",
            t("等待操作", "Waiting for operations"),
            t(
              "实验程序开始调用仪器后，步骤会显示在这里。",
              "Operations appear when the experiment program starts using instruments.",
            ),
          );
      drawArrays($("#detail-operation-list"));
      next = page.cursor;
      $("#ops-prev").parentElement.hidden =
        !pages.length && page.operations.length < 100;
      $("#ops-prev").disabled = !pages.length;
      $("#ops-next").disabled = page.operations.length < 100;
      $("#ops-page").textContent = t(
        "第 " + (pages.length + 1) + " 页",
        "Page " + (pages.length + 1),
      );
      if (!terminalLoaded || active) {
        const [files, streams] = await Promise.all([
          api("runs/" + encodeURIComponent(id) + "/artifacts"),
          api("runs/" + encodeURIComponent(id) + "/streams"),
        ]);
        if ($("#modal").dataset.runDetail !== id || !body.isConnected) return;
        $("#detail-files").innerHTML =
          streamFigures(id, streams) +
          (files.length
            ? `<div class="table-wrap"><table><thead><tr><th>${t("文件", "File")}</th><th>${t("大小", "Size")}</th><th></th></tr></thead><tbody>${files.map((f, i) => `<tr><td>${icon("file")} <span class="mono">${esc(f.name)}</span></td><td class="nowrap">${size(f.size)}</td><td><button class="button small" data-download="${i}">${icon("download")}${t("下载", "Download")}</button></td></tr>`).join("")}</tbody></table></div>`
            : empty(
                "file",
                t("暂无文件", "No files yet"),
                t(
                  "程序输出与上传的数据将保存在这里。",
                  "Program outputs and uploaded data will appear here.",
                ),
              ));
        drawArrays($("#detail-files"));
        $$("[data-download]", body).forEach(
          (b) =>
            (b.onclick = () =>
              busy(b, () => downloadFile(files[Number(b.dataset.download)]))),
        );
        running = active;
        if (!active) terminalLoaded = true;
        await loadResult();
      }
      if (active) {
        clearTimeout(detailTimer);
        detailTimer = setTimeout(load, 2000);
      }
    } catch (error) {
      if (body.isConnected) toast(error.message, true);
    }
  }
  $("#ops-prev").onclick = () => {
    cursor = pages.pop() || 0;
    load();
  };
  $("#ops-next").onclick = () => {
    pages.push(cursor);
    cursor = next;
    load();
  };
  await load();
}
export function newExperiment() {
  let steps = [],
    counter = 0;
  const body = modal(
    t("新建实验", "New experiment"),
    `<div class="form-grid">${field(t("实验名称", "Experiment name"), `<input id="experiment-name" placeholder="${t("为这次实验命名", "Name this experiment")}">`)}${field(t("实验平台", "Platform"), `<select id="experiment-platform"><option value="">${t("直接选择设备", "Select devices directly")}</option>${state.platforms.map((p) => `<option value="${esc(p.id)}">${esc(p.definition.name || p.id)}</option>`).join("")}</select>`)}</div><div class="tabs"><button class="active" data-compose="workflow">${t("操作流程", "Workflow")}</button><button data-compose="package">${t("实验程序包", "Program package")}</button></div><div id="compose-workflow"><div id="experiment-steps"></div><button class="button" id="experiment-add" style="margin-top:15px">${icon("plus")}${t("添加步骤", "Add operation")}</button><details class="raw"><summary>${icon("code")}${t("完整实验定义 · 结果传递与资源数量", "Full definition · result bindings and resource quantities")}</summary><textarea id="experiment-json" spellcheck="false"></textarea><div style="padding:12px 15px" class="actions"><button class="button small" id="experiment-sync">${t("从表单生成", "Update from form")}</button><button class="button small" id="experiment-raw-submit">${t("提交此定义", "Submit this definition")}</button></div></details></div><div id="compose-package" hidden><div class="form-stack">${field(t("实验程序包", "Experiment package"), '<input type="file" id="experiment-archive" accept=".zip">', t("ZIP 根目录包含 package.json 与程序文件。", "The ZIP root contains package.json and program files."))}${field(t("需要保留的设备", "Reserved instruments"), `<input id="experiment-resources" placeholder="${t("设备 ID，以逗号分隔；可留空", "Device IDs separated by commas; optional")}">`)}${field(t("输入参数", "Input parameters"), '<textarea id="experiment-parameters" style="min-height:90px">{}</textarea>')}</div></div><div class="form-actions"><button class="button" id="experiment-cancel">${t("取消", "Cancel")}</button><button class="button primary" id="experiment-submit">${icon("play")}${t("提交实验", "Submit experiment")}</button></div>`,
    "EXPERIMENT",
  );
  $("#modal").classList.add("wide");
  let compose = "workflow";
  function capture() {
    for (const s of steps) {
      const el = $(`[data-step="${s.id}"]`, body);
      if (!el) continue;
      s.device = $("[data-step-device]", el).value;
      s.operation = $("[data-step-operation]", el).value;
      s.after = $("[data-step-after]", el).value;
      // What was typed for each sweep is kept as typed, to be shown again.
      s.sweeping = Object.fromEntries(
        $$("[data-sweep]:not([hidden])", el).map((block) => [
          block.dataset.sweep,
          $$("input", block).map((input) => input.value),
        ]),
      );
      try {
        s.arguments = parameterValues(
          $("[data-step-args]", el),
          state.devices.find((d) => d.id === s.device)?.operations[
            s.operation
          ] || {},
        );
      } catch {}
    }
  }
  function render() {
    const platform = state.platforms.find(
        (p) => p.id === $("#experiment-platform").value,
      ),
      devices = state.devices.filter(
        (d) => !platform || platform.definition.devices.includes(d.id),
      );
    $("#experiment-steps").innerHTML =
      steps
        .map((s, i) => {
          const d = devices.find((d) => d.id === s.device) || devices[0];
          if (!d) return "";
          s.device = d.id;
          if (!d.operations[s.operation])
            s.operation = Object.keys(d.operations)[0];
          return `<div class="experiment-step" data-step="${s.id}"><div class="experiment-step-head"><span>${String(i + 1).padStart(2, "0")}</span><code>${s.id}</code><button class="icon-button" data-step-remove="${s.id}" aria-label="${t("删除步骤", "Remove operation")}">${icon("close")}</button></div><div class="experiment-step-body">${field(t("仪器或软件", "Instrument or software"), `<select data-step-device>${devices.map((v) => `<option value="${esc(v.id)}" ${v.id === s.device ? "selected" : ""}>${esc(v.name || v.id)}</option>`).join("")}</select>`)}${field(
            t("操作", "Operation"),
            `<select data-step-operation>${Object.keys(d.operations)
              .map(
                (k) =>
                  `<option value="${esc(k)}" ${k === s.operation ? "selected" : ""}>${esc(k)}</option>`,
              )
              .join("")}</select>`,
          )}<div class="full form-grid" data-step-args>${parametersHTML(d.operations[s.operation], s.id, true)}</div><div class="full">${field(
            t("开始条件", "Start after"),
            `<select data-step-after><option value="">${t("就绪后执行，可与其他步骤并行", "When ready; may run in parallel")}</option>${steps
              .slice(0, i)
              .map(
                (p) =>
                  `<option value="${p.id}" ${p.id === s.after ? "selected" : ""}>${p.id} ${t("完成后", "completes")}</option>`,
              )
              .join("")}</select>`,
          )}</div></div></div>`;
        })
        .join("") ||
      empty(
        "device",
        t("先注册可操作的设备", "Register a device first"),
        t(
          "仪器与软件中的可用操作会出现在这里。",
          "Available instrument and software operations will appear here.",
        ),
      );
    for (const s of steps) {
      const el = $(`[data-step="${s.id}"]`, body);
      if (!el) continue;
      for (const input of $$("[data-parameter]", el)) {
        const value = s.arguments?.[input.dataset.parameter];
        if (value !== undefined) {
          const spec = state.devices.find((d) => d.id === s.device).operations[
            s.operation
          ].parameters[input.dataset.parameter];
          input.value =
            typeof value === "object" ||
            typeof value === "boolean" ||
            spec?.enum
              ? JSON.stringify(value)
              : value;
        }
      }
      for (const [name, typed] of Object.entries(s.sweeping || {})) {
        setSweep(el, name, true);
        $$(`[data-sweep="${CSS.escape(name)}"] input`, el).forEach(
          (input, k) => (input.value = typed[k]),
        );
      }
      $$("[data-sweep-toggle]", el).forEach(
        (button) =>
          (button.onclick = () =>
            setSweep(
              el,
              button.dataset.sweepToggle,
              button.getAttribute("aria-pressed") !== "true",
            )),
      );
      $("[data-step-device]", el).onchange = () => {
        capture();
        s.operation = "";
        s.arguments = {};
        s.sweeping = {};
        render();
      };
      $("[data-step-operation]", el).onchange = () => {
        capture();
        s.arguments = {};
        s.sweeping = {};
        render();
      };
    }
    $$("[data-step-remove]", body).forEach(
      (b) =>
        (b.onclick = () => {
          capture();
          steps = steps.filter((s) => s.id !== b.dataset.stepRemove);
          for (const s of steps)
            if (s.after === b.dataset.stepRemove) s.after = "";
          render();
        }),
    );
  }
  function definition() {
    capture();
    return {
      name: $("#experiment-name").value.trim() || t("操作流程", "Workflow"),
      platform: $("#experiment-platform").value || undefined,
      steps: steps.map((s) => {
        const el = $(`[data-step="${s.id}"]`, body),
          d = state.devices.find((d) => d.id === s.device);
        if (!el || !d)
          throw Error(t("请先选择设备", "Select an instrument first"));
        const sweep = sweepValues($("[data-step-args]", el));
        return {
          id: s.id,
          device: s.device,
          operation: s.operation,
          arguments: parameterValues(
            $("[data-step-args]", el),
            d.operations[s.operation],
          ),
          ...(Object.keys(sweep).length ? { sweep } : {}),
          after: s.after ? [s.after] : [],
        };
      }),
    };
  }
  async function submitted(spec) {
    spec.request_id = uniqueID();
    const run = await api("runs", spec);
    emit("refresh");
    await inspectRun(run.id);
  }
  function add() {
    capture();
    counter++;
    steps.push({
      id: "step-" + counter,
      arguments: {},
      after: steps.at(-1)?.id || "",
    });
    render();
  }
  $("#experiment-add").onclick = add;
  $("#experiment-platform").onchange = () => {
    capture();
    render();
  };
  $$("[data-compose]", body).forEach(
    (b) =>
      (b.onclick = () => {
        compose = b.dataset.compose;
        $$("[data-compose]", body).forEach((n) => {
          n.classList.toggle("active", n === b);
          $("#compose-" + n.dataset.compose).hidden = n !== b;
        });
      }),
  );
  $("#experiment-sync").onclick = (e) =>
    busy(e.currentTarget, () => {
      $("#experiment-json").value = JSON.stringify(definition(), null, 2);
    });
  $("#experiment-raw-submit").onclick = (e) =>
    busy(e.currentTarget, () => submitted(readJSON("#experiment-json")));
  $("#experiment-cancel").onclick = closeModal;
  $("#experiment-submit").onclick = (e) =>
    busy(e.currentTarget, async () => {
      if (compose === "workflow") return submitted(definition());
      const f = $("#experiment-archive").files[0];
      if (!f)
        throw Error(t("请选择 ZIP 实验包", "Select a ZIP experiment package"));
      if (f.size > 40 * 1024 * 1024)
        throw Error(
          t(
            "实验包请小于 40 MiB，实验数据可在运行中上传",
            "Keep the program package below 40 MiB; data can be uploaded during execution",
          ),
        );
      const bytes = new Uint8Array(await f.arrayBuffer());
      let raw = "";
      for (let i = 0; i < bytes.length; i += 16384)
        raw += String.fromCharCode(...bytes.subarray(i, i + 16384));
      const run = await api("packages", {
        name: $("#experiment-name").value.trim() || f.name,
        platform: $("#experiment-platform").value || undefined,
        resources: $("#experiment-resources")
          .value.split(",")
          .map((v) => v.trim())
          .filter(Boolean),
        parameters: readJSON("#experiment-parameters"),
        archive: btoa(raw),
        request_id: uniqueID(),
      });
      emit("refresh");
      await inspectRun(run.id);
    });
  add();
}

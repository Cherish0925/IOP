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
  emit,
} from "./core.js";
import { parametersHTML, parameterValues } from "./devices.js";
// Work that needs a person: manual steps waiting to be done and resources held until their state is recorded.
let shown = "";
export function updateAttention() {
  const tasks = state.manual,
    holds = state.status?.resource_holds || [],
    signature = JSON.stringify([
      state.lang,
      tasks.map((task) => task.operation_id),
      holds.map((hold) => hold.resource),
    ]);
  if (signature === shown) return;
  shown = signature;
  const bar = $("#attention");
  bar.hidden = !tasks.length && !holds.length;
  bar.innerHTML = [
    tasks.length &&
      `<div class="attention-item">${icon("clock")}<span><strong>${t(`${tasks.length} 个人工步骤等待完成`, tasks.length === 1 ? "1 manual step waiting" : `${tasks.length} manual steps waiting`)}</strong>${esc(tasks[0].instruction)}</span><button class="button small primary" id="manual-open">${t("处理", "Respond")}</button></div>`,
    holds.length &&
      `<div class="attention-item">${icon("stop")}<span><strong>${t(`${holds.length} 项资源已挂起`, holds.length === 1 ? "1 resource held" : `${holds.length} resources held`)}</strong>${esc(holds.map((hold) => hold.resource).join(", "))}</span><button class="button small" id="holds-open">${t("登记实际状态", "Record observed state")}</button></div>`,
  ]
    .filter(Boolean)
    .join("");
  $("#manual-open")?.addEventListener("click", () => manualDialog(tasks[0]));
  $("#holds-open")?.addEventListener("click", () => holdsDialog(holds));
}
function operator() {
  try {
    return localStorage.getItem("iop-operator") || "";
  } catch {
    return "";
  }
}
function manualDialog(task) {
  const readings = { parameters: task.readings || {} };
  modal(
    t("人工步骤", "Manual step"),
    `<div class="form-stack"><p class="manual-instruction">${esc(task.instruction)}</p><p class="inline-note">${esc(task.device)} · ${esc(task.operation)}${task.run_id ? " · " + esc(task.run_id) : ""}</p>${Object.keys(readings.parameters).length ? `<div id="manual-readings" class="form-grid">${parametersHTML(readings, "reading")}</div>` : ""}<div class="form-grid">${field(t("备注", "Note"), '<input id="manual-note" type="text">')}${field(t("报告人", "Reported by"), `<input id="manual-by" type="text" value="${esc(operator())}">`)}</div><p class="inline-note">${t("不确定：改变状态的步骤会挂起相关设备，登记实际状态后恢复。", "Unsure: if the step changes something, its devices are held until the observed state is recorded.")}</p></div><div class="form-actions"><button class="button" data-report="unsure">${t("不确定", "Unsure")}</button><button class="button" data-report="failed">${t("未完成", "Not done")}</button><button class="button primary" data-report="done">${icon("check")}${t("完成", "Done")}</button></div>`,
    "MANUAL STEP",
  );
  $$("[data-report]", $("#modal-body")).forEach(
    (button) =>
      (button.onclick = () =>
        busy(button, async () => {
          const status = button.dataset.report,
            body = {
              status,
              note: $("#manual-note").value.trim(),
              by: $("#manual-by").value.trim(),
            };
          if (status === "done" && $("#manual-readings"))
            body.readings = parameterValues($("#manual-readings"), readings);
          try {
            localStorage.setItem("iop-operator", body.by);
          } catch {}
          try {
            await api(
              "manual-tasks/" + encodeURIComponent(task.operation_id),
              body,
            );
          } catch (error) {
            if (error.status !== 404) throw error;
            closeModal();
            emit("refresh");
            throw Error(
              t(
                "该步骤已结束或已撤回",
                "This step has already ended or was withdrawn",
              ),
            );
          }
          closeModal();
          toast(t("已记录", "Recorded"));
          emit("refresh");
        })),
  );
}
function holdsDialog(holds) {
  modal(
    t("登记实际状态", "Record observed state"),
    `<div class="form-stack"><p class="inline-note">${t("先查看设备，再写下实际状态。登记后所选资源恢复可用。", "Look at the equipment, then describe its actual state. Recording it returns the selected resources to use.")}</p><div class="selection-list">${holds.map((hold, index) => `<label class="selection-item"><input type="checkbox" data-hold="${index}" checked><code>${esc(hold.resource)}</code><small>${esc(hold.reason)}</small></label>`).join("")}</div>${field(t("实际状态", "Observed state"), '<textarea id="hold-observation" rows="3"></textarea>')}</div><div class="form-actions"><button class="button" id="hold-cancel">${t("取消", "Cancel")}</button><button class="button primary" id="hold-save">${t("登记并恢复", "Record and release")}</button></div>`,
    "RESOURCE HOLD",
  );
  $("#hold-cancel").onclick = closeModal;
  $("#hold-save").onclick = (event) =>
    busy(event.currentTarget, async () => {
      const resources = $$("[data-hold]:checked").map(
          (box) => holds[Number(box.dataset.hold)].resource,
        ),
        observation = $("#hold-observation").value.trim();
      if (!resources.length)
        throw Error(t("请选择资源", "Select at least one resource"));
      if (!observation)
        throw Error(t("请写下实际状态", "Describe the observed state"));
      await api("resources/resolve", { resources, observation });
      closeModal();
      toast(t("资源已恢复可用", "Resources returned to use"));
      emit("refresh");
    });
}

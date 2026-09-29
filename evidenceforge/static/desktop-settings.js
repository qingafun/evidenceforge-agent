/* Desktop settings stay in memory until explicitly saved. Stored secrets are
   never returned by the service or persisted in browser storage. */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    settings: null, busy: false, dirty: false,
    cleared: { "api-key": false, "tavily-api-key": false },
  };
  const keyFields = ["api-key", "tavily-api-key"];

  function notify(id, message, kind = "") {
    const node = $(id);
    node.textContent = message || "";
    node.hidden = !message;
    node.className = `${id === "test-result" ? "inline-result" : "notice"} ${kind}`.trim();
  }

  function errorMessage(detail) {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((item) => item.msg || "请检查填写的配置").join("；");
    return "请求失败，请稍后重试。";
  }

  async function request(path, options = {}) {
    let response;
    try {
      response = await fetch(path, {
        credentials: "same-origin", cache: "no-store",
        ...options, headers: { "Content-Type": "application/json", ...options.headers },
      });
    } catch {
      throw new Error("无法连接本地服务，请确认 EvidenceForge 正在运行。已填写的内容仍保留在页面中。");
    }
    let data;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) throw new Error(errorMessage(data?.detail || `请求失败（HTTP ${response.status}）`));
    if (!data || typeof data !== "object") throw new Error("服务返回了无法读取的配置，请重试。");
    return data;
  }

  function activeRuns(settings) {
    return Array.isArray(settings?.active_runs) ? settings.active_runs.length : Number(settings?.active_runs || 0);
  }

  function setDirty(dirty) {
    state.dirty = dirty;
    $("settings-title").classList.toggle("unsaved-dot", dirty);
    if (dirty) notify("save-result", "");
  }

  function hasSavedKey(id) {
    return Boolean(state.settings?.[id === "api-key" ? "api_key_set" : "tavily_api_key_set"]);
  }

  function renderKey(id) {
    const saved = hasSavedKey(id);
    const cleared = state.cleared[id];
    const typed = Boolean($(id).value.trim());
    const badge = $(`${id}-state`);
    badge.textContent = cleared ? "保存后移除" : typed ? "待保存" : saved ? "已安全保存" : "未保存";
    badge.className = `key-state${cleared || typed ? " pending" : saved ? " saved" : ""}`;
    $(id).placeholder = cleared ? "此密钥将在保存后移除" : saved ? "已保存 · 留空沿用原密钥" : id === "api-key" ? "粘贴你的 API Key" : "tvly-…";
    const clear = $(`clear-${id}`);
    clear.hidden = !saved;
    clear.textContent = cleared ? "撤销移除" : "移除已保存密钥";
    $(`${id}-help`).textContent = cleared
      ? "保存后会移除已存密钥；点击撤销或填写新密钥可改变此操作。"
      : saved
        ? "已保存的密钥不会回显。留空保留原密钥，输入新值可替换。"
        : id === "api-key"
          ? "密钥加密保存在本机，设置页不会读取已保存的密钥。"
          : "留空也可以使用本地知识库；联网检索需要同时配置模型。";
  }

  function renderStatus(settings) {
    $("model-state").textContent = settings.model_configured ? "已配置" : "尚未配置";
    $("model-state").className = `status-tag${settings.model_configured ? " ready" : ""}`;
    $("web-state").textContent = settings.web_search_configured ? "已配置" : "未开启";
    $("web-state").className = `status-tag${settings.web_search_configured ? " ready" : ""}`;
    $("data-dir").textContent = settings.data_dir || "本机用户数据目录";
    const running = activeRuns(settings);
    notify("active-runs-note", running
      ? `检测到 ${running} 个任务正在运行。请在任务结束后保存配置，保存时会重新确认运行状态。` : "", "warning");
    $("save-settings").disabled = state.busy;
    $("save-and-enter").disabled = state.busy;
  }

  function populate(settings) {
    state.settings = settings;
    $("base-url").value = settings.base_url || "https://api.openai.com/v1";
    $("model-name").value = settings.model === "your-model-name" ? "" : settings.model || "";
    $("max-tokens").value = settings.max_tokens ?? 64000;
    $("max-output-tokens").value = settings.max_output_tokens ?? 4096;
    $("request-timeout").value = settings.request_timeout ?? 60;
    for (const id of keyFields) {
      $(id).value = "";
      $(id).type = "password";
      state.cleared[id] = false;
      const reveal = document.querySelector(`[data-reveal="${id}"]`);
      reveal.textContent = "显示";
      reveal.setAttribute("aria-pressed", "false");
      reveal.setAttribute("aria-label", `显示${id === "api-key" ? "模型" : " Tavily"} API Key`);
      renderKey(id);
    }
    if (settings.first_run) $("settings-subtitle").textContent = "欢迎使用 EvidenceForge。连接模型，即可开启你的第一项研究。";
    renderStatus(settings);
    setDirty(false);
  }

  function keyValue(id) {
    const value = $(id).value.trim();
    return value || (state.cleared[id] ? "" : null);
  }

  function formPayload() {
    return {
      model: $("model-name").value.trim(), base_url: $("base-url").value.trim(),
      api_key: keyValue("api-key"), tavily_api_key: keyValue("tavily-api-key"),
      max_tokens: Number($("max-tokens").value),
      max_output_tokens: Number($("max-output-tokens").value),
      request_timeout: Number($("request-timeout").value),
    };
  }

  function setBusy(busy, action) {
    state.busy = busy;
    $("settings-fields").disabled = busy;
    $("settings-form").setAttribute("aria-busy", String(busy));
    $("test-connection").classList.toggle("busy", busy && action === "test");
    $("test-connection").querySelector("span").textContent = busy && action === "test" ? "正在测试…" : "测试连接";
    $("save-settings").textContent = busy && action === "save" ? "正在保存…" : "保存设置";
    if (state.settings) renderStatus(state.settings);
  }

  $("settings-form").addEventListener("input", (event) => {
    setDirty(true);
    if (keyFields.includes(event.target.id)) {
      state.cleared[event.target.id] = false;
      renderKey(event.target.id);
    }
    notify("test-result", "");
  });

  for (const id of keyFields) {
    $(`clear-${id}`).addEventListener("click", () => {
      state.cleared[id] = !state.cleared[id];
      $(id).value = "";
      renderKey(id);
      setDirty(true);
      notify("test-result", "");
    });
  }

  document.querySelectorAll("[data-reveal]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = $(button.dataset.reveal);
      const reveal = input.type === "password";
      input.type = reveal ? "text" : "password";
      button.textContent = reveal ? "隐藏" : "显示";
      button.setAttribute("aria-pressed", String(reveal));
      button.setAttribute("aria-label", `${reveal ? "隐藏" : "显示"}${input.id === "api-key" ? "模型" : " Tavily"} API Key`);
    });
  });

  $("test-connection").addEventListener("click", async () => {
    if (state.busy) return;
    for (const id of ["base-url", "model-name", "request-timeout"]) {
      if (!$(id).reportValidity()) return;
    }
    const payload = formPayload();
    notify("test-result", "正在等待模型响应，请稍候…");
    setBusy(true, "test");
    try {
      const result = await request("/api/desktop/test", {
        method: "POST", body: JSON.stringify({
          model: payload.model, base_url: payload.base_url,
          api_key: payload.api_key, request_timeout: payload.request_timeout,
        }),
      });
      notify("test-result", result.message || (result.ok ? "模型连接成功。保存配置后即可开始研究。" : "模型连接未成功，请检查配置。"), result.ok ? "success" : "error");
    } catch (error) {
      notify("test-result", error.message, "error");
    } finally { setBusy(false); }
  });

  $("settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.busy) return;
    const enter = event.submitter?.value === "enter";
    const payload = formPayload();
    notify("settings-error", "");
    notify("save-result", "");
    setBusy(true, "save");
    try {
      const settings = await request("/api/desktop/settings", { method: "PUT", body: JSON.stringify(payload) });
      populate(settings);
      notify("save-result", "设置已保存并生效。可以返回工作台开始研究。", "success");
      if (enter) window.location.assign("/");
    } catch (error) {
      notify("settings-error", error.message, "error");
      $("settings-error").scrollIntoView({ behavior: "smooth", block: "center" });
    } finally { setBusy(false); }
  });

  window.addEventListener("beforeunload", (event) => {
    if (!state.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });

  async function load() {
    try {
      populate(await request("/api/desktop/settings"));
      $("settings-fields").disabled = false;
      renderStatus(state.settings);
    } catch (error) {
      notify("settings-error", `${error.message} 重新打开设置页后可重试。`, "error");
    } finally { $("settings-loading").hidden = true; }
  }
  load();
})();

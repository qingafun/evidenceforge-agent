/* EvidenceForge: dependency-free client. All server content is rendered as text
   or a deliberately small Markdown subset; raw HTML is never interpreted. */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const labels = {
    queued: "排队中", running: "研究中", awaiting_approval: "等待审批",
    completed: "已完成", failed: "运行失败", cancelled: "已终止",
  };
  const answerLabels = { answered: "已有依据", unverified: "待核验", insufficient_evidence: "证据不足" };
  const nodeLabels = {
    plan: "规划 Agent", approval: "人工审批", research: "研究 Agent", curate: "证据筛选",
    write: "撰写 Agent", evidence_review: "证据审阅", review: "审查 Agent", revise: "修订", finalize: "整理报告", system: "系统",
  };
  const evidenceVerdicts = { supported: "支持引用", uncertain: "有疑问", contradicted: "不支持引用", not_used: "未用于报告", unchecked: "待语义审阅" };
  const views = { workspace: "研究工作台", knowledge: "知识库", memory: "长期记忆", evaluations: "评测中心" };
  const state = {
    health: null, runs: [], run: null, events: [], documents: [], memories: [],
    activeView: "workspace", activeTab: "report", source: null, refreshTimer: null,
    pollTimer: null, refreshing: false, selection: 0, questionRevision: 0, lastReport: null,
    reviewDrafts: new Map(), reviewSaving: new Set(),
    copyingReport: false, deletingRuns: new Set(), deletedRuns: new Set(),
    runsRequest: 0, selectingRunId: null,
  };
  const initialReportEmpty = $("report-empty").cloneNode(true);

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }

  function array(value) {
    return Array.isArray(value) ? value : [];
  }

  function readableError(detail) {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((item) => `${array(item.loc).slice(1).join(".")}: ${item.msg || "参数不符合要求"}`).join("；");
    return "请求失败，请稍后重试。";
  }

  async function api(path, options = {}) {
    let response;
    try {
      response = await fetch(path, {
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", ...options.headers },
        ...options,
      });
    } catch {
      throw new Error("无法连接本地服务。请确认 EvidenceForge 正在运行，然后重试。");
    }
    const text = await response.text();
    let payload = null;
    try { payload = text ? JSON.parse(text) : null; } catch { /* Non-JSON errors are not inserted into the page. */ }
    if (!response.ok) {
      const error = new Error(readableError(payload?.detail || `请求失败（HTTP ${response.status}）。`));
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function toast(message, isError = false) {
    const item = el("div", `toast${isError ? " error" : ""}`);
    item.append(icon(isError ? "close" : "check"), el("span", "", message));
    $("toast-region").append(item);
    setTimeout(() => item.remove(), isError ? 8000 : 4000);
  }

  function showError(id, error) {
    const node = $(id);
    node.textContent = error?.message || "";
    node.hidden = !error;
  }

  function shortDate(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  }

  function clockTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("zh-CN", { hour12: false });
  }

  function number(value) {
    return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN") : "—";
  }

  function safeURL(value) {
    if (typeof value !== "string") return null;
    try {
      const url = new URL(value);
      return ["https:", "http:"].includes(url.protocol) ? url.href : null;
    } catch { return null; }
  }

  function externalLink(label, value) {
    const url = safeURL(value);
    if (!url) return el("span", "", label);
    const link = el("a", "", label);
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  }

  function emptyState(title, description, iconName = "file") {
    const box = el("div", "empty-state");
    const visual = el("span", "waiting-icon");
    visual.append(icon(iconName));
    box.append(visual, el("h3", "", title), el("p", "", description));
    return box;
  }

  function closeSidebar() {
    $("sidebar").classList.remove("open");
    $("mobile-backdrop").hidden = true;
    $("mobile-menu").setAttribute("aria-expanded", "false");
  }

  function switchView(view) {
    if (!views[view]) return;
    state.activeView = view;
    Object.keys(views).forEach((name) => {
      $(`view-${name}`).hidden = name !== view;
      $(`view-${name}`).classList.toggle("active", name === view);
    });
    document.querySelectorAll("[data-view]").forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    $("breadcrumb-current").textContent = views[view];
    closeSidebar();
    if (view === "knowledge") loadDocuments();
    if (view === "memory") loadMemory();
    if (view === "evaluations") loadEvaluations();
  }

  function switchTab(tab, focus = false) {
    if (!["report", "evidence", "trace"].includes(tab)) return;
    state.activeTab = tab;
    document.querySelectorAll("[data-tab]").forEach((button) => {
      const active = button.dataset.tab === tab;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      $(`panel-${button.dataset.tab}`).hidden = !active;
      if (active && focus) button.focus();
    });
  }

  async function loadHealth() {
    try {
      state.health = await api("/api/health");
      $("desktop-settings-link").hidden = state.health.desktop !== true;
      if (state.health.desktop === true) document.querySelector(".api-link").target = "_self";
      $("health-label").className = "health-label connected";
      $("health-label").replaceChildren(el("span", "status-dot"), document.createTextNode("本地服务已连接"));
      $("connection-dot").classList.add("connected");
      $("version-label").textContent = state.health.version ? `v${state.health.version} · LOCAL WORKSPACE` : "LOCAL WORKSPACE";
      if (state.health.knowledge?.documents !== undefined) $("knowledge-count").textContent = state.health.knowledge.documents;
      updateModeNotice();
    } catch (error) {
      $("health-label").className = "health-label disconnected";
      $("health-label").replaceChildren(el("span", "status-dot"), document.createTextNode("服务未连接"));
      $("connection-dot").classList.remove("connected");
      toast(error.message, true);
    }
  }

  function updateModeNotice() {
    const live = $("run-mode").value === "live";
    const available = state.health?.live_available === true;
    $("mode-explanation").textContent = live
      ? `模型驱动 · ${available ? state.health.model || "兼容 OpenAI 的 API" : "需配置模型 API"}；Agent 自主选择与调用工具`
      : "离线演示 · 规则驱动；接入模型后启用自主工具选择";
    $("live-warning").hidden = !live;
    $("live-warning").textContent = available
      ? "已检测到服务端模型配置。研究将调用模型 API，可能产生费用；工具预算限制工具调用次数。"
      : state.health?.desktop === true
        ? "模型尚未配置：请在「模型与 API 设置」中填写 API Key、Base URL 和模型名称，保存后即可开始研究。"
        : "模型尚未配置：请在服务端 .env 设置 EF_API_KEY、EF_BASE_URL 和 EF_MODEL 后重启。密钥不会在浏览器中保存。";
    if (live && !available && state.health?.desktop === true) {
      const link = el("a", "", "前往设置 →");
      link.href = "/desktop/settings";
      $("live-warning").append(document.createTextNode(" "), link);
    }
  }

  async function loadRuns(selectFirst = false) {
    const selection = state.selection;
    const request = ++state.runsRequest;
    try {
      const runs = array(await api("/api/runs"));
      if (request !== state.runsRequest) return;
      state.runs = runs.filter((run) => !state.deletedRuns.has(run.id));
      renderRecentRuns();
      if (selectFirst && selection === state.selection && !state.run && state.runs.length) await selectRun(state.runs[0].id);
    } catch (error) {
      if (request !== state.runsRequest) return;
      if (!state.runs.length) $("recent-runs").replaceChildren(el("p", "sidebar-empty", "暂时无法读取记录。点击上方刷新按钮重试。"));
      toast(error.message, true);
    }
  }

  function renderRecentRuns() {
    const container = $("recent-runs");
    container.replaceChildren();
    if (!state.runs.length) {
      container.append(el("p", "sidebar-empty", "还没有研究记录。\n从一个问题开始吧。"));
      return;
    }
    state.runs.forEach((run) => {
      const row = el("div", `recent-run-row${run.id === state.run?.id ? " selected" : ""}`);
      row.dataset.runId = run.id;
      const deleting = state.deletingRuns.has(run.id);
      const active = ["queued", "running", "awaiting_approval"].includes(run.status);
      const button = el("button", `recent-run${run.id === state.run?.id ? " selected" : ""}`);
      const statusText = run.state?.human_review_blocked ? "人工审阅有异议 · 草稿" : isDraft(run) ? "报告草稿" : labels[run.status] || run.status;
      button.type = "button";
      button.disabled = deleting;
      button.title = `${run.question}\n${statusText} · ${shortDate(run.created_at)}`;
      button.setAttribute("aria-label", `${run.question}，${statusText}`);
      const dot = el("i", `recent-status ${run.state?.human_review_blocked ? "awaiting_approval" : Object.hasOwn(labels, run.status) ? run.status : ""}`);
      dot.setAttribute("aria-hidden", "true");
      button.append(icon("file"), el("span", "", run.question), dot);
      button.addEventListener("click", () => { switchView("workspace"); selectRun(run.id); });
      const remove = el("button", "icon-button recent-delete");
      remove.type = "button";
      remove.disabled = deleting;
      remove.setAttribute("aria-label", `删除研究：${run.question}`);
      remove.setAttribute("aria-disabled", String(active || deleting));
      remove.title = deleting ? "正在删除…" : active ? "请先停止研究，再删除记录" : "删除研究记录";
      remove.append(icon(deleting ? "clock" : "trash"));
      remove.addEventListener("click", () => deleteResearch(run));
      row.append(button, remove);
      container.append(row);
    });
  }

  async function deleteResearch(run) {
    if (state.deletingRuns.has(run.id) || state.deletedRuns.has(run.id)) return;
    if (["queued", "running", "awaiting_approval"].includes(run.status)) {
      toast("请先打开这项研究并点击“停止”，再删除历史记录。", true);
      return;
    }
    if (!window.confirm(`删除研究“${run.question}”？\n报告、证据快照和执行轨迹将一并删除，无法恢复。知识库和长期记忆会保留。`)) return;
    state.deletingRuns.add(run.id);
    renderRecentRuns();
    try {
      try { await api(`/api/runs/${encodeURIComponent(run.id)}`, { method: "DELETE" }); }
      catch (error) { if (error.status !== 404) throw error; }
      forgetResearch(run.id);
      toast("研究记录已删除，知识库和长期记忆已保留。");
      // The removed control no longer exists; give keyboard users a stable next target.
      if (document.activeElement === document.body) $("refresh-runs").focus({ preventScroll: true });
      await loadRuns();
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.deletingRuns.delete(run.id);
      renderRecentRuns();
    }
  }

  function forgetResearch(runId) {
    const run = state.run?.id === runId ? state.run : state.runs.find((item) => item.id === runId);
    // Remember removals for this page lifetime, including snapshots captured before deletion.
    state.deletedRuns.add(runId);
    state.runs = state.runs.filter((item) => item.id !== runId);
    for (const key of state.reviewDrafts.keys()) {
      if (JSON.parse(key)[0] === runId) state.reviewDrafts.delete(key);
    }
    if (state.run?.id === runId || state.selectingRunId === runId) {
      const keepPendingSelection = state.selectingRunId && state.selectingRunId !== runId;
      resetSelectedRun(!keepPendingSelection && Boolean(run) && $("question").value === run.question, keepPendingSelection);
    }
    renderRecentRuns();
  }

  function stopSubscription() {
    if (state.source) state.source.close();
    state.source = null;
    clearTimeout(state.refreshTimer);
    clearInterval(state.pollTimer);
    state.refreshTimer = null;
    state.pollTimer = null;
    $("trace-live").textContent = "";
  }

  function isActive(run = state.run) {
    return run && ["queued", "running"].includes(run.status);
  }

  function isDraft(run = state.run) {
    return Boolean(run?.state?.report) && (run.state.report_draft === true || run.state.review?.passed === false || run.state.human_review_blocked === true);
  }

  function evidenceReview(item, run = state.run) {
    const record = run?.state?.evidence_reviews?.[String(item.id)] || {};
    const automatic = record.automatic && Object.hasOwn(evidenceVerdicts, record.automatic.verdict) ? record.automatic : null;
    const human = record.human?.method === "human" && ["supported", "uncertain", "contradicted"].includes(record.human.verdict) ? record.human : null;
    const agent = automatic?.method === "agent" && automatic.verdict !== "unchecked" ? automatic : null;
    return { record, automatic, human, agent, effective: human || agent };
  }

  function evidenceReviewCounts(run = state.run) {
    const evidence = array(run?.state?.evidence);
    const counts = { total: evidence.length, reviewed: 0, pending: 0, rules: 0, issues: 0, human: 0, agent: 0 };
    evidence.forEach((item) => {
      const review = evidenceReview(item, run);
      if (review.effective) counts.reviewed++;
      else counts.pending++;
      if (review.human) counts.human++;
      if (review.agent) counts.agent++;
      if (review.automatic?.method === "rules") counts.rules++;
      if (["uncertain", "contradicted"].includes(review.effective?.verdict)) counts.issues++;
    });
    return counts;
  }

  function scheduleRefresh() {
    if (state.refreshTimer) return;
    state.refreshTimer = setTimeout(() => {
      state.refreshTimer = null;
      refreshCurrentRun();
    }, 160);
  }

  function subscribeRun() {
    stopSubscription();
    if (!isActive()) return;
    const runId = state.run.id;
    const cursor = state.events.reduce((maximum, event) => Math.max(maximum, Number(event.id) || 0), 0);
    $("trace-live").textContent = "实时更新";
    if (typeof EventSource !== "undefined") {
      const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events?after=${cursor}`);
      state.source = source;
      source.addEventListener("trace", (message) => {
        if (state.run?.id !== runId) return;
        try {
          const event = JSON.parse(message.data);
          if (!state.events.some((item) => String(item.id) === String(event.id))) {
            state.events.push(event);
            renderTrace();
          }
          scheduleRefresh();
        } catch { /* A later polling refresh recovers malformed or missed SSE data. */ }
      });
      source.addEventListener("status", (message) => {
        if (state.run?.id !== runId) return;
        try {
          if (JSON.parse(message.data).status === "deleted") {
            forgetResearch(runId);
            toast("这项研究已在其他窗口删除。");
            return;
          }
        } catch { /* A snapshot refresh resolves incomplete status messages. */ }
        scheduleRefresh();
      });
      source.onopen = () => { if (state.run?.id === runId) $("trace-live").textContent = "实时更新"; };
      source.onerror = () => {
        if (state.run?.id === runId) {
          $("trace-live").textContent = "正在同步…";
          scheduleRefresh();
        }
      };
    }
    // Also reconcile snapshots if a proxy buffers SSE, or the server restarts.
    state.pollTimer = setInterval(() => refreshCurrentRun(true), 3000);
  }

  function setQuestion(value) {
    $("question").value = value;
    state.questionRevision++;
  }

  function resetSelectedRun(clearQuestion = true, keepPendingSelection = false) {
    stopSubscription();
    // Invalidate history and refresh requests still waiting for a response.
    if (!keepPendingSelection) {
      state.selection++;
      state.selectingRunId = null;
    }
    state.run = null;
    state.events = [];
    if (clearQuestion) setQuestion("");
    $("approval-feedback").value = "";
    showError("research-error", null);
    switchTab("report");
    renderRun();
    renderRecentRuns();
    renderTrace();
  }

  function newResearch() {
    resetSelectedRun();
    switchView("workspace");
    $("question").focus();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function selectRun(runId, initialRun = null) {
    if (state.deletedRuns.has(runId) || state.deletingRuns.has(runId)) return;
    stopSubscription();
    const selection = ++state.selection;
    state.selectingRunId = runId;
    const questionRevision = state.questionRevision;
    state.events = [];
    state.lastReport = null;
    try {
      const [run, events] = await Promise.all([
        initialRun ? Promise.resolve(initialRun) : api(`/api/runs/${encodeURIComponent(runId)}`),
        api(`/api/runs/${encodeURIComponent(runId)}/trace`),
      ]);
      if (selection !== state.selection || state.deletedRuns.has(runId)) return;
      state.run = run;
      state.events = array(events);
      // Restore the selected question without replacing a draft edited while loading.
      if (questionRevision === state.questionRevision) setQuestion(run.question);
      $("approval-feedback").value = "";
      renderRun();
      renderRecentRuns();
      renderTrace();
      subscribeRun();
    } catch (error) {
      if (selection === state.selection) {
        if (error.status === 404) { forgetResearch(runId); toast("这项研究已不存在，历史记录已更新。"); }
        else toast(error.message, true);
      }
    } finally {
      if (selection === state.selection) state.selectingRunId = null;
    }
  }

  async function refreshCurrentRun(includeTrace = false) {
    const runId = state.run?.id;
    const selection = state.selection;
    if (!runId || state.refreshing) return;
    state.refreshing = true;
    try {
      const run = await api(`/api/runs/${encodeURIComponent(runId)}`);
      if (state.run?.id !== runId || selection !== state.selection || state.deletedRuns.has(runId)) return;
      const oldStatus = state.run.status;
      state.run = run;
      if (includeTrace || oldStatus !== run.status) {
        const events = await api(`/api/runs/${encodeURIComponent(runId)}/trace`);
        if (state.run?.id !== runId || selection !== state.selection) return;
        state.events = array(events);
        renderTrace();
      }
      renderRun();
      const listIndex = state.runs.findIndex((item) => item.id === runId);
      if (listIndex >= 0) state.runs[listIndex] = run;
      else state.runs.unshift(run);
      renderRecentRuns();
      if (!isActive(run)) stopSubscription();
      if (oldStatus !== run.status && run.status === "completed") {
        toast("研究报告已生成，可以查看证据或导出 Markdown。");
        if (run.state?.remember) loadMemory();
      } else if (oldStatus !== run.status && run.status === "failed" && isDraft(run)) {
        toast("报告未通过验收，草稿已保留。请查看质量审查中的具体问题。", true);
      }
    } catch (error) {
      if (state.run?.id === runId && selection === state.selection) {
        if (error.status === 404) { forgetResearch(runId); toast("这项研究已在其他窗口删除。"); }
        else $("trace-live").textContent = "连接中断，正在重试";
      }
    } finally {
      state.refreshing = false;
    }
  }

  async function createResearch(event) {
    event.preventDefault();
    const question = $("question").value.trim();
    showError("research-error", null);
    if (question.length < 5) {
      showError("research-error", new Error("请用至少 5 个字符描述你的研究问题。"));
      $("question").focus();
      return;
    }
    if ($("run-mode").value === "live" && state.health?.live_available === false) {
      showError("research-error", new Error(state.health?.desktop === true
        ? "模型 API 尚未配置。请打开「模型与 API 设置」填写并保存，或切换到离线演示。"
        : "模型 API 尚未配置。请设置 EF_API_KEY、EF_BASE_URL、EF_MODEL 并重启服务，或切换到离线演示。"));
      return;
    }
    const button = $("start-research");
    const selection = state.selection;
    button.disabled = true;
    button.replaceChildren(document.createTextNode("创建中…"));
    try {
      const run = await api("/api/runs", {
        method: "POST", body: JSON.stringify({
          question, mode: $("run-mode").value, require_approval: $("require-approval").checked,
          max_steps: Number($("max-steps").value), remember: $("remember").checked,
        }),
      });
      state.runs.unshift(run);
      renderRecentRuns();
      if (selection !== state.selection) return;
      switchTab("report");
      await selectRun(run.id, run);
      if (state.run?.id !== run.id) return;
      await refreshCurrentRun(true);
      toast("研究任务已创建。");
    } catch (error) {
      if (selection === state.selection) showError("research-error", error);
    } finally {
      button.disabled = false;
      button.replaceChildren(document.createTextNode("开始研究"), icon("arrow"));
    }
  }

  function renderWorkflow() {
    const run = state.run;
    const data = run?.state || {};
    const order = ["plan", "approval", "research", "write", "evidence_review", "review"];
    let active = -1;
    if (run) {
      if (run.status === "completed") active = order.length;
      else if (run.status === "awaiting_approval") active = 1;
      else {
        const latest = [...state.events].reverse().find((event) => order.includes(event.node) || ["revise", "finalize"].includes(event.node));
        active = latest ? order.indexOf(latest.node) : 0;
        if (latest?.node === "revise") active = 3;
        if (latest?.node === "finalize") active = 5;
        if (!latest && data.plan) active = data.approval?.approved ? 2 : 1;
      }
    }
    document.querySelectorAll("[data-stage]").forEach((step) => {
      const position = order.indexOf(step.dataset.stage);
      const skipped = step.dataset.stage === "approval" && run && data.require_approval === false;
      step.classList.toggle("done", position < active && !skipped);
      step.classList.toggle("active", position === active && !skipped && !["failed", "cancelled"].includes(run?.status));
      step.classList.toggle("skipped", Boolean(skipped));
      step.querySelector(".step-number").textContent = position < active && !skipped ? "✓" : String(position + 1);
      if (position === active && isActive(run)) step.setAttribute("aria-current", "step");
      else step.removeAttribute("aria-current");
    });
  }

  function renderRun() {
    const run = state.run;
    const data = run?.state || {};
    const evidence = array(data.evidence);
    const plan = data.plan || data.approval?.plan;
    const metrics = data.metrics || {};
    const report = typeof data.report === "string" ? data.report : "";
    const status = run?.status;
    const draft = isDraft(run);
    const humanBlocked = data.human_review_blocked === true;
    const reviewCounts = evidenceReviewCounts(run);
    const answerStatus = data.answer_status || data.review?.answer_status;
    $("run-status").className = `status-badge ${humanBlocked ? "awaiting_approval" : Object.hasOwn(labels, status) ? status : "neutral"}`;
    $("run-status").textContent = humanBlocked ? "人工审阅有异议" : status === "failed" && draft ? "验收未通过" : labels[status] || "等待开始";
    $("run-question").textContent = run?.question || "尚未创建研究任务";
    $("metric-evidence").textContent = run ? number(evidence.length) : "—";
    $("metric-tools").textContent = run ? number(metrics.tool_calls ?? 0) : "—";
    $("metric-model").textContent = run ? number(metrics.llm_calls ?? 0) : "—";
    const duration = Number(metrics.duration_ms || 0);
    $("metric-time").replaceChildren(document.createTextNode(run ? (duration / 1000).toFixed(duration < 10000 ? 1 : 0) : "—"));
    if (run) $("metric-time").append(el("small", "", "秒"));
    $("detail-mode").textContent = run ? (run.mode === "live" ? "模型驱动" : "离线 · 规则驱动") : "—";
    $("detail-tokens").textContent = run ? number((metrics.prompt_tokens || 0) + (metrics.completion_tokens || 0)) + (metrics.estimated_usage ? "（估算）" : "") : "—";
    $("detail-answer").textContent = answerLabels[answerStatus] || "尚未形成答案";
    $("detail-answer").title = answerStatus === "unverified" ? "已按相关资料给出答案，但来源或结论尚未独立核验。" : answerStatus === "answered" ? "答案有引用依据，仍应结合原文判断事实是否正确。" : "";
    const hasQualityChecks = typeof data.review?.completeness_passed === "boolean";
    $("detail-review").textContent = humanBlocked ? "人工异议待处理" : data.review ? (data.review.passed ? (hasQualityChecks ? "自动验收通过" : "原有审查通过") : "存在待解决问题") : "尚未开始";
    $("detail-review").title = data.review?.note || "";
    $("quality-checks").replaceChildren();
    const checks = [["completeness_passed", "答案完整性"], ["relevance_passed", "证据相关性"], ["format_passed", "输出格式"], ["truncation_passed", "输出完整结束"], ["support_passed", "事实支持"], ["evidence_review_passed", "证据审阅检查"]];
    checks.forEach(([key, label]) => {
      if (typeof data.review?.[key] !== "boolean") return;
      const passed = data.review[key];
      $("quality-checks").append(el("span", `quality-check ${passed ? "passed" : "failed"}`, `${passed ? "✓" : "!"} ${label}${passed ? "通过" : "未通过"}`));
    });
    $("quality-checks").hidden = !$("quality-checks").childElementCount;
    $("review-issues").hidden = !array(data.review?.issues).length && !humanBlocked;
    $("review-issues").replaceChildren();
    if (array(data.review?.issues).length) {
      const issues = el("ul");
      data.review.issues.forEach((issue) => issues.append(el("li", "", issue)));
      $("review-issues").append(issues);
    }
    if (humanBlocked) $("review-issues").append(el("p", "", "人工对已引用证据提出疑问或否定，报告当前按草稿处理；上方仍保留原自动验收记录。"));
    $("evidence-count").textContent = evidence.length;
    $("cancel-run").hidden = !run || !["queued", "running", "awaiting_approval"].includes(status);
    $("resume-run").hidden = status !== "failed";
    $("approval-panel").hidden = status !== "awaiting_approval";
    $("approval-description").textContent = "请审阅下方的研究目标、检索问题和执行策略。" + (run?.mode === "demo"
      ? "离线演示按此固定计划执行，确认后可批准，或拒绝终止。"
      : "可补充要求后批准，或拒绝终止。");
    $("approval-feedback").placeholder = run?.mode === "demo"
      ? "离线演示不使用补充要求；模型驱动模式支持将反馈用于后续检索与报告。"
      : "补充关注点或约束，反馈会交给检索与报告撰写 Agent。";
    $("run-error").hidden = !run?.error;
    $("run-error").textContent = run?.error || "";
    const exportButton = $("export-report");
    exportButton.replaceChildren(icon("download"), document.createTextNode(draft ? "导出草稿" : "导出报告"));
    exportButton.classList.toggle("disabled", !report);
    exportButton.setAttribute("aria-disabled", String(!report));
    exportButton.tabIndex = report ? 0 : -1;
    if (report) {
      exportButton.href = `/api/runs/${encodeURIComponent(run.id)}/report`;
      exportButton.download = `evidenceforge-${run.id.slice(0, 8)}${draft ? "-draft" : ""}.md`;
    } else {
      exportButton.removeAttribute("href");
      exportButton.removeAttribute("download");
    }
    $("copy-report").disabled = !report || state.copyingReport;
    $("copy-report").setAttribute("aria-label", draft ? "复制 Markdown 草稿" : "复制 Markdown 报告");
    const notice = $("report-notice");
    notice.replaceChildren();
    notice.hidden = !report || (!draft && !reviewCounts.total && !["unverified", "insufficient_evidence"].includes(answerStatus));
    notice.className = `report-notice${draft ? " draft" : ""}`;
    if (humanBlocked) {
      notice.append(el("strong", "", "人工审阅发现问题 · 报告暂按草稿处理"), el("p", "", "已引用证据存在人工疑问或否定，请在证据库查看理由。原自动审查结论保留，导出内容会包含最新审阅记录。"));
    }
    else if (draft) {
      const rejected = data.review?.passed === false || status === "failed";
      notice.append(el("strong", "", rejected ? "未通过验收的草稿" : "等待验收的草稿"), el("p", "", rejected ? "这份内容仍有未解决的问题，不能视为完整答案。具体原因见质量审查。" : "答案正在审查，验收通过后才会标记为完成。"));
    }
    else if (answerStatus === "insufficient_evidence") notice.append(el("strong", "", "相关证据不足"), el("p", "", "现有资料不足以回答问题。请按报告中的缺口补充相关资料后重新研究。"));
    else if (reviewCounts.total && reviewCounts.pending === 0) {
      notice.classList.add("reviewed");
      notice.append(el("strong", "", "证据审阅已完成，事实尚未独立核验"), el("p", "", `${reviewCounts.reviewed} 条证据已有审阅记录${reviewCounts.human ? `，其中 ${reviewCounts.human} 条经人工审阅` : ""}。审阅判断原文是否支持报告引用，不等于来源内容已被独立证实。${reviewCounts.issues ? `仍有 ${reviewCounts.issues} 条存在疑问，请查看证据库。` : ""}`));
    }
    else if (reviewCounts.rules && !reviewCounts.reviewed) notice.append(el("strong", "", "规则检查已完成，待语义审阅"), el("p", "", "离线演示仅执行规则检查。你可以在证据库逐条人工审阅；模型驱动的新研究会自动加入 Agent 审阅。事实仍需结合原始来源核验。"));
    else notice.append(el("strong", "", reviewCounts.reviewed ? "证据审阅进行中，事实尚未独立核验" : "答案已整理，证据待审阅"), el("p", "", reviewCounts.total ? `${reviewCounts.reviewed} 条已审阅，${reviewCounts.pending} 条待审阅。打开证据库可查看依据并添加人工审阅；历史报告不会自动标记为已审阅。` : "当前答案尚无证据审阅记录；来源和结论也尚未独立核验。"));
    renderWorkflow();
    renderPlan(plan);
    renderPlan(status === "awaiting_approval" ? plan : null, $("approval-plan"));
    renderEvidence(evidence);
    $("report-content").hidden = !report;
    $("report-empty").hidden = Boolean(report);
    if (report && state.lastReport !== report) {
      $("report-content").replaceChildren(renderMarkdown(report, new Set(evidence.map((item) => String(item.id)))));
      state.lastReport = report;
    } else if (!report) {
      state.lastReport = null;
      $("report-content").replaceChildren();
      if (run) renderPendingReport(run);
      else $("report-empty").replaceChildren(...Array.from(initialReportEmpty.childNodes, (node) => node.cloneNode(true)));
    }
  }

  function renderPendingReport(run) {
    const empty = $("report-empty");
    empty.replaceChildren();
    if (isActive(run)) {
      empty.append(el("span", "live-spinner"), el("h3", "", "Agent 正在推进研究"), el("p", "", "研究完成后，报告会显示在这里。你可以切换到执行轨迹，查看实时步骤与工具调用。"));
      const button = el("button", "button button-small button-secondary", "查看执行轨迹");
      button.style.marginTop = "18px";
      button.type = "button";
      button.addEventListener("click", () => switchTab("trace", true));
      empty.append(button);
    } else {
      const visual = el("span", "waiting-icon");
      visual.append(icon(run.status === "awaiting_approval" ? "shield" : run.status === "failed" ? "refresh" : "file"));
      const title = run.status === "awaiting_approval" ? "研究计划已就绪，等你确认" : run.status === "failed" ? "本次研究遇到问题" : "研究已终止";
      const detail = run.status === "awaiting_approval" ? "请在上方审批研究计划。批准后，Agent 将开始检索资料并生成报告。" : run.status === "failed" ? "查看上方错误信息或执行轨迹。修复问题后，可以从已保存的检查点恢复运行。" : "这次任务没有生成报告。你可以修改问题，开始一项新的研究。";
      empty.append(visual, el("h3", "", title), el("p", "", detail));
    }
  }

  function renderPlan(plan, target = $("plan-content")) {
    target.replaceChildren();
    if (!plan) {
      if (target.id === "plan-content") target.append(el("p", "muted", "任务开始后，研究计划将在这里展开。"));
      else if (state.run?.status === "awaiting_approval") target.append(el("p", "", "尚未读取到研究计划，请刷新任务后查看。"));
      return;
    }
    if (plan.objective) target.append(el("h5", "", "研究目标"), el("p", "", plan.objective));
    if (array(plan.questions).length) {
      target.append(el("h5", "", "检索问题"));
      const list = el("ol");
      plan.questions.forEach((question) => list.append(el("li", "", question)));
      target.append(list);
    }
    if (plan.strategy) target.append(el("h5", "", "执行策略"), el("p", "plan-strategy", plan.strategy));
  }

  function evidenceDomId(id) {
    return `evidence-${encodeURIComponent(String(id))}`;
  }

  function focusEvidence(id) {
    switchTab("evidence", true);
    const card = $(evidenceDomId(id));
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("highlight");
    card.tabIndex = -1;
    card.focus({ preventScroll: true });
    setTimeout(() => card.classList.remove("highlight"), 2200);
  }

  function renderEvidence(evidence) {
    const container = $("evidence-list");
    const openDetails = new Set([...container.querySelectorAll("details[open][data-review-id]")].map((node) => node.dataset.reviewId));
    container.replaceChildren();
    const summary = $("evidence-review-summary");
    summary.replaceChildren();
    summary.hidden = !evidence.length;
    if (!evidence.length) {
      container.append(emptyState("还没有收集到证据", "执行检索后，原文片段、来源与检索分数会显示在这里。", "book"));
      return;
    }
    const counts = evidenceReviewCounts();
    const summaryStats = el("div", "review-summary-stats");
    [["已审阅", counts.reviewed], ["待审阅", counts.pending], ["规则已检查", counts.rules], ["存在疑问", counts.issues]].forEach(([label, value]) => {
      const stat = el("span", `review-summary-stat${label === "存在疑问" && value ? " has-issues" : ""}`);
      stat.append(el("strong", "", value), document.createTextNode(label));
      summaryStats.append(stat);
    });
    summary.append(summaryStats, el("p", "", "审阅判断证据是否支持报告中的引用。Agent 审阅、人工审阅与规则检查分别记录；已审阅不代表事实已独立核验。"));
    evidence.forEach((item) => {
      const review = evidenceReview(item);
      const card = el("article", "evidence-card");
      card.id = evidenceDomId(item.id);
      const header = el("div", "evidence-card-header");
      header.append(el("span", "source-id", String(item.id).length > 12 ? `${String(item.id).slice(0, 12)}…` : item.id), el("h3", "", item.title || "未命名来源"));
      if (typeof item.score === "number") {
        const score = el("span", "evidence-score", `检索分 ${item.score.toFixed(3)}`);
        score.title = "检索排序分数，不代表证据置信度";
        header.append(score);
      }
      card.append(header);
      const badges = el("div", "evidence-review-badges");
      const reviewedBy = review.human ? "人工已审阅" : review.agent ? "Agent 已审阅" : review.automatic?.method === "rules" ? "规则已检查" : "待审阅";
      badges.append(el("span", `review-badge ${review.effective ? "reviewed" : "pending"}`, reviewedBy));
      if (review.effective) badges.append(el("span", `review-badge verdict-${review.effective.verdict}`, evidenceVerdicts[review.effective.verdict]));
      else if (review.automatic?.method === "rules") badges.append(el("span", "review-badge pending", "待语义审阅"));
      card.append(badges);
      const content = String(item.text || "");
      card.append(el("p", "evidence-text", content.slice(0, 650)));
      if (content.length > 650) {
        const details = el("details");
        details.append(el("summary", "", "展开剩余原文"), el("p", "evidence-text", content.slice(650)));
        card.append(details);
      }
      const source = el("div", "evidence-source");
      source.append(icon("link"), externalLink(item.source || "本地知识库", item.source));
      card.append(source);
      if (review.automatic || review.human) {
        const details = el("details", "evidence-review-details");
        details.dataset.reviewId = String(item.id);
        details.open = openDetails.has(String(item.id));
        details.append(el("summary", "", "审阅依据与记录"));
        if (review.automatic) details.append(renderReviewRecord(review.automatic));
        if (review.human) details.append(renderReviewRecord(review.human));
        const audit = el("button", "button button-small button-secondary", "查看审阅变更轨迹");
        audit.type = "button";
        audit.addEventListener("click", () => switchTab("trace", true));
        details.append(audit);
        card.append(details);
      }
      if (["completed", "failed", "cancelled"].includes(state.run?.status) && state.run?.state?.report) card.append(renderManualReview(item, review));
      else card.append(el("p", "evidence-review-help", "研究结束并生成报告后，可以在这里添加人工审阅。"));
      container.append(card);
    });
  }

  function renderReviewRecord(record) {
    const block = el("div", "evidence-review-record");
    const method = record.method === "human" ? "人工审阅" : record.method === "agent" ? "Agent 审阅" : "规则检查";
    const heading = el("div", "review-record-heading");
    heading.append(el("strong", "", method), el("span", "", evidenceVerdicts[record.verdict] || "未记录结论"));
    if (record.reviewed_at) heading.append(el("time", "", shortDate(record.reviewed_at)));
    block.append(heading, el("p", "review-reason", record.reason || "未记录审阅理由。"));
    const meta = [record.model ? `模型：${record.model}` : "", typeof record.relevant === "boolean" ? `与问题${record.relevant ? "相关" : "不相关"}` : ""].filter(Boolean);
    if (meta.length) block.append(el("p", "review-record-meta", meta.join(" · ")));
    return block;
  }

  function renderManualReview(item, review) {
    const runId = state.run.id;
    const key = JSON.stringify([runId, String(item.id)]);
    let draft = state.reviewDrafts.get(key);
    if (!draft) {
      draft = { verdict: review.human?.verdict || "supported", reason: review.human?.reason || "", fingerprint: review.record.fingerprint || "", open: false, error: "" };
      state.reviewDrafts.set(key, draft);
    }
    const panel = el("details", "manual-review");
    panel.open = draft.open;
    panel.append(el("summary", "", review.human ? "更新人工审阅" : "添加人工审阅"));
    panel.addEventListener("toggle", () => { draft.open = panel.open; });
    const form = el("form", "manual-review-form");
    form.dataset.evidenceId = String(item.id);
    const prefix = `${evidenceDomId(item.id)}-review`;
    const verdictLabel = el("label", "", "审阅结论");
    verdictLabel.htmlFor = `${prefix}-verdict`;
    const verdict = el("select");
    verdict.id = verdictLabel.htmlFor;
    ["supported", "uncertain", "contradicted"].forEach((value) => {
      const option = el("option", "", evidenceVerdicts[value]);
      option.value = value;
      verdict.append(option);
    });
    verdict.value = draft.verdict;
    verdict.addEventListener("change", () => { draft.verdict = verdict.value; });
    const reasonLabel = el("label", "", "审阅理由（必填）");
    reasonLabel.htmlFor = `${prefix}-reason`;
    const reason = el("textarea");
    reason.id = reasonLabel.htmlFor;
    reason.rows = 3;
    reason.maxLength = 2000;
    reason.required = true;
    reason.placeholder = "说明你核对了什么，以及这段原文为何支持、无法支持或需要进一步确认报告中的引用。";
    reason.value = draft.reason;
    reason.addEventListener("input", () => { draft.reason = reason.value; });
    const note = el("p", "evidence-review-help", "人工判断会作为当前结论，Agent 原始审阅仍会保留。对报告已引用的证据选择“有疑问”或“不支持引用”，报告将按草稿处理。");
    note.id = `${prefix}-help`;
    reason.setAttribute("aria-describedby", note.id);
    const error = el("p", "form-error", draft.error);
    error.setAttribute("role", "alert");
    error.hidden = !draft.error;
    const actions = el("div", "manual-review-actions");
    const button = el("button", "button button-small button-primary", state.reviewSaving.has(runId) ? "正在保存…" : "保存人工审阅");
    button.type = "submit";
    const hasFingerprint = /^[a-f0-9]{64}$/i.test(review.record.fingerprint || "");
    const stale = draft.fingerprint !== review.record.fingerprint;
    button.disabled = state.reviewSaving.has(runId) || !hasFingerprint || stale;
    verdict.disabled = reason.disabled = state.reviewSaving.has(runId);
    actions.append(button);
    if (stale && hasFingerprint) {
      const reload = el("button", "button button-small button-secondary", "按当前证据重新审阅");
      reload.type = "button";
      reload.disabled = state.reviewSaving.has(runId);
      reload.addEventListener("click", () => {
        draft.fingerprint = review.record.fingerprint;
        draft.error = "";
        draft.open = true;
        renderEvidence(array(state.run?.state?.evidence));
      });
      actions.append(reload);
      form.append(el("p", "review-stale-note", "报告或证据已经更新。请先查看当前内容，确认后重新审阅；你填写的理由已保留。"));
    } else if (!hasFingerprint) form.append(el("p", "review-stale-note", "尚未读取到此证据的审阅版本，请重新打开历史记录后重试。"));
    form.append(verdictLabel, verdict, reasonLabel, reason, note, error, actions);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (state.reviewSaving.has(runId) || !hasFingerprint || stale) return;
      draft.reason = reason.value;
      draft.verdict = verdict.value;
      draft.open = true;
      if (!draft.reason.trim()) {
        draft.error = "请填写审阅理由，说明当前判断的依据。";
        error.textContent = draft.error;
        error.hidden = false;
        reason.focus();
        return;
      }
      saveEvidenceReview(runId, item.id, key, draft);
    });
    panel.append(form);
    return panel;
  }

  async function saveEvidenceReview(runId, evidenceId, key, draft) {
    const selection = state.selection;
    state.reviewSaving.add(runId);
    draft.error = "";
    renderEvidence(array(state.run?.state?.evidence));
    try {
      const run = await api(`/api/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(evidenceId)}/review`, {
        method: "PUT", body: JSON.stringify({ fingerprint: draft.fingerprint, verdict: draft.verdict, reason: draft.reason.trim() }),
      });
      if (state.deletedRuns.has(runId)) return;
      state.reviewDrafts.delete(key);
      const index = state.runs.findIndex((item) => item.id === runId);
      if (index >= 0) state.runs[index] = run;
      if (state.run?.id === runId && selection === state.selection) {
        state.run = run;
        renderRun();
        renderRecentRuns();
        toast("人工审阅已保存，原始自动审阅记录仍可查看。");
        try {
          const events = await api(`/api/runs/${encodeURIComponent(runId)}/trace`);
          if (state.run?.id === runId && selection === state.selection) { state.events = array(events); renderTrace(); }
        } catch { /* The saved review is durable even when refreshing the trace fails. */ }
      }
    } catch (error) {
      draft.error = error.message;
      if (error.status === 409) {
        draft.error += " 你的理由已保留，请核对最新内容后重试。";
        try {
          const latest = await api(`/api/runs/${encodeURIComponent(runId)}`);
          if (state.run?.id === runId && selection === state.selection) state.run = latest;
        } catch { /* Keep the entered note and the original failure if refresh is unavailable. */ }
      }
    } finally {
      state.reviewSaving.delete(runId);
      if (state.run?.id === runId && selection === state.selection) renderRun();
      else if (state.run?.id === runId) renderEvidence(array(state.run?.state?.evidence));
    }
  }

  function renderTrace() {
    $("trace-count").textContent = state.events.length;
    const container = $("trace-list");
    const expandedIds = new Set([...container.querySelectorAll("details[open]")].map((node) => node.dataset.eventId));
    container.replaceChildren();
    if (!state.events.length) {
      container.append(emptyState("每一步，都可追溯", "任务启动后，这里会记录 Agent 角色、工具输入输出与状态变更。", "code"));
      return;
    }
    state.events.forEach((event) => {
      const row = el("div", "trace-event");
      if (["error", "tool", "tool_call", "tool_error"].includes(event.kind)) row.classList.add(`kind-${event.kind}`);
      const body = el("div", "trace-event-body");
      const header = el("div", "trace-event-header");
      const time = el("time", "", clockTime(event.created_at));
      time.dateTime = event.created_at || "";
      time.title = shortDate(event.created_at);
      header.append(el("span", "trace-node", nodeLabels[event.node] || event.node || "系统"), el("span", "trace-kind", event.kind), time);
      body.append(header, el("p", "trace-message", event.message));
      if (event.data && (typeof event.data !== "object" || Object.keys(event.data).length)) {
        const details = el("details");
        details.dataset.eventId = String(event.id);
        details.open = expandedIds.has(String(event.id));
        details.append(el("summary", "", "查看结构化数据"), el("pre", "", JSON.stringify(event.data, null, 2)));
        body.append(details);
      }
      row.append(el("span", "trace-node-dot"), body);
      container.append(row);
    });
  }

  async function runAction(action, body, buttonIds) {
    if (!state.run) return;
    const runId = state.run.id;
    const selection = state.selection;
    buttonIds.forEach((id) => { $(id).disabled = true; });
    try {
      const run = await api(`/api/runs/${encodeURIComponent(runId)}/${action}`, {
        method: "POST", ...(body ? { body: JSON.stringify(body) } : {}),
      });
      if (state.deletedRuns.has(runId)) return;
      if (state.run?.id === runId && selection === state.selection) {
        state.run = run;
        renderRun();
        subscribeRun();
        await refreshCurrentRun(true);
      }
      await loadRuns();
      toast(action === "cancel" ? "已终止任务。" : action === "resume" ? "已从检查点恢复任务。" : body.approved ? "已批准研究计划。" : "已拒绝研究计划，任务将终止。");
    } catch (error) { toast(error.message, true); }
    finally { buttonIds.forEach((id) => { $(id).disabled = false; }); }
  }

  /* Minimal Markdown parser: generated DOM only, no HTML or executable URLs. */
  function appendInline(parent, text, citations = new Set()) {
    // Escapes must win before formatting so imported text stays literal.
    const escapedPunctuation = /\\([!-/:-@\[-`{-~])/g;
    const unescape = (value) => value.replace(escapedPunctuation, "$1");
    const pattern = /(\\[!-/:-@\[-`{-~]|`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^\s)]+\)|\[[A-Za-z0-9_-]+\])/g;
    let last = 0;
    for (const match of String(text).matchAll(pattern)) {
      parent.append(document.createTextNode(text.slice(last, match.index)));
      const token = match[0];
      if (token.startsWith("\\")) parent.append(document.createTextNode(token.slice(1)));
      else if (token.startsWith("`")) parent.append(el("code", "", token.slice(1, -1)));
      else if (token.startsWith("**")) parent.append(el("strong", "", unescape(token.slice(2, -2))));
      else if (token.includes("](")) {
        const split = token.indexOf("](");
        parent.append(externalLink(unescape(token.slice(1, split)), token.slice(split + 2, -1)));
      } else if (citations.has(token.slice(1, -1))) {
        const id = token.slice(1, -1);
        const button = el("button", "citation-button", token);
        button.type = "button";
        button.title = `查看证据 ${id}`;
        button.addEventListener("click", () => focusEvidence(id));
        parent.append(button);
      } else parent.append(document.createTextNode(token));
      last = match.index + token.length;
    }
    parent.append(document.createTextNode(text.slice(last)));
  }

  function renderMarkdown(markdown, citations) {
    const fragment = document.createDocumentFragment();
    const lines = String(markdown).replace(/\r\n?/g, "\n").split("\n");
    let index = 0;
    const isTableDivider = (line) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
    const tableCells = (line) => {
      const source = line.trim();
      const cells = [""];
      for (let index = 0; index < source.length; index++) {
        const char = source[index];
        if (char === "\\" && index + 1 < source.length) {
          // Preserve the escape for inline rendering; an escaped pipe is content.
          cells[cells.length - 1] += char + source[++index];
        } else if (char === "|") cells.push("");
        else cells[cells.length - 1] += char;
      }
      if (source.startsWith("|")) cells.shift();
      if (cells.length > 1 && cells[cells.length - 1] === "" && source.endsWith("|")) cells.pop();
      return cells.map((cell) => cell.trim());
    };
    const special = (line) => !line.trim() || /^\s*(```|~~~|#{1,6}\s|>\s?|[-*+]\s|\d+[.)]\s|([-*_])\2{2,}\s*$)/.test(line);
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) { index++; continue; }
      if (/^\s*(```|~~~)/.test(line)) {
        const fence = line.trim().slice(0, 3);
        const content = [];
        index++;
        while (index < lines.length && !lines[index].trim().startsWith(fence)) content.push(lines[index++]);
        if (index < lines.length) index++;
        const pre = el("pre");
        pre.append(el("code", "", content.join("\n")));
        fragment.append(pre);
        continue;
      }
      const heading = /^(#{1,6})\s+(.+)$/.exec(line);
      if (heading) {
        const node = el(`h${Math.min(heading[1].length, 4)}`);
        appendInline(node, heading[2], citations);
        fragment.append(node); index++; continue;
      }
      if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { fragment.append(el("hr")); index++; continue; }
      if (index + 1 < lines.length && line.includes("|") && isTableDivider(lines[index + 1])) {
        const wrapper = el("div", "table-scroll");
        const table = el("table");
        const head = el("thead");
        const row = el("tr");
        tableCells(line).forEach((cell) => { const th = el("th"); th.scope = "col"; appendInline(th, cell, citations); row.append(th); });
        head.append(row); table.append(head);
        const body = el("tbody");
        index += 2;
        while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
          const tr = el("tr");
          tableCells(lines[index++]).forEach((cell) => { const td = el("td"); appendInline(td, cell, citations); tr.append(td); });
          body.append(tr);
        }
        table.append(body); wrapper.append(table); fragment.append(wrapper); continue;
      }
      if (/^\s*>/.test(line)) {
        const quote = el("blockquote");
        const content = [];
        while (index < lines.length && /^\s*>/.test(lines[index])) content.push(lines[index++].replace(/^\s*>\s?/, ""));
        appendInline(quote, content.join("\n"), citations); fragment.append(quote); continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
        const ordered = /^\s*\d+[.)]\s+/.test(line);
        const list = el(ordered ? "ol" : "ul");
        const matcher = ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*+]\s+/;
        while (index < lines.length && matcher.test(lines[index])) {
          const li = el("li");
          appendInline(li, lines[index++].replace(matcher, ""), citations); list.append(li);
        }
        fragment.append(list); continue;
      }
      const paragraph = el("p");
      const content = [lines[index++]];
      while (index < lines.length && !special(lines[index]) && !(lines[index].includes("|") && isTableDivider(lines[index + 1] || ""))) content.push(lines[index++]);
      appendInline(paragraph, content.join("\n"), citations);
      fragment.append(paragraph);
    }
    return fragment;
  }

  async function loadDocuments() {
    try {
      state.documents = array(await api("/api/documents"));
      renderDocuments();
    } catch (error) {
      $("document-list").replaceChildren(emptyState("暂时无法读取知识库", error.message, "refresh"));
    }
  }

  function renderDocuments() {
    $("knowledge-count").textContent = state.documents.length;
    const chunks = state.documents.reduce((sum, item) => sum + Number(item.chunks ?? item.chunk_count ?? 0), 0);
    $("document-summary").textContent = `${state.documents.length} 篇文档${chunks ? ` · ${chunks} 个片段` : ""}`;
    const container = $("document-list");
    container.replaceChildren();
    if (!state.documents.length) { container.append(emptyState("还没有知识文档", "添加 Markdown 或文本资料，为下一次研究建立证据基础。", "book")); return; }
    state.documents.forEach((doc) => {
      const card = el("article", "document-card");
      const visual = el("span", "document-icon"); visual.append(icon("file"));
      const content = el("div", "document-card-content");
      content.append(el("h3", "", doc.title));
      const meta = el("div", "document-meta");
      if (doc.chunks !== undefined || doc.chunk_count !== undefined) meta.append(el("span", "", `${doc.chunks ?? doc.chunk_count} 个片段`));
      if (doc.created_at) meta.append(el("span", "", shortDate(doc.created_at)));
      content.append(meta);
      if (doc.source) { const source = el("div", "document-source"); source.append(externalLink(doc.source, doc.source)); content.append(source); }
      const remove = el("button", "icon-button delete-button"); remove.type = "button";
      remove.setAttribute("aria-label", `删除文档 ${doc.title}`); remove.title = "删除文档"; remove.append(icon("trash"));
      remove.addEventListener("click", async () => {
        if (!window.confirm(`删除“${doc.title}”？该文档将不再参与后续检索，现有报告中的证据快照会保留。`)) return;
        remove.disabled = true;
        try { await api(`/api/documents/${encodeURIComponent(doc.id)}`, { method: "DELETE" }); await loadDocuments(); toast("文档已删除。"); }
        catch (error) { toast(error.message, true); remove.disabled = false; }
      });
      card.append(visual, content, remove); container.append(card);
    });
  }

  async function addDocument(event) {
    event.preventDefault(); showError("document-error", null);
    const button = $("add-document"); button.disabled = true;
    try {
      await api("/api/documents", { method: "POST", body: JSON.stringify({ title: $("document-title").value.trim(), content: $("document-content").value.trim(), source: $("document-source").value.trim() || "user" }) });
      $("document-form").reset(); await loadDocuments(); toast("文档已加入知识库，可以用于下一次研究。");
    } catch (error) { showError("document-error", error); }
    finally { button.disabled = false; }
  }

  async function importFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 800000) { showError("document-error", new Error("文件较大。请导入 800 KB 以内的纯文本文件，或拆分为多个文档。")); return; }
    try {
      const content = await file.text();
      if (content.includes("\u0000")) throw new Error("请选择 UTF-8 编码的 .txt 或 .md 纯文本文件。");
      if (content.length > 200000) throw new Error("内容超过 200,000 字符，请拆分文档后再导入。");
      $("document-content").value = content;
      if (!$("document-title").value) $("document-title").value = file.name.replace(/\.(md|markdown|txt)$/i, "").slice(0, 200);
      showError("document-error", null); toast("文件已读取。检查内容后点击“添加到知识库”。");
    } catch (error) { showError("document-error", error); }
  }

  async function loadMemory() {
    try { state.memories = array(await api("/api/memory")); renderMemory(); }
    catch (error) { $("memory-list").replaceChildren(emptyState("暂时无法读取记忆", error.message, "refresh")); }
  }

  function renderMemory() {
    const container = $("memory-list"); container.replaceChildren();
    if (!state.memories.length) { container.append(emptyState("从了解你的背景开始", "保存技术偏好、预算与团队背景，后续研究可以据此考虑你的约束。", "memory")); return; }
    state.memories.forEach((memory) => {
      const card = el("article", "document-card");
      const visual = el("span", "document-icon"); visual.append(icon("memory"));
      const content = el("div", "document-card-content");
      content.append(el("p", "", memory.content), el("div", "document-meta", shortDate(memory.created_at)));
      const remove = el("button", "icon-button delete-button"); remove.type = "button";
      remove.setAttribute("aria-label", `删除记忆：${String(memory.content).slice(0, 40)}`); remove.title = "删除记忆"; remove.append(icon("trash"));
      remove.addEventListener("click", async () => {
        if (!window.confirm("删除这条长期记忆？后续研究将不再读取它。")) return;
        remove.disabled = true;
        try { await api(`/api/memory/${encodeURIComponent(memory.id)}`, { method: "DELETE" }); await loadMemory(); toast("记忆已删除。"); }
        catch (error) { toast(error.message, true); remove.disabled = false; }
      });
      card.append(visual, content, remove); container.append(card);
    });
  }

  async function addMemory(event) {
    event.preventDefault(); showError("memory-error", null); $("add-memory").disabled = true;
    try {
      await api("/api/memory", { method: "POST", body: JSON.stringify({ content: $("memory-content").value.trim() }) });
      $("memory-form").reset(); await loadMemory(); toast("研究偏好已保存。");
    } catch (error) { showError("memory-error", error); }
    finally { $("add-memory").disabled = false; }
  }

  function metricPercent(value) {
    return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
  }

  function evalStat(label, value, note) {
    const box = el("div", "eval-stat"); box.append(el("span", "", label), el("strong", "", value), el("small", "", note)); return box;
  }

  async function loadEvaluations() {
    const container = $("evaluation-content");
    $("refresh-evaluations").disabled = true;
    try {
      const payload = await api("/api/evaluations");
      container.replaceChildren();
      if (!payload?.available || !payload.results) {
        const empty = emptyState("先测量，再改进", "还没有本地评测结果。在项目目录执行评测命令，然后刷新此页面。评测结果以实际运行数据为准。", "chart");
        empty.append(el("code", "evaluation-command", "python -m evidenceforge.cli evaluate"));
        container.append(empty); return;
      }
      const result = payload.results;
      const summary = result.summary;
      if (summary?.bm25 && summary?.hybrid) {
        const grid = el("div", "eval-summary-grid");
        grid.append(
          evalStat("评测查询", number(summary.cases ?? array(result.cases).length), `${result.corpus_documents ?? "—"} 篇资料 · 固定测试集`),
          evalStat("BM25 Recall@5", metricPercent(summary.bm25.recall_at_5), "关键词检索召回率"),
          evalStat("Hybrid Recall@5", metricPercent(summary.hybrid.recall_at_5), "混合检索召回率"),
          evalStat("Hybrid MRR", typeof summary.hybrid.mrr === "number" ? summary.hybrid.mrr.toFixed(3) : "—", "首条相关结果的平均倒数排名"),
        );
        container.append(grid);
        const card = el("section", "eval-card");
        card.append(el("h2", "", `逐项检索结果${result.generated_at ? ` · ${shortDate(result.generated_at)}` : ""}`));
        array(result.cases).forEach((item) => {
          const row = el("div", "eval-case");
          const body = el("div");
          body.append(el("h3", "", item.query || item.id));
          body.append(el("p", "", `BM25 Recall@5 ${metricPercent(item.bm25?.recall_at_5)} / MRR ${typeof item.bm25?.mrr === "number" ? item.bm25.mrr.toFixed(3) : "—"} · Hybrid Recall@5 ${metricPercent(item.hybrid?.recall_at_5)} / MRR ${typeof item.hybrid?.mrr === "number" ? item.hybrid.mrr.toFixed(3) : "—"}`));
          if (array(item.expected_titles).length) body.append(el("p", "", `预期资料：${item.expected_titles.join("、")}`));
          row.append(el("span", "source-id", item.id || "CASE"), body); card.append(row);
        });
        array(result.notes).forEach((note) => card.append(el("p", "form-note", note)));
        container.append(card);
      } else {
        const card = el("section", "eval-card");
        card.append(el("h2", "", "本地评测结果"), el("pre", "", JSON.stringify(result, null, 2))); container.append(card);
      }
    } catch (error) { container.replaceChildren(emptyState("暂时无法读取评测结果", error.message, "refresh")); }
    finally { $("refresh-evaluations").disabled = false; }
  }

  function bindEvents() {
    document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
    document.querySelectorAll("[data-tab]").forEach((button) => {
      button.addEventListener("click", () => switchTab(button.dataset.tab));
      button.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const tabs = ["report", "evidence", "trace"];
        let index = tabs.indexOf(state.activeTab);
        index = event.key === "Home" ? 0 : event.key === "End" ? 2 : (index + (event.key === "ArrowRight" ? 1 : 2)) % 3;
        switchTab(tabs[index], true);
      });
    });
    $("question").addEventListener("input", () => { state.questionRevision++; });
    document.querySelectorAll("[data-question]").forEach((button) => button.addEventListener("click", () => { setQuestion(button.dataset.question); $("question").focus(); }));
    $("new-research").addEventListener("click", newResearch);
    $("research-form").addEventListener("submit", createResearch);
    $("run-mode").addEventListener("change", updateModeNotice);
    $("refresh-runs").addEventListener("click", async () => { await Promise.all([loadRuns(), loadHealth()]); await refreshCurrentRun(true); });
    $("accept-approval").addEventListener("click", () => runAction("approve", { approved: true, feedback: $("approval-feedback").value.trim() }, ["accept-approval", "reject-approval"]));
    $("reject-approval").addEventListener("click", () => runAction("approve", { approved: false, feedback: $("approval-feedback").value.trim() }, ["accept-approval", "reject-approval"]));
    $("cancel-run").addEventListener("click", () => runAction("cancel", null, ["cancel-run"]));
    $("resume-run").addEventListener("click", () => runAction("resume", null, ["resume-run"]));
    $("copy-report").addEventListener("click", async () => {
      const runId = state.run?.id;
      const selection = state.selection;
      if (!state.run?.state?.report || state.copyingReport) return;
      state.copyingReport = true;
      $("copy-report").disabled = true;
      let report;
      try {
        const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/report`, { credentials: "same-origin", cache: "no-store" });
        if (!response.ok) throw new Error(`获取最新报告失败（HTTP ${response.status}），请重试。`);
        report = await response.text();
        if (!report.trim()) throw new Error("最新报告内容为空，请刷新任务后重试。");
      } catch (error) {
        if (state.run?.id === runId && selection === state.selection) toast(error instanceof TypeError ? "无法获取最新报告，请确认本地服务正在运行后重试。" : error.message, true);
      }
      try {
        if (report && state.run?.id === runId && selection === state.selection) {
          await navigator.clipboard.writeText(report);
          toast("Markdown 已复制，包含最新审阅记录。");
        }
      } catch { toast("浏览器未允许写入剪贴板，请使用“导出报告”保存。", true); }
      finally {
        state.copyingReport = false;
        $("copy-report").disabled = !state.run?.state?.report;
      }
    });
    $("document-form").addEventListener("submit", addDocument);
    $("document-file").addEventListener("change", importFile);
    $("refresh-documents").addEventListener("click", loadDocuments);
    $("memory-form").addEventListener("submit", addMemory);
    $("refresh-memory").addEventListener("click", loadMemory);
    $("refresh-evaluations").addEventListener("click", loadEvaluations);
    $("mobile-menu").addEventListener("click", () => {
      const open = $("sidebar").classList.toggle("open");
      $("mobile-backdrop").hidden = !open; $("mobile-menu").setAttribute("aria-expanded", String(open));
    });
    $("mobile-backdrop").addEventListener("click", closeSidebar);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeSidebar();
      const editing = event.target.closest("input, textarea, select, [contenteditable=true]");
      if (!editing && event.key.toLowerCase() === "n" && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); $("new-research").click(); }
    });
    window.addEventListener("beforeunload", stopSubscription);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        refreshCurrentRun(true);
        if (isActive() && !state.source) subscribeRun();
      }
    });
  }

  bindEvents();
  renderEvidence([]);
  renderTrace();
  Promise.allSettled([loadHealth(), loadRuns(true)]);
})();

"use strict";

// OpenAPI is untrusted document content: create DOM nodes, never inject HTML.
(() => {
  const $ = (id) => document.getElementById(id);
  const methods = new Set(["get", "post", "put", "patch", "delete", "head", "options"]);
  const groups = [
    { id: "system", label: "服务状态", matches: (path) => path === "/api/health" },
    { id: "runs", label: "研究任务", matches: (path) => path.startsWith("/api/runs") },
    { id: "documents", label: "知识库", matches: (path) => path.startsWith("/api/documents") },
    { id: "memory", label: "长期记忆", matches: (path) => path.startsWith("/api/memory") },
    { id: "evaluations", label: "评测结果", matches: (path) => path.startsWith("/api/evaluations") },
    { id: "other", label: "其他接口", matches: () => true },
  ];
  const titles = {
    "get /api/health": "检查服务状态与模型配置",
    "get /api/runs": "列出研究任务", "post /api/runs": "创建研究任务",
    "get /api/runs/{run_id}": "读取任务状态与研究结果",
    "post /api/runs/{run_id}/approve": "批准或拒绝研究计划",
    "post /api/runs/{run_id}/resume": "从检查点恢复失败的任务",
    "post /api/runs/{run_id}/cancel": "终止研究任务",
    "get /api/runs/{run_id}/trace": "读取结构化执行轨迹",
    "get /api/runs/{run_id}/events": "订阅实时事件（SSE）",
    "get /api/runs/{run_id}/report": "下载 Markdown 研究报告",
    "get /api/documents": "列出知识库文档", "post /api/documents": "导入知识文档",
    "delete /api/documents/{document_id}": "删除知识文档",
    "get /api/memory": "列出长期记忆", "post /api/memory": "保存长期记忆",
    "delete /api/memory/{memory_id}": "删除长期记忆",
    "get /api/evaluations": "读取本地评测结果",
  };
  const localNotes = {
    "get /api/runs/{run_id}/events": "事件流使用 text/event-stream。trace 事件包含节点、事件类型、消息与数据；status 事件标记当前阶段结束。客户端可以通过 after 查询参数或 Last-Event-ID 请求头，从指定事件之后继续读取。",
    "get /api/runs/{run_id}/report": "已生成的报告以 text/markdown 返回，并设置附件下载文件名。报告尚未生成时，服务会返回错误。",
    "post /api/runs": "任务在后台推进。创建后通过任务查询或 SSE 订阅读取进度。demo 模式无需模型 API；live 模式需要服务端配置。",
    "post /api/runs/{run_id}/approve": "仅适用于 awaiting_approval 状态。approved 为 true 时继续执行，false 时终止。feedback 用于补充研究约束。",
    "post /api/runs/{run_id}/resume": "恢复失败或因服务重启中断的任务。等待人工审批的任务应调用 approve。",
    "get /api/evaluations": "读取已生成的本地评测文件；此接口不会运行评测。没有结果时 available 为 false。",
  };
  let documentSpec = null;
  let endpoints = [];
  let activeMethod = "all";

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function schemaName(ref) {
    return String(ref || "").split("/").pop() || "Schema";
  }

  function schemaId(name) {
    return `schema-${encodeURIComponent(name)}`;
  }

  function resolve(ref) {
    if (typeof ref !== "string" || !ref.startsWith("#/")) return null;
    return ref.slice(2).split("/").reduce((node, key) => node?.[key.replace(/~1/g, "/").replace(/~0/g, "~")], documentSpec);
  }

  function typeText(schema, depth = 0) {
    if (!schema || depth > 4) return "any";
    if (schema.$ref) return schemaName(schema.$ref);
    if (schema.anyOf || schema.oneOf) return (schema.anyOf || schema.oneOf).map((item) => typeText(item, depth + 1)).join(" | ");
    if (schema.allOf) return schema.allOf.map((item) => typeText(item, depth + 1)).join(" & ");
    if (schema.type === "array") return `array<${typeText(schema.items, depth + 1)}>`;
    if (schema.enum) return schema.enum.map((value) => JSON.stringify(value)).join(" | ");
    if (schema.type) return Array.isArray(schema.type) ? schema.type.join(" | ") : schema.type;
    if (schema.properties) return "object";
    return "any";
  }

  function referenceLink(ref) {
    const name = schemaName(ref);
    const link = el("a", "api-schema-ref", name);
    link.href = `#${schemaId(name)}`;
    link.addEventListener("click", () => {
      const target = $(schemaId(name));
      if (target) target.open = true;
    });
    return link;
  }

  function appendType(parent, schema) {
    if (schema?.$ref && resolve(schema.$ref)) parent.append(referenceLink(schema.$ref));
    else parent.append(el("code", "api-type", typeText(schema)));
  }

  function constraints(schema) {
    const result = [];
    if (!schema || typeof schema !== "object") return result;
    if (schema.minLength !== undefined) result.push(`最少 ${schema.minLength} 字符`);
    if (schema.maxLength !== undefined) result.push(`最多 ${schema.maxLength} 字符`);
    if (schema.minimum !== undefined) result.push(`≥ ${schema.minimum}`);
    if (schema.maximum !== undefined) result.push(`≤ ${schema.maximum}`);
    if (schema.exclusiveMinimum !== undefined) result.push(`> ${schema.exclusiveMinimum}`);
    if (schema.exclusiveMaximum !== undefined) result.push(`< ${schema.exclusiveMaximum}`);
    if (schema.minItems !== undefined) result.push(`最少 ${schema.minItems} 项`);
    if (schema.maxItems !== undefined) result.push(`最多 ${schema.maxItems} 项`);
    if (schema.pattern) result.push(`格式 ${schema.pattern}`);
    if (schema.format) result.push(`格式 ${schema.format}`);
    if (Object.hasOwn(schema, "default")) result.push(`默认 ${JSON.stringify(schema.default)}`);
    if (schema.additionalProperties === false) result.push("不接受额外字段");
    return result;
  }

  function fieldsTable(properties, required = [], location = null) {
    const wrapper = el("div", "api-table-scroll");
    const table = el("table", "api-fields-table");
    const head = el("thead"); const header = el("tr");
    ["字段", "类型", "必填", "说明与限制"].forEach((title) => { const th = el("th", "", title); th.scope = "col"; header.append(th); });
    head.append(header); table.append(head);
    const body = el("tbody");
    Object.entries(properties || {}).forEach(([name, rawSchema]) => {
      const schema = rawSchema || {};
      const row = el("tr"); const nameCell = el("td");
      nameCell.append(el("code", "api-field-name", name));
      if (location?.[name]) nameCell.append(el("small", "api-field-location", location[name]));
      const typeCell = el("td"); appendType(typeCell, schema);
      const requiredCell = el("td", required.includes(name) ? "api-required" : "api-optional", required.includes(name) ? "是" : "否");
      const detailCell = el("td");
      if (schema.description || schema.title) detailCell.append(el("p", "api-field-description", schema.description || schema.title));
      const limits = constraints(schema);
      if (limits.length) detailCell.append(el("p", "api-field-constraints", limits.join(" · ")));
      if (!detailCell.childNodes.length) detailCell.append(document.createTextNode("—"));
      row.append(nameCell, typeCell, requiredCell, detailCell); body.append(row);
    });
    table.append(body); wrapper.append(table); return wrapper;
  }

  function schemaBlock(schema) {
    const block = el("div", "api-schema-block");
    if (!schema || !Object.keys(schema).length) {
      block.append(el("p", "api-hint", "此接口没有在 OpenAPI 中声明详细字段。请以实际接口响应及项目文档为准。"));
      return block;
    }
    if (schema.$ref) {
      const row = el("div", "api-reference-row"); row.append(el("span", "", "数据结构"), referenceLink(schema.$ref)); block.append(row);
      const resolved = resolve(schema.$ref);
      if (resolved?.properties) block.append(fieldsTable(resolved.properties, resolved.required || []));
      if (resolved?.additionalProperties === false) block.append(el("p", "api-hint", "不接受未声明的额外字段。"));
    } else if (schema.properties) block.append(fieldsTable(schema.properties, schema.required || []));
    else {
      const line = el("div", "api-reference-row"); line.append(el("span", "", "类型")); appendType(line, schema); block.append(line);
      if (schema.type === "array" && schema.items?.$ref) {
        const items = el("div", "api-reference-row"); items.append(el("span", "", "数组元素"), referenceLink(schema.items.$ref)); block.append(items);
      }
    }
    return block;
  }

  function mediaTypeSection(content) {
    const fragment = document.createDocumentFragment();
    Object.entries(content || {}).forEach(([media, definition]) => {
      const line = el("div", "api-content-type"); line.append(el("code", "", media)); fragment.append(line, schemaBlock(definition.schema));
    });
    return fragment;
  }

  function endpointCard(endpoint) {
    const { method, path, operation, common } = endpoint;
    const card = el("details", "api-endpoint"); card.id = endpoint.id;
    const summary = el("summary", "api-endpoint-summary");
    const main = el("div", "api-endpoint-summary-main");
    main.append(el("span", `api-method api-method-${method}`, method.toUpperCase()), el("code", "api-endpoint-path", path));
    summary.append(main, el("span", "api-endpoint-title", endpoint.title), el("span", "api-expand-indicator", "+"));
    card.append(summary);
    const body = el("div", "api-endpoint-body");
    if (operation.description) body.append(el("p", "api-description", operation.description));
    if (localNotes[`${method} ${path}`]) body.append(el("p", "api-implementation-note", localNotes[`${method} ${path}`]));
    const params = [...(common.parameters || []), ...(operation.parameters || [])].map((parameter) => parameter.$ref ? resolve(parameter.$ref) || parameter : parameter);
    if (params.length) {
      body.append(el("h3", "", "路径与查询参数"));
      const properties = {}; const required = []; const locations = {};
      params.forEach((parameter) => {
        properties[parameter.name] = { ...(parameter.schema || {}), description: parameter.description || parameter.schema?.description };
        if (parameter.required) required.push(parameter.name);
        locations[parameter.name] = parameter.in || "";
      });
      body.append(fieldsTable(properties, required, locations));
    }
    const request = operation.requestBody?.$ref ? resolve(operation.requestBody.$ref) : operation.requestBody;
    if (request) {
      const title = el("h3", "", "请求体"); title.append(el("span", "api-inline-note", request.required ? "必填" : "选填")); body.append(title);
      if (request.description) body.append(el("p", "api-description", request.description));
      body.append(mediaTypeSection(request.content));
    } else if (!params.length) body.append(el("p", "api-hint", "此接口不需要请求体或参数。"));
    if (operation.responses) {
      body.append(el("h3", "", "响应"));
      Object.entries(operation.responses).forEach(([status, rawResponse]) => {
        const response = rawResponse.$ref ? resolve(rawResponse.$ref) || rawResponse : rawResponse;
        const responseBox = el("details", "api-response");
        const responseSummary = el("summary"); responseSummary.append(el("code", /^2/.test(status) ? "api-status-success" : "api-status-other", status), el("span", "", response.description || "Response"));
        responseBox.append(responseSummary);
        const content = el("div", "api-response-content");
        if (response.content && Object.keys(response.content).length) content.append(mediaTypeSection(response.content));
        else content.append(el("p", "api-hint", "此响应未声明内容结构。"));
        responseBox.append(content); body.append(responseBox);
      });
    }
    const operationId = el("p", "api-operation-id"); operationId.append(document.createTextNode("Operation ID: "), el("code", "", operation.operationId || "—")); body.append(operationId);
    card.append(body); return card;
  }

  function renderEndpoints() {
    const query = $("api-search").value.trim().toLowerCase();
    const matches = endpoints.filter((endpoint) => (activeMethod === "all" || endpoint.method === activeMethod) && endpoint.search.includes(query));
    const previousOpen = new Set([...document.querySelectorAll(".api-endpoint[open]")].map((item) => item.id));
    const container = $("api-endpoints"); container.replaceChildren();
    groups.forEach((group) => {
      const items = matches.filter((endpoint) => endpoint.group === group.id);
      if (!items.length) return;
      const section = el("section", "api-endpoint-group"); section.id = `group-${group.id}`;
      section.append(el("h3", "api-group-title", group.label));
      items.forEach((item) => { const card = endpointCard(item); card.open = previousOpen.has(item.id); section.append(card); });
      container.append(section);
    });
    $("api-no-results").hidden = matches.length !== 0;
    $("api-endpoint-count").textContent = `${matches.length} / ${endpoints.length} 个接口`;
  }

  function renderSchemas() {
    const container = $("api-schema-list"); container.replaceChildren();
    const schemas = documentSpec.components?.schemas || {};
    if (!Object.keys(schemas).length) { container.append(el("p", "api-hint", "当前接口定义未声明独立数据结构。")); return; }
    Object.entries(schemas).forEach(([name, schema]) => {
      const details = el("details", "api-model"); details.id = schemaId(name);
      const summary = el("summary"); summary.append(el("code", "", name), el("span", "api-inline-note", `${schema.type || "object"}${schema.properties ? ` · ${Object.keys(schema.properties).length} 个字段` : ""}`), el("span", "api-expand-indicator", "+"));
      details.append(summary);
      const body = el("div", "api-model-body");
      if (schema.description) body.append(el("p", "api-description", schema.description));
      if (schema.properties) body.append(fieldsTable(schema.properties, schema.required || []));
      else body.append(schemaBlock(schema));
      const raw = el("details", "api-raw-schema"); raw.append(el("summary", "", "查看原始 JSON Schema"), el("pre", "", JSON.stringify(schema, null, 2))); body.append(raw);
      details.append(body); container.append(details);
    });
  }

  function revealHash() {
    const hash = window.location.hash.slice(1);
    const target = hash ? document.getElementById(hash) : null;
    if (target?.tagName === "DETAILS") target.open = true;
    if (target) target.scrollIntoView({ block: "start" });
  }

  async function boot() {
    $("api-base-url").textContent = window.location.origin;
    try {
      const response = await fetch("/openapi.json", { credentials: "same-origin" });
      if (!response.ok) throw new Error(`无法读取 OpenAPI 定义（HTTP ${response.status}）。`);
      documentSpec = await response.json();
      if (!documentSpec.paths || typeof documentSpec.paths !== "object") throw new Error("接口定义中没有有效的 paths 字段。");
      $("api-version").textContent = `v${documentSpec.info?.version || "—"} · OpenAPI ${documentSpec.openapi || "—"}`;
      Object.entries(documentSpec.paths).forEach(([path, common]) => {
        Object.entries(common).forEach(([method, operation]) => {
          if (!methods.has(method)) return;
          const title = titles[`${method} ${path}`] || operation.summary || operation.operationId || "接口";
          const group = groups.find((item) => item.matches(path));
          endpoints.push({ method, path, operation, common, title, group: group.id,
            id: `endpoint-${method}-${path.replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-|-$/g, "")}`,
            search: `${method} ${path} ${title} ${operation.summary || ""} ${JSON.stringify(operation)}`.toLowerCase(),
          });
        });
      });
      const nav = $("api-nav"); nav.replaceChildren();
      groups.forEach((group) => {
        const count = endpoints.filter((endpoint) => endpoint.group === group.id).length;
        if (!count) return;
        const link = el("a", "api-nav-link"); link.href = `#group-${group.id}`;
        link.append(el("span", "", group.label), el("small", "", count));
        link.addEventListener("click", () => { activeMethod = "all"; $("api-search").value = ""; updateFilters(); renderEndpoints(); });
        nav.append(link);
      });
      renderEndpoints(); renderSchemas(); revealHash();
    } catch (error) {
      $("api-version").textContent = "服务未连接";
      $("api-load-error").hidden = false;
      $("api-load-error").textContent = `${error.message || "文档读取失败。"} 请确认本地服务正在运行，刷新页面重试。`;
      $("api-endpoints").replaceChildren(); $("api-nav").replaceChildren();
    }
  }

  function updateFilters() {
    document.querySelectorAll("[data-method]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.method === activeMethod)));
  }

  $("api-search").addEventListener("input", renderEndpoints);
  document.querySelectorAll("[data-method]").forEach((button) => button.addEventListener("click", () => { activeMethod = button.dataset.method; updateFilters(); renderEndpoints(); }));
  window.addEventListener("hashchange", revealHash);
  boot();
})();

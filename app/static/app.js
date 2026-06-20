const state = {
  servers: [],
  selectedId: null,
  audit: [],
  activeDetailTab: "overview",
  auditCollapsed: false,
  credentials: {},
  credentialRequests: {},
};

const $ = (id) => document.getElementById(id);
const basePath = window.location.pathname.endsWith("/") ? window.location.pathname : `${window.location.pathname}/`;
const apiBase = new URL("api/", window.location.origin + basePath).pathname;

async function api(path, options = {}) {
  const cleanPath = path.startsWith("/api/") ? path.slice(5) : path.replace(/^\/+/, "");
  const res = await fetch(`${apiBase}${cleanPath}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

function showApp(user) {
  $("loginView").classList.add("hidden");
  $("appView").classList.remove("hidden");
  $("whoami").textContent = user.username;
}

function showLogin() {
  $("appView").classList.add("hidden");
  $("loginView").classList.remove("hidden");
}

async function loadAll() {
  state.servers = await api("/api/servers");
  state.audit = await api("/api/audit");
  if (!state.selectedId && state.servers.length) state.selectedId = state.servers[0].id;
  if (state.selectedId && !state.servers.some((s) => s.id === state.selectedId)) state.selectedId = state.servers[0]?.id || null;
  render();
}

function filteredServers() {
  const q = $("searchBox").value.trim().toLowerCase();
  if (!q) return state.servers;
  return state.servers.filter((s) => {
    return [s.name, s.hostname, s.ipv4, s.ipv6, s.login_user, s.provider, s.region, ...(s.tags || [])]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(q);
  });
}

function render() {
  const rows = filteredServers();
  $("serverRows").innerHTML = rows.length
    ? rows.map(rowHtml).join("")
    : `<tr><td colspan="7" class="text-secondary text-center py-5">暂无服务器</td></tr>`;

  document.querySelectorAll(".ops-row").forEach((el) => {
    el.addEventListener("click", () => {
      state.selectedId = Number(el.dataset.id);
      render();
    });
  });
  document.querySelectorAll("[data-row-action]").forEach((el) => {
    el.addEventListener("click", async (event) => {
      event.stopPropagation();
      const id = Number(el.closest(".ops-row")?.dataset.id);
      const server = state.servers.find((item) => item.id === id);
      if (!server) return;
      state.selectedId = server.id;
      if (el.dataset.rowAction === "edit") openForm(server);
      if (el.dataset.rowAction === "check") {
        await api(`/api/servers/${server.id}/check`, { method: "POST" });
        await loadAll();
      }
      if (el.dataset.rowAction === "inspect") {
        await api(`/api/servers/${server.id}/inspect`, { method: "POST" });
        await loadAll();
      }
    });
  });

  const online = state.servers.filter((s) => s.last_status === "online").length;
  const offline = state.servers.filter((s) => s.last_status === "offline").length;
  const unknown = state.servers.filter((s) => s.last_status === "unknown").length;
  const last = state.servers.map((s) => s.last_checked_at).filter(Boolean).sort().pop() || "";
  const checked = state.servers.filter((s) => s.last_checked_at).length;

  $("metricTotal").textContent = state.servers.length;
  $("metricOnline").textContent = online;
  $("metricUnknown").textContent = unknown;
  $("metricOffline").textContent = `${offline} 台离线`;
  $("metricLast").textContent = last ? last.slice(0, 16) : "无";
  $("metricChecked").textContent = `${checked} 台有检查记录`;
  $("summaryText").textContent = `${rows.length} 台可见，${online} 台在线`;
  $("tableCount").textContent = `${rows.length} 台可见`;
  $("assetFooterCount").textContent = `共 ${rows.length} 条`;
  $("summaryOnlineBadge").textContent = `${online} 台在线`;

  renderDetail();
  renderAudit();
  renderAuditDrawer();
}

function rowHtml(s) {
  const active = s.id === state.selectedId ? "active" : "";
  const tags = (s.tags || []).slice(0, 3).map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("");
  return `
    <tr class="ops-row ${active}" data-id="${s.id}">
      <td>
        <span class="server-title">${escapeHtml(s.name)}</span>
        <span class="server-sub">${escapeHtml(s.provider || "未设置")}</span>
        <span class="tags">${tags}</span>
      </td>
      <td data-label="地址">
        <span class="server-host">${escapeHtml(s.hostname)}</span>
        <span class="server-sub">${escapeHtml(s.ipv4 || s.ipv6 || "")}</span>
      </td>
      <td data-label="登录">
        <strong>${escapeHtml(s.login_user)}</strong>
        <span class="server-sub">${escapeHtml(authLabel(s.auth_type))}</span>
      </td>
      <td data-label="状态"><span class="status ${escapeHtml(s.last_status)}">${escapeHtml(statusLabel(s.last_status))}</span></td>
      <td data-label="配置">
        <span class="status config-${escapeHtml(s.config_status || "unknown")}">${escapeHtml(configLabel(s.config_status))}</span>
        <span class="server-sub">${escapeHtml(s.config_summary || "未检查")}</span>
      </td>
      <td data-label="最近检查"><span class="server-sub">${escapeHtml(s.last_checked_at ? s.last_checked_at.slice(0, 16) : "未检查")}</span></td>
      <td data-label="操作">
        <div class="row-actions">
          <button class="btn btn-light btn-icon btn-sm" type="button" title="检查 SSH" data-row-action="check"><i class="ti ti-activity-heartbeat"></i></button>
          <button class="btn btn-light btn-icon btn-sm" type="button" title="检查配置" data-row-action="inspect"><i class="ti ti-list-search"></i></button>
          <button class="btn btn-light btn-icon btn-sm" type="button" title="编辑" data-row-action="edit"><i class="ti ti-pencil"></i></button>
        </div>
      </td>
    </tr>`;
}

function renderDetail() {
  const s = selected();
  if (!s) {
    $("summarySelection").textContent = "选择服务器查看连接与凭据状态";
    $("emptyState").classList.remove("hidden");
    $("detailPanel").classList.add("hidden");
    return;
  }
  $("summarySelection").textContent = `当前选中：${s.name}`;
  $("emptyState").classList.add("hidden");
  $("detailPanel").classList.remove("hidden");
  $("detailName").textContent = s.name;
  $("detailHost").textContent = s.hostname;
  $("detailStatus").textContent = statusLabel(s.last_status);
  $("detailStatus").className = `status ${s.last_status}`;
  renderDetailTabs();
  $("detailIpv4").textContent = s.ipv4 || "未设置";
  $("detailIpv6").textContent = s.ipv6 || "未设置";
  $("detailLoginUser").textContent = s.login_user || "未设置";
  $("detailAuthType").textContent = authLabel(s.auth_type);
  $("detailSshHost").textContent = s.ssh_host || s.ipv4 || s.hostname || "未设置";
  $("detailSshPort").textContent = s.ssh_port || 22;
  $("detailSshKeyPath").textContent = s.ssh_key_path || "未设置";
  $("detailSshOptions").textContent = s.ssh_options || "未设置";
  $("detailServiceCode").textContent = s.service_code || "未设置";
  $("detailProviderRegion").textContent = [s.provider, s.region].filter(Boolean).join(" / ") || "未设置";
  $("detailTags").innerHTML = (s.tags || []).map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("") || "未设置";
  $("detailNotes").textContent = s.notes || "无";
  $("detailCreated").textContent = formatDateTime(s.created_at);
  $("detailUpdated").textContent = formatDateTime(s.updated_at);
  $("detailCheckedAt").textContent = formatDateTime(s.last_checked_at);
  $("detailConfigStatus").innerHTML = `<span class="status config-${escapeHtml(s.config_status || "unknown")}">${escapeHtml(configLabel(s.config_status))}</span> ${escapeHtml(s.config_summary || "未检查")}`;
  $("detailConfigStatusPanel").innerHTML = `<span class="status config-${escapeHtml(s.config_status || "unknown")}">${escapeHtml(configLabel(s.config_status))}</span> ${escapeHtml(s.config_summary || "未检查")}`;
  $("detailConfigReport").innerHTML = configReportHtml(s.config_report || {});
  $("inspectionSummary").textContent = s.last_config_check_at ? `检查时间 ${formatDateTime(s.last_config_check_at)}` : "未检查";
  $("installedAppsCount").textContent = `${(s.installed_apps || []).length} 项`;
  $("runningServicesCount").textContent = `${(s.services || []).length} 项`;
  $("installedApps").innerHTML = listAppsHtml(s.installed_apps || []);
  $("runningServices").innerHTML = listServicesHtml(s.services || []);
  $("sshCommand").textContent = sshCommand(s);
  renderCredentialField(s);
}

function renderDetailTabs() {
  document.querySelectorAll("[data-detail-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.detailTab === state.activeDetailTab);
  });
  document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.tabPanel !== state.activeDetailTab);
  });
}

function renderAudit() {
  $("auditRows").innerHTML = state.audit.length
    ? state.audit.map((a) => `
      <article class="audit-item">
        <div class="audit-item-top">
          <span class="audit-action ${escapeHtml(a.action)}">${escapeHtml(actionLabel(a.action))}</span>
          <time>${escapeHtml((a.created_at || "").slice(5, 16))}</time>
        </div>
        <strong>${escapeHtml(a.actor)}</strong>
        <span>${escapeHtml(a.target_type)} #${escapeHtml(a.target_id || "")}</span>
        <p>${escapeHtml(a.detail || "无")}</p>
      </article>
    `).join("")
    : `<div class="text-secondary text-center py-4">暂无审计记录</div>`;
}

function renderAuditDrawer() {
  $("auditDrawer").classList.toggle("collapsed", state.auditCollapsed);
  document.querySelector(".main-grid")?.classList.toggle("audit-collapsed", state.auditCollapsed);
  $("toggleAuditBtn").title = state.auditCollapsed ? "展开" : "收缩";
}

function openForm(server = null) {
  $("dialogTitle").textContent = server ? "编辑服务器" : "新增服务器";
  $("serverId").value = server?.id || "";
  for (const id of ["name", "hostname", "ipv4", "ipv6", "provider", "region", "login_user", "auth_type", "ssh_host", "ssh_port", "ssh_key_path", "ssh_options", "service_code", "notes"]) {
    $(id).value = server?.[id] || "";
  }
  $("ssh_port").value = server?.ssh_port || 22;
  $("tags").value = (server?.tags || []).join(", ");
  $("credential").value = "";
  $("deleteBtn").classList.toggle("hidden", !server);
  $("serverDialog").showModal();
}

function payloadFromForm() {
  return {
    name: $("name").value.trim(),
    hostname: $("hostname").value.trim(),
    ipv4: $("ipv4").value.trim(),
    ipv6: $("ipv6").value.trim(),
    provider: $("provider").value.trim(),
    region: $("region").value.trim(),
    login_user: $("login_user").value.trim(),
    auth_type: $("auth_type").value,
    ssh_host: $("ssh_host").value.trim(),
    ssh_port: Number($("ssh_port").value || 22),
    ssh_key_path: $("ssh_key_path").value.trim(),
    ssh_options: $("ssh_options").value.trim(),
    service_code: $("service_code").value.trim(),
    tags: $("tags").value.split(",").map((x) => x.trim()).filter(Boolean),
    credential: $("credential").value,
    notes: $("notes").value.trim(),
  };
}

function selected() {
  return state.servers.find((s) => s.id === state.selectedId);
}

function renderCredentialField(server) {
  const input = $("detailCredential");
  input.type = "password";
  updateCredentialToggle(false);
  const cached = Object.prototype.hasOwnProperty.call(state.credentials, server.id);
  if (cached) {
    const value = state.credentials[server.id];
    input.value = value;
    input.placeholder = value ? "" : "未保存凭据";
    $("credentialStatus").textContent = value ? "凭据已加载，默认遮蔽显示。" : "这台服务器还没有保存凭据。";
    return;
  }
  input.value = "";
  input.placeholder = "正在加载凭据";
  $("credentialStatus").textContent = "正在从加密存储中读取凭据。";
  loadCredential(server.id);
}

async function loadCredential(serverId, options = {}) {
  if (!options.force && Object.prototype.hasOwnProperty.call(state.credentials, serverId)) {
    return state.credentials[serverId];
  }
  if (!options.force && state.credentialRequests[serverId]) {
    return state.credentialRequests[serverId];
  }
  state.credentialRequests[serverId] = api(`/api/servers/${serverId}/credential`)
    .then(async (data) => {
      const value = data.credential || "";
      state.credentials[serverId] = value;
      if (selected()?.id === serverId) {
        $("detailCredential").value = value;
        $("detailCredential").placeholder = value ? "" : "未保存凭据";
        $("credentialStatus").textContent = value ? "凭据已加载，默认遮蔽显示。" : "这台服务器还没有保存凭据。";
      }
      state.audit = await api("/api/audit");
      renderAudit();
      return value;
    })
    .catch((error) => {
      if (selected()?.id === serverId) {
        $("detailCredential").value = "";
        $("detailCredential").placeholder = "凭据读取失败";
        $("credentialStatus").textContent = error.message || "凭据读取失败。";
      }
      return "";
    })
    .finally(() => {
      delete state.credentialRequests[serverId];
    });
  return state.credentialRequests[serverId];
}

function updateCredentialToggle(visible) {
  const icon = $("toggleCredentialBtn").querySelector("i");
  icon.className = visible ? "ti ti-eye-off" : "ti ti-eye";
  $("toggleCredentialBtn").title = visible ? "隐藏凭据" : "显示凭据";
  $("revealBtn").innerHTML = visible ? '<i class="ti ti-eye-off"></i>隐藏凭据' : '<i class="ti ti-eye"></i>显示凭据';
}

async function showCredential(visible) {
  const s = selected();
  if (!s) return;
  await loadCredential(s.id);
  $("detailCredential").type = visible ? "text" : "password";
  updateCredentialToggle(visible);
  $("credentialStatus").textContent = visible ? "凭据正在明文显示。" : "凭据已加载，默认遮蔽显示。";
}

function authLabel(value) {
  return value === "key" ? "密钥" : "密码";
}

function statusLabel(value) {
  return {
    online: "在线",
    offline: "离线",
    unknown: "未检查",
  }[value || "unknown"] || value;
}

function configLabel(value) {
  return {
    ok: "正常",
    warning: "需确认",
    error: "失败",
    unknown: "未检查",
  }[value || "unknown"] || value;
}

function actionLabel(value) {
  return {
    login: "登录",
    create: "创建",
    update: "更新",
    delete: "删除",
    check: "检查",
    inspect: "配置检查",
    reveal_credential: "查看凭据",
  }[value] || value;
}

function sshCommand(server) {
  const args = ["ssh"];
  if (server.ssh_key_path) args.push("-i", server.ssh_key_path);
  if (server.ssh_port && Number(server.ssh_port) !== 22) args.push("-p", String(server.ssh_port));
  if (server.ssh_options) args.push(server.ssh_options);
  args.push(`${server.login_user}@${server.ssh_host || server.ipv4 || server.hostname}`);
  return args.join(" ");
}

function configReportHtml(report) {
  const osName = (report.os || []).find((line) => line.startsWith("PRETTY_NAME=")) || "";
  const cleanOs = osName ? osName.replace("PRETTY_NAME=", "").replace(/^"|"$/g, "") : "未记录";
  const items = [
    ["系统", cleanOs],
    ["内核", report.kernel || "未记录"],
    ["CPU", report.cpu_count || "未记录"],
    ["内存", report.memory || "未记录"],
    ["磁盘", report.disk_root || "未记录"],
  ];
  if (report.error) items.unshift(["错误", report.error]);
  return items.map(([key, value]) => `<span class="config-line"><strong>${escapeHtml(key)}</strong>${escapeHtml(value)}</span>`).join("");
}

function listAppsHtml(apps) {
  if (!apps.length) return `<div class="muted-item">未记录</div>`;
  const custom = apps.filter((app) => (app.category || "custom") !== "system");
  const system = apps.filter((app) => app.category === "system");
  return `
    <details class="app-group" open>
      <summary>自装应用 <span>${custom.length} 项</span></summary>
      <ul class="inspection-list wide">${appItemsHtml(custom)}</ul>
    </details>
    <details class="app-group">
      <summary>系统基础应用 <span>${system.length} 项</span></summary>
      <ul class="inspection-list wide">${appItemsHtml(system)}</ul>
    </details>
  `;
}

function appItemsHtml(apps) {
  if (!apps.length) return `<li class="muted-item">未记录</li>`;
  return apps.map((app) => `<li><span>${escapeHtml(app.name)}</span><small>${escapeHtml(app.version || "")}</small></li>`).join("");
}

function listServicesHtml(services) {
  if (!services.length) return `<div class="muted-item">未记录</div>`;
  const custom = services.filter((service) => (service.category || "custom") !== "system");
  const system = services.filter((service) => service.category === "system");
  return `
    <details class="app-group" open>
      <summary>自装服务 <span>${custom.length} 项</span></summary>
      <ul class="inspection-list wide">${serviceItemsHtml(custom)}</ul>
    </details>
    <details class="app-group">
      <summary>系统基础服务 <span>${system.length} 项</span></summary>
      <ul class="inspection-list wide">${serviceItemsHtml(system)}</ul>
    </details>
  `;
}

function serviceItemsHtml(services) {
  if (!services.length) return `<li class="muted-item">未记录</li>`;
  return services.map((service) => {
    const exposure = service.external ? "外部可访问" : "内部监听";
    const ports = (service.ports || []).slice(0, 3).join(" / ");
    return `<li><span>${escapeHtml(service.name)}</span><small>${escapeHtml(exposure)}${ports ? ` · ${escapeHtml(ports)}` : ""}</small></li>`;
  }).join("");
}

function formatDateTime(value) {
  return value ? String(value).slice(0, 19) : "未记录";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[ch]));
}

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("loginError").textContent = "";
  try {
    const user = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ username: $("loginUser").value, password: $("loginPassword").value }),
    });
    showApp(user);
    await loadAll();
  } catch {
    $("loginError").textContent = "登录失败";
  }
});

$("logoutBtn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  showLogin();
});
$("refreshBtn").addEventListener("click", loadAll);
$("addBtn").addEventListener("click", () => openForm());
$("editBtn").addEventListener("click", () => openForm(selected()));
$("searchBox").addEventListener("input", render);
$("detailTabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-detail-tab]");
  if (!button) return;
  state.activeDetailTab = button.dataset.detailTab;
  renderDetailTabs();
});
$("detailPanel").addEventListener("click", (event) => {
  if (!event.target.closest(".detail-close")) return;
  state.selectedId = null;
  render();
});
$("toggleAuditBtn").addEventListener("click", () => {
  state.auditCollapsed = true;
  renderAuditDrawer();
});
$("expandAuditBtn").addEventListener("click", () => {
  state.auditCollapsed = false;
  renderAuditDrawer();
});
$("closeDialog").addEventListener("click", () => $("serverDialog").close());
$("cancelBtn").addEventListener("click", () => $("serverDialog").close());

$("serverForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = $("serverId").value;
  const method = id ? "PUT" : "POST";
  const path = id ? `/api/servers/${id}` : "/api/servers";
  const saved = await api(path, { method, body: JSON.stringify(payloadFromForm()) });
  delete state.credentials[saved.id];
  state.selectedId = saved.id;
  $("serverDialog").close();
  await loadAll();
});

$("deleteBtn").addEventListener("click", async () => {
  const id = $("serverId").value;
  if (!id || !confirm("确认删除这台服务器？")) return;
  await api(`/api/servers/${id}`, { method: "DELETE" });
  delete state.credentials[id];
  state.selectedId = null;
  $("serverDialog").close();
  await loadAll();
});

$("checkBtn").addEventListener("click", async () => {
  const s = selected();
  if (!s) return;
  await api(`/api/servers/${s.id}/check`, { method: "POST" });
  await loadAll();
});

$("inspectBtn").addEventListener("click", async () => {
  const s = selected();
  if (!s) return;
  await api(`/api/servers/${s.id}/inspect`, { method: "POST" });
  await loadAll();
});

$("copySshBtn").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("sshCommand").textContent);
});

$("revealBtn").addEventListener("click", async () => {
  await showCredential($("detailCredential").type === "password");
});

$("toggleCredentialBtn").addEventListener("click", async () => {
  await showCredential($("detailCredential").type === "password");
});

$("copyCredentialBtn").addEventListener("click", async () => {
  const s = selected();
  if (!s) return;
  const value = await loadCredential(s.id);
  if (!value) {
    $("credentialStatus").textContent = "这台服务器还没有保存凭据。";
    return;
  }
  await navigator.clipboard.writeText(value);
  $("credentialStatus").textContent = "凭据已复制。";
});

(async function init() {
  try {
    const user = await api("/api/me");
    if (user.authenticated) {
      showApp(user);
      await loadAll();
    } else {
      showLogin();
    }
  } catch {
    showLogin();
  }
})();

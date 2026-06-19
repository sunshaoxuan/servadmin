const state = { servers: [], selectedId: null, audit: [] };

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
    : `<tr><td colspan="5" class="text-secondary text-center py-5">暂无服务器</td></tr>`;

  document.querySelectorAll(".ops-row").forEach((el) => {
    el.addEventListener("click", () => {
      state.selectedId = Number(el.dataset.id);
      render();
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
  $("summaryOnlineBadge").textContent = `${online} online`;

  renderDetail();
  renderAudit();
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
      <td data-label="状态"><span class="status ${escapeHtml(s.last_status)}">${escapeHtml(s.last_status)}</span></td>
      <td data-label="最近检查"><span class="server-sub">${escapeHtml(s.last_checked_at ? s.last_checked_at.slice(0, 16) : "未检查")}</span></td>
    </tr>`;
}

function renderDetail() {
  const s = selected();
  $("credentialBox").classList.add("hidden");
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
  $("detailStatus").textContent = s.last_status;
  $("detailStatus").className = `status ${s.last_status}`;
  $("detailIpv4").textContent = s.ipv4 || "未设置";
  $("detailIpv6").textContent = s.ipv6 || "未设置";
  $("detailServiceCode").textContent = s.service_code || "未设置";
  $("detailProviderRegion").textContent = [s.provider, s.region].filter(Boolean).join(" / ") || "未设置";
  $("detailAuth").textContent = `${s.login_user} / ${authLabel(s.auth_type)}`;
  $("detailTags").innerHTML = (s.tags || []).map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("") || "未设置";
  $("detailNotes").textContent = s.notes || "无";
  $("sshCommand").textContent = `ssh ${s.login_user}@${s.hostname}`;
}

function renderAudit() {
  $("auditRows").innerHTML = state.audit.length
    ? state.audit.map((a) => `
      <tr>
        <td data-label="操作者">${escapeHtml(a.actor)}</td>
        <td data-label="动作">${escapeHtml(a.action)}</td>
        <td data-label="对象">${escapeHtml(a.target_type)} #${escapeHtml(a.target_id || "")}</td>
        <td data-label="时间">${escapeHtml((a.created_at || "").slice(0, 19))}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="4" class="text-secondary text-center py-4">暂无审计记录</td></tr>`;
}

function openForm(server = null) {
  $("dialogTitle").textContent = server ? "编辑服务器" : "新增服务器";
  $("serverId").value = server?.id || "";
  for (const id of ["name", "hostname", "ipv4", "ipv6", "provider", "region", "login_user", "auth_type", "service_code", "notes"]) {
    $(id).value = server?.[id] || "";
  }
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
    service_code: $("service_code").value.trim(),
    tags: $("tags").value.split(",").map((x) => x.trim()).filter(Boolean),
    credential: $("credential").value,
    notes: $("notes").value.trim(),
  };
}

function selected() {
  return state.servers.find((s) => s.id === state.selectedId);
}

function authLabel(value) {
  return value === "key" ? "SSH Key" : "password";
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
$("closeDialog").addEventListener("click", () => $("serverDialog").close());
$("cancelBtn").addEventListener("click", () => $("serverDialog").close());

$("serverForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = $("serverId").value;
  const method = id ? "PUT" : "POST";
  const path = id ? `/api/servers/${id}` : "/api/servers";
  const saved = await api(path, { method, body: JSON.stringify(payloadFromForm()) });
  state.selectedId = saved.id;
  $("serverDialog").close();
  await loadAll();
});

$("deleteBtn").addEventListener("click", async () => {
  const id = $("serverId").value;
  if (!id || !confirm("确认删除这台服务器？")) return;
  await api(`/api/servers/${id}`, { method: "DELETE" });
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

$("copySshBtn").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("sshCommand").textContent);
});

$("revealBtn").addEventListener("click", async () => {
  const s = selected();
  if (!s) return;
  const data = await api(`/api/servers/${s.id}/credential`);
  $("credentialBox").textContent = data.credential || "未保存凭据";
  $("credentialBox").classList.remove("hidden");
  state.audit = await api("/api/audit");
  renderAudit();
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

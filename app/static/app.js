const state = {
  servers: [],
  selectedId: null,
  audit: [],
  activeDetailTab: "overview",
  auditCollapsed: false,
  credentials: {},
  connectionSecrets: {},
  credentialRequests: {},
  connectionSecretRequests: {},
  activeTab: "dashboard",
  services: null,
  meshHealth: { servers: [] },
  dashboard: { summary: {}, servers: [] },
  runningActions: {},
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
  [state.servers, state.audit, state.meshHealth, state.dashboard] = await Promise.all([
    api("/api/servers"),
    api("/api/audit"),
    api("/api/mesh/health?hours=3"),
    api("/api/dashboard"),
  ]);
  const rows = filteredServers();
  if (!state.selectedId && rows.length) state.selectedId = rows[0].id;
  if (state.selectedId && !rows.some((s) => s.id === state.selectedId)) state.selectedId = rows[0]?.id || null;
  render();
}

function filteredServers() {
  const q = $("searchBox").value.trim().toLowerCase();
  const includeRetired = Boolean($("showRetiredToggle")?.checked);
  const rows = state.servers.filter((s) => includeRetired || !s.is_retired);
  if (!q) return rows;
  return rows.filter((s) => {
    return [s.name, s.hostname, s.ipv4, s.ipv6, s.login_user, s.provider, s.region, s.is_retired ? "已失效" : "有效", ...(s.tags || [])]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(q);
  });
}

function formatBytes(value, digits = 1) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return "等待样本";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = Math.max(0, bytes);
  let index = 0;
  while (amount >= 1000 && index < units.length - 1) {
    amount /= 1000;
    index += 1;
  }
  return `${amount.toFixed(index === 0 ? 0 : digits)} ${units[index]}`;
}

function metricValue(value, suffix = "%") {
  return Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)}${suffix}` : "等待样本";
}

function telemetryBar(value, tone = "orange") {
  const bounded = Number.isFinite(Number(value)) ? Math.max(0, Math.min(100, Number(value))) : 0;
  return `<span class="telemetry-track"><i class="${tone}" style="width:${bounded}%"></i></span>`;
}

function stateLabel(value) {
  return ({ online: "在线", delayed: "同步延迟", offline: "离线", pending: "待采样", unknown: "待确认" })[value] || "待确认";
}

function providerResetHtml(item) {
  if (!item.next_reset_at || !item.reset_timezone) {
    return `${escapeHtml(item.period_start)} 至 ${escapeHtml(item.period_end)}`;
  }
  const reset = new Date(item.next_reset_at);
  if (Number.isNaN(reset.getTime())) return `${escapeHtml(item.period_start)} 至 ${escapeHtml(item.period_end)}`;
  const formatIn = (timeZone) => new Intl.DateTimeFormat("zh-CN", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(reset);
  return `重置 ${escapeHtml(formatIn("Asia/Tokyo"))} JST<br><small>供应商 ${escapeHtml(formatIn(item.reset_timezone))} ${escapeHtml(item.reset_timezone)}</small>`;
}

function subscriptionHtml(server) {
  const item = server.subscription;
  if (!item) {
    return `
      <div class="traffic-empty">
        <div><strong>供应商账单流量</strong><small>等待管理画面或官方 API 读数</small></div>
        <button type="button" class="traffic-link" data-monitor-action="traffic" data-id="${server.id}">录入后台读数 <i class="ti ti-arrow-up-right"></i></button>
      </div>`;
  }
  const usedPercent = Math.max(0, Number(item.used_percent || 0));
  const displayPercent = Math.min(100, usedPercent);
  const collected = formatDateTime(item.collected_at);
  const source = item.source_url
    ? `<a href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.source_label)}</a>`
    : escapeHtml(item.source_label);
  return `
    <div class="traffic-heading">
      <div><span>月度套餐流量</span><strong>${metricValue(usedPercent)}</strong></div>
      <button type="button" class="icon-ghost" data-monitor-action="traffic" data-id="${server.id}" title="更新读数"><i class="ti ti-pencil"></i></button>
    </div>
    <div class="traffic-progress"><i style="width:${displayPercent}%"></i></div>
    <div class="traffic-meta">
      <span>${formatBytes(item.used_bytes)} / ${formatBytes(item.quota_bytes)}</span>
      <span>${providerResetHtml(item)}</span>
    </div>
    <small class="traffic-source">供应商管理画面读数 · 来源 ${source} · 采集 ${escapeHtml(collected)}</small>`;
}

function monitorCardHtml(server) {
  const telemetry = server.telemetry || {};
  const diskPercent = telemetry.disk_used_percent;
  const memoryPercent = telemetry.memory_used_percent;
  const cpuValue = telemetry.cpu_used_percent ?? telemetry.load_1m;
  const cpuLabel = telemetry.cpu_used_percent == null ? "1 分钟负载" : "CPU 使用率";
  const cpuDisplay = telemetry.cpu_used_percent == null
    ? (Number.isFinite(Number(cpuValue)) ? Number(cpuValue).toFixed(2) : "等待样本")
    : metricValue(cpuValue);
  const sampled = telemetry.sampled_at
    ? new Date(Number(telemetry.sampled_at) * 1000).toLocaleString("zh-CN", { hour12: false })
    : "尚无样本";
  return `
    <article class="monitor-card" data-state="${escapeHtml(server.state)}">
      <header class="monitor-card-head">
        <div class="server-identity">
          <span class="server-avatar">${escapeHtml(server.name.slice(0, 2).toUpperCase())}</span>
          <div>
            <div class="server-name-line"><h3>${escapeHtml(server.name)}</h3>${server.is_starred ? '<i class="ti ti-star-filled starred"></i>' : ""}</div>
            <p>${escapeHtml(server.hostname)} · ${escapeHtml(server.ipv4 || "无公网 IPv4")}</p>
          </div>
        </div>
        <button type="button" class="icon-ghost" data-monitor-action="assets" data-id="${server.id}" title="打开资产详情"><i class="ti ti-arrow-up-right"></i></button>
      </header>
      <div class="server-meta-row">
        <span>${escapeHtml(server.provider)}</span><span>${escapeHtml(server.region)}</span>
        <span class="live-pill ${escapeHtml(server.state)}"><i></i>${stateLabel(server.state)}</span>
      </div>
      <div class="liveness-copy"><strong>${escapeHtml(server.state_detail)}</strong><small>最新样本 ${escapeHtml(sampled)}</small></div>
      <div class="telemetry-grid">
        <div class="telemetry-cell"><span>${cpuLabel}</span><strong>${cpuDisplay}</strong>${telemetryBar(telemetry.cpu_used_percent, "orange")}</div>
        <div class="telemetry-cell"><span>内存占用</span><strong>${metricValue(memoryPercent)}</strong>${telemetryBar(memoryPercent, "cyan")}</div>
        <div class="telemetry-cell"><span>系统盘空间</span><strong>${metricValue(diskPercent)}</strong>${telemetryBar(diskPercent, Number(diskPercent) >= 85 ? "red" : "orange")}</div>
      </div>
      <div class="io-grid">
        <div><span><i class="ti ti-world-download"></i>网络下行</span><strong>${formatBytes(telemetry.network_rx_bytes_per_second)}/s</strong></div>
        <div><span><i class="ti ti-world-upload"></i>网络上行</span><strong>${formatBytes(telemetry.network_tx_bytes_per_second)}/s</strong></div>
        <div><span><i class="ti ti-database-import"></i>磁盘读取</span><strong>${formatBytes(telemetry.disk_read_bytes_per_second)}/s</strong></div>
        <div><span><i class="ti ti-database-export"></i>磁盘写入</span><strong>${formatBytes(telemetry.disk_write_bytes_per_second)}/s</strong></div>
      </div>
      <div class="space-note"><span>根分区可用</span><strong>${formatBytes(telemetry.disk_free_bytes)} / ${formatBytes(telemetry.disk_total_bytes)}</strong></div>
      <div class="subscription-block">${subscriptionHtml(server)}</div>
    </article>`;
}

function renderDashboard() {
  const dashboard = state.dashboard || { summary: {}, servers: [] };
  const summary = dashboard.summary || {};
  const query = $("searchBox").value.trim().toLowerCase();
  const visibleServers = (dashboard.servers || []).filter((server) => {
    if (!query) return true;
    return [server.name, server.hostname, server.ipv4, server.provider, server.region]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(query);
  });
  $("metricTotal").textContent = summary.total || 0;
  $("metricOnline").textContent = summary.online || 0;
  $("metricUnknown").textContent = summary.attention || 0;
  $("metricOffline").textContent = `${summary.attention || 0} 台延迟、离线或待采样`;
  $("metricLast").textContent = `${summary.subscription_ready || 0} / ${summary.total || 0}`;
  $("metricChecked").textContent = `${summary.subscription_ready || 0} 台已有供应商读数`;
  $("tableCount").textContent = `${summary.total || 0} 台纳入监控`;
  $("monitorGrid").innerHTML = visibleServers.length
    ? visibleServers.map(monitorCardHtml).join("")
    : '<div class="service-loading">暂无生产节点</div>';
  document.querySelectorAll("[data-monitor-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const serverId = Number(button.dataset.id);
      if (button.dataset.monitorAction === "assets") {
        state.selectedId = serverId;
        showTab("servers");
        render();
      }
      if (button.dataset.monitorAction === "traffic") openTrafficForm(serverId);
    });
  });
}

function render() {
  const rows = filteredServers();
  if (!state.selectedId && rows.length) {
    state.selectedId = rows[0].id;
  }
  if (state.selectedId && !rows.some((s) => s.id === state.selectedId)) {
    state.selectedId = rows[0]?.id || null;
  }
  $("serverRows").innerHTML = rows.length
    ? rows.map(rowHtml).join("")
    : `<tr><td colspan="8" class="text-secondary text-center py-5">暂无服务器</td></tr>`;

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
        if (server.is_retired || isServerBusy(server.id)) return;
        await runServerAction(server.id, "check");
      }
      if (el.dataset.rowAction === "inspect") {
        if (server.is_retired || isServerBusy(server.id)) return;
        await runServerAction(server.id, "inspect");
      }
      if (el.dataset.rowAction === "quality-check") {
        if (server.is_retired || isServerBusy(server.id)) return;
        state.activeDetailTab = "environment";
        await runServerAction(server.id, "quality-check");
      }
    });
  });

  const activeServers = state.servers.filter((s) => !s.is_retired);
  const retiredCount = state.servers.length - activeServers.length;
  const online = activeServers.filter((s) => s.last_status === "online").length;
  renderDashboard();
  if (state.activeTab === "dashboard") {
    $("summaryText").textContent = `全部 ${state.dashboard?.summary?.total || 0} 台生产节点的实时运行视图`;
  } else if (state.activeTab !== "settings") {
    $("summaryText").textContent = `${rows.length} 台可见，${online} 台在线${retiredCount ? `，${retiredCount} 台已失效` : ""}`;
  }
  $("assetFooterCount").textContent = `共 ${rows.length} 条`;
  $("summaryOnlineBadge").textContent = `${online} 台在线`;

  renderDetail();
  renderAudit();
  renderAuditDrawer();
}

function showTab(tab) {
  state.activeTab = tab;
  document.querySelectorAll(".nav-item[data-tab], .mobile-tab[data-tab]").forEach((item) => {
    item.classList.toggle("active", item.dataset.tab === tab);
  });
  const dashboardVisible = tab === "dashboard";
  const serversVisible = tab === "servers" || tab === "audit";
  const settings = tab === "settings";
  $("dashboardView").classList.toggle("hidden", !dashboardVisible);
  $("serversView").classList.toggle("hidden", !serversVisible);
  $("settingsView").classList.toggle("hidden", !settings);
  document.querySelector(".search-wrap").classList.toggle("hidden", !serversVisible);
  $("retiredFilterLabel").classList.toggle("hidden", !serversVisible);
  $("addBtn").classList.toggle("hidden", !serversVisible);
  $("pageTitle").textContent = settings ? "设置" : dashboardVisible ? "运行监控" : "服务器资产";
  if (settings) {
    $("summaryText").textContent = "应用和服务状态";
    refreshServices();
    return;
  }
  render();
  if (tab === "audit") {
    setTimeout(() => document.querySelector(".audit-panel")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  }
}

function meshRecord(serverId) {
  return (state.meshHealth?.servers || []).find((item) => item.server_id === serverId) || null;
}

function meshScore(value) {
  if (value === null || value === undefined || value === "") return "无数据";
  return Number.isFinite(Number(value)) ? `${Math.round(Number(value))}%` : "无数据";
}

function meshTimeline() {
  const end = Number(state.meshHealth?.generated_at || Math.floor(Date.now() / 1000));
  const fallbackStart = end - Number(state.meshHealth?.window_hours || 3) * 60 * 60;
  const start = Number(state.meshHealth?.window_started_at || fallbackStart);
  return { start, end: Math.max(start + 1, end) };
}

function sparklineX(sampledAt, width = 148) {
  const timeline = meshTimeline();
  const ratio = Math.max(0, Math.min(1, (Number(sampledAt) - timeline.start) / (timeline.end - timeline.start)));
  return 3 + ratio * (width - 6);
}

function failedMeshPollCycles() {
  return (state.meshHealth?.poll_cycles || []).filter((item) => item.status === "failed");
}

function latestMeshPollCycle() {
  const cycles = state.meshHealth?.poll_cycles || [];
  return cycles.length ? cycles[cycles.length - 1] : null;
}

function sparklineSegments(samples, field, width = 148, height = 40) {
  const failedTimes = failedMeshPollCycles().map((item) => Number(item.sampled_at));
  const interval = Number(state.meshHealth?.interval_seconds || 60);
  const segments = [];
  let points = [];
  let previousSampleAt = null;
  const flush = () => {
    if (points.length) segments.push(points.join(" "));
    points = [];
  };
  for (const item of samples) {
    const sampledAt = Number(item.sampled_at);
    const rawValue = item[field];
    const valid = rawValue !== null && rawValue !== undefined && Number.isFinite(Number(rawValue));
    const crossesFailedCycle = previousSampleAt !== null
      && failedTimes.some((failedAt) => failedAt > previousSampleAt && failedAt <= sampledAt);
    const hasTimeGap = previousSampleAt !== null && sampledAt - previousSampleAt > interval * 1.5;
    if (!valid || crossesFailedCycle || hasTimeGap) flush();
    if (valid) {
      const x = sparklineX(sampledAt, width);
      const value = Math.max(0, Math.min(100, Number(rawValue)));
      const y = 3 + ((100 - value) / 100) * (height - 6);
      points.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    previousSampleAt = sampledAt;
  }
  flush();
  return segments;
}

function meshCollectionFailureLines(width = 148, height = 40) {
  return failedMeshPollCycles().map((item) => {
    const x = sparklineX(item.sampled_at, width).toFixed(1);
    return `<line x1="${x}" y1="3" x2="${x}" y2="${height - 3}" class="mesh-collection-failure"></line>`;
  }).join("");
}

function meshHealthHtml(server) {
  if (!server.heartbeat_enabled) return '<div class="mesh-empty">未部署</div>';
  const record = meshRecord(server.id);
  const latestCycle = latestMeshPollCycle();
  if (!record?.current) {
    return latestCycle?.status === "failed"
      ? `<div class="mesh-empty collection-failed">采集异常 · 0/${latestCycle.attempted_sources} 报告源</div>`
      : '<div class="mesh-empty pending">等待首个样本</div>';
  }
  const samples = record.samples || [];
  const current = record.current;
  const age = Math.max(0, Math.floor(Date.now() / 1000) - Number(current.sampled_at || 0));
  const stale = age > Number(state.meshHealth?.freshness_seconds || 300);
  const syncDelayed = Boolean(current.details?.sync_delayed);
  const visibilityMissing = Boolean(current.details?.visibility_missing);
  const heartbeatAge = Math.max(0, Number(current.details?.heartbeat_age_seconds || 0));
  const networkScore = current.network_trend_score ?? current.network_score;
  const networkSegments = sparklineSegments(samples, "network_trend_score");
  const appSegments = sparklineSegments(samples, "app_score");
  const collectionFailures = failedMeshPollCycles();
  const collectionFailed = latestCycle?.status === "failed"
    && Number(latestCycle.sampled_at) >= Number(current.sampled_at);
  const failureNote = collectionFailures.length ? ` · 灰线 ${collectionFailures.length} 次采集异常` : "";
  const statusText = collectionFailed
    ? `采集异常 · 0/${latestCycle.attempted_sources} 报告源，沿用上次节点状态`
    : visibilityMissing
      ? `未被邻居确认 · ${current.peer_visible}/${current.peer_expected}`
      : syncDelayed
        ? `同步延迟 ${Math.round(heartbeatAge)} 秒 · ${current.peer_visible}/${current.peer_expected}${failureNote}`
        : `${current.peer_visible}/${current.peer_expected} 个同伴可见${failureNote}`;
  const title = `网络趋势 ${meshScore(networkScore)}，应用 ${meshScore(current.app_score)}，心跳年龄 ${Math.round(heartbeatAge)} 秒，传播 ${current.peer_visible}/${current.peer_expected}`;
  return `
    <div class="mesh-health ${stale ? "stale" : ""} ${syncDelayed ? "delayed" : ""} ${visibilityMissing ? "unconfirmed" : ""} ${collectionFailed ? "collection-failed" : ""}" title="${escapeHtml(title)}">
      <svg class="mesh-sparkline" viewBox="0 0 148 40" role="img" aria-label="${escapeHtml(title)}">
        <line x1="3" y1="20" x2="145" y2="20" class="mesh-guide"></line>
        ${networkSegments.map((points) => `<polyline points="${points}" class="mesh-network-line"></polyline>`).join("")}
        ${appSegments.map((points) => `<polyline points="${points}" class="mesh-app-line"></polyline>`).join("")}
        ${meshCollectionFailureLines()}
      </svg>
      <div class="mesh-scores">
        <span><i class="mesh-key network"></i>网络 ${meshScore(networkScore)}</span>
        <span><i class="mesh-key apps"></i>应用 ${meshScore(current.app_score)}</span>
      </div>
      ${stale && !collectionFailed ? '<small>样本已过期</small>' : `<small>${statusText}</small>`}
    </div>`;
}

function rowHtml(s) {
  const active = s.id === state.selectedId ? "active" : "";
  const retired = Boolean(s.is_retired);
  const checkRunning = isActionRunning(s.id, "check");
  const inspectRunning = isActionRunning(s.id, "inspect");
  const qualityRunning = isActionRunning(s.id, "quality-check");
  const busy = checkRunning || inspectRunning || qualityRunning;
  const serverStatus = s.last_status || "unknown";
  const configStatus = s.config_status || "unknown";
  const tags = (s.tags || []).slice(0, 3).map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("");
  const disabledAttr = retired || busy ? 'disabled aria-disabled="true"' : "";
  const checkTitle = retired ? "已失效节点不可检测" : checkRunning ? "SSH 检查中" : busy ? "另一项检查正在运行" : "检查 SSH";
  const qualityTitle = retired ? "已失效节点不可检测" : qualityRunning ? "全面体检中" : busy ? "另一项检查正在运行" : "环境质量全面体检";
  return `
    <tr class="ops-row ${active} ${retired ? "retired" : ""}" data-id="${s.id}">
      <td>
        <span class="server-title">${statusDot(retired ? "retired" : serverStatus, retired ? "已失效" : statusLabel(serverStatus))}${s.is_starred ? '<span class="server-star" title="重点生产机">★</span>' : ""}<span class="server-title-text">${escapeHtml(s.name)}</span>${retired ? '<span class="retired-badge">已失效</span>' : ""}</span>
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
      <td data-label="状态">${retired ? statusPill("retired", "已失效") : statusPill(serverStatus, statusLabel(serverStatus))}</td>
      <td data-label="配置">
        ${statusPill(configStatus, configLabel(configStatus), "config")}
        <span class="server-sub">${escapeHtml(s.config_summary || "未检查")}</span>
      </td>
      <td data-label="3 小时健康">${meshHealthHtml(s)}</td>
      <td data-label="最近检查"><span class="server-sub">${escapeHtml(s.last_checked_at ? s.last_checked_at.slice(0, 16) : "未检查")}</span></td>
      <td data-label="操作">
        <div class="row-actions">
          <button class="btn btn-light btn-icon btn-sm ${checkRunning ? "action-loading" : ""}" type="button" title="${checkTitle}" data-row-action="check" ${disabledAttr}>${actionIcon("check", checkRunning)}</button>
          <button class="btn btn-light btn-icon btn-sm ${qualityRunning ? "action-loading" : ""}" type="button" title="${qualityTitle}" data-row-action="quality-check" ${disabledAttr}>${actionIcon("quality-check", qualityRunning)}</button>
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
  const detailState = s.is_retired ? "retired" : s.last_status || "unknown";
  const detailLabel = s.is_retired ? "已失效" : statusLabel(s.last_status);
  $("detailStatus").innerHTML = `${statusDot(detailState, detailLabel)}${escapeHtml(detailLabel)}`;
  $("detailStatus").className = `status ${detailState} detail-mini-status`;
  renderDetailTabs();
  $("detailIpv4").textContent = s.ipv4 || "未设置";
  $("detailIpv6").textContent = s.ipv6 || "未设置";
  $("detailLoginUser").textContent = s.login_user || "未设置";
  $("detailAuthType").textContent = authLabel(s.auth_type);
  $("detailSshHost").textContent = s.ssh_host || s.ipv4 || s.hostname || "未设置";
  $("detailSshPort").textContent = s.ssh_port || 22;
  $("detailSshKeyPath").textContent = s.ssh_key_path || "未设置";
  $("detailSshLocalKeyPath").textContent = s.ssh_local_key_path || "未设置";
  $("detailSshWindowsKeyPath").textContent = s.ssh_windows_key_path || "未设置";
  $("detailSshOptions").textContent = s.ssh_options || "未设置";
  renderPanelConnection(s);
  renderProviderConnection(s);
  $("detailServiceCode").textContent = s.service_code || "未设置";
  $("detailProviderRegion").textContent = [s.provider, s.region].filter(Boolean).join(" / ") || "未设置";
  $("detailTags").innerHTML = (s.tags || []).map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("") || "未设置";
  $("detailRetiredStatus").innerHTML = s.is_retired ? statusPill("retired", "已失效") : statusPill("online", "有效");
  $("detailNotes").textContent = s.notes || "无";
  $("detailCreated").textContent = formatDateTime(s.created_at);
  $("detailUpdated").textContent = formatDateTime(s.updated_at);
  $("detailCheckedAt").textContent = formatDateTime(s.last_checked_at);
  $("detailConfigStatus").innerHTML = `${statusPill(s.config_status || "unknown", configLabel(s.config_status), "config")} ${escapeHtml(s.config_summary || "未检查")}`;
  const mesh = meshRecord(s.id)?.current;
  const latestPollCycle = latestMeshPollCycle();
  const meshCollectionFailed = latestPollCycle?.status === "failed"
    && (!mesh || Number(latestPollCycle.sampled_at) >= Number(mesh.sampled_at));
  const meshHeartbeat = mesh?.details?.visibility_missing
    ? "未被邻居确认"
    : mesh?.details?.sync_delayed
      ? `同步延迟 ${Math.max(0, Math.round(Number(mesh.details?.heartbeat_age_seconds || 0)))} 秒`
      : mesh?.direct_ok ? "心跳正常" : "心跳过期";
  $("detailMeshNetwork").textContent = !s.heartbeat_enabled
    ? "未启用"
    : meshCollectionFailed
      ? `采集异常，0/${latestPollCycle.attempted_sources} 报告源${mesh ? "，沿用上次节点状态" : ""}`
      : mesh
        ? `${meshHeartbeat}，网络趋势 ${meshScore(mesh.network_trend_score ?? mesh.network_score)}，传播 ${mesh.peer_visible}/${mesh.peer_expected}，报告源 ${mesh.details?.source_report_name || "无"}`
        : "等待首个样本";
  $("detailMeshApps").textContent = s.heartbeat_enabled && mesh
    ? meshScore(mesh.app_score)
    : s.heartbeat_enabled ? "等待首个样本" : "未启用";
  $("detailConfigStatusPanel").innerHTML = `${statusPill(s.config_status || "unknown", configLabel(s.config_status), "config")} ${escapeHtml(s.config_summary || "未检查")}`;
  $("detailConfigReport").innerHTML = configReportHtml(s.config_report || {});
  $("inspectionSummary").textContent = s.last_config_check_at ? `检查时间 ${formatDateTime(s.last_config_check_at)}` : "未检查";
  $("environmentDetailSummary").textContent = s.last_config_check_at
    ? `检查时间 ${formatDateTime(s.last_config_check_at)}`
    : s.is_retired
      ? "已失效节点保留历史报告"
      : "未检查";
  $("environmentDetailReport").innerHTML = environmentReportHtml(s);
  $("installedAppsCount").textContent = `${(s.installed_apps || []).length} 项`;
  $("runningServicesCount").textContent = `${(s.services || []).length} 项`;
  $("installedApps").innerHTML = listAppsHtml(s.installed_apps || []);
  $("runningServices").innerHTML = listServicesHtml(s.services || []);
  renderSshCommands(s);
  renderCredentialField(s);
  renderPanelPasswordField(s);
  renderProviderPasswordField(s);
  $("retiredNotice").classList.toggle("hidden", !s.is_retired);
  renderActionButtons(s);
}

function renderDetailTabs() {
  document.querySelectorAll("[data-detail-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.detailTab === state.activeDetailTab);
  });
  document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.tabPanel !== state.activeDetailTab);
  });
}

function actionKey(serverId, action) {
  return `${serverId}:${action}`;
}

function isActionRunning(serverId, action) {
  return Boolean(state.runningActions[actionKey(serverId, action)]);
}

function isServerBusy(serverId) {
  return isActionRunning(serverId, "check") || isActionRunning(serverId, "inspect") || isActionRunning(serverId, "quality-check");
}

function actionIcon(action, running) {
  if (running) return '<i class="ti ti-loader-2 action-spinner" aria-hidden="true"></i>';
  if (action === "check") return '<i class="ti ti-activity-heartbeat"></i>';
  if (action === "quality-check") return '<i class="ti ti-stethoscope"></i>';
  return '<i class="ti ti-list-search"></i>';
}

function renderActionButtons(server) {
  const checkRunning = isActionRunning(server.id, "check");
  const inspectRunning = isActionRunning(server.id, "inspect");
  const qualityRunning = isActionRunning(server.id, "quality-check");
  const busy = checkRunning || inspectRunning || qualityRunning;
  const checkBtn = $("checkBtn");
  const inspectBtn = $("inspectBtn");
  const qualityCheckBtn = $("qualityCheckBtn");
  checkBtn.disabled = Boolean(server.is_retired || busy);
  inspectBtn.disabled = Boolean(server.is_retired || busy);
  qualityCheckBtn.disabled = Boolean(server.is_retired || busy);
  checkBtn.title = server.is_retired ? "已失效节点不可检测" : checkRunning ? "SSH 检查中" : busy ? "另一项检查正在运行" : "";
  inspectBtn.title = server.is_retired ? "已失效节点不可检测" : inspectRunning ? "配置检查中" : busy ? "另一项检查正在运行" : "";
  qualityCheckBtn.title = server.is_retired ? "已失效节点不可检测" : qualityRunning ? "全面体检中" : busy ? "另一项检查正在运行" : "";
  checkBtn.classList.toggle("action-loading", checkRunning);
  inspectBtn.classList.toggle("action-loading", inspectRunning);
  qualityCheckBtn.classList.toggle("action-loading", qualityRunning);
  checkBtn.innerHTML = `${actionIcon("check", checkRunning)}${checkRunning ? "检查中" : "检查 SSH"}`;
  inspectBtn.innerHTML = `${actionIcon("inspect", inspectRunning)}${inspectRunning ? "检查中" : "检查配置"}`;
  qualityCheckBtn.innerHTML = `${actionIcon("quality-check", qualityRunning)}${qualityRunning ? "体检中" : "全面体检"}`;
}

async function runServerAction(serverId, action) {
  if (isServerBusy(serverId)) return;
  state.runningActions[actionKey(serverId, action)] = true;
  render();
  try {
    await api(`/api/servers/${serverId}/${action}`, { method: "POST" });
    await loadAll();
  } finally {
    delete state.runningActions[actionKey(serverId, action)];
    render();
  }
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

function renderServices(data) {
  const groups = [
    ["系统服务", data.services || []],
    ["应用代理", data.applications || []],
  ];
  $("servicesSummary").textContent = `最近刷新：${escapeHtml(data.checked_at || "未知")}`;
  $("serviceStatusGrid").innerHTML = groups.map(([title, items]) => `
    <section class="service-group">
      <div class="service-group-title">
        <h3>${escapeHtml(title)}</h3>
        <span>${items.length} 项</span>
      </div>
      <div class="service-list">
        ${items.length ? items.map(serviceCardHtml).join("") : '<div class="service-loading">未发现可检测项目</div>'}
      </div>
    </section>
  `).join("");
}

function serviceCardHtml(item) {
  const status = item.status || "unknown";
  const target = item.public_url
    ? `<a href="${escapeHtml(item.public_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.public_url)}</a>`
    : escapeHtml(item.target || "未设置");
  return `
    <article class="service-item">
      <div class="service-name">
        ${statusDot(status, status)}
        <strong>${escapeHtml(item.name)}</strong>
        <small>${target}</small>
      </div>
      ${statusPill(status, status)}
      <div class="service-detail">
        <span>${escapeHtml(item.detail || "无详情")}</span>
        <span>${Number.isFinite(item.latency_ms) ? `${item.latency_ms} ms` : "未计时"}</span>
      </div>
    </article>
  `;
}

async function refreshServices() {
  $("servicesSummary").textContent = "正在刷新状态";
  $("serviceStatusGrid").innerHTML = '<div class="service-loading">正在检测 systemd 服务和本机代理端口...</div>';
  try {
    state.services = await api("/api/services/status");
    renderServices(state.services);
  } catch (error) {
    $("servicesSummary").textContent = "状态刷新失败";
    $("serviceStatusGrid").innerHTML = `<div class="service-loading error">${escapeHtml(error.message)}</div>`;
  }
}

function environmentReportHtml(server) {
  if (!server) return `<div class="service-loading">暂无节点</div>`;
  const report = server.config_report || {};
  if (!server.last_config_check_at && server.config_status === "unknown") {
    if (server.is_retired) {
      return `<div class="service-loading">这台服务器已失效，没有可用历史报告。</div>`;
    }
    return `<div class="service-loading">选择节点后点击检测生成环境报告。</div>`;
  }
  if (report.error) {
    return `<div class="environment-error">${escapeHtml(report.error)}</div>`;
  }
  if (report.quality_report?.version >= 2) {
    return qualityReportHtml(server, report.quality_report);
  }
  const sections = [
    {
      title: "一、操作系统信息",
      rows: [
        ["容器/虚拟化", report.runtime?.virtualization || "未记录"],
        ["架构", report.cpu?.architecture || "未记录"],
        ["操作系统/内核", `${osDisplay(report)} ${report.kernel || ""}`.trim()],
        ["主机名/FQDN", `${report.hostname || "未记录"} ${report.hostname_fqdn || ""}`.trim()],
        ["运行时间", report.runtime?.uptime || "未记录"],
        ["负载", report.runtime?.load_average || "未记录"],
        ["进程/服务", `${report.runtime?.processes || "?"} 进程，${report.runtime?.active_services || "?"} 活跃服务`],
        ["区域设置", `${report.runtime?.locale || "未记录"} ${report.runtime?.timezone || ""}`.trim()],
      ],
    },
    {
      title: "二、硬件资源",
      rows: [
        ["主板/机型", boardSummary(report)],
        ["BIOS", biosSummary(report)],
        ["CPU", report.cpu?.model || `${report.cpu_count || "未记录"} 核`],
        ["CPU 核心", report.cpu_count || "未记录"],
        ["GPU/显示", lineSummary(report.gpu || [])],
        ["内存", memorySummary(report)],
        ["根分区", report.disk_root || "未记录"],
        ["磁盘列表", lineSummary(report.disks || [])],
        ["块设备", lineSummary(report.block_devices || report.report_sections?.block_devices || [])],
      ],
    },
    {
      title: "三、网络策略",
      rows: [
        ["地址", lineSummary(report.network?.addresses || [])],
        ["公网 IP", publicIpSummary(report)],
        ["TCP 拥塞控制", report.network?.tcp?.congestion_control || "未记录"],
        ["队列调度", report.network?.tcp?.qdisc || "未记录"],
        ["TCP 接收缓冲", report.network?.tcp?.tcp_rmem || "未记录"],
        ["TCP 发送缓冲", report.network?.tcp?.tcp_wmem || "未记录"],
        ["网络质量", lineSummary(report.network?.quality || report.report_sections?.network_quality || [])],
        ["网络明细", lineSummary(report.report_sections?.network || [])],
      ],
    },
    {
      title: "四、服务和端口",
      rows: [
        ["应用数量", `${(server.installed_apps || []).length} 项`],
        ["服务数量", `${(server.services || []).length} 项`],
        ["外部监听", `${report.external_service_count || 0} 项`],
        ["自装服务", lineSummary((server.services || []).filter((item) => (item.category || "custom") !== "system").map((item) => item.name).slice(0, 8))],
        ["端口样本", lineSummary(report.report_sections?.ports || [])],
      ],
    },
    {
      title: "五、应用清单",
      rows: [
        ["自装应用", lineSummary((server.installed_apps || []).filter((item) => (item.category || "custom") !== "system").map((item) => `${item.name} ${item.version || ""}`).slice(0, 10))],
        ["系统基础应用", `${(server.installed_apps || []).filter((item) => item.category === "system").length} 项`],
      ],
    },
  ];
  return `
    <div class="terminal-report">
      <div class="report-banner">
        <span>Server Desk 环境质量体检报告</span>
        <strong>${escapeHtml(server.name)}，${escapeHtml(server.hostname)}</strong>
        <small>报告时间：${escapeHtml(formatDateTime(server.last_config_check_at))}，环境评分：${escapeHtml(String(report.health_score || "未评分"))}</small>
      </div>
      ${sections.map(reportSectionHtml).join("")}
    </div>`;
}

function qualityStatusLabel(status) {
  return { pass: "通过", warn: "关注", fail: "异常", info: "信息" }[status] || status;
}

function qualityReportHtml(server, quality) {
  const findings = quality.findings || [];
  const mesh = quality.mesh_evidence || {};
  const meshSummary = mesh.sample_count
    ? `最近 ${mesh.sample_count} 个样本中，${mesh.confirmed_count} 个获得邻居确认`
    : "暂无分布式心跳样本";
  return `
    <div class="quality-report">
      <div class="quality-hero quality-${escapeHtml(quality.status || "warning")}">
        <div class="quality-score"><strong>${escapeHtml(quality.score)}</strong><span>/ 100</span></div>
        <div>
          <span>Server Desk 环境质量体检报告</span>
          <h3>${escapeHtml(server.name)}，${escapeHtml(server.hostname)}</h3>
          <p>等级 ${escapeHtml(quality.grade || "未评级")}，${escapeHtml(quality.summary || "体检完成")}</p>
          <small>报告时间 ${escapeHtml(formatDateTime(server.last_config_check_at))}</small>
        </div>
      </div>
      <div class="quality-dimensions">
        ${(quality.dimensions || []).map(qualityDimensionHtml).join("")}
      </div>
      <section class="quality-findings">
        <div class="quality-section-head">
          <h3>需要关注的项目</h3>
          <span>${findings.length} 项</span>
        </div>
        ${findings.length ? findings.map((item) => `
          <article class="quality-finding quality-check-${escapeHtml(item.status)}">
            <div><span>${escapeHtml(qualityStatusLabel(item.status))}</span><strong>${escapeHtml(item.label)}</strong></div>
            <p>${escapeHtml(item.evidence || "无补充证据")}</p>
            ${item.recommendation ? `<small>${escapeHtml(item.recommendation)}</small>` : ""}
          </article>
        `).join("") : '<div class="quality-empty">本次体检没有发现需要关注的项目。</div>'}
      </section>
      <section class="quality-evidence">
        <h3>心跳传播证据</h3>
        <p>${escapeHtml(meshSummary)}</p>
        <small>Agent 直连：${escapeHtml(quality.heartbeat_probe?.detail || "未执行")}${Number.isFinite(quality.heartbeat_probe?.latency_ms) ? `，${quality.heartbeat_probe.latency_ms} ms` : ""}</small>
      </section>
    </div>`;
}

function qualityDimensionHtml(dimension) {
  return `
    <section class="quality-dimension">
      <div class="quality-section-head">
        <h3>${escapeHtml(dimension.name)}</h3>
        <span>${escapeHtml(dimension.score)} / ${escapeHtml(dimension.max_score)}</span>
      </div>
      <div class="quality-check-list">
        ${(dimension.checks || []).map((item) => `
          <div class="quality-check quality-check-${escapeHtml(item.status)}">
            <i class="ti ${item.status === "pass" ? "ti-circle-check" : item.status === "fail" ? "ti-alert-circle" : item.status === "warn" ? "ti-alert-triangle" : "ti-info-circle"}"></i>
            <div><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.evidence || "未记录")}</small></div>
            <span>${escapeHtml(item.points)} / ${escapeHtml(item.max_points)}</span>
          </div>
        `).join("")}
      </div>
    </section>`;
}

function reportSectionHtml(section) {
  return `
    <section class="report-section">
      <h3>${escapeHtml(section.title)}</h3>
      <div class="report-row-list">
        ${section.rows.map(([label, value]) => `
          <div class="report-row">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(value || "未记录")}</strong>
          </div>
        `).join("")}
      </div>
    </section>`;
}

function openForm(server = null) {
  $("dialogTitle").textContent = server ? "编辑服务器" : "新增服务器";
  $("serverId").value = server?.id || "";
  for (const id of ["name", "hostname", "ipv4", "ipv6", "provider", "region", "login_user", "auth_type", "ssh_host", "ssh_port", "ssh_key_path", "ssh_local_key_path", "ssh_windows_key_path", "ssh_options", "panel_url", "panel_username", "service_code", "provider_portal_url", "provider_username", "provider_service_id", "provider_server_id", "provider_connector", "heartbeat_port", "notes"]) {
    $(id).value = server?.[id] || "";
  }
  $("ssh_port").value = server?.ssh_port || 22;
  $("heartbeat_port").value = server?.heartbeat_port || 9108;
  $("heartbeat_enabled").checked = Boolean(server?.heartbeat_enabled);
  $("provider_sync_enabled").checked = Boolean(server?.provider_sync_enabled);
  $("is_starred").checked = Boolean(server?.is_starred);
  $("is_retired").checked = Boolean(server?.is_retired);
  $("tags").value = (server?.tags || []).join(", ");
  $("panel_password").value = "";
  $("provider_password").value = "";
  $("credential").value = "";
  $("deleteBtn").classList.toggle("hidden", !server);
  $("serverDialog").showModal();
}

function openTrafficForm(serverId) {
  const server = (state.dashboard?.servers || []).find((item) => item.id === serverId);
  if (!server) return;
  const item = server.subscription;
  const now = new Date();
  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
  const monthEnd = new Date(now.getFullYear(), now.getMonth() + 1, 0);
  const localDate = (value) => {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, "0");
    const day = String(value.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  };
  $("trafficServerId").value = server.id;
  $("trafficServerName").textContent = `${server.name} · ${server.provider}`;
  $("trafficPeriodStart").value = item?.period_start || localDate(monthStart);
  $("trafficPeriodEnd").value = item?.period_end || localDate(monthEnd);
  $("trafficUsedGb").value = item ? Number(item.used_bytes) / 1_000_000_000 : "";
  $("trafficQuotaGb").value = item ? Number(item.quota_bytes) / 1_000_000_000 : "";
  $("trafficSourceLabel").value = item?.source_label || `${server.provider} 管理画面`;
  $("trafficSourceUrl").value = item?.source_url || "";
  $("trafficNextResetAt").value = item?.next_reset_at ? item.next_reset_at.slice(0, 19) : "";
  $("trafficResetTimezone").value = item?.reset_timezone || "";
  $("trafficDialog").showModal();
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
    ssh_local_key_path: $("ssh_local_key_path").value.trim(),
    ssh_windows_key_path: $("ssh_windows_key_path").value.trim(),
    ssh_options: $("ssh_options").value.trim(),
    panel_url: $("panel_url").value.trim(),
    panel_username: $("panel_username").value.trim(),
    panel_password: $("panel_password").value,
    service_code: $("service_code").value.trim(),
    provider_portal_url: $("provider_portal_url").value.trim(),
    provider_username: $("provider_username").value.trim(),
    provider_password: $("provider_password").value,
    provider_service_id: $("provider_service_id").value.trim(),
    provider_server_id: $("provider_server_id").value.trim(),
    provider_connector: $("provider_connector").value,
    provider_sync_enabled: $("provider_sync_enabled").checked,
    heartbeat_enabled: $("heartbeat_enabled").checked,
    heartbeat_port: Number($("heartbeat_port").value || 9108),
    is_starred: $("is_starred").checked,
    is_retired: $("is_retired").checked,
    tags: $("tags").value.split(",").map((x) => x.trim()).filter(Boolean),
    credential: $("credential").value,
    notes: $("notes").value.trim(),
  };
}

function renderPanelConnection(server) {
  const link = $("detailPanelUrl");
  if (server.panel_url) {
    link.href = server.panel_url;
    link.textContent = server.panel_url;
    link.classList.remove("disabled-link");
  } else {
    link.removeAttribute("href");
    link.textContent = "未设置";
    link.classList.add("disabled-link");
  }
  $("detailPanelUser").textContent = server.panel_username || "未设置";
}

function renderProviderConnection(server) {
  const link = $("detailProviderPortalUrl");
  if (server.provider_portal_url) {
    link.href = server.provider_portal_url;
    link.textContent = server.provider_portal_url;
    link.classList.remove("disabled-link");
  } else {
    link.removeAttribute("href");
    link.textContent = "未设置";
    link.classList.add("disabled-link");
  }
  $("detailProviderUsername").textContent = server.provider_username || "未设置";
  $("detailProviderServiceId").textContent = server.provider_service_id || "未设置";
  $("detailProviderServerId").textContent = server.provider_server_id || "未设置";
  const syncLabel = ({ ok: "同步正常", failed: "同步失败", pending: "等待首次同步", unconfigured: "未配置" })[server.last_sync_status]
    || server.last_sync_status
    || "未配置";
  $("detailProviderSyncStatus").textContent = server.last_synced_at
    ? `${syncLabel} · ${formatDateTime(server.last_synced_at)}`
    : syncLabel;
  const syncButton = $("syncProviderUsageBtn");
  syncButton.disabled = !server.provider_sync_enabled || server.provider_connector !== "riven_cloud";
  syncButton.title = syncButton.disabled ? "当前供应商尚未配置自动连接器" : "立即从供应商后台更新账期与流量";
}

function renderSshCommands(server) {
  const unix = sshCommand(server, "unix");
  const windows = sshCommand(server, "windows");
  $("sshCommandUnix").textContent = unix.command;
  $("sshCommandWindows").textContent = windows.command;
  $("sshUnixHint").textContent = unix.hint;
  $("sshWindowsHint").textContent = windows.hint;
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

function renderPanelPasswordField(server) {
  const input = $("detailPanelPassword");
  input.type = "password";
  updatePanelPasswordToggle(false);
  if (!server.panel_url && !server.panel_username && !server.has_panel_password) {
    input.value = "";
    input.placeholder = "未设置 1Panel";
    $("panelPasswordStatus").textContent = "这台服务器没有保存 1Panel 信息。";
    return;
  }
  const cached = Object.prototype.hasOwnProperty.call(state.connectionSecrets, server.id);
  if (cached) {
    const value = state.connectionSecrets[server.id]?.panel_password || "";
    input.value = value;
    input.placeholder = value ? "" : "未保存面板密码";
    $("panelPasswordStatus").textContent = value ? "面板密码已加载，默认遮蔽显示。" : "这台服务器没有保存面板密码。";
    return;
  }
  input.value = "";
  input.placeholder = "正在加载面板密码";
  $("panelPasswordStatus").textContent = "正在从加密存储中读取面板密码。";
  loadConnectionSecret(server.id);
}

function renderProviderPasswordField(server) {
  const input = $("detailProviderPassword");
  input.type = "password";
  updateProviderPasswordToggle(false);
  if (!server.provider_portal_url && !server.provider_username && !server.has_provider_password) {
    input.value = "";
    input.placeholder = "未设置供应商后台";
    $("providerPasswordStatus").textContent = "这台服务器没有保存供应商后台资料。";
    return;
  }
  const cached = Object.prototype.hasOwnProperty.call(state.connectionSecrets, server.id);
  if (cached) {
    const value = state.connectionSecrets[server.id]?.provider_password || "";
    input.value = value;
    input.placeholder = value ? "" : "未保存供应商密码";
    $("providerPasswordStatus").textContent = value ? "供应商密码已加载，默认遮蔽显示。" : "这台服务器没有保存供应商密码。";
    return;
  }
  input.value = "";
  input.placeholder = "正在加载供应商密码";
  $("providerPasswordStatus").textContent = "正在从加密存储中读取供应商密码。";
  loadConnectionSecret(server.id);
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

async function loadConnectionSecret(serverId, options = {}) {
  if (!options.force && Object.prototype.hasOwnProperty.call(state.connectionSecrets, serverId)) {
    return state.connectionSecrets[serverId];
  }
  if (!options.force && state.connectionSecretRequests[serverId]) {
    return state.connectionSecretRequests[serverId];
  }
  state.connectionSecretRequests[serverId] = api(`/api/servers/${serverId}/connection-secret`)
    .then(async (data) => {
      state.connectionSecrets[serverId] = data;
      if (selected()?.id === serverId) {
        const value = data.panel_password || "";
        $("detailPanelPassword").value = value;
        $("detailPanelPassword").placeholder = value ? "" : "未保存面板密码";
        $("panelPasswordStatus").textContent = value ? "面板密码已加载，默认遮蔽显示。" : "这台服务器没有保存面板密码。";
        const providerValue = data.provider_password || "";
        $("detailProviderPassword").value = providerValue;
        $("detailProviderPassword").placeholder = providerValue ? "" : "未保存供应商密码";
        $("providerPasswordStatus").textContent = providerValue ? "供应商密码已加载，默认遮蔽显示。" : "这台服务器没有保存供应商密码。";
      }
      state.audit = await api("/api/audit");
      renderAudit();
      return data;
    })
    .catch((error) => {
      if (selected()?.id === serverId) {
        $("detailPanelPassword").value = "";
        $("detailPanelPassword").placeholder = "面板密码读取失败";
        $("panelPasswordStatus").textContent = error.message || "面板密码读取失败。";
        $("detailProviderPassword").value = "";
        $("detailProviderPassword").placeholder = "供应商密码读取失败";
        $("providerPasswordStatus").textContent = error.message || "供应商密码读取失败。";
      }
      return { credential: "", panel_password: "", provider_password: "" };
    })
    .finally(() => {
      delete state.connectionSecretRequests[serverId];
    });
  return state.connectionSecretRequests[serverId];
}

function updateCredentialToggle(visible) {
  const icon = $("toggleCredentialBtn").querySelector("i");
  icon.className = visible ? "ti ti-eye-off" : "ti ti-eye";
  $("toggleCredentialBtn").title = visible ? "隐藏凭据" : "显示凭据";
  $("revealBtn").innerHTML = visible ? '<i class="ti ti-eye-off"></i>隐藏凭据' : '<i class="ti ti-eye"></i>显示凭据';
}

function updatePanelPasswordToggle(visible) {
  const icon = $("togglePanelPasswordBtn").querySelector("i");
  icon.className = visible ? "ti ti-eye-off" : "ti ti-eye";
  $("togglePanelPasswordBtn").title = visible ? "隐藏面板密码" : "显示面板密码";
}

function updateProviderPasswordToggle(visible) {
  const icon = $("toggleProviderPasswordBtn").querySelector("i");
  icon.className = visible ? "ti ti-eye-off" : "ti ti-eye";
  $("toggleProviderPasswordBtn").title = visible ? "隐藏供应商密码" : "显示供应商密码";
}

async function showCredential(visible) {
  const s = selected();
  if (!s) return;
  await loadCredential(s.id);
  $("detailCredential").type = visible ? "text" : "password";
  updateCredentialToggle(visible);
  $("credentialStatus").textContent = visible ? "凭据正在明文显示。" : "凭据已加载，默认遮蔽显示。";
}

async function showPanelPassword(visible) {
  const s = selected();
  if (!s) return;
  await loadConnectionSecret(s.id);
  $("detailPanelPassword").type = visible ? "text" : "password";
  updatePanelPasswordToggle(visible);
  $("panelPasswordStatus").textContent = visible ? "面板密码正在明文显示。" : "面板密码已加载，默认遮蔽显示。";
}

async function showProviderPassword(visible) {
  const s = selected();
  if (!s) return;
  await loadConnectionSecret(s.id);
  $("detailProviderPassword").type = visible ? "text" : "password";
  updateProviderPasswordToggle(visible);
  $("providerPasswordStatus").textContent = visible ? "供应商密码正在明文显示。" : "供应商密码已加载，默认遮蔽显示。";
}

function authLabel(value) {
  return value === "key" ? "密钥" : "密码";
}

function statusLabel(value) {
  return {
    online: "在线",
    offline: "离线",
    retired: "已失效",
    unknown: "未检查",
  }[value || "unknown"] || value;
}

function statusDot(value, label, scope = "runtime") {
  const normalized = value || "unknown";
  return `<span class="status-dot ${escapeHtml(scope)}-${escapeHtml(normalized)}" title="${escapeHtml(label || normalized)}" aria-label="${escapeHtml(label || normalized)}"></span>`;
}

function statusPill(value, label, scope = "runtime") {
  const normalized = value || "unknown";
  const className = scope === "config" ? `config-${normalized}` : normalized;
  return `<span class="status ${escapeHtml(className)}">${statusDot(normalized, label, scope)}${escapeHtml(label || normalized)}</span>`;
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
    quality_check: "环境体检",
    reveal_credential: "查看凭据",
    reveal_connection_secret: "查看连接密钥",
  }[value] || value;
}

function shellQuote(value, platform) {
  const raw = String(value || "");
  if (platform === "windows") return `"${raw.replace(/"/g, '\\"')}"`;
  return `'${raw.replace(/'/g, "'\\''")}'`;
}

function sshCommand(server, platform) {
  const args = ["ssh"];
  const keyPath = platform === "windows"
    ? server.ssh_windows_key_path
    : (server.ssh_local_key_path || server.ssh_key_path);
  if (keyPath) args.push("-i", shellQuote(keyPath, platform));
  if (server.ssh_port && Number(server.ssh_port) !== 22) args.push("-p", String(server.ssh_port));
  if (server.ssh_options) args.push(server.ssh_options);
  args.push(`${server.login_user}@${server.ssh_host || server.ipv4 || server.hostname}`);
  const needsKey = server.auth_type === "key";
  const hint = needsKey
    ? keyPath
      ? `请确认密钥文件在本机存在：${keyPath}`
      : "这台服务器使用密钥登录，请先填写本机密钥路径。"
    : "这台服务器使用密码登录。";
  return { command: args.join(" "), hint };
}

function osDisplay(report) {
  if (report.os_name) return report.os_name;
  const osName = (report.os || []).find((line) => line.startsWith("PRETTY_NAME=")) || "";
  return osName ? osName.replace("PRETTY_NAME=", "").replace(/^"|"$/g, "") : "未记录";
}

function memorySummary(report) {
  const detail = report.memory_detail || {};
  if (detail.memory_total || detail.memory_used || detail.memory_available) {
    return `${detail.memory_used || "?"} / ${detail.memory_total || "?"}，可用 ${detail.memory_available || "?"}`;
  }
  return report.memory || "未记录";
}

function boardSummary(report) {
  const board = report.board || {};
  return [
    board.system_vendor || board.sys_vendor,
    board.product_name,
    board.board_name,
  ].filter(Boolean).join(" / ") || "未记录";
}

function biosSummary(report) {
  const board = report.board || {};
  return [board.bios_version, board.bios_date].filter(Boolean).join(" / ") || "未记录";
}

function publicIpSummary(report) {
  const publicIp = report.network?.public_ip || {};
  return [
    publicIp.ipv4 ? `IPv4 ${publicIp.ipv4}` : "",
    publicIp.ipv6 ? `IPv6 ${publicIp.ipv6}` : "",
  ].filter(Boolean).join(" / ") || "未记录";
}

function lineSummary(lines) {
  const values = Array.isArray(lines) ? lines.filter(Boolean) : [];
  if (!values.length) return "未记录";
  return values.slice(0, 6).join(" / ");
}

function configReportHtml(report) {
  const items = [
    ["系统", osDisplay(report)],
    ["内核", report.kernel || "未记录"],
    ["CPU", report.cpu_count || "未记录"],
    ["内存", memorySummary(report)],
    ["磁盘", report.disk_root || "未记录"],
    ["网络", lineSummary(report.network?.addresses || [])],
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
    const signal = serviceSignal(service);
    const exposure = service.external ? "外部可访问" : "内部监听";
    const ports = (service.ports || []).slice(0, 3).join(" / ");
    return `<li class="service-inspection-item">
      ${statusDot(signal.value, signal.label, "service")}
      <span>${escapeHtml(service.name)}</span>
      <small>${escapeHtml(exposure)}${ports ? ` · ${escapeHtml(ports)}` : ""}</small>
    </li>`;
  }).join("");
}

function serviceSignal(service) {
  const state = String(service.state || "").toLowerCase();
  if (state && !["running", "listening"].includes(state)) {
    return { value: "offline", label: "异常" };
  }
  if (service.external) {
    return { value: "online", label: "外部可访问" };
  }
  return { value: "internal", label: "内部监听" };
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
$("refreshBtn").addEventListener("click", () => {
  if (state.activeTab === "settings") {
    refreshServices();
  } else {
    loadAll();
  }
});
$("addBtn").addEventListener("click", () => openForm());
$("editBtn").addEventListener("click", () => openForm(selected()));
$("searchBox").addEventListener("input", render);
$("showRetiredToggle").addEventListener("change", render);
$("detailTabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-detail-tab]");
  if (!button) return;
  state.activeDetailTab = button.dataset.detailTab;
  renderDetailTabs();
});
$("refreshServicesBtn").addEventListener("click", refreshServices);
document.querySelectorAll(".nav-item[data-tab], .mobile-tab[data-tab]").forEach((item) => {
  item.addEventListener("click", () => showTab(item.dataset.tab || "servers"));
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
$("closeTrafficDialog").addEventListener("click", () => $("trafficDialog").close());
$("cancelTrafficBtn").addEventListener("click", () => $("trafficDialog").close());

$("trafficForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const serverId = Number($("trafficServerId").value);
  const result = await api(`/api/servers/${serverId}/subscription-usage`, {
    method: "PUT",
    body: JSON.stringify({
      period_start: $("trafficPeriodStart").value,
      period_end: $("trafficPeriodEnd").value,
      used_gb: Number($("trafficUsedGb").value),
      quota_gb: Number($("trafficQuotaGb").value),
      source_label: $("trafficSourceLabel").value.trim(),
      source_url: $("trafficSourceUrl").value.trim(),
      next_reset_at: $("trafficNextResetAt").value,
      reset_timezone: $("trafficResetTimezone").value.trim(),
    }),
  });
  state.dashboard = result.dashboard;
  $("trafficDialog").close();
  render();
});

$("serverForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = $("serverId").value;
  const method = id ? "PUT" : "POST";
  const path = id ? `/api/servers/${id}` : "/api/servers";
  const saved = await api(path, { method, body: JSON.stringify(payloadFromForm()) });
  delete state.credentials[saved.id];
  delete state.connectionSecrets[saved.id];
  state.selectedId = saved.id;
  $("serverDialog").close();
  await loadAll();
});

$("deleteBtn").addEventListener("click", async () => {
  const id = $("serverId").value;
  if (!id || !confirm("确认删除这台服务器？")) return;
  await api(`/api/servers/${id}`, { method: "DELETE" });
  delete state.credentials[id];
  delete state.connectionSecrets[id];
  state.selectedId = null;
  $("serverDialog").close();
  await loadAll();
});

$("checkBtn").addEventListener("click", async () => {
  const s = selected();
  if (!s || s.is_retired || isServerBusy(s.id)) return;
  await runServerAction(s.id, "check");
});

$("inspectBtn").addEventListener("click", async () => {
  const s = selected();
  if (!s || s.is_retired || isServerBusy(s.id)) return;
  await runServerAction(s.id, "inspect");
});

$("qualityCheckBtn").addEventListener("click", async () => {
  const s = selected();
  if (!s || s.is_retired || isServerBusy(s.id)) return;
  state.activeDetailTab = "environment";
  await runServerAction(s.id, "quality-check");
});

$("copySshUnixBtn").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("sshCommandUnix").textContent);
});

$("copySshWindowsBtn").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("sshCommandWindows").textContent);
});

$("revealBtn").addEventListener("click", async () => {
  await showCredential($("detailCredential").type === "password");
});

$("toggleCredentialBtn").addEventListener("click", async () => {
  await showCredential($("detailCredential").type === "password");
});

$("togglePanelPasswordBtn").addEventListener("click", async () => {
  await showPanelPassword($("detailPanelPassword").type === "password");
});

$("toggleProviderPasswordBtn").addEventListener("click", async () => {
  await showProviderPassword($("detailProviderPassword").type === "password");
});

$("copyPanelPasswordBtn").addEventListener("click", async () => {
  const s = selected();
  if (!s) return;
  const data = await loadConnectionSecret(s.id);
  const value = data.panel_password || "";
  if (!value) {
    $("panelPasswordStatus").textContent = "这台服务器没有保存面板密码。";
    return;
  }
  await navigator.clipboard.writeText(value);
  $("panelPasswordStatus").textContent = "面板密码已复制。";
});

$("copyProviderPasswordBtn").addEventListener("click", async () => {
  const s = selected();
  if (!s) return;
  const data = await loadConnectionSecret(s.id);
  const value = data.provider_password || "";
  if (!value) {
    $("providerPasswordStatus").textContent = "这台服务器没有保存供应商密码。";
    return;
  }
  await navigator.clipboard.writeText(value);
  $("providerPasswordStatus").textContent = "供应商密码已复制。";
});

$("syncProviderUsageBtn").addEventListener("click", async () => {
  const s = selected();
  if (!s || !s.provider_sync_enabled || s.provider_connector !== "riven_cloud") return;
  const button = $("syncProviderUsageBtn");
  button.disabled = true;
  button.innerHTML = '<i class="ti ti-loader-2 spin"></i>同步中';
  $("providerPasswordStatus").textContent = "正在登录供应商后台并读取流量。";
  try {
    const result = await api(`/api/servers/${s.id}/provider-sync`, { method: "POST" });
    state.dashboard = result.dashboard;
    const index = state.servers.findIndex((item) => item.id === s.id);
    if (index >= 0) state.servers[index] = result.server;
    $("providerPasswordStatus").textContent = "供应商流量同步成功。";
    render();
  } catch (error) {
    $("providerPasswordStatus").textContent = error.message || "供应商流量同步失败。";
  } finally {
    button.innerHTML = '<i class="ti ti-refresh"></i>立即同步供应商流量';
    const current = selected();
    button.disabled = !current?.provider_sync_enabled || current?.provider_connector !== "riven_cloud";
  }
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

setInterval(async () => {
  if ($("appView").classList.contains("hidden")) return;
  try {
    [state.meshHealth, state.dashboard] = await Promise.all([
      api("/api/mesh/health?hours=3"),
      api("/api/dashboard"),
    ]);
    render();
  } catch {
    // The next interval retries without interrupting the dashboard.
  }
}, 60_000);

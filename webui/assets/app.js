const state = {
  csrf: "",
  loggedIn: false,
  collectorLogTimer: null,
  collectorLogManual: false,
  collectorLogBusy: false,
};

function pretty(obj) {
  return JSON.stringify(obj, null, 2);
}

function nowText() {
  return new Date().toLocaleString("zh-CN", { hour12: false });
}

function codeLevel(code) {
  if (!code || code === "-") {
    return "";
  }
  if (code === "OK") {
    return "ok";
  }
  if (code === "DDNS_NOOP") {
    return "noop";
  }
  return "error";
}

async function api(path, method = "GET", body = null) {
  const headers = {};
  if (body) {
    headers["Content-Type"] = "application/json";
  }
  if (method !== "GET" && state.csrf) {
    headers["X-CSRF-Token"] = state.csrf;
  }
  const res = await fetch(path, {
    method,
    headers,
    credentials: "include",
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) {
    throw data;
  }
  return data;
}

function errorPayload(err) {
  if (err && typeof err === "object" && "code" in err) {
    return err;
  }
  return {
    ok: false,
    code: "UNKNOWN_ERROR",
    message: err?.message || "请求失败",
    data: {},
    ts: new Date().toISOString(),
    traceId: "",
  };
}

function setOutput(id, payload) {
  const node = document.getElementById(id);
  node.textContent = pretty(payload);
}

function setStatus(msg, ok = false) {
  const el = document.getElementById("loginMsg");
  el.textContent = msg;
  el.style.color = ok ? "#106b43" : "#8a2716";
}

function setSessionUI(isLoggedIn, user = "guest") {
  const session = document.getElementById("sessionState");
  const loginUser = document.getElementById("loginUser");
  session.textContent = isLoggedIn ? "已登录" : "未登录";
  loginUser.textContent = user;
}

function setCodeBadge(id, code) {
  const el = document.getElementById(id);
  el.textContent = code || "-";
  el.classList.remove("ok", "error", "noop");
  const level = codeLevel(code);
  if (level) {
    el.classList.add(level);
  }
}

function switchTab(name) {
  document.querySelectorAll(".tabs button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".tab").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${name}`);
  });
}

function setCollectorLogState(text, running) {
  const stateNode = document.getElementById("collectorLogState");
  stateNode.textContent = text;
  stateNode.classList.toggle("running", !!running);
  const toggleBtn = document.getElementById("toggleCollectorLogBtn");
  toggleBtn.textContent = state.collectorLogManual ? "停止实时日志" : "开启实时日志";
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function normalizeLogLevel(level) {
  const upper = String(level || "INFO").toUpperCase();
  if (upper.includes("ERR")) {
    return "ERROR";
  }
  if (upper.includes("WARN")) {
    return "WARN";
  }
  return upper;
}

function renderCollectorEvents(lines) {
  const node = document.getElementById("collectorEventList");
  const items = [];

  for (const line of lines) {
    const raw = String(line || "").trim();
    if (!raw) {
      continue;
    }
    try {
      const obj = JSON.parse(raw);
      const ts = obj.ts || obj.time || "";
      const level = normalizeLogLevel(obj.level);
      const msg = obj.message || obj.msg || raw;
      const ctx = obj.context && typeof obj.context === "object" && Object.keys(obj.context).length > 0
        ? JSON.stringify(obj.context, null, 2)
        : "";
      items.push({
        ts: ts ? new Date(ts).toLocaleString("zh-CN", { hour12: false }) : "",
        level,
        msg: String(msg),
        ctx,
      });
    } catch (_) {
      items.push({
        ts: "",
        level: "INFO",
        msg: raw,
        ctx: "",
      });
    }
  }

  const latest = items.slice(-24).reverse();
  if (!latest.length) {
    node.innerHTML = '<div class="event-empty">暂无事件日志</div>';
    return;
  }

  node.innerHTML = latest
    .map((item) => {
      const levelClass = item.level === "ERROR" ? "error" : item.level === "WARN" ? "warn" : "";
      const tsHtml = item.ts ? `<span class="event-time">${escapeHtml(item.ts)}</span>` : "";
      const ctxHtml = item.ctx ? `<pre class="event-ctx">${escapeHtml(item.ctx)}</pre>` : "";
      return `<article class="event-card">
  <div class="event-meta">${tsHtml}<span class="event-level ${levelClass}">${escapeHtml(item.level)}</span></div>
  <div class="event-msg">${escapeHtml(item.msg)}</div>
  ${ctxHtml}
</article>`;
    })
    .join("");
}

async function fetchCollectorLiveLog() {
  if (state.collectorLogBusy) {
    return;
  }
  state.collectorLogBusy = true;
  try {
    const [cfstLog, appLog] = await Promise.all([
      api("/api/v1/system/logs?name=cfst&tail=120"),
      api("/api/v1/system/logs?name=app&tail=40"),
    ]);
    const cfstLines = cfstLog.data?.lines || [];
    const appLines = appLog.data?.lines || [];
    renderCollectorEvents(appLines);
    const rawNode = document.getElementById("collectorRawLog");
    rawNode.textContent = cfstLines.length
      ? `${cfstLines.join("\n")}\n\n更新时间: ${nowText()}`
      : `暂无原始输出\n\n更新时间: ${nowText()}`;
  } catch (err) {
    const payload = pretty(errorPayload(err));
    document.getElementById("collectorEventList").innerHTML = `<div class="event-empty">${escapeHtml(payload)}</div>`;
    document.getElementById("collectorRawLog").textContent = payload;
  } finally {
    state.collectorLogBusy = false;
  }
}

function startCollectorLogFollow(reason = "实时跟随中") {
  if (!state.collectorLogTimer) {
    state.collectorLogTimer = setInterval(fetchCollectorLiveLog, 2000);
  }
  setCollectorLogState(reason, true);
  void fetchCollectorLiveLog();
}

function stopCollectorLogFollow(reason = "未跟随") {
  if (state.collectorLogTimer) {
    clearInterval(state.collectorLogTimer);
    state.collectorLogTimer = null;
  }
  setCollectorLogState(reason, false);
}

async function runAction(buttonId, outputId, fn, withRefresh = false) {
  const btn = document.getElementById(buttonId);
  const oldText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "执行中...";
  setOutput(outputId, {
    ok: true,
    code: "PENDING",
    message: "任务正在执行，请稍候...",
    data: {},
    ts: new Date().toISOString(),
    traceId: "",
  });
  try {
    const out = await fn();
    setOutput(outputId, out);
    if (withRefresh) {
      await refreshDashboard();
    }
  } catch (err) {
    setOutput(outputId, errorPayload(err));
  } finally {
    btn.disabled = false;
    btn.textContent = oldText;
  }
}

async function runCollectorActionWithLiveLog() {
  const btn = document.getElementById("collectorRunBtn");
  const oldText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "采集中...";

  setOutput("collectorOut", {
    ok: true,
    code: "PENDING",
    message: "采集任务已提交，正在实时拉取日志...",
    data: {},
    ts: new Date().toISOString(),
    traceId: "",
  });

  startCollectorLogFollow("采集执行中，实时跟随");

  try {
    const out = await api("/api/v1/collector/run", "POST", {});
    setOutput("collectorOut", out);
    await refreshDashboard();
  } catch (err) {
    setOutput("collectorOut", errorPayload(err));
  } finally {
    await fetchCollectorLiveLog();
    btn.disabled = false;
    btn.textContent = oldText;
    if (state.collectorLogManual) {
      setCollectorLogState("手动实时跟随中", true);
    } else {
      stopCollectorLogFollow("空闲（可手动开启）");
    }
  }
}

async function refreshDashboard() {
  const [collector, ddns] = await Promise.all([
    api("/api/v1/collector/status"),
    api("/api/v1/ddns/status"),
  ]);

  document.getElementById("bestIp").textContent = collector.data?.state?.bestIp || "-";
  document.getElementById("dnsIp").textContent = ddns.data?.state?.currentDnsIp || "-";
  setCodeBadge("collectorCode", collector.code);
  setCodeBadge("ddnsCode", ddns.code);
  document.getElementById("lastRefresh").textContent = `最近更新：${nowText()}`;
}

async function login() {
  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;
  try {
    const out = await api("/api/v1/auth/login", "POST", { username, password });
    state.csrf = out.data.csrfToken;
    state.loggedIn = true;
    document.getElementById("loginPanel").classList.add("hidden");
    document.getElementById("appPanel").classList.remove("hidden");
    setSessionUI(true, out.data.user);
    setStatus("登录成功", true);
    await refreshDashboard();
  } catch (err) {
    setStatus(err.message || "登录失败");
  }
}

async function bootstrapSession() {
  try {
    const out = await api("/api/v1/auth/me");
    state.csrf = out.data.csrfToken;
    state.loggedIn = true;
    document.getElementById("loginPanel").classList.add("hidden");
    document.getElementById("appPanel").classList.remove("hidden");
    setSessionUI(true, out.data.user);
    await refreshDashboard();
  } catch (_) {
    state.loggedIn = false;
    setSessionUI(false, "guest");
  }
}

function bindEvents() {
  document.getElementById("loginBtn").addEventListener("click", login);
  document.getElementById("logoutBtn").addEventListener("click", async () => {
    try {
      await api("/api/v1/auth/logout", "POST", {});
    } finally {
      location.reload();
    }
  });

  document.querySelectorAll(".tabs button").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  document.getElementById("toggleCollectorLogBtn").addEventListener("click", async () => {
    state.collectorLogManual = !state.collectorLogManual;
    if (state.collectorLogManual) {
      startCollectorLogFollow("手动实时跟随中");
    } else {
      stopCollectorLogFollow("未跟随");
    }
  });

  document.getElementById("collectorRunBtn").addEventListener("click", runCollectorActionWithLiveLog);

  document.getElementById("collectorValidateBtn").addEventListener("click", async () => {
    await runAction(
      "collectorValidateBtn",
      "collectorOut",
      () => api("/api/v1/collector/config/validate", "POST", {}),
    );
  });

  document.getElementById("collectorEnableBtn").addEventListener("click", async () => {
    await runAction("collectorEnableBtn", "collectorOut", async () => {
      const intervalMinutes = Number(document.getElementById("collectorInterval").value || 20);
      return api("/api/v1/collector/schedule/enable", "POST", { intervalMinutes });
    });
  });

  document.getElementById("collectorPauseBtn").addEventListener("click", async () => {
    await runAction(
      "collectorPauseBtn",
      "collectorOut",
      () => api("/api/v1/collector/schedule/pause", "POST", {}),
    );
  });

  document.getElementById("ddnsSyncBtn").addEventListener("click", async () => {
    await runAction(
      "ddnsSyncBtn",
      "ddnsOut",
      () => api("/api/v1/ddns/sync", "POST", {}),
      true,
    );
  });

  document.getElementById("ddnsValidateBtn").addEventListener("click", async () => {
    await runAction(
      "ddnsValidateBtn",
      "ddnsOut",
      () => api("/api/v1/ddns/config/validate", "POST", {}),
    );
  });

  document.getElementById("ddnsEnableBtn").addEventListener("click", async () => {
    await runAction("ddnsEnableBtn", "ddnsOut", async () => {
      const intervalMinutes = Number(document.getElementById("ddnsInterval").value || 20);
      return api("/api/v1/ddns/schedule/enable", "POST", { intervalMinutes });
    });
  });

  document.getElementById("ddnsPauseBtn").addEventListener("click", async () => {
    await runAction(
      "ddnsPauseBtn",
      "ddnsOut",
      () => api("/api/v1/ddns/schedule/pause", "POST", {}),
    );
  });

  document.getElementById("rollbackBtn").addEventListener("click", async () => {
    await runAction(
      "rollbackBtn",
      "ddnsOut",
      async () => {
        const targetIp = document.getElementById("rollbackIp").value.trim();
        return api("/api/v1/ddns/rollback", "POST", { targetIp });
      },
      true,
    );
  });

  document.getElementById("refreshSystemBtn").addEventListener("click", async () => {
    await runAction("refreshSystemBtn", "systemOut", async () => {
      const [healthz, metrics] = await Promise.all([
        api("/api/v1/system/healthz"),
        api("/api/v1/system/metrics"),
      ]);
      return {
        ok: true,
        code: "OK",
        message: "success",
        data: { healthz, metrics },
      };
    });
  });

  document.getElementById("loadAuditBtn").addEventListener("click", async () => {
    await runAction("loadAuditBtn", "systemOut", () => api("/api/v1/system/audit?limit=100"));
  });

  document.getElementById("loadApiLogBtn").addEventListener("click", async () => {
    await runAction("loadApiLogBtn", "systemOut", () => api("/api/v1/system/logs?name=api&tail=200"));
  });
}

(async function init() {
  bindEvents();
  setCollectorLogState("未跟随", false);
  await bootstrapSession();
})();

const state = {
  csrf: "",
  loggedIn: false,
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

  document.getElementById("collectorRunBtn").addEventListener("click", async () => {
    const out = await api("/api/v1/collector/run", "POST", {});
    setOutput("collectorOut", out);
    await refreshDashboard();
  });

  document.getElementById("collectorValidateBtn").addEventListener("click", async () => {
    const out = await api("/api/v1/collector/config/validate", "POST", {});
    setOutput("collectorOut", out);
  });

  document.getElementById("collectorEnableBtn").addEventListener("click", async () => {
    const intervalMinutes = Number(document.getElementById("collectorInterval").value || 20);
    const out = await api("/api/v1/collector/schedule/enable", "POST", { intervalMinutes });
    setOutput("collectorOut", out);
  });

  document.getElementById("collectorPauseBtn").addEventListener("click", async () => {
    const out = await api("/api/v1/collector/schedule/pause", "POST", {});
    setOutput("collectorOut", out);
  });

  document.getElementById("ddnsSyncBtn").addEventListener("click", async () => {
    const out = await api("/api/v1/ddns/sync", "POST", {});
    setOutput("ddnsOut", out);
    await refreshDashboard();
  });

  document.getElementById("ddnsValidateBtn").addEventListener("click", async () => {
    const out = await api("/api/v1/ddns/config/validate", "POST", {});
    setOutput("ddnsOut", out);
  });

  document.getElementById("ddnsEnableBtn").addEventListener("click", async () => {
    const intervalMinutes = Number(document.getElementById("ddnsInterval").value || 20);
    const out = await api("/api/v1/ddns/schedule/enable", "POST", { intervalMinutes });
    setOutput("ddnsOut", out);
  });

  document.getElementById("ddnsPauseBtn").addEventListener("click", async () => {
    const out = await api("/api/v1/ddns/schedule/pause", "POST", {});
    setOutput("ddnsOut", out);
  });

  document.getElementById("rollbackBtn").addEventListener("click", async () => {
    const targetIp = document.getElementById("rollbackIp").value.trim();
    const out = await api("/api/v1/ddns/rollback", "POST", { targetIp });
    setOutput("ddnsOut", out);
    await refreshDashboard();
  });

  document.getElementById("refreshSystemBtn").addEventListener("click", async () => {
    const [healthz, metrics] = await Promise.all([
      api("/api/v1/system/healthz"),
      api("/api/v1/system/metrics"),
    ]);
    setOutput("systemOut", { healthz, metrics });
  });

  document.getElementById("loadAuditBtn").addEventListener("click", async () => {
    const out = await api("/api/v1/system/audit?limit=100");
    setOutput("systemOut", out);
  });

  document.getElementById("loadApiLogBtn").addEventListener("click", async () => {
    const out = await api("/api/v1/system/logs?name=api&tail=200");
    setOutput("systemOut", out);
  });
}

(async function init() {
  bindEvents();
  await bootstrapSession();
})();

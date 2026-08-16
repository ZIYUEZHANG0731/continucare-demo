let csrfToken = "";

function assertSafePayload(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("服务器返回了无效状态");
  }
  return value;
}

async function loadStateFrom(path, fallbackMessage) {
  const response = await fetch(path, {
    method: "GET",
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body?.error?.message || fallbackMessage);
  }
  csrfToken = response.headers.get("x-continucare-csrf") || "";
  if (!csrfToken) throw new Error("页面安全令牌缺失，请刷新");
  return assertSafePayload(body.data);
}

export async function loadState() {
  return loadStateFrom("/api/state", "患者端暂时不可用");
}

export async function loadNurseState(taskId = "") {
  const query = taskId ? `?taskId=${encodeURIComponent(taskId)}` : "";
  return loadStateFrom(`/api/nurse/state${query}`, "护士端暂时不可用");
}

export async function postCommand(path, payload) {
  if (!csrfToken) throw new Error("页面安全令牌已经失效，请刷新");
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-ContinuCare-CSRF": csrfToken,
    },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body?.error?.message || "操作未完成，请刷新后重试");
    error.code = body?.error?.code || "unknown";
    throw error;
  }
  return body;
}

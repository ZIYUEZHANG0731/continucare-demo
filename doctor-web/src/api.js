async function jsonResponse(response) {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body?.error?.message || "请求未完成");
    error.code = body?.error?.code || "unknown";
    error.status = response.status;
    throw error;
  }
  return body;
}

export async function loadDashboard(patientId = "") {
  const query = patientId ? `?patientId=${encodeURIComponent(patientId)}` : "";
  const response = await fetch(`/api/doctor/dashboard${query}`, {
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" }
  });
  const body = await jsonResponse(response);
  return body.data;
}

export async function loadPatients() {
  const response = await fetch("/api/doctor/patients", {
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" }
  });
  const body = await jsonResponse(response);
  return body.data;
}

export async function loadPlanning(patientId = "") {
  const query = patientId ? `?patientId=${encodeURIComponent(patientId)}` : "";
  const response = await fetch(`/api/doctor/planning${query}`, {
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" }
  });
  const body = await jsonResponse(response);
  return body.data;
}

export async function confirmPlan(plan) {
  const response = await fetch("/api/doctor/plans", {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json"
    },
    body: JSON.stringify(plan)
  });
  const body = await jsonResponse(response);
  return body.data;
}

export async function createSession(accessKey) {
  const response = await fetch("/api/session", {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ accessKey })
  });
  return jsonResponse(response);
}

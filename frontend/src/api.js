// All dashboard data comes from the FastAPI backend. Nothing here is hardcoded.
const BASE = import.meta.env.VITE_API_BASE || "";

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch {
    throw new Error("Cannot reach the backend. Is the API running on port 8000?");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body?.detail;
    const msg = Array.isArray(detail)
      ? detail.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join("; ")
      : detail || `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return body;
}

const qs = (params) => {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => v !== "" && v != null && p.append(k, v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const api = {
  meta: () => request("/api/meta"),
  stats: () => request("/api/stats"),
  samples: () => request("/api/samples"),
  seedSamples: () => request("/api/samples/seed", { method: "POST" }),
  tickets: (filters) => request(`/api/tickets${qs(filters)}`),
  ticket: (id) => request(`/api/tickets/${id}`),
  updateTicket: (id, body) => request(`/api/tickets/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  completeReview: (id, body) =>
    request(`/api/tickets/${id}/review`, { method: "POST", body: JSON.stringify(body) }),
  submitConversation: (body) => request("/api/conversations", { method: "POST", body: JSON.stringify(body) }),
  reprocess: (id) => request(`/api/conversations/${id}/process`, { method: "POST" }),
};

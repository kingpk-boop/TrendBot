/* Talks to the TrendBot server. Every non-GET request carries the X-TrendBot header the server requires. */

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

function errorText(data, status) {
  const d = data && data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {  // FastAPI validation errors
    return d.map(e => {
      const field = (e.loc || []).filter(p => p !== "body").join(".");
      const msg = String(e.msg || "Invalid value").replace(/^Value error, /, "");
      return field ? `${field}: ${msg}` : msg;
    }).join(" · ");
  }
  return `Request failed (${status}).`;
}

export async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch("/api" + path, {
      method,
      credentials: "same-origin",
      headers: { "X-TrendBot": "1", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Can't reach the TrendBot app. Is it still running on your computer?", 0);
  }
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    if (res.status === 401 && path !== "/login") onUnauthorized();
    throw new ApiError(errorText(data, res.status), res.status);
  }
  return data;
}

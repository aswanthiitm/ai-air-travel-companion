async function request(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const getUsers = () => request("/api/users");
export const getAirports = () => request("/api/airports");
export const getTwin = (userId) => request(`/api/twin/${userId}`);
export const postPlan = (body) =>
  request("/api/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
export const postFeedback = (body) =>
  request("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
export const getProfile = (userId) => request(`/api/profile/${userId}`);
export const getBenchmarks = () => request("/api/benchmarks");
export const postRecommend = (body) =>
  request("/api/recommend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

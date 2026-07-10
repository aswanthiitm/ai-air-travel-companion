async function request(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const getUsers = () => request("/api/users");
export const getProfile = (userId) => request(`/api/profile/${userId}`);
export const getBenchmarks = () => request("/api/benchmarks");
export const postRecommend = (body) =>
  request("/api/recommend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

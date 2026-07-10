import React, { useEffect, useMemo, useState } from "react";
import { getBenchmarks, getProfile, getUsers, postRecommend } from "./api.js";
import TwinPanel from "./panels/TwinPanel.jsx";
import ReasoningPanel from "./panels/ReasoningPanel.jsx";
import VerdictPanel from "./panels/VerdictPanel.jsx";

export default function App() {
  const [users, setUsers] = useState([]);
  const [benchmarks, setBenchmarks] = useState([]);
  const [userId, setUserId] = useState("");
  const [profile, setProfile] = useState(null);
  const [request, setRequest] = useState("");
  const [weights, setWeights] = useState(null); // slider overrides
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    getUsers().then(setUsers).catch((e) => setError(e.message));
    getBenchmarks().then(setBenchmarks).catch(() => {});
  }, []);

  useEffect(() => {
    if (!userId) return;
    setWeights(null);
    setResult(null);
    getProfile(userId).then(setProfile).catch((e) => setError(e.message));
  }, [userId]);

  const run = async (overrideWeights) => {
    if (!userId || !request.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const data = await postRecommend({
        user_id: userId,
        request,
        weights: overrideWeights ?? weights ?? undefined,
      });
      setResult(data);
      setProfile(data.profile); // includes request-adjusted weights + rationale
    } catch (e) {
      setError(e.message);
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  const userLabel = useMemo(
    () => Object.fromEntries(users.map((u) => [
      u.user_id,
      `${u.user_id} · ${u.home_city} · ${u.trip_purpose} · driven by ${u.driver}`,
    ])),
    [users]
  );

  return (
    <>
      <header className="masthead">
        <h1>Traveler Twin</h1>
        <span className="tagline">Google Flights knows flights. We know travelers.</span>
        {result && <span className="tagline">simulated today: {result.simulated_now}</span>}
      </header>

      <div className="controls">
        <select value={userId} onChange={(e) => setUserId(e.target.value)}>
          <option value="">— pick a traveler —</option>
          {users.map((u) => (
            <option key={u.user_id} value={u.user_id}>{userLabel[u.user_id]}</option>
          ))}
        </select>
        <input
          type="text"
          placeholder='Ask like a traveler: "Cheapest way to Tokyo next month"'
          value={request}
          onChange={(e) => setRequest(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
        />
        <button onClick={() => run()} disabled={loading || !userId || !request.trim()}>
          {loading ? "Thinking…" : "Ask the Twin"}
        </button>
      </div>

      <div className="bench-row">
        {benchmarks.map((b) => (
          <button
            key={b.prompt_id}
            className="bench-chip"
            title={b.request}
            onClick={() => { setUserId(b.user_id); setRequest(b.request); }}
          >
            {b.prompt_id} · {b.user_id}
          </button>
        ))}
      </div>

      <main className="deck">
        <TwinPanel
          profile={profile}
          weights={weights}
          onWeightsChange={setWeights}
          onRerun={() => run(weights)}
        />
        <ReasoningPanel result={result} loading={loading} error={error} />
        <VerdictPanel result={result} />
      </main>
    </>
  );
}

import React, { useEffect, useMemo, useState } from "react";
import { getAirports, getBenchmarks, getProfile, getTwin, getUsers,
         postFeedback, postPlan } from "./api.js";
import TwinPanel from "./panels/TwinPanel.jsx";
import ReasoningPanel from "./panels/ReasoningPanel.jsx";
import VerdictPanel from "./panels/VerdictPanel.jsx";
import JourneySummary from "./panels/JourneySummary.jsx";
import PipelineStrip from "./panels/PipelineStrip.jsx";
import TwinSummary from "./panels/TwinSummary.jsx";
import RouteMap from "./panels/RouteMap.jsx";

const EMPTY_FIELDS = { origin: "", destination: "", dates: "", travellers: "", cabin: "", budget: "" };

export default function App() {
  const [users, setUsers] = useState([]);
  const [benchmarks, setBenchmarks] = useState([]);
  const [airports, setAirports] = useState(null);
  const [userId, setUserId] = useState("");
  const [profile, setProfile] = useState(null);
  const [twinLog, setTwinLog] = useState([]);
  const [fields, setFields] = useState(EMPTY_FIELDS);
  const [message, setMessage] = useState("");
  const [conversationId] = useState(() => `web-${Date.now()}`);
  const [clarify, setClarify] = useState(null);      // agent's pending question
  const [acknowledged, setAcknowledged] = useState(null);
  const [result, setResult] = useState(null);
  const [toast, setToast] = useState(null);          // twin_updates flash
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [view, setView] = useState("user");          // user | judge

  useEffect(() => {
    getUsers().then(setUsers).catch((e) => setError(e.message));
    getBenchmarks().then(setBenchmarks).catch(() => {});
    getAirports().then(setAirports).catch(() => {});
  }, []);

  const refreshTwin = (uid) => {
    getTwin(uid)
      .then((t) => { setProfile(t.profile); setTwinLog(t.changelog); })
      .catch((e) => setError(e.message));
  };

  useEffect(() => {
    if (!userId) return;
    setResult(null); setClarify(null); setAcknowledged(null);
    refreshTwin(userId);
  }, [userId]);

  const flashTwinUpdates = (updates) => {
    if (updates?.length) {
      setToast(updates.map((u) => u.description));
      setTimeout(() => setToast(null), 6000);
      refreshTwin(userId);
    }
  };

  const plan = async () => {
    const hasFields = Object.values(fields).some((v) => String(v).trim());
    if (!userId || (!hasFields && !message.trim())) return;
    setLoading(true); setError(null); setAcknowledged(null);
    try {
      const cleaned = Object.fromEntries(
        Object.entries(fields).filter(([, v]) => String(v).trim()));
      const data = await postPlan({
        user_id: userId, conversation_id: conversationId,
        fields: hasFields ? cleaned : undefined,
        message: message.trim() || undefined,
      });
      if (data.status === "clarify") {
        setClarify(data.question); setResult(null); setMessage("");
      } else if (data.status === "acknowledged") {
        setClarify(null); setResult(null);
        setAcknowledged(data.narrative);
        flashTwinUpdates(data.twin_updates);
        setMessage("");
      } else {
        setClarify(null);
        setResult(data);
        setProfile(data.profile);
        flashTwinUpdates(data.twin_updates);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const sendFeedback = async (eventType, payload) => {
    try {
      const r = await postFeedback({
        user_id: userId, conversation_id: conversationId,
        event_type: eventType, payload,
      });
      flashTwinUpdates(r.twin_updates.length ? r.twin_updates
        : [{ description: "noted — consistent with what the Twin already believes" }]);
    } catch (e) { setError(e.message); }
  };

  const steerWeights = async (newWeights) => {
    if (!profile) return;
    const deltas = {};
    for (const k of Object.keys(newWeights)) {
      const d = newWeights[k] - profile.weights[k];
      if (Math.abs(d) > 0.02) deltas[k] = Number(d.toFixed(3));
    }
    if (Object.keys(deltas).length) {
      await sendFeedback("weights_steered", { deltas });
    }
    await plan();
  };

  const setField = (k, v) => setFields((f) => ({ ...f, [k]: v }));
  const userLabel = useMemo(
    () => Object.fromEntries(users.map((u) => [
      u.user_id, `${u.user_id} · ${u.home_city} · ${u.trip_purpose} · driven by ${u.driver}`,
    ])), [users]);

  return (
    <>
      <header className="masthead">
        <h1>Traveler Twin</h1>
        <span className="tagline">Intelligent Travel, Tailored to Every Traveler</span>
        {result && <span className="tagline">simulated today: {result.simulated_now}</span>}
        <div className="view-toggle" role="tablist" aria-label="View mode">
          <button className={view === "user" ? "on" : ""} onClick={() => setView("user")}>
            Traveler View
          </button>
          <button className={view === "judge" ? "on" : ""} onClick={() => setView("judge")}>
            Judge / Developer View
          </button>
        </div>
      </header>

      <div className="plan-surface">
        <div className="plan-row">
          <select value={userId} onChange={(e) => setUserId(e.target.value)}>
            <option value="">— pick a traveler —</option>
            {users.map((u) => (
              <option key={u.user_id} value={u.user_id}>{userLabel[u.user_id]}</option>
            ))}
          </select>
          <input type="text" className="pf" placeholder="From (optional — Twin knows home)"
                 value={fields.origin} onChange={(e) => setField("origin", e.target.value)} />
          <input type="text" className="pf" placeholder="To"
                 value={fields.destination} onChange={(e) => setField("destination", e.target.value)} />
          <input type="text" className="pf pf-s" placeholder="Dates (2025-06, next month…)"
                 value={fields.dates} onChange={(e) => setField("dates", e.target.value)} />
          <input type="text" className="pf pf-xs" placeholder="Pax"
                 value={fields.travellers} onChange={(e) => setField("travellers", e.target.value)} />
          <select value={fields.cabin} onChange={(e) => setField("cabin", e.target.value)}>
            <option value="">Cabin — any</option>
            {["Economy", "Premium Economy", "Business", "First"].map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <input type="text" className="pf pf-s" placeholder="Budget $ (optional)"
                 value={fields.budget} onChange={(e) => setField("budget", e.target.value)} />
        </div>

        {clarify && (
          <div className="agent-question">
            <span className="pass-eyebrow">The agent asks</span> {clarify}
          </div>
        )}

        <div className="plan-row">
          <textarea
            rows={2}
            placeholder={clarify
              ? "Answer the agent…"
              : 'Tell us anything else — "This is our honeymoon. I don\'t mind paying extra for comfort."'}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && (e.preventDefault(), plan())}
          />
          <button className="go" onClick={plan}
                  disabled={loading || !userId}>
            {loading ? "Thinking…" : clarify ? "Reply" : "Plan my trip"}
          </button>
        </div>
      </div>

      <div className="bench-row">
        {benchmarks.map((b) => (
          <button key={b.prompt_id} className="bench-chip" title={b.request}
                  onClick={() => { setUserId(b.user_id); setFields(EMPTY_FIELDS);
                                   setMessage(b.request); }}>
            {b.prompt_id} · {b.user_id}
          </button>
        ))}
      </div>

      {toast && (
        <div className="twin-toast">
          <b>Twin updated:</b> {toast.join(" · ")}
        </div>
      )}
      {acknowledged && (
        <div className="ack-banner">{acknowledged}</div>
      )}

      {loading && <div className="stage-status">Planning your trip…</div>}
      {error && !loading && <div className="stage-status error">{error}</div>}

      {!loading && !error && result?.recommendation?.feasible && view === "user" && (
        <main className="story">
          <JourneySummary result={result} />

          {result.narrative && (
            <section className="companion-note">
              <h3 className="eyebrow">Your AI companion</h3>
              <p>{result.narrative.startsWith("###")
                ? result.explanation.headline : result.narrative}</p>
            </section>
          )}

          <VerdictPanel result={result} onFeedback={sendFeedback} embedded />

          {result.recommendation.top && (
            <section className="story-map">
              <h3 className="eyebrow">Your route</h3>
              <RouteMap legs={result.recommendation.top.legs} airports={airports} />
            </section>
          )}

          <TwinSummary profile={profile} />
        </main>
      )}

      {!loading && !error && result && view === "user"
        && !result.recommendation?.feasible && (
        <main className="story">
          <JourneySummary result={result} />
          <section className="companion-note">
            <p>{result.explanation?.headline || "No itinerary matched — try widening the dates."}</p>
          </section>
        </main>
      )}

      {view === "judge" && (
        <main className="judge">
          {result && <PipelineStrip result={result} />}
          <div className="deck">
            <TwinPanel profile={profile} twinLog={twinLog} onSteer={steerWeights} />
            <ReasoningPanel result={result} loading={loading} error={error} airports={airports} />
            <VerdictPanel result={result} onFeedback={sendFeedback} />
          </div>
        </main>
      )}

      {!result && !loading && view === "user" && (
        <div className="stage-status">Pick a traveler and describe your trip to begin.</div>
      )}
    </>
  );
}

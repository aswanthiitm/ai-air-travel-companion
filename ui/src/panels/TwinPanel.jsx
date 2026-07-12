import React, { useState } from "react";
import TravelerDNA from "./TravelerDNA.jsx";

const WEIGHT_KEYS = ["price", "time", "convenience", "comfort", "loyalty"];

export default function TwinPanel({ profile, twinLog, onSteer }) {
  const [weights, setWeights] = useState(null); // local slider state
  if (!profile) return <div className="panel"><h2>The Twin</h2><p className="status">Pick a traveler</p></div>;

  const history = profile.signals.filter(
    (s) => s.source !== "structured_field" && s.dimension !== "unclassified");
  const learned = profile.signals.filter(
    (s) => s.source === "behavior" || s.source === "feedback");
  const shown = history.filter((s) => !learned.includes(s)).slice(0, 7);

  return (
    <div className="panel">
      <h2>The Twin — {profile.user_id}</h2>
      <p style={{ fontFamily: "var(--serif)", marginBottom: 8 }}>
        {profile.home_city} ({profile.home_airport}) · {profile.trip_purpose} traveler
        {profile.party.children > 0 && ` · ${profile.party.children} kid(s)`}
      </p>

      <TravelerDNA
        weights={Object.fromEntries(
          Object.entries(profile.weights).map(([k, v]) => [k, weights?.[k] ?? v]))}
        userId={profile.user_id}
      />

      <h3>Decision weights (drag to steer — the Twin remembers)</h3>
      {WEIGHT_KEYS.map((k) => (
        <div className="weight-row" key={k}>
          <label>{k}</label>
          <input
            type="range" min="0" max="100"
            value={Math.round((weights?.[k] ?? profile.weights[k]) * 100)}
            onChange={(e) => setWeights({ ...(weights ?? profile.weights), [k]: e.target.value / 100 })}
          />
          <output>{((weights?.[k] ?? profile.weights[k]) * 100).toFixed(0)}%</output>
        </div>
      ))}
      {weights && (
        <button className="bench-chip" style={{ marginTop: 6 }}
                onClick={() => { onSteer(weights); setWeights(null); }}>
          Re-plan with these weights
        </button>
      )}

      {learned.length > 0 && (
        <>
          <h3>Recently learned (live)</h3>
          <div className="chip-list">
            {learned.slice(-5).reverse().map((s, i) => (
              <div className="evidence-chip learned" key={i}>
                <span className="dim">{s.dimension.replace(/_/g, " ")}</span> → {String(s.value)}
                {" "}<span className="conf">({Math.round(s.confidence * 100)}%)</span>
                <div className="quote">“{s.evidence}”</div>
              </div>
            ))}
          </div>
        </>
      )}

      <h3>Hard limits</h3>
      <ul className="note-list">
        <li>Layovers ≤ {profile.hard.max_layover_minutes} min (floor {profile.hard.min_layover_minutes})</li>
        <li>{profile.hard.required_seats} seat(s) needed</li>
        {profile.hard.cabin_strict && <li>Business-or-above only</li>}
        {profile.flexibility.value_of_time_usd_per_hr && (
          <li>Values time at ~${profile.flexibility.value_of_time_usd_per_hr.toFixed(0)}/hr (revealed)</li>
        )}
      </ul>

      <h3>Baseline evidence</h3>
      <div className="chip-list">
        {shown.map((s, i) => (
          <div className="evidence-chip" key={i}>
            <span className="dim">{s.dimension.replace(/_/g, " ")}</span>{" — "}
            <span className="quote">“{s.evidence}”</span>
          </div>
        ))}
      </div>

      {twinLog?.length > 0 && (
        <>
          <h3>Twin changelog</h3>
          <ul className="note-list">
            {twinLog.slice(0, 5).map((c, i) => <li key={i}>{c.description}</li>)}
          </ul>
        </>
      )}

      {profile.conflicts.length > 0 && (
        <>
          <h3>Conflicts resolved</h3>
          <div className="chip-list">
            {profile.conflicts.map((c, i) => (
              <div className="evidence-chip conflict" key={i}>{c.reason}</div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

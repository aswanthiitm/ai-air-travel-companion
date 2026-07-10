import React from "react";

const WEIGHT_KEYS = ["price", "time", "convenience", "comfort", "loyalty"];

export default function TwinPanel({ profile, weights, onWeightsChange, onRerun }) {
  if (!profile) return <div className="panel"><h2>The Twin</h2><p className="status">Pick a traveler</p></div>;

  const history = profile.signals.filter((s) => s.source === "raw_history" && s.dimension !== "unclassified");
  const shown = history.slice(0, 10);

  return (
    <div className="panel">
      <h2>The Twin — {profile.user_id}</h2>
      <p style={{ fontFamily: "var(--serif)", marginBottom: 8 }}>
        {profile.home_city} ({profile.home_airport}) · {profile.trip_purpose} traveler
        {profile.party.children > 0 && ` · ${profile.party.children} kid(s)`}
      </p>

      <h3>Decision weights (drag to steer)</h3>
      {WEIGHT_KEYS.map((k) => (
        <div className="weight-row" key={k}>
          <label>{k}</label>
          <input
            type="range" min="0" max="100"
            value={Math.round((weights?.[k] ?? profile.weights[k]) * 100)}
            onChange={(e) => onWeightsChange({ ...(weights ?? profile.weights), [k]: e.target.value / 100 })}
          />
          <output>{((weights?.[k] ?? profile.weights[k]) * 100).toFixed(0)}%</output>
        </div>
      ))}
      {weights && (
        <button className="bench-chip" onClick={onRerun} style={{ marginTop: 6 }}>
          Re-run with these weights
        </button>
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

      <h3>Evidence ({history.length} history signals)</h3>
      <div className="chip-list">
        {shown.map((s, i) => (
          <div className="evidence-chip" key={i}>
            <span className="dim">{s.dimension.replace(/_/g, " ")}</span>{" — "}
            <span className="quote">“{s.evidence}”</span>
          </div>
        ))}
      </div>

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

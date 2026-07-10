import React from "react";

export default function ReasoningPanel({ result, loading, error }) {
  if (error) return <div className="panel"><h2>The Reasoning</h2><p className="status error">{error}</p></div>;
  if (loading) return <div className="panel"><h2>The Reasoning</h2><p className="status">Negotiating with 50,000 flights…</p></div>;
  if (!result) return <div className="panel"><h2>The Reasoning</h2><p className="status">Ask something to watch the search unfold</p></div>;

  const { recommendation: rec, trip } = result;
  const max = rec.funnel.length ? rec.funnel[0].count : 1;

  return (
    <div className="panel">
      <h2>The Reasoning</h2>

      <h3>How the request was resolved</h3>
      <ul className="note-list">
        <li>
          {trip.origin} → {trip.destinations.join(" + ")} · {trip.trip_type.replace("_", " ")} ·
          window {trip.depart_window.start} → {trip.depart_window.end}
        </li>
        {trip.notes.map((n, i) => <li key={i}>{n}</li>)}
      </ul>

      <h3>The funnel (first leg)</h3>
      {rec.funnel.map((f, i) => (
        <div className="funnel-stage" key={i}>
          <span className="label">{f.stage}</span>
          <div className="bar">
            <div style={{ width: `${Math.max(1.5, (Math.log10(f.count + 1) / Math.log10(max + 1)) * 100)}%` }} />
          </div>
          <output>{f.count.toLocaleString()}</output>
        </div>
      ))}

      {rec.relaxations.length > 0 && (
        <>
          <h3>Honest negotiation — what had to give</h3>
          <ul className="note-list concession-list">
            {rec.relaxations.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </>
      )}
    </div>
  );
}

import React from "react";

function fmtMin(m) {
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}m`;
}

function Legs({ legs }) {
  return legs.map((l, i) => (
    <div className="leg-line" key={i}>
      {l.origin}→{l.destination} · {l.airline_code} {l.cabin_class} ·{" "}
      {l.stops === 0 ? "direct" : `${l.stops} stop (${l.layover_airports.join(", ")})`} ·{" "}
      {l.departure_date_local} · ${Math.round(l.price).toLocaleString()}
    </div>
  ));
}

function Card({ itinerary, label, headline, deltaPrice, deltaMinutes, worthIt, isTop }) {
  const scarce = itinerary.scarce;
  const holiday = itinerary.annotations.some((a) => a.is_holiday_season);
  return (
    <div className={`verdict-card${isTop ? "" : " alt"}`}>
      {label && <span className="badge label">{label.replace("_", " ")}</span>}
      {scarce && <span className="badge scarce">few seats left</span>}
      {holiday && <span className="badge holiday">holiday pricing</span>}
      {headline && <div className="headline">{headline}</div>}
      <div className="price-line">
        <span className="price">${Math.round(itinerary.total_price).toLocaleString()}</span>
        <span className="meta">
          {fmtMin(itinerary.total_minutes)} in the air
          {deltaPrice != null &&
            ` · ${deltaPrice >= 0 ? "+" : "−"}$${Math.abs(Math.round(deltaPrice)).toLocaleString()} vs top`}
          {deltaMinutes != null && deltaMinutes !== 0 &&
            ` · ${deltaMinutes > 0 ? "+" : "−"}${fmtMin(Math.abs(deltaMinutes))}`}
        </span>
      </div>
      <Legs legs={itinerary.legs} />
      {worthIt && (
        <div className="worth-it">
          Worth-It math: {Math.abs(worthIt.extra_hours).toFixed(1)} hrs at ~$
          {worthIt.value_of_time_usd_per_hr.toFixed(0)}/hr ≈ ${Math.abs(worthIt.time_cost_usd).toFixed(0)} vs $
          {Math.abs(worthIt.savings_usd).toLocaleString()} —{" "}
          <b className={worthIt.verdict === "worth_it" ? "yes" : "no"}>
            {worthIt.verdict === "worth_it" ? "worth it" : "not worth it"}
          </b>
        </div>
      )}
    </div>
  );
}

export default function VerdictPanel({ result }) {
  if (!result) return <div className="panel"><h2>The Verdict</h2><p className="status">No recommendation yet</p></div>;
  const { recommendation: rec, explanation: expl } = result;
  if (!rec.feasible) {
    return (
      <div className="panel">
        <h2>The Verdict</h2>
        <p className="status">{expl.headline}</p>
      </div>
    );
  }
  return (
    <div className="panel">
      <h2>The Verdict</h2>
      <Card itinerary={rec.top} headline={expl.headline} isTop />

      {rec.alternatives.length > 0 && <h3>The alternatives</h3>}
      {rec.alternatives.map((alt) => (
        <Card
          key={alt.label}
          itinerary={alt.itinerary}
          label={alt.label}
          deltaPrice={alt.delta_price}
          deltaMinutes={alt.delta_minutes}
          worthIt={alt.worth_it}
        />
      ))}

      <h3>Why this pick</h3>
      <ul className="note-list section-list">
        {expl.why_top.map((w, i) => <li key={i}>{w}</li>)}
      </ul>

      {expl.market_context.length > 0 && (
        <>
          <h3>Market context</h3>
          <ul className="note-list section-list">
            {expl.market_context.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        </>
      )}

      {expl.caveats.length > 0 && (
        <>
          <h3>Fine print</h3>
          <ul className="note-list section-list">
            {expl.caveats.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </>
      )}
    </div>
  );
}

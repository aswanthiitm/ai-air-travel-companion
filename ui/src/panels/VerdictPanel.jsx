import React from "react";

function fmtMin(m) {
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}m`;
}

function Legs({ legs }) {
  return (
    <div className="pass-legs">
      {legs.map((l, i) => (
        <div className="leg-line" key={i}>
          <span className="leg-route">{l.origin}→{l.destination}</span>
          <span>{l.airline_code} · {l.cabin_class.toUpperCase()}</span>
          <span>{l.stops === 0 ? "DIRECT" : `${l.stops} STOP ${l.layover_airports.join("/")}`}</span>
          <span>{l.departure_date_local}</span>
          <span className="leg-price">${Math.round(l.price).toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

function BoardingPass({ itinerary, label, headline, deltaPrice, deltaMinutes, worthIt, isTop, index }) {
  const scarce = itinerary.scarce;
  const holiday = itinerary.annotations.some((a) => a.is_holiday_season);
  const stops = [itinerary.legs[0].origin, ...itinerary.legs.map((l) => l.destination)];

  return (
    <article className={`pass${isTop ? " top" : ""}`} style={{ "--i": index }}>
      <header className="pass-head">
        <span className="pass-eyebrow">
          {isTop ? "Traveler Twin · top pick" : `Alternative · ${label.replace("_", " ")}`}
        </span>
        <span className="pass-badges">
          {scarce && <span className="badge scarce">few seats left</span>}
          {holiday && <span className="badge holiday">holiday pricing</span>}
        </span>
      </header>

      <div className="pass-route">{stops.join(" → ")}</div>
      {isTop && headline && <p className="pass-headline">{headline}</p>}

      <div className="pass-stub">
        <div className="pass-fact">
          <label>Total</label>
          <output>${Math.round(itinerary.total_price).toLocaleString()}</output>
        </div>
        <div className="pass-fact">
          <label>In the air</label>
          <output>{fmtMin(itinerary.total_minutes)}</output>
        </div>
        {deltaPrice != null && (
          <div className="pass-fact">
            <label>vs top</label>
            <output>
              {deltaPrice >= 0 ? "+" : "−"}${Math.abs(Math.round(deltaPrice)).toLocaleString()}
              {deltaMinutes !== 0 &&
                ` / ${deltaMinutes > 0 ? "+" : "−"}${fmtMin(Math.abs(deltaMinutes))}`}
            </output>
          </div>
        )}
        <div className="pass-barcode" aria-hidden="true" />
      </div>

      <Legs legs={itinerary.legs} />

      {worthIt && (
        <div className="worth-it">
          {Math.abs(worthIt.extra_hours).toFixed(1)} hrs at ~$
          {worthIt.value_of_time_usd_per_hr.toFixed(0)}/hr ≈ ${Math.abs(worthIt.time_cost_usd).toFixed(0)}{" "}
          vs ${Math.abs(worthIt.savings_usd).toLocaleString()} —{" "}
          <b className={worthIt.verdict === "worth_it" ? "yes" : "no"}>
            {worthIt.verdict === "worth_it" ? "worth it" : "not worth it"}
          </b>
        </div>
      )}
    </article>
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
      <BoardingPass itinerary={rec.top} headline={expl.headline} isTop index={0} />

      {rec.alternatives.map((alt, i) => (
        <BoardingPass
          key={alt.label}
          itinerary={alt.itinerary}
          label={alt.label}
          deltaPrice={alt.delta_price}
          deltaMinutes={alt.delta_minutes}
          worthIt={alt.worth_it}
          index={i + 1}
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

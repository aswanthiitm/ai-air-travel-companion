import React, { useState } from "react";

function fmtMin(m) {
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}m`;
}

// --- Traveler-facing copy, built from structured data (no engine jargon) ----

function friendlyHighlights(result) {
  const { recommendation: rec, profile } = result;
  const top = rec.top;
  const legs = top.legs;
  const pref = profile?.soft?.airlines || [];
  const usedPref = [...new Set(legs.map((l) => l.airline_code))].filter((a) => pref.includes(a));
  const cabins = [...new Set(legs.map((l) => l.cabin_class))];
  const names = [...new Set(legs.map((l) => l.airline_name))];
  const out = [];

  if (top.max_stops === 0) out.push("Non-stop the whole way — no connections to worry about");
  else out.push(`Just ${top.max_stops} short stop${top.max_stops > 1 ? "s" : ""}, chosen to keep the price down`);

  if (usedPref.length) {
    const pretty = usedPref.map((c) => names.find((n) => n) || c);
    out.push(`Flies ${usedPref.join(" & ")} — ${usedPref.length > 1 ? "airlines" : "an airline"} you like`);
  }
  if (cabins.length === 1 && (cabins[0] === "Business" || cabins[0] === "First"))
    out.push(`${cabins[0]}-class comfort on every leg`);
  if (profile?.soft?.redeye_policy === "avoid" && !legs.some((l) => l.is_redeye))
    out.push("No overnight flights — you arrive rested");
  if ((profile?.soft?.checked_bags || 0) > 0 && legs.every((l) => l.baggage_included))
    out.push("Checked bags included on every flight");

  const cheapest = rec.ranked.reduce((a, b) => (b.total_price < a.total_price ? b : a), rec.ranked[0]);
  if (cheapest && cheapest.flight_ids.join() === top.flight_ids.join())
    out.push("The best-value fare we could find for you");

  const vot = profile?.flexibility?.value_of_time_usd_per_hr;
  if (vot && out.length < 5)
    out.push("Balances price and travel time the way you usually do");

  return out.slice(0, 5);
}

function friendlyInsights(result) {
  const anns = result.recommendation.top?.annotations || [];
  if (!anns.length) return [];
  const out = [];
  const seats = Math.min(...anns.map((a) => a.seats_available));
  if (seats <= 3)
    out.push({ tone: "urgent", text: `Only ${seats} seat${seats > 1 ? "s" : ""} left at this price — worth booking soon.` });

  const holiday = anns.some((a) => a.is_holiday_season);
  const uplift = Math.max(...anns.map((a) => a.seasonal_uplift ?? 0));
  const dip = Math.min(...anns.map((a) => a.seasonal_uplift ?? 0));
  if (holiday)
    out.push({ tone: "info", text: "You're travelling in peak season, so fares run higher than usual — but this is a strong pick for these dates." });
  else if (uplift >= 0.15)
    out.push({ tone: "info", text: "Prices this time of year are a little above average, so locking this in now is smart." });
  else if (dip <= -0.1)
    out.push({ tone: "good", text: "Good timing — you're travelling off-peak, so fares are lower than usual." });
  return out;
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

function itinerarySummary(itinerary) {
  return {
    max_stops: itinerary.max_stops,
    is_redeye: itinerary.legs.some((l) => l.is_redeye),
    airlines: [...new Set(itinerary.legs.map((l) => l.airline_code))],
    cabins: [...new Set(itinerary.legs.map((l) => l.cabin_class))],
    total_price: itinerary.total_price,
  };
}

function FeedbackBar({ itinerary, onFeedback }) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [done, setDone] = useState(null);
  if (done) return <div className="feedback-bar done">✓ {done} — the Twin took note</div>;
  if (rejecting) {
    return (
      <div className="feedback-bar">
        <input type="text" placeholder="Why not? (optional — the Twin learns from this)"
               value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
        <button onClick={() => {
          onFeedback("recommendation_rejected",
                     { itinerary: itinerarySummary(itinerary), reason: reason || undefined });
          setDone("rejected");
        }}>Send</button>
        <button className="ghost" onClick={() => setRejecting(false)}>Cancel</button>
      </div>
    );
  }
  return (
    <div className="feedback-bar">
      <button onClick={() => {
        onFeedback("recommendation_accepted", { itinerary: itinerarySummary(itinerary) });
        setDone("accepted");
      }}>Take this one</button>
      <button className="ghost" onClick={() => setRejecting(true)}>Not for me</button>
    </div>
  );
}

function BoardingPass({ itinerary, label, headline, deltaPrice, deltaMinutes, worthIt, isTop, index, onFeedback }) {
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
      {isTop && onFeedback && <FeedbackBar itinerary={itinerary} onFeedback={onFeedback} />}
    </article>
  );
}

export default function VerdictPanel({ result, onFeedback, embedded }) {
  if (!result) return <div className="panel"><h2>The Verdict</h2><p className="status">No recommendation yet</p></div>;
  const { recommendation: rec, explanation: expl } = result;
  if (!rec.feasible) {
    return (
      <div className={embedded ? "verdict-embedded" : "panel"}>
        {!embedded && <h2>The Verdict</h2>}
        <p className="status">{expl.headline}</p>
      </div>
    );
  }
  const Wrap = embedded ? "section" : "div";
  return (
    <Wrap className={embedded ? "verdict-embedded" : "panel"}>
      {!embedded && <h2>The Verdict</h2>}
      {embedded && <h3 className="eyebrow">Recommended for you</h3>}
      <BoardingPass itinerary={rec.top} headline={expl.headline} isTop index={0}
                    onFeedback={onFeedback} />

      {rec.alternatives.length > 0 && embedded && (
        <h3 className="eyebrow" style={{ marginTop: 18 }}>You may also consider</h3>)}
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

      {embedded ? (
        <>
          <h3 className="eyebrow" style={{ marginTop: 18 }}>Why we picked this for you</h3>
          <ul className="highlight-list">
            {friendlyHighlights(result).map((h, i) => (
              <li key={i}><span className="hl-check">✓</span>{h}</li>
            ))}
          </ul>

          {friendlyInsights(result).length > 0 && (
            <>
              <h3 className="eyebrow" style={{ marginTop: 18 }}>Good to know</h3>
              <div className="insight-list">
                {friendlyInsights(result).map((ins, i) => (
                  <div className={`insight ${ins.tone}`} key={i}>{ins.text}</div>
                ))}
              </div>
            </>
          )}
        </>
      ) : (
        <>
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
        </>
      )}
    </Wrap>
  );
}

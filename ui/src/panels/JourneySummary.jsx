import React from "react";

/**
 * "What the AI understood" — the interpreted request as clean chips, so the
 * user sees the system parsed them correctly before the recommendation.
 */
export default function JourneySummary({ result }) {
  if (!result?.trip) return null;
  const { trip, profile, reasoning, slots } = result;
  const cabin = slots?.cabin?.value;
  const budget = slots?.budget?.value;

  const seats = profile?.hard?.required_seats ?? slots?.travellers?.value ?? 1;
  const chips = [
    ["Destination", trip.destinations.join(" → ")],
    ["Dates", `${trip.depart_window.start} → ${trip.depart_window.end}`],
    ["Travellers", `${seats} ${seats > 1 ? "travellers" : "traveller"}`],
    ["Trip", (reasoning?.purpose || trip.trip_type || "").replace(/_/g, " ")],
    budget ? ["Budget", `$${Number(budget).toLocaleString()}`] : null,
    cabin ? ["Cabin", cabin] : null,
    ["Strategy", (reasoning?.strategy || "balanced").replace(/_/g, " ").toLowerCase()],
  ].filter(Boolean);

  return (
    <section className="journey-summary">
      <h3 className="eyebrow">What I understood</h3>
      <div className="summary-grid">
        {chips.map(([label, value]) => (
          <div className="summary-cell" key={label}>
            <label>{label}</label>
            <output>{value || "—"}</output>
          </div>
        ))}
      </div>
    </section>
  );
}

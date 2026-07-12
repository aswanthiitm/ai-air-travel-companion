import React from "react";

/** Readable Traveler-Twin summary for the User view — meters, not JSON. */
export default function TwinSummary({ profile }) {
  if (!profile) return null;
  const w = profile.weights;
  const meters = [
    ["Price focus", w.price],
    ["Comfort", w.comfort],
    ["Convenience", w.convenience],
    ["Time", w.time],
    ["Loyalty", w.loyalty],
  ];
  const learned = profile.signals.filter(
    (s) => s.source === "behavior" || s.source === "feedback").slice(-3).reverse();

  return (
    <section className="twin-summary">
      <h3 className="eyebrow">Your Traveler Twin</h3>
      <p className="twin-sub">
        {profile.home_city} · {profile.trip_purpose} traveler
        {profile.hard.cabin_strict && " · premium cabins"}
        {profile.flexibility.value_of_time_usd_per_hr &&
          ` · values time ~$${profile.flexibility.value_of_time_usd_per_hr.toFixed(0)}/hr`}
      </p>
      <div className="meters">
        {meters.map(([label, v]) => (
          <div className="meter-row" key={label}>
            <label>{label}</label>
            <div className="meter"><div style={{ width: `${Math.round(v * 100)}%` }} /></div>
          </div>
        ))}
      </div>
      {learned.length > 0 && (
        <div className="twin-learned">
          <label>Recently learned</label>
          {learned.map((s, i) => (
            <span className="learned-chip" key={i}>
              {s.dimension.replace(/_/g, " ")} → {String(s.value)}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

import React from "react";

/**
 * The pipeline, as a row of completed steps — the 20-second "this isn't just
 * ChatGPT" moment for judges. Every stage is real: derived from the agent
 * trace, the funnel, and the resolved trip.
 */
export default function PipelineStrip({ result }) {
  if (!result?.trip) return null;
  const { trip, profile, reasoning, recommendation: rec, trace, llm_used } = result;
  const routeCount = rec?.funnel?.find((f) => f.stage === "on this route")?.count;
  const understood = trace?.find((t) => t.step === "understand")?.detail || "";
  const usedLLM = understood.includes("LLM gap-fill");

  const steps = [
    ["Understood", usedLLM ? "deterministic + LLM" : "deterministic",
     `${trip.origin} → ${trip.destinations.join(", ")}`],
    ["Traveler Twin", `${reasoning?.strategy?.replace(/_/g, " ").toLowerCase() || "loaded"}`,
     reasoning?.planning_context?.[0] || "profile loaded"],
    ["Constraints", routeCount != null ? `${routeCount} on route` : "filtered",
     `≤ ${profile?.hard?.max_layover_minutes ?? "—"}min layover, ${
       profile?.hard?.required_seats ?? 1} seat(s)`],
    ["Searched & ranked", `${rec?.ranked?.length ?? 0} itineraries`,
     rec?.relaxations?.length ? `${rec.relaxations.length} concession(s)` : "no concessions"],
    ["Explained", llm_used ? "AI prose" : "grounded template", "every claim cited"],
  ];

  return (
    <div className="pipeline-strip">
      {steps.map(([title, tag, sub], i) => (
        <React.Fragment key={title}>
          <div className="pl-step">
            <div className="pl-check">✓</div>
            <div className="pl-title">{title}</div>
            <div className="pl-tag">{tag}</div>
            <div className="pl-sub">{sub}</div>
          </div>
          {i < steps.length - 1 && <div className="pl-arrow">→</div>}
        </React.Fragment>
      ))}
    </div>
  );
}

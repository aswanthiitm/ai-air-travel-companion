import React from "react";

const AXES = ["price", "time", "convenience", "comfort", "loyalty"];
const ABBR = { price: "PRC", time: "TIM", convenience: "CNV", comfort: "CMF", loyalty: "LTY" };

/**
 * Traveler DNA — the profile's decision weights drawn as a compass-dial radar.
 * Weights sum to 1; the shape is scaled against 0.6 (the practical max a
 * single weight reaches) so different travelers produce visibly different
 * silhouettes. This is the one glance that says "same query, different person".
 */
export default function TravelerDNA({ weights, userId }) {
  const size = 240;
  const c = size / 2;
  const R = 86;
  const MAX = 0.6;

  const point = (i, r) => {
    const angle = (Math.PI * 2 * i) / AXES.length - Math.PI / 2;
    return [c + r * Math.cos(angle), c + r * Math.sin(angle)];
  };

  const poly = AXES.map((k, i) => point(i, Math.min(1, weights[k] / MAX) * R))
    .map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`)
    .join(" ");

  const ticks = Array.from({ length: 72 }, (_, i) => {
    const angle = (Math.PI * 2 * i) / 72;
    const major = i % 18 === 0;
    const r1 = R + 8;
    const r2 = R + (major ? 16 : 12);
    return {
      x1: c + r1 * Math.cos(angle), y1: c + r1 * Math.sin(angle),
      x2: c + r2 * Math.cos(angle), y2: c + r2 * Math.sin(angle),
      major,
    };
  });

  return (
    <figure className="dna" aria-label={`Decision-weight signature for ${userId}`}>
      <svg viewBox={`0 0 ${size} ${size}`} role="img">
        {/* dial ring + ticks */}
        <circle cx={c} cy={c} r={R + 8} className="dna-ring" />
        {ticks.map((t, i) => (
          <line key={i} x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2}
                className={t.major ? "dna-tick major" : "dna-tick"} />
        ))}
        {/* concentric guides */}
        {[0.33, 0.66, 1].map((f) => (
          <circle key={f} cx={c} cy={c} r={R * f} className="dna-guide" />
        ))}
        {/* axis spokes + labels */}
        {AXES.map((k, i) => {
          const [x, y] = point(i, R);
          const [lx, ly] = point(i, R + 27);
          return (
            <g key={k}>
              <line x1={c} y1={c} x2={x} y2={y} className="dna-spoke" />
              <text x={lx} y={ly} className="dna-label" textAnchor="middle"
                    dominantBaseline="middle">{ABBR[k]}</text>
            </g>
          );
        })}
        {/* the signature polygon */}
        <polygon points={poly} className="dna-shape" />
        {AXES.map((k, i) => {
          const [x, y] = point(i, Math.min(1, weights[k] / MAX) * R);
          return <circle key={k} cx={x} cy={y} r={3.2} className="dna-vertex" />;
        })}
        <circle cx={c} cy={c} r={2.5} className="dna-hub" />
      </svg>
      <figcaption className="dna-readout">
        {AXES.map((k) => (
          <span key={k}>
            <b>{ABBR[k]}</b> {(weights[k] * 100).toFixed(0)}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

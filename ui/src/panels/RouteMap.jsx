import React from "react";

/**
 * Route map — the itinerary's legs as arcs over a plain graticule (aviation-
 * chart vernacular, no coastline clutter). The projection auto-fits the
 * trip's bounding box, so a Europe hop fills the canvas as well as a
 * trans-Pacific chain. Coordinates come from /api/airports.
 */
const W = 640;
const H = 300;
const PAD = 34;

function gridStep(span) {
  if (span > 90) return 30;
  if (span > 40) return 15;
  if (span > 15) return 10;
  return 5;
}

export default function RouteMap({ legs, airports }) {
  if (!legs?.length || !airports) return null;

  const stops = [legs[0].origin, ...legs.map((l) => l.destination)];
  if (!stops.every((s) => airports[s])) return null;

  const lats = stops.map((s) => airports[s].lat);
  const lons = stops.map((s) => airports[s].lon);
  const latPad = Math.max(5, (Math.max(...lats) - Math.min(...lats)) * 0.25);
  const lonPad = Math.max(5, (Math.max(...lons) - Math.min(...lons)) * 0.18);
  const box = {
    latMin: Math.max(-85, Math.min(...lats) - latPad),
    latMax: Math.min(85, Math.max(...lats) + latPad),
    lonMin: Math.max(-180, Math.min(...lons) - lonPad),
    lonMax: Math.min(180, Math.max(...lons) + lonPad),
  };
  // uniform scale, centered — shapes stay honest
  const scale = Math.min(
    (W - PAD * 2) / (box.lonMax - box.lonMin),
    (H - PAD * 2) / (box.latMax - box.latMin)
  );
  const cx = (box.lonMin + box.lonMax) / 2;
  const cy = (box.latMin + box.latMax) / 2;
  const project = (lat, lon) => [
    W / 2 + (lon - cx) * scale,
    H / 2 + (cy - lat) * scale,
  ];

  const points = Object.fromEntries(
    [...new Set(stops)].map((iata) => [iata, project(airports[iata].lat, airports[iata].lon)])
  );

  const arcPath = ([x1, y1], [x2, y2]) => {
    const dist = Math.hypot(x2 - x1, y2 - y1);
    const lift = Math.min(46, dist * 0.22);
    return `M ${x1.toFixed(1)} ${y1.toFixed(1)} Q ${((x1 + x2) / 2).toFixed(1)} ${((y1 + y2) / 2 - lift).toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`;
  };

  const step = gridStep(Math.max(box.lonMax - box.lonMin, box.latMax - box.latMin));
  const gridLats = [];
  for (let lat = Math.ceil(box.latMin / step) * step; lat <= box.latMax; lat += step) gridLats.push(lat);
  const gridLons = [];
  for (let lon = Math.ceil(box.lonMin / step) * step; lon <= box.lonMax; lon += step) gridLons.push(lon);

  return (
    <figure className="route-map" aria-label="Route map">
      <svg viewBox={`0 0 ${W} ${H}`} role="img">
        {gridLats.map((lat) => {
          const [, y] = project(lat, cx);
          return <line key={`a${lat}`} x1={0} y1={y} x2={W} y2={y}
                       className={lat === 0 ? "map-equator" : "map-grid"} />;
        })}
        {gridLons.map((lon) => {
          const [x] = project(cy, lon);
          return <line key={`o${lon}`} x1={x} y1={0} x2={x} y2={H} className="map-grid" />;
        })}

        {legs.map((l, i) => (
          <g key={i} className="map-leg" style={{ "--i": i }}>
            <path d={arcPath(points[l.origin], points[l.destination])} className="map-arc" />
          </g>
        ))}

        {Object.entries(points).map(([iata, [x, y]]) => {
          const isHome = iata === legs[0].origin;
          return (
            <g key={iata}>
              <circle cx={x} cy={y} r={isHome ? 4.5 : 3.5}
                      className={isHome ? "map-dot home" : "map-dot"} />
              <text x={x} y={y - 9} textAnchor="middle" className="map-code">{iata}</text>
            </g>
          );
        })}
      </svg>
      <figcaption className="map-caption">{stops.join(" → ")}</figcaption>
    </figure>
  );
}

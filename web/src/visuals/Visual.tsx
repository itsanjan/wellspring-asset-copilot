import { useState } from "react";
import {
  Bar, BarChart, CartesianGrid, LabelList, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { CircleMarker, MapContainer, TileLayer, Tooltip as MapTip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import type { BarVisual, CardVisual, HeatmapVisual, LineVisual, MapVisual, TableVisual, Visual } from "../types";

const KIND_LABEL: Record<Visual["type"], string> = {
  card: "Status card", table: "Table", line: "Line chart", bar: "Bar chart", map: "Map", heatmap: "Heatmap",
};

/** Renders any visual from the copilot, with a table view for every chart (values never gated behind hover). */
export function VisualView({ visual }: { visual: Visual }) {
  const [asTable, setAsTable] = useState(false);
  const title = visual.type === "card" ? visual.title : visual.title;
  return (
    <div className="visual">
      <div className="visual-head">
        {title && <h4>{title}</h4>}
        <span className="kind">{KIND_LABEL[visual.type]}</span>
        {visual.type !== "table" && (
          <button className="ghost" onClick={() => setAsTable(!asTable)}>{asTable ? "Show visual" : "Table view"}</button>
        )}
      </div>
      {asTable ? <DataTable t={toTable(visual)} /> : <Body visual={visual} />}
    </div>
  );
}

function Body({ visual }: { visual: Visual }) {
  switch (visual.type) {
    case "card": return <Card v={visual} />;
    case "table": return <DataTable t={visual} />;
    case "line": return <LineV v={visual} />;
    case "bar": return <BarV v={visual} />;
    case "map": return <MapV v={visual} />;
    case "heatmap": return <Heatmap v={visual} />;
  }
}

// --- card -----------------------------------------------------------------------------------------

function Card({ v }: { v: CardVisual }) {
  const producing = v.status === "producing";
  const unknown = v.status.startsWith("unknown");
  return (
    <div>
      <div className="card-status">
        {producing ? <span className="dot" style={{ background: "var(--good)" }} aria-hidden />
          : <span className="square" style={{ background: unknown ? "var(--muted)" : "var(--critical)" }} aria-hidden />}
        <span>{producing ? "Producing" : unknown ? "Status unknown" : "Not producing"}</span>
      </div>
      <dl className="fields">
        {Object.entries(v.fields).map(([k, val]) => (<FragmentRow key={k} k={k} v={val} />))}
      </dl>
    </div>
  );
}
const FragmentRow = ({ k, v }: { k: string; v: string }) => (<><dt>{k}</dt><dd>{v}</dd></>);

// --- table ----------------------------------------------------------------------------------------

export function fmt(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.length ? v.map(fmt).join("; ") : "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (typeof v === "object") return JSON.stringify(v);
  const s = String(v);
  const iso = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(:\d{2})?Z$/.exec(s);
  return iso ? `${iso[1]} ${iso[2]} UTC` : s;
}
const label = (c: string) => c.replace(/_/g, " ").replace(/\bc\b/, "°C").replace(/\bid\b/, "ID").replace(/\bbbl\b/, "(bbl)").replace(/\busd\b/, "(USD)").replace(/^./, (s) => s.toUpperCase());
const cellClass = (cell: unknown) =>
  typeof cell === "number" ? "num" : typeof cell === "string" && /^(WELL|INS|MNT|REG|CTX|OVL)-[\d-]+$/.test(cell) ? "id"
  : typeof cell === "string" && /^\d{4}-\d{2}-\d{2}/.test(cell) ? "when" : "";

function DataTable({ t }: { t: TableVisual }) {
  return (
    <div className="table-wrap">
      <table className="dtable">
        <thead><tr>{t.columns.map((c) => <th key={c}>{label(c)}</th>)}</tr></thead>
        <tbody>
          {t.rows.map((r, i) => (
            <tr key={i}>{r.map((cell, j) => <td key={j} className={cellClass(cell)}>{fmt(cell)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function toTable(v: Visual): TableVisual {
  switch (v.type) {
    case "table": return v;
    case "card": return { type: "table", columns: ["field", "value"], rows: [["Status", v.status], ...Object.entries(v.fields)] };
    case "line": return {
      type: "table", columns: [v.x, ...v.series.map((s) => s.label), "producing"],
      rows: v.points.map((p) => [p[v.x], ...v.series.map((s) => p[s.key]), p.producing === 0 ? "no" : p.producing === 1 ? "yes" : "—"]),
    };
    case "bar": {
      const cols = Array.from(new Set(v.bars.flatMap((b) => Object.keys(b))));
      return { type: "table", columns: cols, rows: v.bars.map((b) => cols.map((c) => b[c])) };
    }
    case "map": return { type: "table", columns: ["well", "detail", "lat", "lon"], rows: v.pins.map((p) => [p.label, p.detail, p.lat, p.lon]) };
    case "heatmap": return {
      type: "table", columns: ["", ...v.cols, "total"],
      rows: v.rows.map((r, i) => [r, ...v.values[i], v.row_totals?.[i] ?? v.values[i].reduce((a, b) => a + b, 0)]),
    };
  }
}

// --- line: two series on one axis, non-producing readings marked with the critical status color + label ---

function LineV({ v }: { v: LineVisual }) {
  const colors = ["var(--series-1)", "var(--series-2)"];
  const offDot = (props: { cx?: number; cy?: number; payload?: Record<string, unknown> }) => {
    const { cx, cy, payload } = props;
    if (cx === undefined || cy === undefined || payload?.producing !== 0) return <g />;
    return <rect x={cx - 5} y={cy - 5} width={10} height={10} rx={2} fill="var(--critical)" stroke="var(--surface)" strokeWidth={2} />;
  };
  return (
    <div>
      <div style={{ width: "100%", height: 280 }}>
        <ResponsiveContainer>
          <LineChart data={v.points} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey={v.x} tick={{ fill: "var(--muted)", fontSize: 12 }} stroke="var(--axis)" tickFormatter={shortDate} minTickGap={24} />
            <YAxis tick={{ fill: "var(--muted)", fontSize: 12 }} stroke="var(--axis)" unit=" °C" width={56} />
            <Tooltip cursor={{ stroke: "var(--axis)", strokeWidth: 1 }} content={<LineTip v={v} colors={colors} />} />
            {v.series.map((s, i) => (
              <Line key={s.key} type="linear" dataKey={s.key} name={s.label} stroke={colors[i % colors.length]} strokeWidth={2}
                dot={i === 0 ? offDot : false} activeDot={{ r: 5, stroke: "var(--surface)", strokeWidth: 2 }} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="legend">
        {v.series.map((s, i) => <span key={s.key}><i className="key-line" style={{ background: colors[i % colors.length] }} />{s.label} (°C)</span>)}
        {v.markers.length > 0 && <span><i className="square" style={{ background: "var(--critical)", width: 10, height: 10 }} />Not producing ({v.markers.length})</span>}
      </div>
    </div>
  );
}

type TipProps = { active?: boolean; label?: string; payload?: { payload: Record<string, unknown> }[] };
function LineTip({ active, payload, label: x, v, colors }: TipProps & { v: LineVisual; colors: string[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="tip">
      <div className="l">{x}</div>
      {v.series.map((s, i) => (
        <div key={s.key}><i className="key-line" style={{ background: colors[i], marginRight: 6, verticalAlign: "middle" }} />
          <span className="v">{fmt(p[s.key])} °C</span> <span className="l">{s.label}</span></div>
      ))}
      {p.producing === 0 && <div style={{ marginTop: 4 }}><b>■</b> Not producing</div>}
    </div>
  );
}
const shortDate = (s: string) => (/^\d{4}-\d{2}-\d{2}/.test(s) ? new Date(s + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }) : s);

// --- bar: one series, one color, value at the tip, thin rounded columns ---

const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)"];

// Stacked bars: one segment per series in fixed categorical order, 2px surface gap between segments,
// rounded ends only on the top segment, legend always shown (identity is never color alone).
function StackedBarV({ v }: { v: BarVisual }) {
  const series = v.series ?? [];
  return (
    <div>
      <div style={{ width: "100%", height: 300 }}>
        <ResponsiveContainer>
          <BarChart data={v.bars} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey={v.x} tick={{ fill: "var(--muted)", fontSize: 12 }} stroke="var(--axis)" interval={0} />
            <YAxis tick={{ fill: "var(--muted)", fontSize: 12 }} stroke="var(--axis)" width={44} allowDecimals={false} />
            <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<StackTip series={series} />} />
            {series.map((s, i) => (
              <Bar key={s.key} dataKey={s.key} name={s.label} stackId="a" fill={SERIES[i % SERIES.length]} barSize={24}
                stroke="var(--surface)" strokeWidth={2} isAnimationActive={false}
                radius={i === series.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="legend">
        {series.map((s, i) => <span key={s.key}><i className="square" style={{ background: SERIES[i % SERIES.length] }} />{s.label}</span>)}
      </div>
    </div>
  );
}
function StackTip({ active, payload, label: x, series }: TipProps & { series: { key: string; label: string }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  const total = series.reduce((a, s) => a + (Number(p[s.key]) || 0), 0);
  return (
    <div className="tip">
      <div className="l">{x}</div>
      {series.map((s, i) => (
        <div key={s.key}><i className="square" style={{ background: SERIES[i % SERIES.length], marginRight: 6 }} />
          <span className="v">{fmt(p[s.key])}</span> <span className="l">{s.label} ({total ? Math.round((100 * (Number(p[s.key]) || 0)) / total) : 0}%)</span></div>
      ))}
    </div>
  );
}

function BarV({ v }: { v: BarVisual }) {
  if (v.stacked && v.series?.length) return <StackedBarV v={v} />;
  const y = v.y ?? "value";
  return (
    <div style={{ width: "100%", height: 280 }}>
      <ResponsiveContainer>
        <BarChart data={v.bars} margin={{ top: 22, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey={v.x} tick={{ fill: "var(--muted)", fontSize: 12 }} stroke="var(--axis)" interval={0} />
          <YAxis tick={{ fill: "var(--muted)", fontSize: 12 }} stroke="var(--axis)" width={56} tickFormatter={compact} />
          <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<BarTip v={v} />} />
          <Bar dataKey={y} fill="var(--series-1)" barSize={24} radius={[4, 4, 0, 0]} isAnimationActive={false}>
            <LabelList dataKey={y} position="top" fill="var(--ink-2)" fontSize={12} formatter={(x: number) => fmt(x)} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
function BarTip({ active, payload, v }: TipProps & { v: BarVisual }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  const y = v.y ?? "value";
  return (
    <div className="tip">
      <div className="v">{fmt(p[y])} <span className="l">{label(y)}</span></div>
      {Object.entries(p).filter(([k]) => k !== v.y).map(([k, val]) => <div key={k} className="l">{label(k)}: {fmt(val)}</div>)}
    </div>
  );
}
const compact = (n: number) => (Math.abs(n) >= 1000 ? `${Math.round(n / 100) / 10}K` : String(n));

// --- map ---

function MapV({ v }: { v: MapVisual }) {
  const pins = v.pins.filter((p) => p.lat != null && p.lon != null);
  if (!pins.length) return <p className="empty">No locations to show.</p>;
  const lats = pins.map((p) => p.lat), lons = pins.map((p) => p.lon);
  const pad = 0.01;
  const bounds: [[number, number], [number, number]] = [[Math.min(...lats) - pad, Math.min(...lons) - pad], [Math.max(...lats) + pad, Math.max(...lons) + pad]];
  return (
    <div>
      <div className="mapbox">
      <MapContainer bounds={bounds} style={{ height: "100%", width: "100%" }} scrollWheelZoom={false}>
        <TileLayer attribution='&copy; OpenStreetMap contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        {pins.map((p) => (
          <CircleMarker key={p.label + p.detail} center={[p.lat, p.lon]} radius={7}
            pathOptions={{ color: "#ffffff", weight: 2, fillColor: "#2a78d6", fillOpacity: 1 }}>
            <MapTip direction="top" offset={[0, -6]}><b>{p.label}</b> {p.detail}</MapTip>
          </CircleMarker>
        ))}
      </MapContainer>
      </div>
      {/* Labels sit beside the map, not on it: nearby pins would collide. Hover a pin for the same text. */}
      <div className="pins">
        {pins.map((p) => <span key={p.label + p.detail}><i className="dot" style={{ background: "#2a78d6", width: 8, height: 8 }} /><b>{p.label}</b> {p.detail}</span>)}
      </div>
    </div>
  );
}

// --- heatmap: one-hue sequential ramp, zero in neutral gray, counts in cells with luminance-picked ink ---

const RAMP = ["var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)", "var(--seq-6)", "var(--seq-7)"];
function Heatmap({ v }: { v: HeatmapVisual }) {
  const max = Math.max(1, ...v.values.flat());
  const step = (n: number) => (n <= 0 ? -1 : Math.min(RAMP.length - 1, Math.ceil((n / max) * RAMP.length) - 1));
  const totals = v.row_totals ?? v.values.map((r) => r.reduce((a, b) => a + b, 0));
  return (
    <div className="table-wrap">
      <div className="heat" style={{ gridTemplateColumns: `max-content repeat(${v.cols.length}, minmax(34px, 1fr)) 48px` }}>
        <div />
        {v.cols.map((c) => <div key={c} className="h">{c.split(" ")[0]}</div>)}
        <div className="h">Total</div>
        {v.rows.map((r, i) => (
          <Row key={r} name={r} values={v.values[i]} cols={v.cols} total={totals[i]} step={step} />
        ))}
      </div>
      <div className="scale">
        <span>0</span><i style={{ background: "var(--surface-2)" }} />
        {RAMP.map((c) => <i key={c} style={{ background: c }} />)}
        <span>{max}</span>
      </div>
    </div>
  );
}
function Row({ name, values, cols, total, step }: { name: string; values: number[]; cols: string[]; total: number; step: (n: number) => number }) {
  return (
    <>
      <div className="r">{name}</div>
      {values.map((n, j) => {
        const s = step(n);
        return (
          <div key={j} className="c" tabIndex={0} title={`${name}, ${cols[j]}: ${n}`}
            style={{ background: s < 0 ? "var(--surface-2)" : RAMP[s], color: s >= 3 ? "#ffffff" : s < 0 ? "var(--muted)" : "#0b0b0b" }}>
            {n}
          </div>
        );
      })}
      <div className="t">{total}</div>
    </>
  );
}

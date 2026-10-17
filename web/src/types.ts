export type User = { user_id: string; name: string; role: string; team: string | null };
export type Evidence = { kind: string; ref: string; detail: string | null; source: string | null };
export type Flag = { kind: string; message: string; refs: (string | null)[] };

export type CardVisual = { type: "card"; title: string; status: string; fields: Record<string, string> };
export type TableVisual = { type: "table"; title?: string; columns: string[]; rows: unknown[][] };
export type LineVisual = {
  type: "line"; title?: string; x: string;
  series: { key: string; label: string }[];
  points: Record<string, number | string | null>[];
  markers: { x: string; label: string }[];
};
export type BarVisual = { type: "bar"; title?: string; x: string; y: string; bars: Record<string, unknown>[] };
export type MapVisual = { type: "map"; title?: string; pins: { lat: number; lon: number; label: string; detail: string }[] };
export type HeatmapVisual = { type: "heatmap"; title?: string; rows: string[]; cols: string[]; values: number[][]; row_totals?: number[] };
export type Visual = CardVisual | TableVisual | LineVisual | BarVisual | MapVisual | HeatmapVisual;

export type Answer = {
  text: string;
  visual: Visual | null;
  evidence: Evidence[];
  flags: Flag[];
  tools_used: { id: string; tool: string; input: Record<string, unknown> }[];
  usage: { model: string; model_calls: number; tool_calls: number; input_tokens: number; output_tokens: number; cache_read_tokens: number; cost_usd: number };
  conversation_id: string;
};

export type Turn = { role: "user" | "assistant"; text: string; answer?: Answer | null; error?: string };
export type Conversation = { conversation_id: string; started_at: string; title: string };
export type FeedbackEntry = {
  entry_id: string; kind: string; target_type: string; target_ref: string; field: string | null;
  original_value: string | null; new_value: string | null; text: string | null; reason: string | null;
  status: string; created_at: string; author: string;
};

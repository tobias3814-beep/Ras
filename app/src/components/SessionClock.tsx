import { useEffect, useState } from "react";
import { API } from "../ws";

type Row = { name: string; start_local: string; end_local: string; open: boolean };
const SHOW: Record<string, string> = { tokyo: "TOKYO", london: "LONDON", new_york: "NEW YORK" };

/** Session clock in the user's time zone (DST handled server-side). */
export function SessionClock() {
  const [rows, setRows] = useState<Row[]>([]);
  useEffect(() => {
    const load = () => fetch(`${API}/api/sessions`).then((r) => r.json()).then(setRows).catch(() => {});
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);
  const scalpOn = rows.filter((r) => r.open && (r.name === "london_open" || r.name === "ny_open")).map((r) => r.name.replace("_", " "));
  const overlap = rows.some((r) => r.name === "london" && r.open) && rows.some((r) => r.name === "new_york" && r.open);
  return (
    <span className="flex items-center gap-1 text-xs">
      {rows.filter((r) => SHOW[r.name]).map((r) => (
        <span key={r.name} title={`${r.start_local}–${r.end_local} Bahrain`}
          className={`rounded border px-1.5 py-0.5 ${r.open ? "border-hud-green/50 text-hud-green" : "border-hud-line text-slate-500"}`}>
          {SHOW[r.name]} {r.start_local}–{r.end_local}
        </span>
      ))}
      <span className={`rounded px-1.5 py-0.5 ${scalpOn.length || overlap ? "bg-hud-scalp/15 text-hud-scalp" : "text-slate-500"}`}>
        scalp: {overlap ? "overlap" : scalpOn.join(", ") || "inactive"}
      </span>
    </span>
  );
}

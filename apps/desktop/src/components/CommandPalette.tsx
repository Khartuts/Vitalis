import { Command } from "cmdk";
import { useEffect, useState } from "react";
import type { RouteDef } from "../App";
import { navigate } from "../App";
import { useT } from "../i18n";
import { invoke } from "../lib/bridge";
import { useEngine, useGlobalRange } from "../lib/hooks";
import { useUi } from "../lib/store";

export function CommandPalette({ routes }: { routes: RouteDef[] }) {
  const t = useT();
  const { paletteOpen, setPalette, toggleAi, setTheme, theme, showEvidence, setPreset } = useUi();
  const range = useGlobalRange();
  const catalog = useEngine<any[]>("features.catalog", undefined, { enabled: paletteOpen });
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette(!paletteOpen); }
      else if (mod && e.key.toLowerCase() === "j") { e.preventDefault(); navigate("journal"); }
      else if (mod && (e.key === "/" || e.key.toLowerCase() === "i")) { e.preventDefault(); toggleAi(); }
      else if (mod && e.key === ",") { e.preventDefault(); navigate("settings"); }
      else if (mod && /^[1-9]$/.test(e.key)) { e.preventDefault(); const r = routes[Number(e.key) - 1]; if (r) navigate(r.id); }
      else if (e.key === "Escape" && paletteOpen) setPalette(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paletteOpen, routes, setPalette, toggleAi]);

  if (!paletteOpen) return null;
  const run = (f: () => unknown) => { setPalette(false); f(); };
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 pt-[12vh]" onClick={() => setPalette(false)}>
      <Command label="Command palette" className="w-[min(640px,92vw)] overflow-hidden rounded-xl border border-line bg-s1 shadow-2xl"
        onClick={(e) => e.stopPropagation()}>
        <Command.Input autoFocus placeholder="Search screens, metrics and actions…" data-testid="palette-input"
          className="w-full border-b border-line bg-transparent px-4 py-3 text-sm text-fg outline-none placeholder:text-muted" />
        <Command.List className="max-h-[50vh] overflow-auto p-2 text-[13px]">
          <Command.Empty className="px-3 py-6 text-center text-muted">No results.</Command.Empty>
          <Command.Group heading="Go to" className="text-[11px] text-muted [&_[cmdk-item]]:text-fg">
            {routes.map((r) => (
              <Command.Item key={r.id} value={`go ${t(r.key)}`} onSelect={() => run(() => navigate(r.id))}
                className="cursor-pointer rounded-md px-3 py-2 data-[selected=true]:bg-s3">{t(r.key)}</Command.Item>
            ))}
          </Command.Group>
          <Command.Group heading="Actions" className="text-[11px] text-muted [&_[cmdk-item]]:text-fg">
            <Command.Item onSelect={() => run(() => navigate("journal"))} className="rounded-md px-3 py-2 data-[selected=true]:bg-s3">Log today's check-in (Ctrl J)</Command.Item>
            <Command.Item onSelect={() => run(() => toggleAi(true))} className="rounded-md px-3 py-2 data-[selected=true]:bg-s3">Ask the AI analyst (Ctrl I)</Command.Item>
            <Command.Item onSelect={() => run(() => setTheme(theme === "dark" ? "light" : "dark"))} className="rounded-md px-3 py-2 data-[selected=true]:bg-s3">Toggle light / dark theme</Command.Item>
            {(["7", "30", "90"] as const).map((p) => (
              <Command.Item key={p} onSelect={() => run(() => setPreset(p))} className="rounded-md px-3 py-2 data-[selected=true]:bg-s3">Show last {p} days</Command.Item>
            ))}
            <Command.Item onSelect={() => run(async () => { await invoke("engine:call", { method: "recompute.run" }); setMsg("Recomputed"); })}
              className="rounded-md px-3 py-2 data-[selected=true]:bg-s3">Recompute all analytics</Command.Item>
            <Command.Item onSelect={() => run(() => invoke("report:pdf", { start: range.start, end: range.end }))}
              className="rounded-md px-3 py-2 data-[selected=true]:bg-s3">Export PDF report for current range</Command.Item>
          </Command.Group>
          <Command.Group heading="Metrics" className="text-[11px] text-muted [&_[cmdk-item]]:text-fg">
            {(catalog.data ?? []).filter((m) => m.days > 0 && !m.private).map((m) => (
              <Command.Item key={m.key} value={`metric ${m.label} ${m.key}`}
                onSelect={() => run(() => showEvidence({ metric: m.key, start: range.start, end: range.end, label: m.label }))}
                className="flex justify-between rounded-md px-3 py-2 data-[selected=true]:bg-s3">
                <span>{m.label}</span><span className="text-muted">{m.days} days</span>
              </Command.Item>
            ))}
          </Command.Group>
        </Command.List>
      </Command>
      {msg && <span className="sr-only" role="status">{msg}</span>}
    </div>
  );
}

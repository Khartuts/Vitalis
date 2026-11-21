import { Bot, CalendarRange, Command, Menu, Moon, RefreshCw, Sun, WifiOff, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import type { RouteDef } from "../App";
import { useT } from "../i18n";
import { fmtDate } from "../lib/format";
import { useGlobalRange, useStatus } from "../lib/hooks";
import { IS_ANDROID } from "../lib/platform";
import { useUi, type Preset } from "../lib/store";
import { Button, cx, inputCls, Modal, Tip } from "./ui";

export function Layout({ routes, active, aside, children }: { routes: RouteDef[]; active: string; aside: ReactNode; children: ReactNode }) {
  const t = useT();
  const aiOpen = useUi((s) => s.aiOpen);
  const status = useStatus();
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const on = () => setOnline(true), off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => { window.removeEventListener("online", on); window.removeEventListener("offline", off); };
  }, []);
  return (
    <div className="flex h-full flex-col">
      {status.data?.demo && (
        <div role="status" data-testid="demo-banner" className="bg-warning/90 px-4 py-1 text-center text-[11px] font-semibold tracking-wider text-black">
          {t("demo.banner")}
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <nav aria-label="Main navigation" className="hidden w-60 shrink-0 flex-col border-r border-line bg-s1 md:flex">
          <div className="px-5 pb-4 pt-5">
            <div className="text-lg font-semibold tracking-[0.18em] text-fg">VITALIS</div>
            <div className="text-[11px] text-muted">Personal AI Health Intelligence</div>
          </div>
          <ul className="flex-1 space-y-0.5 overflow-auto px-2">
            {routes.map((r, i) => {
              const Icon = r.icon;
              return (
                <li key={r.id}>
                  <a href={`#/${r.id}`} aria-current={active === r.id ? "page" : undefined} data-testid={`nav-${r.id}`}
                    className={cx("flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] transition",
                      active === r.id ? "bg-s3 text-fg" : "text-fg2 hover:bg-s2 hover:text-fg")}>
                    <Icon size={16} /><span className="flex-1 truncate">{t(r.key)}</span>
                    {i < 9 && <kbd className="hidden text-[10px] text-muted xl:inline">Ctrl {i + 1}</kbd>}
                  </a>
                </li>
              );
            })}
          </ul>
          <div className="border-t border-line px-4 py-3 text-[11px] text-muted">
            <div className="truncate" title={status.data?.profile_name ?? ""}>Profile: {status.data?.profile_name ?? "—"}</div>
            <div>{status.data?.data_range ? `Data ${fmtDate(status.data.data_range[0])} – ${fmtDate(status.data.data_range[1])}` : "No data yet"}</div>
          </div>
        </nav>
        <div className="flex min-w-0 flex-1 flex-col">
          <MobileHeader routes={routes} active={active} />
          <TopBar online={online} />
          <main id="main" className="min-h-0 flex-1 overflow-auto px-3 pb-24 pt-4 md:px-7 md:py-6" tabIndex={-1}>
            <div className="mx-auto max-w-[1500px]">{children}</div>
            <footer className="mx-auto mt-10 max-w-[1500px] border-t border-line pt-3 text-[11px] text-muted">{t("disclaimer")}</footer>
          </main>
        </div>
        {aiOpen && <aside aria-label="AI analyst" className="fixed inset-0 z-40 flex flex-col bg-s1 md:static md:z-auto md:w-[420px] md:shrink-0 md:border-l md:border-line">{aside}</aside>}
      </div>
      <BottomNav routes={routes} active={active} />
    </div>
  );
}

/** Phones: compact header with a full-screen menu of all screens. */
function MobileHeader({ routes, active }: { routes: RouteDef[]; active: string }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const status = useStatus();
  useEffect(() => { setOpen(false); }, [active]);
  return (
    <div className="flex items-center gap-2 border-b border-line bg-s1 px-3 py-2 md:hidden">
      <button onClick={() => setOpen(true)} aria-label="Menu" data-testid="mobile-menu" className="rounded-md p-1.5 text-fg2 hover:bg-s2"><Menu size={18} /></button>
      <span className="text-sm font-semibold tracking-[0.18em]">VITALIS</span>
      <span className="ml-auto truncate text-[11px] text-muted">{status.data?.profile_name ?? ""}</span>
      {open && (
        <div className="fixed inset-0 z-50 flex flex-col bg-s1" role="dialog" aria-label="All screens">
          <div className="flex items-center border-b border-line px-3 py-2">
            <span className="text-sm font-semibold tracking-[0.18em]">VITALIS</span>
            <button onClick={() => setOpen(false)} aria-label="Close menu" className="ml-auto rounded-md p-1.5 text-fg2 hover:bg-s2"><X size={18} /></button>
          </div>
          <ul className="flex-1 overflow-auto p-2">
            {routes.map((r) => {
              const Icon = r.icon;
              return (
                <li key={r.id}><a href={`#/${r.id}`} onClick={() => setOpen(false)} aria-current={active === r.id ? "page" : undefined}
                  className={cx("flex items-center gap-3 rounded-lg px-3 py-3 text-sm", active === r.id ? "bg-s3 text-fg" : "text-fg2")}>
                  <Icon size={18} />{t(r.key)}</a></li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Phones: bottom tab bar with the most used screens; everything else is in the menu. */
function BottomNav({ routes, active }: { routes: RouteDef[]; active: string }) {
  const t = useT();
  const main = ["overview", "sleep", "recovery", "journal"].map((id) => routes.find((r) => r.id === id)).filter(Boolean) as RouteDef[];
  return (
    <nav aria-label="Main screens" className="fixed inset-x-0 bottom-0 z-30 flex border-t border-line bg-s1 pb-[env(safe-area-inset-bottom)] md:hidden">
      {main.map((r) => {
        const Icon = r.icon;
        return (
          <a key={r.id} href={`#/${r.id}`} aria-current={active === r.id ? "page" : undefined} data-testid={`tab-${r.id}`}
            className={cx("flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px]", active === r.id ? "text-accent-ink" : "text-fg2")}>
            <Icon size={18} /><span className="truncate">{t(r.key)}</span>
          </a>
        );
      })}
    </nav>
  );
}

function TopBar({ online }: { online: boolean }) {
  const t = useT();
  const { preset, setPreset, theme, setTheme, toggleAi, setPalette, setCustom } = useUi();
  const range = useGlobalRange();
  const [customOpen, setCustomOpen] = useState(false);
  const [cs, setCs] = useState(range.start);
  const [ce, setCe] = useState(range.end);
  const sync = range.status?.sync;
  const presets: Preset[] = ["1", "7", "30", "90", "365"];
  return (
    <header className="flex items-center gap-3 overflow-x-auto border-b border-line bg-s1 px-3 py-2 md:px-5 md:py-2.5">
      <div role="group" aria-label="Date range" className="flex shrink-0 items-center gap-0.5 rounded-lg border border-line bg-s2 p-0.5">
        {presets.map((p) => (
          <button key={p} onClick={() => setPreset(p)} data-testid={`range-${p}`} aria-pressed={preset === p}
            className={cx("rounded-md px-2.5 py-1 text-xs", preset === p ? "bg-s3 text-fg" : "text-fg2 hover:text-fg")}>
            {t(`range.${p}` as any)}
          </button>
        ))}
        <button onClick={() => { setCs(range.start); setCe(range.end); setCustomOpen(true); }} aria-pressed={preset === "custom"}
          className={cx("flex items-center gap-1 rounded-md px-2.5 py-1 text-xs", preset === "custom" ? "bg-s3 text-fg" : "text-fg2 hover:text-fg")}>
          <CalendarRange size={12} />{t("range.custom")}
        </button>
      </div>
      <span className="num hidden text-xs text-fg2 md:inline" data-testid="range-label">{fmtDate(range.start)} – {fmtDate(range.end, { day: "numeric", month: "short", year: "numeric" })}</span>
      <div className="flex-1" />
      {!online && <Tip text="Offline: local analysis keeps working; cloud AI and Google sync are unavailable.">
        <span className="flex items-center gap-1 text-xs text-warning"><WifiOff size={14} />Offline</span></Tip>}
      {sync?.running && <Tip text={`Phone sync listening on ${sync.hosts.join(", ")}:${sync.port}`}>
        <span className="flex items-center gap-1 text-xs text-good"><RefreshCw size={13} />Sync on</span></Tip>}
      {!IS_ANDROID && <span className="hidden md:inline"><Button variant="ghost" size="sm" onClick={() => setPalette(true)} title="Command palette (Ctrl+K)"><Command size={14} />Ctrl K</Button></span>}
      <Button variant="ghost" size="sm" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} title="Toggle light/dark theme">
        {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}</Button>
      {!IS_ANDROID && <Button variant="primary" size="sm" onClick={() => toggleAi()} testId="toggle-ai"><Bot size={14} />Analyst</Button>}
      <Modal open={customOpen} onOpenChange={setCustomOpen} title="Custom date range">
        <div className="flex items-end gap-3">
          <label className="text-xs">Start<input type="date" className={inputCls} value={cs} onChange={(e) => setCs(e.target.value)} /></label>
          <label className="text-xs">End<input type="date" className={inputCls} value={ce} onChange={(e) => setCe(e.target.value)} /></label>
          <Button variant="primary" disabled={!cs || !ce || cs > ce} onClick={() => { setCustom(cs, ce); setCustomOpen(false); }}>Apply</Button>
        </div>
      </Modal>
    </header>
  );
}

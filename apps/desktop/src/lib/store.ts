import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Preset = "1" | "7" | "30" | "90" | "365" | "custom";
export interface ChartContext {
  metrics: string[];
  start: string;
  end: string;
  title?: string;
}

interface UiState {
  theme: "dark" | "light" | "system";
  preset: Preset;
  customStart: string | null;
  customEnd: string | null;
  aiOpen: boolean;
  paletteOpen: boolean;
  chartContext: ChartContext | null;
  pendingQuestion: string | null;
  evidence: { metric: string; start: string; end: string; label?: string } | null;
  locale: "en" | "hr";
  setTheme: (t: UiState["theme"]) => void;
  setPreset: (p: Preset) => void;
  setCustom: (s: string, e: string) => void;
  toggleAi: (open?: boolean) => void;
  setPalette: (o: boolean) => void;
  explain: (ctx: ChartContext, question?: string) => void;
  clearPending: () => void;
  showEvidence: (e: UiState["evidence"]) => void;
  setLocale: (l: "en" | "hr") => void;
}

export const useUi = create<UiState>()(
  persist(
    (set) => ({
      theme: "dark", preset: "30", customStart: null, customEnd: null, aiOpen: false, paletteOpen: false,
      chartContext: null, pendingQuestion: null, evidence: null, locale: "en",
      setTheme: (theme) => set({ theme }),
      setPreset: (preset) => set({ preset }),
      setCustom: (customStart, customEnd) => set({ preset: "custom", customStart, customEnd }),
      toggleAi: (open) => set((s) => ({ aiOpen: open ?? !s.aiOpen })),
      setPalette: (paletteOpen) => set({ paletteOpen }),
      explain: (chartContext, question) => set({ chartContext, aiOpen: true,
        pendingQuestion: question ?? `Explain this change in ${chartContext.title ?? chartContext.metrics.join(", ")} between ${chartContext.start} and ${chartContext.end}.` }),
      clearPending: () => set({ pendingQuestion: null, chartContext: null }), // the panel keeps its own copy
      showEvidence: (evidence) => set({ evidence }),
      setLocale: (locale) => set({ locale }),
    }),
    { name: "vitalis-ui", partialize: (s) => ({ theme: s.theme, preset: s.preset, customStart: s.customStart, customEnd: s.customEnd, locale: s.locale }) },
  ),
);

export function addDays(iso: string, n: number) {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function useRange(today: string | undefined) {
  const { preset, customStart, customEnd } = useUi();
  const end = preset === "custom" && customEnd ? customEnd : today ?? new Date().toISOString().slice(0, 10);
  const start = preset === "custom" && customStart ? customStart : addDays(end, -(Number(preset) - 1));
  return { start, end, days: Math.round((Date.parse(end) - Date.parse(start)) / 86400000) + 1 };
}

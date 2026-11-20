// Localization: every navigation/UI string has a message ID. English is complete; Croatian is scaffolded with the
// same keys (translations marked TODO fall back to English until reviewed by a native speaker).
import { useUi } from "../lib/store";

const en = {
  "nav.overview": "Overview", "nav.sleep": "Sleep Lab", "nav.recovery": "Recovery", "nav.stress": "Stress & Nervous System",
  "nav.cardio": "Cardiovascular Health", "nav.activity": "Activity & Fitness", "nav.trends": "Health Trends",
  "nav.journal": "Habit Journal", "nav.lab": "Correlation Lab", "nav.ai": "AI Health Analyst", "nav.reports": "Reports",
  "nav.settings": "Data Sources & Settings",
  "range.1": "Today", "range.7": "7 days", "range.30": "30 days", "range.90": "90 days", "range.365": "1 year", "range.custom": "Custom",
  "demo.banner": "SYNTHETIC DEMO DATA — not your real health data",
  "common.insufficient": "Insufficient data", "common.loading": "Loading…", "common.explain": "Explain this",
  "common.png": "Save PNG", "common.table": "Table", "common.chart": "Chart",
  "disclaimer": "Wellness estimates from consumer wearables — not a medical diagnosis.",
} as const;

type Key = keyof typeof en;

const hr: Partial<Record<Key, string>> = {
  "nav.overview": "Pregled", "nav.sleep": "Laboratorij sna", "nav.recovery": "Oporavak", "nav.stress": "Stres i živčani sustav",
  "nav.cardio": "Kardiovaskularno zdravlje", "nav.activity": "Aktivnost i kondicija", "nav.trends": "Zdravstveni trendovi",
  "nav.journal": "Dnevnik navika", "nav.lab": "Laboratorij korelacija", "nav.ai": "AI analitičar zdravlja", "nav.reports": "Izvješća",
  "nav.settings": "Izvori podataka i postavke", "range.1": "Danas", "range.7": "7 dana", "range.30": "30 dana",
  "range.90": "90 dana", "range.365": "1 godina", "range.custom": "Prilagođeno",
  "demo.banner": "SINTETIČKI DEMO PODACI — nisu vaši stvarni zdravstveni podaci",
  "common.insufficient": "Nedovoljno podataka", "common.loading": "Učitavanje…", "common.explain": "Objasni ovo",
  "disclaimer": "Procjene dobrobiti s nosivih uređaja — nije medicinska dijagnoza.",
};

const dicts = { en, hr } as const;

export function useT() {
  const locale = useUi((s) => s.locale);
  return (k: Key) => (dicts[locale] as Partial<Record<Key, string>>)[k] ?? en[k];
}

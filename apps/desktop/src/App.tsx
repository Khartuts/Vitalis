import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, BarChart3, BookOpen, Bot, FlaskConical, FileText, Gauge, HeartPulse, Moon, Settings, TrendingUp, Waves } from "lucide-react";
import { lazy, Suspense, useEffect, useState, type ComponentType } from "react";
import { AiPanel } from "./components/AiPanel";
import { CommandPalette } from "./components/CommandPalette";
import { EvidenceModal } from "./components/EvidenceModal";
import { Layout } from "./components/Layout";
import { Skeleton } from "./components/ui";
import { invoke, onEngineEvent } from "./lib/bridge";
import { useUi } from "./lib/store";
import { Welcome } from "./pages/Welcome";

const Overview = lazy(() => import("./pages/Overview"));
const SleepLab = lazy(() => import("./pages/SleepLab"));
const Recovery = lazy(() => import("./pages/Recovery"));
const Stress = lazy(() => import("./pages/Stress"));
const Cardio = lazy(() => import("./pages/Cardio"));
const ActivityPage = lazy(() => import("./pages/Activity"));
const Trends = lazy(() => import("./pages/Trends"));
const Journal = lazy(() => import("./pages/Journal"));
const Lab = lazy(() => import("./pages/Lab"));
const Analyst = lazy(() => import("./pages/Analyst"));
const Reports = lazy(() => import("./pages/Reports"));
const SettingsPage = lazy(() => import("./pages/Settings"));
const PrintReport = lazy(() => import("./pages/PrintReport"));

export interface RouteDef {
  id: string;
  key: "nav.overview" | "nav.sleep" | "nav.recovery" | "nav.stress" | "nav.cardio" | "nav.activity" | "nav.trends" | "nav.journal" | "nav.lab" | "nav.ai" | "nav.reports" | "nav.settings";
  icon: ComponentType<{ size?: number }>;
  page: ComponentType;
}

export const ROUTES: RouteDef[] = [
  { id: "overview", key: "nav.overview", icon: Gauge, page: Overview },
  { id: "sleep", key: "nav.sleep", icon: Moon, page: SleepLab },
  { id: "recovery", key: "nav.recovery", icon: Waves, page: Recovery },
  { id: "stress", key: "nav.stress", icon: Activity, page: Stress },
  { id: "cardio", key: "nav.cardio", icon: HeartPulse, page: Cardio },
  { id: "activity", key: "nav.activity", icon: BarChart3, page: ActivityPage },
  { id: "trends", key: "nav.trends", icon: TrendingUp, page: Trends },
  { id: "journal", key: "nav.journal", icon: BookOpen, page: Journal },
  { id: "lab", key: "nav.lab", icon: FlaskConical, page: Lab },
  { id: "ai", key: "nav.ai", icon: Bot, page: Analyst },
  { id: "reports", key: "nav.reports", icon: FileText, page: Reports },
  { id: "settings", key: "nav.settings", icon: Settings, page: SettingsPage },
];

export function useHashRoute() {
  const [hash, setHash] = useState(() => window.location.hash.slice(1) || "/overview");
  useEffect(() => {
    const f = () => setHash(window.location.hash.slice(1) || "/overview");
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return hash;
}

export const navigate = (id: string) => { window.location.hash = `/${id}`; };

export default function App() {
  const hash = useHashRoute();
  const qc = useQueryClient();
  const theme = useUi((s) => s.theme);
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: () => invoke<{ profiles: any[]; active: any; last: string | null }>("profiles:list") });

  useEffect(() => {
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => onEngineEvent((e) => {
    if (e.type === "data_changed") qc.invalidateQueries();
    if (e.type === "sync") qc.invalidateQueries({ queryKey: ["system.status"] });
  }), [qc]);

  if (hash.startsWith("/print/report")) {
    return <Suspense fallback={null}><PrintReport /></Suspense>;
  }
  if (profiles.isLoading) return <div className="p-10"><Skeleton h={300} /></div>;
  if (!profiles.data?.active) return <Welcome data={profiles.data} />;

  const id = hash.split("?")[0].replace(/^\//, "") || "overview";
  const route = ROUTES.find((r) => r.id === id) ?? ROUTES[0];
  const Page = route.page;
  return (
    <>
      <Layout routes={ROUTES} active={route.id} aside={<AiPanel />}>
        <Suspense fallback={<Skeleton h={400} />}><Page /></Suspense>
      </Layout>
      <CommandPalette routes={ROUTES} />
      <EvidenceModal />
    </>
  );
}

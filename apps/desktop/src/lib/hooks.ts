import { useQuery, type UseQueryOptions } from "@tanstack/react-query";
import type { RendererEngineMethod } from "@shared/contract";
import { engine } from "./bridge";
import { useRange } from "./store";

export function useEngine<T = any>(method: RendererEngineMethod, params?: Record<string, unknown>,
  opts: Partial<UseQueryOptions<T>> = {}) {
  return useQuery<T>({ queryKey: [method, params ?? {}], queryFn: () => engine<T>(method, params), staleTime: 30_000, ...opts });
}

export interface SystemStatus {
  version: string; demo: boolean; profile_name: string | null; data_range: [string, string] | null; today: string;
  counts: Record<string, number | string | null>; algorithms: Record<string, string>;
  sync: { running: boolean; port: number | null; hosts: string[]; fingerprint: string | null; devices: any[] } | null;
}

export function useStatus() {
  return useEngine<SystemStatus>("system.status", undefined, { staleTime: 10_000 });
}

/** Global date range resolved against "today" (the data end date for demo profiles). */
export function useGlobalRange() {
  const st = useStatus();
  return { ...useRange(st.data?.today), status: st.data };
}

export interface Series {
  dates: string[];
  series: Record<string, (number | null)[]>;
  info: Record<string, { label: string; unit: string; group?: string; provenance?: string; better?: string }>;
}

export function useSeries(features: string[], start: string, end: string) {
  return useEngine<Series>("features.series", { features, start, end }, { enabled: !!start && !!end });
}

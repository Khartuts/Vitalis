// ECharts wrapper: theme-aware (reads CSS tokens), crosshair tooltips, zoom/pan, PNG export,
// a data-table view for accessibility, and an "Ask AI" action that hands the chart context to the AI analyst.
import * as echarts from "echarts";
import { IS_ANDROID } from "../lib/platform";
import { Image, MessageSquareText, Table2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "../lib/bridge";
import { useUi, type ChartContext } from "../lib/store";
import { Button, cx } from "./ui";

export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

export function palette() {
  return { s1: token("--series-1"), s2: token("--series-2"), s3: token("--series-3"), s4: token("--series-4"), s5: token("--series-5"),
    deep: token("--stage-deep"), light: token("--stage-light"), rem: token("--stage-rem"), awake: token("--stage-awake"),
    text: token("--text-primary"), text2: token("--text-secondary"), muted: token("--text-muted"), grid: token("--grid"),
    surface: token("--surface-1"), surface3: token("--surface-3"), border: token("--border"), band: token("--band"),
    good: token("--good"), warning: token("--warning"), critical: token("--critical") };
}
export type Palette = ReturnType<typeof palette>;

export function baseOption(p: Palette): echarts.EChartsOption {
  return {
    backgroundColor: "transparent",
    textStyle: { color: p.text2, fontFamily: "Inter, Segoe UI, system-ui, sans-serif", fontSize: 11 },
    grid: { left: 44, right: 16, top: 28, bottom: 36, containLabel: true },
    tooltip: { trigger: "axis", backgroundColor: p.surface3, borderColor: p.border, textStyle: { color: p.text, fontSize: 12 },
      axisPointer: { type: "line", lineStyle: { color: p.muted, width: 1 } }, confine: true },
    legend: { top: 0, right: 0, textStyle: { color: p.text2 }, icon: "roundRect", itemWidth: 10, itemHeight: 4 },
    xAxis: { axisLine: { lineStyle: { color: p.border } }, axisTick: { show: false }, axisLabel: { color: p.muted },
      splitLine: { show: false } },
    yAxis: { axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: p.muted },
      splitLine: { lineStyle: { color: p.grid } } },
    aria: { enabled: true, decal: { show: false } },
    animationDuration: 300,
  } as echarts.EChartsOption;
}

/** Deep-merge a page option onto the base, mapping axis objects. */
export function merge(base: any, o: any): any {
  const out = { ...base, ...o };
  for (const k of ["xAxis", "yAxis"]) {
    if (o[k] !== undefined) {
      out[k] = Array.isArray(o[k]) ? o[k].map((a: any) => ({ ...base[k], ...a })) : { ...base[k], ...o[k] };
    }
  }
  for (const k of ["tooltip", "grid", "legend", "textStyle"]) if (o[k] !== undefined && o[k] !== false) out[k] = { ...base[k], ...o[k] };
  return out;
}

export interface TableData {
  columns: string[];
  rows: (string | number | null)[][];
}

export function Chart({ option, height = 240, context, table, title, onClick, testId, zoom, bare }: {
  option: (p: Palette) => echarts.EChartsOption; height?: number; context?: ChartContext; table?: TableData; title?: string;
  onClick?: (params: any) => void; testId?: string; zoom?: boolean; bare?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const theme = useUi((s) => s.theme);
  const explain = useUi((s) => s.explain);
  const [showTable, setShowTable] = useState(false);
  const opt = useMemo(() => {
    const p = palette();
    const o = merge(baseOption(p), option(p));
    if (zoom) {
      o.dataZoom = [{ type: "inside", xAxisIndex: 0, filterMode: "none" }];
    }
    return o;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [option, theme, zoom]);

  useEffect(() => {
    if (!ref.current || showTable) return;
    const c = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chart.current = c;
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(ref.current);
    return () => { ro.disconnect(); c.dispose(); chart.current = null; };
  }, [showTable]);

  useEffect(() => {
    chart.current?.setOption(opt, true);
    if (onClick && chart.current) {
      chart.current.off("click");
      chart.current.on("click", onClick);
    }
  }, [opt, onClick, showTable]);

  const savePng = async () => {
    if (!chart.current) return;
    const url = chart.current.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: token("--surface-1") });
    await invoke("chart:savePng", { dataUrl: url, name: (title ?? "vitalis-chart").replace(/[^\w .-]/g, "") });
  };

  return (
    <div className="relative" data-testid={testId}>
      {!bare && <div className="mb-1 flex justify-end gap-1">
        {table && <Button size="sm" variant="ghost" onClick={() => setShowTable((v) => !v)} title={showTable ? "Show chart" : "Show data table"}>
          <Table2 size={13} /></Button>}
        {!showTable && <Button size="sm" variant="ghost" onClick={savePng} title="Save PNG"><Image size={13} /></Button>}
        {context && !IS_ANDROID && <Button size="sm" variant="ghost" onClick={() => explain(context)} title="Ask the AI analyst about this chart"
          testId="ask-ai-chart"><MessageSquareText size={13} /> Ask AI</Button>}
      </div>}
      {showTable && table ? (
        <div className="max-h-[360px] overflow-auto rounded-lg border border-line" style={{ minHeight: height }}>
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-s2 text-fg2"><tr>{table.columns.map((c) => <th key={c} className="px-2 py-1 text-left font-medium">{c}</th>)}</tr></thead>
            <tbody>{table.rows.map((r, i) => <tr key={i} className="border-t border-line">{r.map((v, j) =>
              <td key={j} className="num px-2 py-1 text-fg">{v ?? "—"}</td>)}</tr>)}</tbody>
          </table>
        </div>
      ) : (
        <div ref={ref} style={{ height }} className={cx("w-full")} role="img" aria-label={title ?? "chart"} />
      )}
    </div>
  );
}

/** Line/area helper for daily series (gaps stay gaps — missing data is never interpolated). */
export function dailyLines(dates: string[], series: { name: string; data: (number | null)[]; color: string; area?: boolean;
  dashed?: boolean }[], opts: { yName?: string; band?: { lo: number; hi: number } | null; min?: number | "dataMin";
  formatter?: (v: number) => string } = {}) {
  return (p: Palette): echarts.EChartsOption => ({
    legend: series.length > 1 ? { data: series.map((s) => s.name) } : { show: false },
    xAxis: { type: "category", data: dates, axisLabel: { color: p.muted, formatter: (v: string) => v.slice(5) } },
    yAxis: { type: "value", name: opts.yName, nameTextStyle: { color: p.muted }, scale: true, min: opts.min,
      axisLabel: { color: p.muted, formatter: opts.formatter as any } },
    tooltip: { valueFormatter: (v: any) => (v == null ? "—" : opts.formatter ? opts.formatter(Number(v)) : String(Math.round(Number(v) * 10) / 10)) },
    series: series.map((s, i) => ({
      name: s.name, type: "line", data: s.data, connectNulls: false, showSymbol: dates.length < 45, symbolSize: 6,
      lineStyle: { width: 2, color: s.color, type: s.dashed ? "dashed" : "solid" }, itemStyle: { color: s.color },
      areaStyle: s.area ? { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: s.color + "40" },
        { offset: 1, color: s.color + "00" }]) } : undefined,
      markArea: i === 0 && opts.band ? { silent: true, itemStyle: { color: p.band },
        data: [[{ yAxis: opts.band.lo, name: "Your usual range" }, { yAxis: opts.band.hi }]],
        label: { color: p.muted, position: "insideTopRight", fontSize: 10 } } : undefined,
    })),
  });
}

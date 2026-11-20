import { useEffect, useState } from "react";
import { ReportView } from "../components/ReportView";
import { engine } from "../lib/bridge";

/** Hidden-window route used by main to render the A4 PDF (light theme, charts without animation). */
export default function PrintReport() {
  const [r, setR] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", "light");
    const q = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
    engine("report.build", { start: q.get("start")!, end: q.get("end")! }).then(setR).catch((e) => setErr(String(e.message ?? e)));
  }, []);
  useEffect(() => {
    if (r || err) {
      const t = setTimeout(() => window.vitalis.reportReady(), 1200); // allow charts to render
      return () => clearTimeout(t);
    }
  }, [r, err]);
  if (err) return <div className="p-8">Report failed: {err}</div>;
  if (!r) return <div className="p-8">Building report…</div>;
  return <div className="bg-white p-6 text-black"><ReportView r={r} print /></div>;
}

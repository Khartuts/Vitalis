import { useQueryClient } from "@tanstack/react-query";
import { MessageSquare, Trash2 } from "lucide-react";
import { useState } from "react";
import { AiPanel } from "../components/AiPanel";
import { Button, PageHeader } from "../components/ui";
import { engine } from "../lib/bridge";
import { fmtDate } from "../lib/format";
import { useEngine } from "../lib/hooks";

export default function Analyst() {
  const qc = useQueryClient();
  const threads = useEngine<any[]>("chat.threads");
  const [sel, setSel] = useState<string | null>(null);
  return (
    <div data-testid="analyst-page">
      <PageHeader title="AI Health Analyst" subtitle="A conversational analyst that answers from your stored measurements through read-only, validated tools.
        It cannot run arbitrary queries or code, and every cloud request is previewed for your approval. Not medical advice." />
      <div className="grid h-[calc(100vh-230px)] min-h-[520px] grid-cols-[260px_1fr] gap-4">
        <div className="flex min-h-0 flex-col rounded-xl border border-line bg-s1">
          <div className="flex items-center justify-between border-b border-line px-3 py-2 text-xs font-semibold">Conversations
            <Button size="sm" variant="ghost" onClick={() => setSel(null)}>New</Button></div>
          <ul className="min-h-0 flex-1 overflow-auto p-1.5">
            {(threads.data ?? []).map((t) => (
              <li key={t.id} className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 text-[13px] ${sel === t.id ? "bg-s3" : "hover:bg-s2"}`}>
                <button className="flex flex-1 items-center gap-1.5 truncate text-left" onClick={() => setSel(t.id)}><MessageSquare size={12} className="shrink-0 text-muted" />
                  <span className="truncate">{t.title}</span></button>
                <span className="text-[10px] text-muted">{fmtDate(t.updated_at)}</span>
                <button aria-label="Delete conversation" className="hidden text-muted hover:text-critical group-hover:block"
                  onClick={async () => { await engine("chat.thread_delete", { id: t.id }); if (sel === t.id) setSel(null); qc.invalidateQueries({ queryKey: ["chat.threads"] }); }}><Trash2 size={12} /></button>
              </li>
            ))}
            {!threads.data?.length && <li className="px-2 py-3 text-xs text-muted">No saved conversations.</li>}
          </ul>
        </div>
        <AiPanel full threadId={sel} />
      </div>
    </div>
  );
}

// IPC contract shared by main, preload and renderer.
// The renderer may only invoke the channels listed here; main validates every payload with Zod.
import { z } from "zod";

/** Engine (Python) methods the renderer may call through `engine:call`. Methods that take filesystem paths,
 * raw keys or AI tool execution are deliberately excluded and are only reachable through main-mediated flows. */
export const RENDERER_ENGINE_METHODS = [
  "system.status", "prefs.get", "prefs.set", "features.catalog", "features.series", "features.day", "baseline.get",
  "overview.get", "sleep.nights", "sleep.consistency", "sleep.planner", "sleep.plan_save", "sleep.adherence",
  "recovery.range", "load.range", "energy.day", "stress.timeline", "stress.summary", "cardio.summary",
  "activity.summary", "compare.periods", "stats.correlate", "stats.habit", "stats.scan", "journal.habits",
  "journal.habit_upsert", "journal.habit_delete", "journal.day_get", "journal.day_set", "journal.range",
  "experiments.create", "experiments.list", "experiments.analyze", "experiments.status", "experiments.delete",
  "insights.list", "insights.dismiss", "briefs.get", "quality.report", "sources.availability", "import.history",
  "sync.status", "sync.revoke", "sync.rotate_file_key", "demo.load", "recompute.run", "report.build", "chat.threads",
  "chat.thread_create", "chat.thread_rename", "chat.thread_delete", "chat.delete_all", "chat.messages",
  "ai.disclosures", "ai.private_habits", "import.google_filter",
] as const;
export type RendererEngineMethod = (typeof RENDERER_ENGINE_METHODS)[number];

export const EngineCallSchema = z.object({
  method: z.enum(RENDERER_ENGINE_METHODS),
  params: z.record(z.string(), z.unknown()).optional(),
});

export const ProfileCreateSchema = z.object({
  name: z.string().trim().min(1).max(60),
  demo: z.boolean(),
  timezone: z.string().min(1).max(64).default("Europe/Zagreb"),
  birthYear: z.number().int().min(1900).max(2020).nullable().optional(),
  sleepNeedMin: z.number().min(240).max(720).optional(),
});
export const IdSchema = z.object({ id: z.string().min(1).max(80) });

export const AiProviderSchema = z.enum(["chatgpt", "openai_api", "local"]);
export type AiProvider = z.infer<typeof AiProviderSchema>;

export const AiSendSchema = z.object({
  threadId: z.string().min(1).max(64),
  message: z.string().trim().min(1).max(8000),
  provider: AiProviderSchema,
  model: z.string().min(1).max(100),
  chartContext: z
    .object({ metrics: z.array(z.string().max(64)).max(4), start: z.string().max(10), end: z.string().max(10),
      title: z.string().max(120).optional() })
    .nullable()
    .optional(),
});
export const AiDecisionSchema = z.object({ requestId: z.string().min(1).max(64), approve: z.boolean(),
  approveRemaining: z.boolean().default(false) });
export const ApiKeySchema = z.object({ apiKey: z.string().trim().min(20).max(400).startsWith("sk-") });
export const LocalAiSchema = z.object({
  baseUrl: z.string().url().refine((u) => /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?\//.test(u + "/"),
    "local runtime must be on 127.0.0.1/localhost"),
  enabled: z.boolean(),
});
export const GoogleConfigSchema = z.object({
  clientId: z.string().trim().min(10).max(200).regex(/\.apps\.googleusercontent\.com$/),
  clientSecret: z.string().trim().max(200).optional().default(""),
});
export const GoogleSyncSchema = z.object({ days: z.number().int().min(1).max(365) });
export const RangeSchema = z.object({ start: z.string().regex(/^\d{4}-\d{2}-\d{2}$/), end: z.string().regex(/^\d{4}-\d{2}-\d{2}$/) });
export const PassphraseSchema = z.object({ passphrase: z.string().min(10).max(512) });
export const ImportKindSchema = z.object({ kind: z.enum(["samsung_export", "vitalis_sync_file"]) });
export const ImportCommitSchema = z.object({ token: z.string().min(1).max(64) });
export const ExternalUrlSchema = z.object({ url: z.string().url() });
export const CsvExportSchema = RangeSchema.extend({ features: z.array(z.string().max(64)).max(80).optional() });

/** External URLs the app may open in the system browser. */
export const EXTERNAL_URL_ALLOWLIST = [
  "https://chatgpt.com/settings/usage", "https://chatgpt.com/", "https://console.cloud.google.com/",
  "https://developers.google.com/health", "https://developer.samsung.com/health/data",
  "https://developer.android.com/health-and-fitness/health-connect", "https://platform.openai.com/api-keys",
  "https://myaccount.google.com/permissions", "https://ollama.com/",
];

export type AiEvent =
  | { type: "consent"; requestId: string; round: number; provider: AiProvider; model: string; payload: string;
      categories: string[]; bytes: number }
  | { type: "status"; requestId: string; text: string }
  | { type: "delta"; requestId: string; text: string }
  | { type: "tool_call"; requestId: string; name: string; arguments: unknown; ok?: boolean; error?: string }
  | { type: "done"; requestId: string; messageId: string; content: string; evidence: Evidence[]; toolCalls: number;
      unverifiedNumbers: boolean }
  | { type: "error"; requestId: string; code: string; message: string; manageUsage?: boolean };

export interface Evidence {
  id: string; kind: string; metric?: string; label?: string; unit?: string; start?: string; end?: string; n?: number | null;
}

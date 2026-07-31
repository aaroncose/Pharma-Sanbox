"use server";

import { api, ApiError } from "@/lib/api";

export type Envelope = {
  delivered: boolean;
  blocked_reason: string | null;
  requires_human_review: boolean;
  degraded: boolean;
  policy_codes: string[];
  output: Record<string, unknown> | null;
  verifier: { verdict: string } | null;
  sources: {
    source_id: string;
    title: string;
    version: string;
    section: string | null;
    excerpt: string;
  }[];
  meta: {
    output_id: string;
    review_item_id: string | null;
    trace_id: string;
    model: string;
    prompt: string;
    cost_eur: number;
  };
  consent?: {
    data_analysis: boolean;
    history_included: boolean;
    reason: string | null;
  };
};

export type Result =
  | { ok: true; data: Envelope }
  | { ok: false; message: string };

async function run(path: string, body: unknown): Promise<Result> {
  try {
    return { ok: true, data: await api<Envelope>(path, { method: "POST", body }) };
  } catch (error) {
    return {
      ok: false,
      message:
        error instanceof ApiError
          ? error.message
          : "No se ha podido contactar con el servicio.",
    };
  }
}

export async function generateBriefing(input: {
  hcp_id: string;
  product_id: string;
  objective: string;
  duration_minutes: number;
}) {
  return run("/api/v1/agent/briefing", input);
}

export async function generateSummary(input: {
  hcp_id: string;
  product_id: string;
  notes: string;
  channel: string;
}) {
  return run("/api/v1/agent/summary", input);
}

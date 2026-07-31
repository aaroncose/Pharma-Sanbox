"use server";

import { api, ApiError } from "@/lib/api";

export type StartResult = {
  id: string;
  hcp: { full_name: string; specialty: string; institution: string };
  product: string;
  objective: string;
  max_turns: number;
  opening_turn: { ordinal: number; content: string; intent: string };
};

export type TurnResult = {
  rep_turn: {
    ordinal: number;
    compliance_flag: string | null;
    risk_level: string;
    policy_codes: string[];
    hint: string | null;
  };
  hcp_turn: { ordinal: number; content: string; intent: string };
  turns_used: number;
  max_turns: number;
};

export type Debrief = {
  id: string;
  score: number;
  score_cap: number | null;
  score_breakdown: string;
  communication: { score: number; summary: string; strengths: string[] };
  compliance: {
    score: number;
    flagged_turns: number;
    total_rep_turns: number;
    penalties: { turn_ordinal: number; policy_code: string; severity: string; penalty: number }[];
    bonus_applied: number;
  };
  improvable_answers: {
    turn_ordinal: number | null;
    what_was_said: string;
    why: string;
    suggested_rewrite: string;
  }[];
  handled_out_of_bounds_well: boolean;
  sources_you_could_have_used: {
    source_id: string;
    title: string;
    section: string | null;
    excerpt: string;
  }[];
};

type R<T> = { ok: true; data: T } | { ok: false; message: string };

async function call<T>(path: string, body?: unknown): Promise<R<T>> {
  try {
    return { ok: true, data: await api<T>(path, { method: "POST", body }) };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof ApiError ? error.message : "Error de red.",
    };
  }
}

export async function startSimulation(input: {
  hcp_id: string;
  product_id: string;
  scenario: string;
  objective: string;
  attitude: string;
}) {
  return call<StartResult>("/api/v1/simulations", input);
}

export async function takeTurn(id: string, utterance: string) {
  return call<TurnResult>(`/api/v1/simulations/${id}/turns`, { utterance });
}

export async function endSimulation(id: string) {
  return call<Debrief>(`/api/v1/simulations/${id}/end`);
}

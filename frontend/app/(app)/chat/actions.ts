"use server";

import { api, ApiError } from "@/lib/api";

/**
 * Acciones de servidor del asistente.
 *
 * Van aquí y no en un route handler porque el token vive en una cookie del
 * servidor: una acción de servidor puede leerla, un `fetch` desde el navegador
 * no. El componente cliente llama a esto y nunca ve la credencial.
 */

export type AgentEnvelope = {
  delivered: boolean;
  blocked_reason: string | null;
  requires_human_review: boolean;
  degraded: boolean;
  policy_codes: string[];
  output: {
    answer?: string;
    confidence: number;
    risk_level: string;
    sources: string[];
    gaps: string[];
    flags?: string[];
  } | null;
  verifier: {
    verdict: string;
    unsupported_claims: { claim: string; why: string; severity: string }[];
    confidence_adjustment: number;
  } | null;
  sources: {
    source_id: string;
    document_id: string;
    title: string;
    version: string;
    section: string | null;
    excerpt: string;
    similarity: number;
    semantic_rank: number | null;
    lexical_rank: number | null;
  }[];
  meta: {
    output_id: string;
    review_item_id: string | null;
    trace_id: string;
    model: string;
    prompt: string;
    latency_ms: number;
    cost_eur: number;
  };
};

export type AskResult =
  | { ok: true; data: AgentEnvelope }
  | { ok: false; code: string; message: string };

export async function ask(
  question: string,
  productId?: string,
): Promise<AskResult> {
  try {
    const data = await api<AgentEnvelope>("/api/v1/agent/chat", {
      method: "POST",
      body: { question, product_id: productId || undefined },
    });
    return { ok: true, data };
  } catch (error) {
    if (error instanceof ApiError) {
      // El 429 se distingue del resto: no es un fallo del sistema sino el
      // presupuesto del agente, que es un cubo aparte del de lectura
      // precisamente porque generar cuesta dinero y segundos.
      return { ok: false, code: error.code, message: error.message };
    }
    return {
      ok: false,
      code: "NETWORK",
      message: "No se ha podido contactar con el servicio.",
    };
  }
}

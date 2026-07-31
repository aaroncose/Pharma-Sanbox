"use server";

import { revalidatePath } from "next/cache";

import { api, ApiError } from "@/lib/api";

export type ScenarioOutcome =
  | {
      ok: true;
      slug: string;
      passed: boolean;
      traceId: string;
      request: Record<string, unknown>;
      result: Record<string, unknown>;
      auditLogId: string | null;
    }
  | { ok: false; message: string };

export async function runScenario(slug: string): Promise<ScenarioOutcome> {
  try {
    const data = await api<{
      slug: string;
      passed: boolean;
      trace_id: string;
      request: Record<string, unknown>;
      result: Record<string, unknown>;
      audit_log_id: string | null;
    }>("/api/v1/failure-lab/run", { method: "POST", body: { slug } });

    revalidatePath("/failure-lab");

    return {
      ok: true,
      slug: data.slug,
      passed: data.passed,
      traceId: data.trace_id,
      request: data.request,
      result: data.result,
      auditLogId: data.audit_log_id,
    };
  } catch (error) {
    if (error instanceof ApiError) return { ok: false, message: error.message };
    return { ok: false, message: "No se ha podido ejecutar el escenario." };
  }
}

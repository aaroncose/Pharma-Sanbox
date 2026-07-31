"use server";

import { revalidatePath } from "next/cache";

import { api, ApiError } from "@/lib/api";

export type RunResult =
  | { ok: true; runId: string }
  | { ok: false; message: string };

export async function runSuite(
  promptVersion: string | null,
): Promise<RunResult> {
  try {
    const data = await api<{ run_id: string }>("/api/v1/evals/run", {
      method: "POST",
      body: { prompt_version: promptVersion, force_mock: true },
    });
    revalidatePath("/evals");
    return { ok: true, runId: data.run_id };
  } catch (error) {
    if (error instanceof ApiError) return { ok: false, message: error.message };
    return { ok: false, message: "No se ha podido ejecutar la suite." };
  }
}

export async function compareVersions(
  versions: string[],
): Promise<RunResult> {
  try {
    const data = await api<{ runs: { run_id: string }[] }>(
      "/api/v1/evals/compare",
      { method: "POST", body: { versions, force_mock: true } },
    );
    revalidatePath("/evals");
    return { ok: true, runId: data.runs[data.runs.length - 1].run_id };
  } catch (error) {
    if (error instanceof ApiError) return { ok: false, message: error.message };
    return { ok: false, message: "No se ha podido comparar las versiones." };
  }
}

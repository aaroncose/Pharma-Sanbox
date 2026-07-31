"use server";

import { revalidatePath } from "next/cache";

import { api, ApiError } from "@/lib/api";

export type DecisionResult =
  | { ok: true }
  | { ok: false; message: string; rule?: string };

/**
 * Decisiones de compliance.
 *
 * Los cuatro desenlaces tienen la misma firma a propósito: cualquiera de ellos
 * exige un motivo escrito, y ninguno puede aplicarse dos veces. Que aprobar sea
 * tan costoso como rechazar es el punto — si aprobar fuera un clic y rechazar
 * exigiera justificarse, la cola se vaciaría aprobando.
 */
async function decide(
  reviewItemId: string,
  action: string,
  body: Record<string, unknown>,
): Promise<DecisionResult> {
  try {
    await api(`/api/v1/review/${reviewItemId}/${action}`, {
      method: "POST",
      body,
    });
    revalidatePath("/compliance");
    revalidatePath(`/compliance/${reviewItemId}`);
    return { ok: true };
  } catch (error) {
    if (error instanceof ApiError) {
      return {
        ok: false,
        message: error.message,
        rule: error.details?.rule as string | undefined,
      };
    }
    return { ok: false, message: "No se ha podido registrar la decisión." };
  }
}

export async function approve(id: string, rationale: string) {
  return decide(id, "approve", { rationale });
}

export async function reject(id: string, rationale: string) {
  return decide(id, "reject", { rationale });
}

export async function edit(
  id: string,
  rationale: string,
  editedContent: string,
  expectedBehaviour: string,
) {
  return decide(id, "edit", {
    rationale,
    edited_content: editedContent,
    expected_behaviour: expectedBehaviour,
  });
}

export async function requestRegeneration(
  id: string,
  rationale: string,
  guidance: string,
) {
  return decide(id, "request-regeneration", { rationale, guidance });
}

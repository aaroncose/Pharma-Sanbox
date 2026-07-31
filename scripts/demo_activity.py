"""Genera actividad de demostración atravesando la API real.

Una base recién sembrada tiene documentos, profesionales y políticas, pero
ninguna ejecución del agente: el panel, la cola de revisión y la auditoría
aparecen vacíos, que es exactamente igual a como se ven cuando algo está roto.

No se insertan filas a mano. Cada salida se produce llamando a los mismos
endpoints que usa la interfaz, con el proveedor que esté configurado, así que
las trazas, las fuentes citadas, el coste y las entradas de la cola son las que
el sistema genera de verdad.

    make demo            # requiere la API levantada
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

BASE = os.environ.get("API_BASE", "http://127.0.0.1:8010")
PASSWORD = os.environ.get("DEMO_PASSWORD", "Demo1234!")

# Tres preguntas cubiertas por el material aprobado y una que no lo está. La
# cuarta no sobra: que el asistente declare el hueco en vez de rellenarlo es
# parte de lo que hay que poder enseñar.
CHAT_QUESTIONS = [
    "¿Qué posología recoge la ficha de producto de CardioX?",
    "¿Qué resultados y qué limitaciones declara el estudio CARDIO-101?",
    "¿Qué hay que hacer ante una sospecha de evento adverso con CardioX?",
    "¿Qué eficacia tiene CardioX en pacientes con insuficiencia renal grave?",
]

BRIEFINGS = [
    "Repasar los resultados y las limitaciones del estudio CARDIO-101",
    "Resolver dudas sobre posología y seguridad recogidas en la ficha de producto",
]

NOTES = (
    "La doctora pregunta por la evidencia en pacientes mayores de 75 años y "
    "comenta que un colega le mencionó una reducción del 37 % en eventos. "
    "Pide material que pueda revisar con su equipo y queda pendiente enviarle "
    "la ficha técnica actualizada antes de la próxima visita."
)


def login(client: httpx.Client, email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def call(client: httpx.Client, path: str, headers: dict[str, str], body: dict) -> bool:
    response = client.post(path, headers=headers, json=body)
    if response.status_code >= 400:
        print(f"  ✕ {path} → {response.status_code} {response.text[:160]}")
        return False
    print(f"  ✓ {path}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="novapharma", choices=["novapharma"])
    args = parser.parse_args()
    del args

    with httpx.Client(base_url=BASE, timeout=300.0) as client:
        try:
            client.get("/healthz").raise_for_status()
        except httpx.HTTPError:
            print(f"La API no responde en {BASE}. Arráncala con 'make api'.")
            return 2

        rep = login(client, "laura.garcia@novapharma.demo")

        hcps = client.get("/api/v1/hcps?limit=3", headers=rep).json()["items"]
        products = client.get("/api/v1/products", headers=rep).json()["items"]

        if not hcps or not products:
            print("Faltan profesionales o productos. Ejecuta 'make seed' primero.")
            return 2

        # Las preguntas hablan de CardioX; que el producto no coincida haría que
        # la recuperación devolviera material de otro y el ejemplo perdería
        # sentido.
        product = next((p for p in products if p["name"] == "CardioX"), products[0])
        product_id = product["id"]
        ok = 0

        print("Consultas al asistente documental")
        for question in CHAT_QUESTIONS:
            ok += call(
                client,
                "/api/v1/agent/chat",
                rep,
                {"question": question, "product_id": product_id},
            )

        print("Briefings de visita")
        for hcp, objective in zip(hcps, BRIEFINGS):
            ok += call(
                client,
                "/api/v1/agent/briefing",
                rep,
                {
                    "hcp_id": hcp["id"],
                    "product_id": product_id,
                    "objective": objective,
                    "duration_minutes": 20,
                },
            )

        print("Resumen posterior a la visita")
        ok += call(
            client,
            "/api/v1/agent/summary",
            rep,
            {
                "hcp_id": hcps[0]["id"],
                "product_id": product_id,
                "notes": NOTES,
                "channel": "in_person",
            },
        )

    print(f"\n{ok} ejecuciones generadas.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

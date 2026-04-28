from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]  # .../AGENTE_AVARIAS_DEVOLUCAO
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents.peritos.perito_lataria import PeritoLataria, ConfigPeritoLataria
from core.config import config


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python src/scripts/debug/rerun_perito_lataria_from_output.py <PLACA>")
        return 2

    placa = sys.argv[1].strip()
    laudo_path = BASE_DIR / "output" / placa / "laudo.json"
    if not laudo_path.exists():
        raise FileNotFoundError(f"Laudo não encontrado: {laudo_path}")

    laudo = json.loads(laudo_path.read_text(encoding="utf-8"))
    perito = (laudo.get("peritos") or {}).get("perito_lataria") or {}
    imagens_usadas = perito.get("imagens_usadas") or []

    # Reconstroi image_paths na mesma ordem do orquestrador
    image_paths: list[str] = []
    for meta in imagens_usadas:
        if not isinstance(meta, dict):
            continue
        image_id = str(meta.get("image_id") or "").strip()
        if not image_id:
            continue
        image_paths.append(str(BASE_DIR / "input" / placa / "fotos" / f"{image_id}.jpg"))

    if not image_paths:
        print("Sem image_paths para rodar.")
        return 0

    instancia = PeritoLataria(ConfigPeritoLataria(caminho_lpu_xlsx=config.LPU_DEFAULT_PATH))
    res = instancia.run(image_paths=image_paths, imagens_usadas=imagens_usadas, checklist_summary="")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

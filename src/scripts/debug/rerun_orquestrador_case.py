from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.orquestrador import rodar_orquestrador, ConfigOrquestrador
from core.input_resolver import resolve_checklist_pdf, resolve_fotos_dir

def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python src/scripts/debug/rerun_orquestrador_case.py <PLACA>")
        return 2

    placa = sys.argv[1].strip()
    if not placa:
        print("PLACA vazia")
        return 2

    case_dir = BASE_DIR / "input" / placa
    fotos_dir = resolve_fotos_dir(case_dir)
    output_dir = BASE_DIR / "output" / placa
    checklist_pdf = resolve_checklist_pdf(case_dir)

    cfg = ConfigOrquestrador()

    res = rodar_orquestrador(
        case_id=placa,
        fotos_dir=str(fotos_dir),
        output_dir=str(output_dir),
        config=cfg,
        checklist_path=str(checklist_pdf) if checklist_pdf else None,
    )

    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

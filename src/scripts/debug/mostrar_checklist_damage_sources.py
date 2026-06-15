from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agents.triage_agent import _checklist_damage_for_part  # noqa: E402
from core.input_resolver import resolve_checklist_pdf  # noqa: E402
from core.pdf_utils import extract_reldev_avaria_items, extract_reldev_rows  # noqa: E402


def _find_matches(part_id: str, avaria_items: list) -> list:
    direct = [item for item in avaria_items if item.part_id == part_id]
    if direct:
        return direct

    return [
        item
        for item in avaria_items
        if item.part_id and _checklist_damage_for_part(part_id, {item.part_id}) is True
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Mostra quais linhas do checklist explicam imagens com "
            "checklist_damage_reported=True no triage.json."
        )
    )
    parser.add_argument("placa", help="Placa/case_id. Ex.: EMJ9F62")
    args = parser.parse_args()

    placa = args.placa.strip()
    triage_path = ROOT / "output" / placa / "triage.json"
    if not triage_path.exists():
        print(f"triage.json nao encontrado: {triage_path}")
        return 1

    checklist_pdf = resolve_checklist_pdf(ROOT / "input" / placa)
    if not checklist_pdf:
        print(f"Checklist RELDEV nao encontrado em input/{placa}/PDF")
        return 1

    triage = json.loads(triage_path.read_text(encoding="utf-8"))
    avaria_items = extract_reldev_avaria_items(checklist_pdf)
    all_rows = extract_reldev_rows(checklist_pdf)
    true_images = [
        img
        for img in triage.get("images", [])
        if img.get("checklist_damage_reported") is True
    ]

    print(f"Placa: {placa}")
    print(f"Checklist: {checklist_pdf}")
    print(f"Imagens com checklist_damage_reported=True: {len(true_images)}")
    print(f"Linhas REGISTRO=AVARIA extraidas do checklist: {len(avaria_items)}")
    print()

    if not true_images:
        print("Nenhuma imagem marcada como checklist_damage_reported=True.")
        return 0

    if not avaria_items:
        print(
            "ATENCAO: nao ha nenhuma linha estruturada com REGISTRO=AVARIA no "
            "RELDEV. Portanto os TRUE atuais nao possuem linha-fonte rastreavel "
            "pelo extrator; eles provavelmente vieram do fallback do LLM sobre o "
            "texto bruto do checklist."
        )
        print()

    for img in true_images:
        image_id = img.get("image_id")
        part_id = str(img.get("part_id") or "")
        confidence = img.get("confidence")
        matches = _find_matches(part_id, avaria_items)

        print(f"{image_id} | part_id={part_id} | confidence={confidence}")
        if matches:
            for item in matches:
                print(
                    "  -> "
                    f"pagina {item.page}: "
                    f"DESCRICAO='{item.descricao}' | "
                    f"ITEM='{item.item}' | "
                    "REGISTRO='AVARIA'"
                )
        else:
            print("  -> sem linha REGISTRO=AVARIA correspondente no checklist")
        print()

    if not avaria_items and all_rows:
        print("Linhas da tabela extraidas do RELDEV:")
        for row in all_rows:
            print(
                f"- pagina {row.page}: "
                f"DESCRICAO='{row.descricao}' | "
                f"ITEM='{row.item}' | "
                f"REGISTRO='{row.registro}'"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

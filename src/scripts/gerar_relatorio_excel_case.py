from __future__ import annotations

import sys
from pathlib import Path

# =========================
# RESOLUÇÃO DE PATH
# =========================
BASE_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents.excel_agent import ExcelAgent  # noqa: E402
from core.input_resolver import resolve_checklist_pdf


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python src/scripts/gerar_relatorio_excel_case.py <PLACA> [--out <arquivo.xlsx>]")
        return 2

    placa = sys.argv[1].strip()
    if not placa:
        print("PLACA inválida")
        return 2

    output_dir = BASE_DIR / "output" / placa
    laudo_json = output_dir / "laudo.json"
    checklist_pdf = resolve_checklist_pdf(BASE_DIR / "input" / placa)
    template_xlsx = BASE_DIR / "template_excel_padrao.xlsx"
    output_xlsx = output_dir / f"{placa}.xlsx"

    # Permite sobrescrever output
    if "--out" in sys.argv:
        idx = sys.argv.index("--out")
        if idx + 1 < len(sys.argv):
            output_xlsx = Path(sys.argv[idx + 1])

    if not laudo_json.exists():
        print(f"ERRO: Laudo não encontrado: {laudo_json}")
        return 1

    if not template_xlsx.exists():
        print(f"ERRO: Template não encontrado: {template_xlsx}")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    agent = ExcelAgent(template_xlsx)
    try:
        agent.generate_report(laudo_json, output_xlsx, pdf_path=str(checklist_pdf) if checklist_pdf else None)
    except PermissionError as e:
        print(f"PermissionError ao salvar {output_xlsx}: {e}")
        print("Feche o arquivo no Excel e tente novamente.")
        return 1

    print(f"OK: {output_xlsx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

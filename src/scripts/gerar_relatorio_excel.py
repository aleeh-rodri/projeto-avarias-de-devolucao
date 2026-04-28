import sys
from pathlib import Path

# =========================
# RESOLUÇÃO DE PATH
# =========================
BASE_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents.excel_agent import ExcelAgent
from core.input_resolver import resolve_checklist_pdf

def main():
    placa = "RUY5J95"
    
    # Caminhos
    output_dir = BASE_DIR / "output" / placa
    laudo_json = output_dir / "laudo.json"
    checklist_pdf = resolve_checklist_pdf(BASE_DIR / "input" / placa)
    template_xlsx = BASE_DIR / "template_excel_padrao.xlsx"
    output_xlsx = output_dir / f"{placa}.xlsx"
    
    print(f"Lendo laudo de: {laudo_json}")
    print(f"Usando template: {template_xlsx}")
    
    if not laudo_json.exists():
        print(f"ERRO: Arquivo {laudo_json} não encontrado.")
        return

    if not template_xlsx.exists():
        print(f"ERRO: Arquivo {template_xlsx} não encontrado.")
        return

    try:
        agent = ExcelAgent(template_xlsx)
        agent.generate_report(laudo_json, output_xlsx, pdf_path=str(checklist_pdf) if checklist_pdf else None)
        print(f"Sucesso! Planilha gerada em: {output_xlsx}")
    except Exception as e:
        print(f"Erro ao gerar Excel: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

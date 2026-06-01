from pathlib import Path
import subprocess
import sys
from typing import Iterable


# =========================
# RESOLUÇÃO DE PATH
# =========================
BASE_DIR = Path(__file__).resolve().parents[2]
# .../AGENTE_AVARIAS_DEVOLUCAO

SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.orquestrador import rodar_orquestrador, ConfigOrquestrador
from agents.excel_agent import ExcelAgent
from core.input_resolver import resolve_checklist_pdf, resolve_fotos_dir
from core.template_resolver import ExcelTemplateResolver
 


def _listar_placas_no_input(input_dir: Path) -> list[str]:
    if not input_dir.exists() or not input_dir.is_dir():
        return []

    placas: list[str] = []
    for child in input_dir.iterdir():
        if not child.is_dir():
            continue
        nome = child.name.strip()
        if not nome or nome.startswith("."):
            continue
        placas.append(nome)

    placas.sort()
    return placas


def _has_any_photo_files(fotos_dir: Path) -> bool:
    if not fotos_dir.exists() or not fotos_dir.is_dir():
        return False
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
    for p in fotos_dir.iterdir():
        if p.is_file() and p.suffix.lower() in exts:
            return True
    return False


def _listar_placas_pendentes(input_dir: Path, output_dir: Path) -> list[str]:
    """Placas pendentes = têm pasta em input com fotos e não têm laudo em output."""
    #Responde a pergunta: Quais placas ainda faltam processar?
    placas_input = _listar_placas_no_input(input_dir)
    pendentes: list[str] = []
    for placa in placas_input:
        case_dir = input_dir / placa
        try:
            fotos_dir = resolve_fotos_dir(case_dir)
        except Exception:
            continue

        if not _has_any_photo_files(fotos_dir):
            continue

        laudo_path = output_dir / placa / "laudo.json"
        if laudo_path.exists():
            continue

        pendentes.append(placa)

    pendentes.sort()
    return pendentes


def _normalizar_placas(args: Iterable[str], input_dir: Path) -> list[str]:
    placas_args = [a.strip() for a in args if a.strip()]
    if placas_args:
        return placas_args
    return _listar_placas_no_input(input_dir)


# =========================
# EXECUÇÃO
# =========================
def executar_processo_completo(placa: str):
    print("\n" + "=" * 60)
    print(f"Iniciando processamento completo para a PLACA: {placa}")
    print("=" * 60 + "\n")

    try:
        config = ConfigOrquestrador()

        template_resolver = ExcelTemplateResolver(metadata_xlsx_path=BASE_DIR / "input" / "vehicle_metadata_cache.xlsx", templates_dir=BASE_DIR)

        case_dir = BASE_DIR / "input" / placa
        fotos_dir = resolve_fotos_dir(case_dir)
        output_dir = BASE_DIR / "output" / placa
        checklist_pdf = resolve_checklist_pdf(case_dir)
        template_xlsx = template_resolver.resolve_template_path(placa)
        output_xlsx = output_dir / f"{placa}.xlsx"

        if not case_dir.exists():
            raise FileNotFoundError(
                f"Pasta do case não encontrada: {case_dir} (esperado: {BASE_DIR / 'input' / '<PLACA>'})"
            )

        if not fotos_dir.exists():
            conteudo = sorted([p.name for p in case_dir.iterdir()])
            raise FileNotFoundError(
                "Pasta de fotos não encontrada. "
                f"Tente criar uma pasta 'Fotos' (ou 'fotos') dentro de {case_dir}. "
                f"Conteúdo atual do case: {conteudo}. "
                f"Caminho tentado: {fotos_dir}"
            )

        resultado = rodar_orquestrador(
            case_id=placa,
            fotos_dir=str(fotos_dir),
            output_dir=str(output_dir),
            config=config,
            checklist_path=str(checklist_pdf) if checklist_pdf else None
        )

        print("\n" + "=" * 60)
        print("Sucesso! O laudo final foi gerado.")
        print(f"Local: {output_dir / 'laudo.json'}")
        print("=" * 60 + "\n")

        # Gerar Relatório Excel
        print("Gerando relatório Excel padronizado...")
        excel_agent = ExcelAgent(template_xlsx)
        excel_agent.generate_report(output_dir / "laudo.json", output_xlsx, pdf_path=str(checklist_pdf) if checklist_pdf else None)

        # Gerar comparação Checklist vs Excel (markdown)
        try:
            script_comparacao = Path(__file__).resolve().parent / "gerar_comparacao_checklist_vs_excel.py"
            if script_comparacao.exists():
                print("Gerando comparação Checklist vs Excel...")
                subprocess.run(
                    [sys.executable, str(script_comparacao), placa],
                    check=True,
                    cwd=str(BASE_DIR),
                )
            else:
                print(
                    "Aviso: script de comparação não encontrado em: "
                    f"{script_comparacao} (etapa ignorada)"
                )
        except Exception as e:
            print(f"Aviso: falha ao gerar comparação Checklist vs Excel: {e}")

        return resultado

    except Exception as e:
        print(f"\nERRO CRÍTICO durante a execução do fluxo: {e}")
        import traceback
        traceback.print_exc()
        return None


def executar_lote(placas: list[str]) -> dict[str, dict[str, str]]:
    """Executa o fluxo para várias placas, sem abortar o lote por falha individual."""
    if not placas:
        print("Nenhuma placa encontrada/fornecida para processar.")
        return {"sucesso": {}, "falha": {}}

    resultados: dict[str, dict[str, str]] = {"sucesso": {}, "falha": {}}

    for idx, placa in enumerate(placas, start=1):
        print(f"\n[{idx}/{len(placas)}] Processando placa: {placa}")
        resultado = executar_processo_completo(placa)
        if resultado is None:
            resultados["falha"][placa] = "falha"
        else:
            resultados["sucesso"][placa] = "ok"

    print("\n" + "=" * 60)
    print("Resumo do lote")
    print(f"Sucesso: {len(resultados['sucesso'])} | Falha: {len(resultados['falha'])}")
    if resultados["falha"]:
        print("Falharam:")
        for placa in sorted(resultados["falha"].keys()):
            print(f"- {placa}")
    print("=" * 60 + "\n")

    return resultados


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    input_dir = BASE_DIR / "input"
    output_dir = BASE_DIR / "output"

    raw_args = list(sys.argv[1:])
    only_pending = False
    list_only = False
    placas_args: list[str] = []
    for a in raw_args:
        a = (a or "").strip()
        if not a:
            continue
        if a in ("--pendentes", "--pending", "--only-pending"):
            only_pending = True
            continue
        if a in ("--listar", "--list"):
            list_only = True
            continue
        placas_args.append(a)

    if placas_args:
        placas = _normalizar_placas(placas_args, input_dir)
    else:
        placas = (
            _listar_placas_pendentes(input_dir, output_dir)
            if only_pending
            else _listar_placas_no_input(input_dir)
        )

    if list_only:
        label = "pendentes" if only_pending else "input"
        print(f"Placas ({label}): {len(placas)}")
        for p in placas:
            print(p)
        raise SystemExit(0)

    if not placas:
        if only_pending:
            print("Nenhuma placa pendente encontrada (com fotos no input e sem laudo no output).")
        else:
            print(f"Nenhuma placa encontrada em: {input_dir}")
        print("Uso:")
        print("  python src/scripts/rodar_fluxo_completo.py RVF5B81 RVY1F06")
        print("  python src/scripts/rodar_fluxo_completo.py   (processa todas as pastas em input/)")
        print("  python src/scripts/rodar_fluxo_completo.py --pendentes   (somente placas com fotos no input/ e sem output/<placa>/laudo.json)")
        print("  python src/scripts/rodar_fluxo_completo.py --pendentes --listar   (lista as pendentes sem executar)")
        raise SystemExit(2)

    executar_lote(placas)

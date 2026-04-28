from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROMPT_CONTAR_IMAGENS_CHECKLIST = """
Voce e um analisador visual especializado em leitura de paginas com grupos de miniaturas.

TAREFA
Observe a pagina inteira e execute estas etapas:
1) identifique todos os grupos VISIVELMENTE presentes na pagina;
2) para cada grupo visivel, identifique o titulo imediatamente acima dele;
3) se um grupo visivel nao tiver titulo imediatamente acima, use exatamente "sem_titulo";
4) conte quantas miniaturas/fotos existem em cada grupo;
5) informe o total geral de miniaturas da pagina.

DEFINICAO DE GRUPO
- Um grupo e um conjunto visual de miniaturas/fotos que aparecem organizadas juntas na pagina.
- Um titulo sozinho NAO cria um grupo.
- So existe grupo quando houver miniaturas/fotos visiveis associadas a ele.
- Se um titulo aparecer na pagina, mas as miniaturas desse titulo nao estiverem visiveis, NAO inclua esse titulo como grupo.

REGRA DE ASSOCIACAO TITULO -> GRUPO
- O titulo do grupo deve ser o texto imediatamente acima do conjunto de miniaturas visiveis.
- Use o titulo apenas se houver miniaturas claramente visiveis logo abaixo dele.
- Nao associe miniaturas de um grupo ao titulo seguinte que aparece mais abaixo na pagina.
- Nao crie grupo para um titulo que aparece no final da pagina sem miniaturas visiveis correspondentes.

O QUE DEVE SER CONTADO
- Conte 1 para cada miniatura/foto individual visivel.
- Considere miniatura/foto qualquer bloco retangular ou quadrado com conteudo visual proprio.
- Conte miniaturas no topo da pagina, no meio ou no final, desde que estejam visiveis.
- Se varias miniaturas estiverem na mesma secao visual, conte cada uma separadamente.

O QUE NAO DEVE SER CONTADO
- titulos, textos, subtitulos, rodape, numeracao da pagina;
- linhas, margens, molduras, containers e fundos vazios;
- espacos em branco;
- um titulo sem miniaturas visiveis abaixo dele;
- a mesma miniatura mais de uma vez.

REGRAS OBRIGATORIAS DE CONTAGEM
- Cada miniatura deve pertencer a UM UNICO grupo.
- Nenhuma miniatura pode ser contada em dois grupos.
- Faca a varredura da pagina de cima para baixo e da esquerda para a direita.
- Ao final, some as quantidades dos grupos.
- O valor de "quantidade_imagens_total" DEVE ser exatamente igual a soma das quantidades de todos os grupos.
- Se a soma dos grupos nao bater com o total, a resposta esta errada e deve ser corrigida antes de responder.

REGRAS DE CONFIANCA
- Use confidence alta apenas se:
  1) todos os grupos visiveis estiverem claros;
  2) cada miniatura estiver alocada em um unico grupo;
  3) a soma dos grupos bater exatamente com o total.
- Se houver qualquer ambiguidade de titulo, de limite de grupo ou de contagem, reduza a confidence.

RETORNE SOMENTE JSON VALIDO:
{
  "quantidade_imagens_total": 0,
  "grupos": [
    {
      "ordem_visual": 1,
      "classificacao_grupo": "",
      "titulo": "",
      "quantidade_imagens": 0
    }
  ],
  "confidence": 0.0,
  "justificativa": "liste os grupos efetivamente visiveis, em ordem visual, e confirme que a soma dos grupos bate com o total"
}
"""

@dataclass(frozen=True)
class PageTestResult:
    page_number_1based: int
    rendered_image_path: str
    raw_response: str
    parsed_response: dict[str, Any] | None
    error: str | None = None


def _resolve_project_root(explicit_root: str | None) -> Path:
    candidates: list[Path] = []
    if explicit_root:
        candidates.append(Path(explicit_root).expanduser().resolve())

    env_root = os.getenv("PROJECT_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root).expanduser().resolve())

    cwd = Path.cwd().resolve()
    candidates.extend([cwd, cwd.parent, cwd.parent.parent])

    for cand in candidates:
        src_dir = cand / "src"
        client_file = src_dir / "core" / "llm_gate_client.py"
        if src_dir.exists() and client_file.exists():
            return cand

    raise RuntimeError(
        "Nao foi possivel localizar a raiz do projeto. Use --project-root ou defina PROJECT_ROOT."
    )


def _add_src_to_path(project_root: Path) -> None:
    src_dir = project_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def _render_pdf_page(pdf_path: Path, page_number_1based: int, out_dir: Path, zoom: float = 2.0) -> Path:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "PyMuPDF (fitz) nao esta disponivel neste ambiente."
        ) from exc

    doc = fitz.open(pdf_path)
    try:
        page_index = page_number_1based - 1
        if page_index < 0 or page_index >= len(doc):
            raise ValueError(
                f"Pagina invalida: {page_number_1based}. O PDF tem {len(doc)} paginas."
            )
        page = doc.load_page(page_index)
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        out_path = out_dir / f"page_{page_number_1based}.png"
        pix.save(out_path)
        return out_path
    finally:
        doc.close()


def _clean_json_fences(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    return raw


def _parse_json_response(raw: str) -> dict[str, Any] | None:
    cleaned = _clean_json_fences(raw)
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def run_test(checklist_pdf: Path, pages: list[int], project_root: Path, out_json: Path) -> dict[str, Any]:
    _add_src_to_path(project_root)

    from core.llm_gate_client import call_llm_with_image  # type: ignore

    out_json.parent.mkdir(parents=True, exist_ok=True)

    results: list[PageTestResult] = []
    with tempfile.TemporaryDirectory(prefix="teste_checklist_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)

        for page_number in pages:
            rendered_path = _render_pdf_page(checklist_pdf, page_number, tmp_dir)
            raw_response = ""
            parsed_response: dict[str, Any] | None = None
            error: str | None = None

            try:
                raw_response = call_llm_with_image(
                    prompt=PROMPT_CONTAR_IMAGENS_CHECKLIST,
                    image_path=str(rendered_path),
                    temperature=0,
                    max_tokens=500,
                )
                parsed_response = _parse_json_response(raw_response)
                if parsed_response is None:
                    error = "Resposta nao veio em JSON valido."
            except Exception as exc:  # noqa: BLE001
                error = f"Falha ao chamar o LLM: {type(exc).__name__}: {exc}"

            # Copia a imagem renderizada para perto do JSON final para facilitar auditoria.
            audit_image_path = out_json.parent / f"checklist_page_{page_number}.png"
            audit_image_path.write_bytes(rendered_path.read_bytes())

            results.append(
                PageTestResult(
                    page_number_1based=page_number,
                    rendered_image_path=str(audit_image_path),
                    raw_response=raw_response,
                    parsed_response=parsed_response,
                    error=error,
                )
            )

    payload = {
        "checklist_pdf": str(checklist_pdf),
        "pages_tested": pages,
        "prompt": PROMPT_CONTAR_IMAGENS_CHECKLIST,
        "results": [
            {
                "page_number_1based": r.page_number_1based,
                "rendered_image_path": r.rendered_image_path,
                "raw_response": r.raw_response,
                "parsed_response": r.parsed_response,
                "error": r.error,
            }
            for r in results
        ],
    }

    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _parse_pages(value: str) -> list[int]:
    out: list[int] = []
    for token in (value or "").split(","):
        token = token.strip()
        if not token:
            continue
        out.append(int(token))
    if not out:
        raise argparse.ArgumentTypeError("Informe ao menos uma pagina, ex.: 3,4")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Testa se o LLM consegue identificar titulo de cada grupo de imagens e contar miniaturas por grupo em paginas do checklist PDF."
    )
    parser.add_argument("--checklist", required=True, help="Caminho para o checklist PDF")
    parser.add_argument(
        "--pages",
        default="3,4",
        help="Paginas do PDF para testar, separadas por virgula. Ex.: 3,4",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="Raiz do projeto (onde existe a pasta src). Opcional se voce rodar da raiz do projeto.",
    )
    parser.add_argument(
        "--out",
        default="./saida_teste_contar_imagens_checklist_grupos.json",
        help="Arquivo JSON de saida",
    )
    args = parser.parse_args()

    checklist_pdf = Path(args.checklist).expanduser().resolve()
    if not checklist_pdf.exists():
        print(f"ERRO: checklist nao encontrado: {checklist_pdf}")
        return 1

    try:
        pages = _parse_pages(args.pages)
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO em --pages: {exc}")
        return 1

    try:
        project_root = _resolve_project_root(args.project_root)
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO ao localizar projeto: {exc}")
        return 1

    out_json = Path(args.out).expanduser().resolve()

    try:
        payload = run_test(
            checklist_pdf=checklist_pdf,
            pages=pages,
            project_root=project_root,
            out_json=out_json,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO durante o teste: {type(exc).__name__}: {exc}")
        return 1

    print("OK: teste concluido")
    print(f"Checklist: {payload['checklist_pdf']}")
    print(f"Paginas testadas: {payload['pages_tested']}")
    print(f"Saida JSON: {out_json}")
    for item in payload.get("results", []):
        page = item.get("page_number_1based")
        parsed = item.get("parsed_response") or {}
        qtd_total = parsed.get("quantidade_imagens_total") if isinstance(parsed, dict) else None
        grupos = parsed.get("grupos") if isinstance(parsed, dict) else None
        qtd_grupos = len(grupos) if isinstance(grupos, list) else None
        err = item.get("error")
        print(f"- Pagina {page}: quantidade_imagens_total={qtd_total} grupos={qtd_grupos} erro={err}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


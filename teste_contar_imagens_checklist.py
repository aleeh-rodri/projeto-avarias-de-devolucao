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
Você é um analisador visual especializado em contagem de miniaturas organizadas em grade.

PADRÃO ESPERADO DA IMAGEM
A imagem terá:
1) um único título;
2) abaixo dele, um único bloco visual com miniaturas/fotos;
3) esse bloco é organizado como uma grade com uma ou mais linhas e uma ou mais colunas.

TAREFA
Conte quantas miniaturas/fotos visíveis existem dentro do único bloco abaixo do título.

OBJETIVO
Sua tarefa não é interpretar o conteúdo das imagens.
Sua tarefa é somente identificar a estrutura visual da grade e contar as miniaturas corretamente.

MÉTODO OBRIGATÓRIO
Siga exatamente estes passos antes de responder:
1) identifique o bloco único de miniaturas abaixo do título;
2) determine quantas linhas visíveis existem dentro desse bloco;
3) em cada linha, conte quantas miniaturas distintas existem da esquerda para a direita;
4) registre a contagem por linha;
5) some todas as linhas para obter o total final;
6) faça uma checagem final para confirmar que nenhuma linha foi ignorada e que nenhuma miniatura foi contada duas vezes.

REGRA PRINCIPAL
- Considere que todas as miniaturas localizadas dentro do mesmo bloco/container abaixo do título pertencem ao mesmo grupo.
- O grupo continua até o final do bloco/container visível.
- Não pare a contagem na primeira ou na segunda linha.
- Você deve contar todas as linhas visíveis do bloco.
- Cada miniatura deve ser contada uma única vez.

COMO RECONHECER UMA MINIATURA
- Conte como miniatura/foto qualquer bloco retangular ou quadrado com conteúdo visual próprio.
- As miniaturas normalmente aparecem separadas por espaços brancos/margens entre elas.
- Use esses espaços visuais para separar uma miniatura da outra.
- Não junte duas miniaturas em uma só.
- Não divida uma miniatura em duas.

REGRAS DE CONTAGEM
- Conte apenas miniaturas realmente visíveis dentro do bloco.
- Se uma miniatura estiver parcialmente cortada, conte apenas se ainda for claramente uma miniatura visível.
- Não é necessário identificar o conteúdo da miniatura.
- Não é necessário identificar se é lateral direita ou esquerda.

NÃO CONTE
- o título;
- textos;
- número da página;
- molduras sem conteúdo;
- espaços vazios;
- fundo da página;
- o container como se fosse uma imagem.

REGRAS DE CONSISTÊNCIA
- O valor "quantidade_imagens" deve ser exatamente a soma dos valores em "contagem_por_linha".
- Se a soma não bater, a resposta está errada e deve ser corrigida antes de responder.
- Se você não tiver certeza sobre o número de linhas ou o número de miniaturas em alguma linha, reduza a confidence.

REGRAS DE CONFIANÇA
- Use confidence alta apenas se:
  1) todas as linhas visíveis do bloco foram identificadas;
  2) a quantidade de miniaturas em cada linha ficou clara;
  3) a soma das linhas bate exatamente com o total.
- Se houver qualquer dúvida sobre a grade, a confidence não deve ser alta.

RETORNE SOMENTE JSON VÁLIDO:
{
  "quantidade_imagens": 0,
  "numero_de_linhas": 0,
  "contagem_por_linha": [0],
  "confidence": 0.0,
  "justificativa": "explique brevemente quantas linhas visíveis foram identificadas, quantas miniaturas havia em cada linha e como a soma levou ao total"
}
"""

@dataclass(frozen=True)
class PageTestResult:
    page_number_1based: int | None
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


def _run_llm_on_image(
    *,
    image_path: Path,
    out_json: Path,
    call_llm_with_image: Any,
    page_number_1based: int | None,
) -> PageTestResult:
    raw_response = ""
    parsed_response: dict[str, Any] | None = None
    error: str | None = None

    try:
        raw_response = call_llm_with_image(
            prompt=PROMPT_CONTAR_IMAGENS_CHECKLIST,
            image_path=str(image_path),
            temperature=0,
            max_tokens=500,
        )
        parsed_response = _parse_json_response(raw_response)
        if parsed_response is None:
            error = "Resposta nao veio em JSON valido."
    except Exception as exc:  # noqa: BLE001
        error = f"Falha ao chamar o LLM: {type(exc).__name__}: {exc}"

    if page_number_1based is None:
        audit_image_path = out_json.parent / image_path.name
    else:
        audit_image_path = out_json.parent / f"checklist_page_{page_number_1based}.png"
    audit_image_path.write_bytes(image_path.read_bytes())

    return PageTestResult(
        page_number_1based=page_number_1based,
        rendered_image_path=str(audit_image_path),
        raw_response=raw_response,
        parsed_response=parsed_response,
        error=error,
    )


def run_test(checklist_pdf: Path, pages: list[int], project_root: Path, out_json: Path) -> dict[str, Any]:
    _add_src_to_path(project_root)

    from core.llm_gate_client import call_llm_with_image  # type: ignore

    out_json.parent.mkdir(parents=True, exist_ok=True)

    results: list[PageTestResult] = []
    with tempfile.TemporaryDirectory(prefix="teste_checklist_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)

        for page_number in pages:
            rendered_path = _render_pdf_page(checklist_pdf, page_number, tmp_dir)
            results.append(
                _run_llm_on_image(
                    image_path=rendered_path,
                    out_json=out_json,
                    call_llm_with_image=call_llm_with_image,
                    page_number_1based=page_number,
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


def run_test_from_image(image_path: Path, project_root: Path, out_json: Path) -> dict[str, Any]:
    _add_src_to_path(project_root)

    from core.llm_gate_client import call_llm_with_image  # type: ignore

    out_json.parent.mkdir(parents=True, exist_ok=True)

    result = _run_llm_on_image(
        image_path=image_path,
        out_json=out_json,
        call_llm_with_image=call_llm_with_image,
        page_number_1based=None,
    )

    payload = {
        "input_image": str(image_path),
        "prompt": PROMPT_CONTAR_IMAGENS_CHECKLIST,
        "results": [
            {
                "page_number_1based": result.page_number_1based,
                "rendered_image_path": result.rendered_image_path,
                "raw_response": result.raw_response,
                "parsed_response": result.parsed_response,
                "error": result.error,
            }
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
        description="Testa se o LLM consegue contar miniaturas/fotos em um recorte com um unico titulo e um unico grupo."
    )
    parser.add_argument("--checklist", required=False, default=None, help="Caminho para o checklist PDF")
    parser.add_argument(
        "--image",
        required=False,
        default=None,
        help="Caminho para uma imagem unica (ex.: recorte gerado pelo teste_recorte_laterais_pdf)",
    )
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
        default="./saida_teste_contar_imagens_checklist.json",
        help="Arquivo JSON de saida",
    )
    args = parser.parse_args()

    try:
        project_root = _resolve_project_root(args.project_root)
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO ao localizar projeto: {exc}")
        return 1

    out_json = Path(args.out).expanduser().resolve()

    image_arg = (args.image or "").strip()
    checklist_arg = (args.checklist or "").strip()

    if image_arg:
        image_path = Path(image_arg).expanduser().resolve()
        if not image_path.exists():
            print(f"ERRO: imagem nao encontrada: {image_path}")
            return 1
        try:
            payload = run_test_from_image(
                image_path=image_path,
                project_root=project_root,
                out_json=out_json,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"ERRO durante o teste: {type(exc).__name__}: {exc}")
            return 1
        print("OK: teste concluido")
        print(f"Imagem de entrada: {payload['input_image']}")
    else:
        if not checklist_arg:
            print("ERRO: informe --checklist ou --image.")
            return 1
        checklist_pdf = Path(checklist_arg).expanduser().resolve()
        if not checklist_pdf.exists():
            print(f"ERRO: checklist nao encontrado: {checklist_pdf}")
            return 1

        try:
            pages = _parse_pages(args.pages)
        except Exception as exc:  # noqa: BLE001
            print(f"ERRO em --pages: {exc}")
            return 1

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
        qtd = parsed.get("quantidade_imagens") if isinstance(parsed, dict) else None
        if qtd is None and isinstance(parsed, dict):
            qtd = parsed.get("quantidade_imagens_total")
        err = item.get("error")
        print(f"- Pagina {page}: quantidade_imagens={qtd} erro={err}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROMPT_CORRESPONDENCIA_PRINCIPAL_MINIATURAS = """
Voce e um analisador visual de imagem composta.

ENTRADA ESPERADA
- Uma unica imagem contendo:
  1) no topo: uma foto principal (maior e primeira da imagem);
  2) abaixo: varias fotos menores (miniaturas).

TAREFA
1) Identificar a foto principal no topo.
2) Identificar todas as miniaturas abaixo.
3) Comparar visualmente a foto principal com cada miniatura.
4) Se existir ao menos uma miniatura com correspondencia visual da foto principal, retornar status "OK".
5) Se nao existir correspondencia visual, retornar status "nao_identificado".

REGRA DE CORRESPONDENCIA
- Considere correspondencia quando a miniatura representar a mesma imagem da foto do topo.
- A miniatura pode estar em escala menor, com leve corte ou pequena variacao de iluminacao.
- Nao use apenas cor de fundo como criterio.

SAIDA
Retorne SOMENTE JSON valido no formato:
{
  "status": "OK|nao_identificado",
  "indice_miniatura_correspondente": 0,
  "indices_correspondentes": [0],
  "confidence": 0.0,
  "justificativa": "breve explicacao"
}

REGRAS DA SAIDA
- Se status for "OK", informe ao menos um indice de miniatura correspondente.
- Use indices com base 0 (primeira miniatura abaixo da foto principal = indice 0).
- Se status for "nao_identificado", use indice_miniatura_correspondente como null e indices_correspondentes como [].
"""


@dataclass(frozen=True)
class TestResult:
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


def _run_llm_on_image(*, image_path: Path, call_llm_with_image: Any) -> TestResult:
    raw_response = ""
    parsed_response: dict[str, Any] | None = None
    error: str | None = None

    try:
        raw_response = call_llm_with_image(
            prompt=PROMPT_CORRESPONDENCIA_PRINCIPAL_MINIATURAS,
            image_path=str(image_path),
            temperature=0,
            max_tokens=500,
        )
        parsed_response = _parse_json_response(raw_response)
        if parsed_response is None:
            error = "Resposta nao veio em JSON valido."
    except Exception as exc:  # noqa: BLE001
        error = f"Falha ao chamar o LLM: {type(exc).__name__}: {exc}"

    return TestResult(
        raw_response=raw_response,
        parsed_response=parsed_response,
        error=error,
    )


def run_test_from_image(image_path: Path, project_root: Path, out_json: Path) -> dict[str, Any]:
    _add_src_to_path(project_root)

    from core.llm_gate_client import call_llm_with_image  # type: ignore

    out_json.parent.mkdir(parents=True, exist_ok=True)
    result = _run_llm_on_image(image_path=image_path, call_llm_with_image=call_llm_with_image)

    parsed = result.parsed_response or {}
    status = parsed.get("status") if isinstance(parsed, dict) else None
    if status not in {"OK", "nao_identificado"}:
        status = "erro"

    payload = {
        "input_image": str(image_path),
        "prompt": PROMPT_CORRESPONDENCIA_PRINCIPAL_MINIATURAS,
        "status_final": status,
        "result": {
            "raw_response": result.raw_response,
            "parsed_response": result.parsed_response,
            "error": result.error,
        },
    }

    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Testa correspondencia visual entre a foto principal (topo) e miniaturas "
            "abaixo da mesma imagem."
        )
    )
    parser.add_argument("--image", required=True, help="Caminho da imagem composta de entrada")
    parser.add_argument(
        "--project-root",
        default=None,
        help="Raiz do projeto (onde existe a pasta src). Opcional se rodar da raiz.",
    )
    parser.add_argument(
        "--out",
        default="./saida_teste_correspondencia_principal_miniaturas.json",
        help="Arquivo JSON de saida",
    )
    args = parser.parse_args()

    image_path = Path(args.image).expanduser().resolve()
    if not image_path.exists():
        print(f"ERRO: imagem nao encontrada: {image_path}")
        return 1

    try:
        project_root = _resolve_project_root(args.project_root)
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO ao localizar projeto: {exc}")
        return 1

    out_json = Path(args.out).expanduser().resolve()

    try:
        payload = run_test_from_image(
            image_path=image_path,
            project_root=project_root,
            out_json=out_json,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO durante o teste: {type(exc).__name__}: {exc}")
        return 1

    parsed = payload.get("result", {}).get("parsed_response") or {}
    print("OK: teste concluido")
    print(f"Imagem de entrada: {payload['input_image']}")
    print(f"Status final: {payload.get('status_final')}")
    print(f"Indice principal: {parsed.get('indice_miniatura_correspondente')}")
    print(f"Indices correspondentes: {parsed.get('indices_correspondentes')}")
    print(f"Saida JSON: {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

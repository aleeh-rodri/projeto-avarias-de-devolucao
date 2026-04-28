from __future__ import annotations

"""
Harness de testes para diferenciar lateral esquerda x lateral direita em fotos de avarias.

Objetivo
--------
Testar prompts diferentes para verificar se o LLM consegue classificar corretamente
lado/peça usando:
- a foto em análise
- o contexto do checklist (lateral esquerda / lateral direita)
- regras visuais explícitas

Este arquivo foi pensado para ficar separado do pipeline principal.
Ele NÃO altera triagem/orquestrador/peritos; serve para experimento.

Como usar
---------
1) Ajuste PROJECT_ROOT se quiser apontar manualmente para a raiz do projeto.
2) Passe:
   - uma pasta com fotos
   - opcionalmente o checklist PDF
3) Rode, por exemplo:

   python teste_esquerda_direita.py \
       --fotos ./input/QXW8F67/Fotos \
       --checklist ./input/QXW8F67/RELDEV_17.pdf

Saída
-----
Gera um JSON em ./saida_testes_esquerda_direita.json com os resultados de cada prompt.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# =========================
# RESOLUCAO DE PATH / IMPORTS
# =========================
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", "")).expanduser() if os.getenv("PROJECT_ROOT") else None
CURRENT_FILE = Path(__file__).resolve()
CANDIDATE_ROOTS = [
    PROJECT_ROOT,
    CURRENT_FILE.parent,
    CURRENT_FILE.parent.parent,
    CURRENT_FILE.parent.parent.parent,
]

ROOT_FOUND: Path | None = None
for candidate in CANDIDATE_ROOTS:
    if not candidate:
        continue
    src_dir = candidate / "src"
    if src_dir.exists() and src_dir.is_dir():
        ROOT_FOUND = candidate
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))
        break

IMPORT_ERROR: str | None = None
call_llm_with_image = None
extract_reldev_avaria_part_ids = None
extract_checklist_text = None

try:
    from core.llm_gate_client import call_llm_with_image  # type: ignore
    from core.pdf_utils import extract_reldev_avaria_part_ids, extract_checklist_text  # type: ignore
except Exception as e:  # pragma: no cover - ambiente local do usuario pode variar
    IMPORT_ERROR = str(e)


# =========================
# MODELOS
# =========================
@dataclass
class PromptVariant:
    name: str
    description: str
    prompt_builder: Any


# =========================
# HELPERS
# =========================
def _clean_json_fences(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    return raw


def _load_checklist_context(checklist_path: str | None) -> dict[str, Any]:
    if not checklist_path:
        return {
            "path": None,
            "part_ids": [],
            "text": "",
            "raw_summary": "",
        }

    p = Path(checklist_path)
    if not p.exists():
        return {
            "path": str(p),
            "part_ids": [],
            "text": "",
            "raw_summary": f"Checklist nao encontrado: {p}",
        }

    part_ids: list[str] = []
    raw_text = ""

    if extract_reldev_avaria_part_ids is not None:
        try:
            part_ids = sorted(list(extract_reldev_avaria_part_ids(str(p))))
        except Exception:
            part_ids = []

    if extract_checklist_text is not None:
        try:
            raw_text = extract_checklist_text(str(p)) or ""
        except Exception:
            raw_text = ""

    checklist_text = "\n".join(part_ids) if part_ids else raw_text[:3000]

    return {
        "path": str(p),
        "part_ids": part_ids,
        "text": checklist_text,
        "raw_summary": raw_text[:3000],
    }


def _infer_expected_side_from_filename(name: str) -> str | None:
    n = (name or "").lower()
    left_tokens = ["esq", "esquer", "left", "lat_esq", "lateral_esquerda"]
    right_tokens = ["dir", "direit", "right", "lat_dir", "lateral_direita"]

    if any(t in n for t in left_tokens) and not any(t in n for t in right_tokens):
        return "esquerda"
    if any(t in n for t in right_tokens) and not any(t in n for t in left_tokens):
        return "direita"
    return None


def _list_images(fotos_dir: str) -> list[Path]:
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    base = Path(fotos_dir)
    if not base.exists() or not base.is_dir():
        raise FileNotFoundError(f"Pasta de fotos nao encontrada: {base}")

    amostra = {
        "FOTDEV_68",
        "FOTDEV_69",
        "FOTDEV_88",
        "FOTDEV_90",
        "FOTDEV_92",
    }

    files = [
        p for p in base.iterdir()
        if p.is_file()
        and p.suffix.lower() in exts
        and p.stem.upper() in amostra
    ]
    files.sort(key=lambda p: p.name.lower())

    print("Imagens selecionadas:")
    for f in files:
        print("-", f.name)
        
    return files

def _parse_json_or_fallback(raw: str) -> dict[str, Any]:
    cleaned = _clean_json_fences(raw)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
        return {"_raw": cleaned, "_parse_error": "JSON nao era objeto"}
    except Exception as e:
        return {"_raw": cleaned, "_parse_error": str(e)}


# =========================
# PROMPTS
# =========================
def build_prompt_baseline(checklist_text: str) -> str:
    checklist_block = f"\nCHECKLIST (somente contexto):\n{checklist_text}\n" if checklist_text else ""
    return f"""
Voce e um classificador de lado do veiculo em fotos de lataria.
{checklist_block}
TAREFA
- Classificar se a foto mostra LATERAL ESQUERDA, LATERAL DIREITA ou se esta INCERTO.
- Se possivel, identificar tambem a peca principal.
- Nao use o checklist para inventar o lado; a imagem prevalece.

RETORNE SOMENTE JSON:
{{
  "lado_veiculo": "esquerda|direita|incerto",
  "peca_principal": "porta traseira|porta dianteira|paralama|lateral|incerto",
  "confidence": 0.0,
  "justificativa": "breve"
}}
""".strip()


def build_prompt_visual_rules(checklist_text: str) -> str:
    checklist_block = f"\nCHECKLIST (somente contexto):\n{checklist_text}\n" if checklist_text else ""
    return f"""
Voce e um classificador especialista em orientacao lateral de veiculos.
{checklist_block}
OBJETIVO
- Dizer se a foto pertence a LATERAL ESQUERDA ou LATERAL DIREITA do veiculo.
- Se nao houver evidencias suficientes, responda INCERTO.

REGRAS VISUAIS IMPORTANTES
- ESQUERDA do veiculo = lado do motorista.
- DIREITA do veiculo = lado do passageiro.
- Use pistas visuais como:
  1) posicao da tampa de combustivel
  2) sentido do carro em foto lateral/panoramica
  3) desenho da lanterna dianteira/traseira em relacao ao lado visivel
  4) alinhamento de portas dianteira/traseira
  5) contexto de outras fotos do mesmo checklist, quando citado
- A foto pode estar rotacionada.
- Nao copie o checklist cegamente.

SAIDA JSON:
{{
  "lado_veiculo": "esquerda|direita|incerto",
  "peca_principal": "porta traseira esquerda|porta traseira direita|porta dianteira esquerda|porta dianteira direita|paralama esquerdo|paralama direito|lateral esquerda|lateral direita|incerto",
  "confidence": 0.0,
  "sinais_visuais": ["...", "..."],
  "justificativa": "breve"
}}
""".strip()


def build_prompt_checklist_guided(checklist_text: str) -> str:
    checklist_block = f"\nCHECKLIST DO CASO (itens marcados com avaria):\n{checklist_text}\n" if checklist_text else ""
    return f"""
Voce e um classificador de lado e peca para fotos de avarias automotivas.
{checklist_block}
OBJETIVO
- Classificar o lado correto da foto: esquerda, direita ou incerto.
- Identificar a peca principal mais provavel.

POLITICA DE DECISAO
1) Primeiro determine o lado pela IMAGEM.
2) Depois use o checklist apenas para DESEMPATE ou reforco de hipotese.
3) Se imagem e checklist entrarem em conflito, explique o conflito.
4) Se a imagem for ambigua, responda incerto em vez de chutar.

IMPORTANTE PARA ESTE TIPO DE CASO
- O checklist pode ter itens separados como:
  - porta_traseira_esquerda
  - porta_traseira_direita
  - lateral_esquerda
  - lateral_direita
- Entao sua resposta deve ser a mais especifica possivel.

SAIDA JSON:
{{
  "lado_veiculo": "esquerda|direita|incerto",
  "peca_principal": "porta traseira esquerda|porta traseira direita|porta dianteira esquerda|porta dianteira direita|lateral esquerda|lateral direita|incerto",
  "confidence": 0.0,
  "conflito_imagem_vs_checklist": true,
  "justificativa": "breve"
}}
""".strip()


def build_prompt_strict_door_side(checklist_text: str) -> str:
    checklist_block = f"\nCHECKLIST DO CASO:\n{checklist_text}\n" if checklist_text else ""
    return f"""
Voce e um classificador estrito para PORTAS e LATERAIS.
{checklist_block}
TAREFA
- Responder qual dos rótulos abaixo melhor descreve a foto.
- Nao inventar dano; estamos testando somente lado/peca.

ROTULOS PERMITIDOS
- porta_traseira_esquerda
- porta_traseira_direita
- porta_dianteira_esquerda
- porta_dianteira_direita
- lateral_esquerda
- lateral_direita
- incerto

REGRAS
- Priorize a imagem.
- Use o checklist apenas como prior fraco.
- Se a foto mostrar close de arranhao sem referencia espacial suficiente, use incerto.

SAIDA JSON:
{{
  "label": "porta_traseira_esquerda|porta_traseira_direita|porta_dianteira_esquerda|porta_dianteira_direita|lateral_esquerda|lateral_direita|incerto",
  "confidence": 0.0,
  "justificativa": "breve"
}}
""".strip()


PROMPT_VARIANTS = [
    PromptVariant(
        name="baseline",
        description="Prompt simples, so pedindo lado e peca.",
        prompt_builder=build_prompt_baseline,
    ),
    PromptVariant(
        name="visual_rules",
        description="Prompt com regras visuais explicitas.",
        prompt_builder=build_prompt_visual_rules,
    ),
    PromptVariant(
        name="checklist_guided",
        description="Prompt que primeiro usa a imagem e depois checklist como desempate.",
        prompt_builder=build_prompt_checklist_guided,
    ),
    PromptVariant(
        name="strict_door_side",
        description="Prompt com labels fechados para testar classificacao direta.",
        prompt_builder=build_prompt_strict_door_side,
    ),
]


# =========================
# EXECUCAO
# =========================
def run_single_prompt(*, image_path: str, prompt_text: str) -> dict[str, Any]:
    if call_llm_with_image is None:
        raise RuntimeError(
            "Nao foi possivel importar core.llm_gate_client.call_llm_with_image. "
            f"Erro de importacao: {IMPORT_ERROR}"
        )

    raw = call_llm_with_image(prompt=prompt_text, image_path=image_path)
    parsed = _parse_json_or_fallback(raw)
    parsed["_raw_response"] = raw
    return parsed


def run_experiment(fotos_dir: str, checklist_path: str | None, output_json: str) -> dict[str, Any]:
    checklist = _load_checklist_context(checklist_path)
    imagens = _list_images(fotos_dir)

    results: dict[str, Any] = {
        "fotos_dir": str(Path(fotos_dir).resolve()),
        "checklist": checklist,
        "root_found": str(ROOT_FOUND) if ROOT_FOUND else None,
        "prompt_variants": [
            {"name": p.name, "description": p.description}
            for p in PROMPT_VARIANTS
        ],
        "images": [],
    }

    for img in imagens:
        image_entry: dict[str, Any] = {
            "image_name": img.name,
            "image_path": str(img),
            "expected_side_from_filename": _infer_expected_side_from_filename(img.name),
            "results": {},
        }

        for variant in PROMPT_VARIANTS:
            prompt_text = variant.prompt_builder(checklist.get("text", ""))
            try:
                image_entry["results"][variant.name] = run_single_prompt(
                    image_path=str(img),
                    prompt_text=prompt_text,
                )
            except Exception as e:
                image_entry["results"][variant.name] = {
                    "_error": str(e),
                }

        results["images"].append(image_entry)

    out_path = Path(output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def main() -> int:
    print("entrou no main")
    parser = argparse.ArgumentParser(description="Teste de prompts para esquerda x direita.")
    parser.add_argument("--fotos", required=True, help="Pasta com as fotos do caso.")
    parser.add_argument("--checklist", required=False, default=None, help="PDF RELDEV/checklist do caso.")
    parser.add_argument(
        "--out",
        required=False,
        default="saida_testes_esquerda_direita.json",
        help="Arquivo JSON de saida.",
    )
    args = parser.parse_args()

    if call_llm_with_image is None:
        print("ERRO: dependencia do projeto nao encontrada.")
        print(f"ROOT_FOUND={ROOT_FOUND}")
        print(f"IMPORT_ERROR={IMPORT_ERROR}")
        print("Dica: rode este arquivo dentro da raiz do projeto ou exporte PROJECT_ROOT=/caminho/do/projeto")
        return 2

    print("vai entrar em results")
    results = run_experiment(
        fotos_dir=args.fotos,
        checklist_path=args.checklist,
        output_json=args.out,
    )

    print("saiu de results")
    total_images = len(results.get("images", []))
    print(f"OK: experimento concluido. Imagens avaliadas: {total_images}")
    print(f"Saida: {Path(args.out).resolve()}")
    print("Prompts testados:")
    for p in PROMPT_VARIANTS:
        print(f"- {p.name}: {p.description}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from agents.peritos.base_perito import BasePerito
from core.llm_gate_client import call_llm_with_image
from core.schemas import ExpertConsolidatedOutput, ServiceItem


@dataclass(frozen=True)
class ConfigPeritoEmblemas:
    caminho_lpu_xlsx: str


def _clean_json_fences(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    return raw


GRUPO_DIANTEIRO_PART_IDS = {"parachoque_dianteiro"}
GRUPO_TRASEIRO_PART_IDS = {"parachoque_traseiro", "tampa_porta_malas"}


def _posicao_por_part_id(part_id: Any) -> str | None:
    pid = str(part_id or "").strip().lower()
    if pid in GRUPO_DIANTEIRO_PART_IDS:
        return "dianteiro"
    if pid in GRUPO_TRASEIRO_PART_IDS:
        return "traseiro"
    return None


def build_emblemas_prompt(posicao: str, checklist_summary: str) -> str:
    posicao_label = "DIANTEIRA" if posicao == "dianteiro" else "TRASEIRA"
    return f"""
Voce e um PERITO TECNICO DE ITENS EXTERNOS (emblemas/logotipos do veiculo).

TAREFA
- A foto pertence a {posicao_label} do carro. Use esse contexto como dado de entrada.
- Nao classifique se a foto e dianteira ou traseira.
- Verificar se o EMBLEMA/LOGO da montadora (normalmente no centro) esta PRESENTE, FALTANDO ou DANIFICADO.
- Seja conservador: so marque como faltando/danificado se houver evidencia visual clara.
- Se a foto nao mostrar a regiao do emblema com nitidez suficiente, responda como nao_conclusivo.

DEFINICOES (use exatamente estas)
- "faltando": o emblema NAO esta presente onde deveria (ex.: espaco vazio, base sem logo, buraco/encaixe visivel).
- "danificado": o emblema esta presente, mas quebrado, trincado, solto, arrancando ou claramente avariado.
- Se estiver em duvida entre "faltando" e "danificado", escolha "nao_conclusivo".

CONTEXTO DO CHECKLIST (apenas como pista; evidencia visual prevalece):
{checklist_summary}

RETORNE SOMENTE JSON VALIDO (sem texto extra):
{{
  "status": "presente|faltando|danificado|nao_conclusivo",
  "justificativa": "breve descricao objetiva do que foi visto"
}}
""".strip()


def _rank_status(status: str) -> int:
    s = (status or "").strip().lower()
    return {
        "faltando": 3,
        "danificado": 2,
        "presente": 1,
        "nao_conclusivo": 0,
    }.get(s, 0)


class PeritoEmblemas(BasePerito):
    def __init__(self, config: ConfigPeritoEmblemas):
        self.config = config

    def run(self, image_paths: list[str], **kwargs) -> dict[str, Any]:
        if not image_paths:
            return {"erro": "nenhuma imagem elegivel"}

        checklist_summary = kwargs.get("checklist_summary", "") or ""
        imagens_usadas = kwargs.get("imagens_usadas") or []

        progress_enabled = os.getenv("AGENTE_PROGRESS", "1").strip().lower() not in ("0", "false", "no")
        total = len(image_paths or [])

        grupo_dianteiro: list[dict[str, Any]] = []
        grupo_traseiro: list[dict[str, Any]] = []

        for idx, img_path in enumerate(image_paths):
            meta = (
                imagens_usadas[idx]
                if idx < len(imagens_usadas) and isinstance(imagens_usadas[idx], dict)
                else {}
            )
            part_id = str(meta.get("part_id") or "").strip().lower()
            posicao = _posicao_por_part_id(part_id)
            item = {
                "path": img_path,
                "part_id": part_id,
                "index": idx + 1,
            }

            if posicao == "dianteiro":
                grupo_dianteiro.append(item)
            elif posicao == "traseiro":
                grupo_traseiro.append(item)

        avaliados: list[dict[str, Any]] = []

        def _avaliar_grupo(posicao: str, grupo: list[dict[str, Any]]) -> None:
            if not grupo:
                return

            prompt = build_emblemas_prompt(posicao, checklist_summary)

            for item in grupo:
                img_path = str(item.get("path") or "")

                if progress_enabled:
                    try:
                        name = Path(img_path).stem
                    except Exception:
                        name = img_path
                    print(
                        f"[perito_emblemas] {item.get('index')}/{total} {posicao} {name}",
                        flush=True,
                    )

                try:
                    raw = call_llm_with_image(prompt=prompt, image_path=img_path)
                    raw = _clean_json_fences(raw)
                    d = json.loads(raw)
                except Exception:
                    continue

                status = str(d.get("status") or "").strip().lower()
                justificativa = str(d.get("justificativa") or "").strip() or None

                if status not in {"presente", "faltando", "danificado", "nao_conclusivo"}:
                    status = "nao_conclusivo"

                avaliados.append(
                    {
                        "posicao": posicao,
                        "status": status,
                        "justificativa": justificativa,
                        "path": img_path,
                        "part_id": item.get("part_id"),
                    }
                )

        _avaliar_grupo("dianteiro", grupo_dianteiro)
        _avaliar_grupo("traseiro", grupo_traseiro)

        if not avaliados:
            return {"erro": "falha emblemas: nenhuma avaliacao valida com part_id dianteiro/traseiro"}

        def _best_for(posicao: str) -> dict[str, Any] | None:
            return max(
                (a for a in avaliados if a.get("posicao") == posicao),
                default=None,
                key=lambda x: _rank_status(str(x.get("status"))),
            )

        best_dianteiro = _best_for("dianteiro")
        best_traseiro = _best_for("traseiro")

        servicos: list[ServiceItem] = []
        pecas_a_cotar: list[dict[str, Any]] = []
        fotos_analisadas: list[str] = []
        justificativas: list[str] = []

        def _add_issue(*, pos: str, status: str, path: str, just: str | None) -> None:
            nonlocal servicos, pecas_a_cotar, fotos_analisadas, justificativas

            pos_label = "dianteiro" if pos == "dianteiro" else "traseiro"

            if status == "faltando":
                servicos.append(
                    ServiceItem(
                        descricao=f"REVISAR - Emblema {pos_label} faltando (reposicao)",
                        preco=0.0,
                    )
                )
                pecas_a_cotar.append(
                    {
                        "descricao": f"emblema {pos_label}",
                        "quantidade": 1,
                        "observacao": "Item faltante detectado visualmente; revisar cobranca e cotar peca se aplicavel.",
                    }
                )

            elif status == "danificado":
                servicos.append(
                    ServiceItem(
                        descricao=(
                            f"REVISAR - Emblema {pos_label} danificado/solto (inspecionar e substituir se necessario)"
                        ),
                        preco=0.0,
                    )
                )
                pecas_a_cotar.append(
                    {
                        "descricao": f"emblema {pos_label}",
                        "quantidade": 1,
                        "observacao": (
                            "Possivel dano/soltura do emblema detectado visualmente; "
                            "inspecionar e cotar substituicao se aplicavel."
                        ),
                    }
                )

            fotos_analisadas.append(path)
            if just:
                justificativas.append(f"{pos_label}: {just}")

        for best in (best_dianteiro, best_traseiro):
            if not best:
                continue

            pos = str(best.get("posicao"))
            status = str(best.get("status"))
            path = str(best.get("path"))
            just = best.get("justificativa")

            if status in {"faltando", "danificado"}:
                _add_issue(pos=pos, status=status, path=path, just=just)
            else:
                fotos_analisadas.append(path)
                if just:
                    justificativas.append(f"{pos}: {just}")

        any_issue = bool(servicos)
        justificativa_final = "; ".join([j for j in justificativas if j]) or None

        out = ExpertConsolidatedOutput(
            nivel_dano="reposicao" if any_issue else "sem_dano",
            peca="emblemas (dianteiro/traseiro)",
            servicos=servicos,
            preco_total=0.0,
            justificativa=justificativa_final,
            fotos_analisadas=fotos_analisadas,
        ).model_dump()

        if any_issue:
            out["force_include"] = True
            out["needs_human_review"] = True
            out["pecas_a_cotar"] = pecas_a_cotar

        return out

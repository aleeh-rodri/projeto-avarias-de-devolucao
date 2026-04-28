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


def build_emblemas_prompt(checklist_summary: str = "") -> str:
    return f"""
Você é um PERITO TÉCNICO DE ITENS EXTERNOS (emblemas/logotipos do veículo).

TAREFA
- Identificar se a foto mostra a DIANTEIRA ou a TRASEIRA do carro.
- Verificar se o EMBLEMA/LOGO da montadora (normalmente no centro) está PRESENTE, FALTANDO ou DANIFICADO.
- Seja conservador: só marque como faltando/danificado se houver evidência visual clara.
- Se a foto não mostrar a região do emblema com nitidez suficiente, responda como nao_conclusivo.

DEFINIÇÕES (use exatamente estas)
- "faltando": o emblema NÃO está presente onde deveria (ex.: espaço vazio, base sem logo, buraco/encaixe visível).
- "danificado": o emblema está presente, mas quebrado, trincado, solto, arrancando ou claramente avariado.
- Se estiver em dúvida entre "faltando" e "danificado", escolha "nao_conclusivo".

CONTEXTO DO CHECKLIST (apenas como pista; evidência visual prevalece):
{checklist_summary}

RETORNE SOMENTE JSON VÁLIDO (sem texto extra):
{{
  "posicao": "dianteiro|traseiro|nao_identificavel",
  "status": "presente|faltando|danificado|nao_conclusivo",
  "justificativa": "breve descrição objetiva do que foi visto"
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
        prompt = build_emblemas_prompt(checklist_summary)

        progress_enabled = os.getenv("AGENTE_PROGRESS", "1").strip().lower() not in ("0", "false", "no")
        total = len(image_paths or [])

        avaliados: list[dict[str, Any]] = []
        for idx, img_path in enumerate(image_paths, start=1):
            if progress_enabled:
                try:
                    name = Path(img_path).stem
                except Exception:
                    name = str(img_path)
                print(f"[perito_emblemas] {idx}/{total} {name}", flush=True)
            try:
                raw = call_llm_with_image(prompt=prompt, image_path=img_path)
                raw = _clean_json_fences(raw)
                d = json.loads(raw)
            except Exception:
                continue

            posicao = str(d.get("posicao") or "").strip().lower()
            status = str(d.get("status") or "").strip().lower()
            justificativa = str(d.get("justificativa") or "").strip() or None

            if posicao not in {"dianteiro", "traseiro", "nao_identificavel"}:
                posicao = "nao_identificavel"
            if status not in {"presente", "faltando", "danificado", "nao_conclusivo"}:
                status = "nao_conclusivo"

            avaliados.append(
                {
                    "posicao": posicao,
                    "status": status,
                    "justificativa": justificativa,
                    "path": img_path,
                }
            )

        if not avaliados:
            return {"erro": "falha emblemas: nenhuma avaliacao valida"}

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
                        descricao=f"REVISAR — Emblema {pos_label} faltando (reposição)",
                        preco=0.0,
                    )
                )
                pecas_a_cotar.append(
                    {
                        "descricao": f"emblema {pos_label}",
                        "quantidade": 1,
                        "observacao": "Item faltante detectado visualmente; revisar cobrança e cotar peça se aplicável.",
                    }
                )

            elif status == "danificado":
                servicos.append(
                    ServiceItem(
                        descricao=(
                            f"REVISAR — Emblema {pos_label} danificado/solto (inspecionar e substituir se necessário)"
                        ),
                        preco=0.0,
                    )
                )
                pecas_a_cotar.append(
                    {
                        "descricao": f"emblema {pos_label}",
                        "quantidade": 1,
                        "observacao": (
                            "Possível dano/soltura do emblema detectado visualmente; "
                            "inspecionar e cotar substituição se aplicável."
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

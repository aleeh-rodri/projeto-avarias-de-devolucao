from __future__ import annotations

import json
import os
from typing import Any

from core.llm_gate_client import call_llm_with_image
from core.schemas import QualityAssessment, QualityOutput, TriageImage


def build_quality_prompt(part_id: str, checklist_damage_reported: bool | None = None) -> str:
    # Keep part context for diagnostic output, but approval is based on image usability.
    expected_label = part_id
    if (part_id or "").strip().lower().startswith("retrovisor_"):
        expected_label = "retrovisor (qualquer lado)"

    pid = (part_id or "").strip().lower()
    if pid == "parachoque_dianteiro":
        expected_label = "dianteira do carro (para-choque/grade/logo frontal/farois)"
    elif pid == "parachoque_traseiro":
        expected_label = "traseira do carro (para-choque/placa traseira/lanternas)"

    checklist_hint = ""
    if checklist_damage_reported is True:
        checklist_hint = (
            "\nCONTEXTO EXTRA: o checklist reportou avaria para esta peca. "
            "Se a foto for minimamente utilizavel para analise/orcamento, mantenha qualidade media ou alta.\n"
        )

    return f"""
Voce e um AGENTE DE QUALIDADE DE IMAGENS AUTOMOTIVAS.

Sua tarefa principal e avaliar se a imagem tem qualidade visual suficiente para uma pericia tecnica.
A peca esperada ja veio de uma planilha externa e foi validada em uma etapa anterior.

Peca esperada: {expected_label}
{checklist_hint}

Criterios de avaliacao:
1. Qualidade da imagem:
   - "baixa": muito escura, borrada, reflexos excessivos que impedem ver danos, ou muito longe.
   - "media": visivel, mas com algumas limitacoes, como leve reflexo ou angulo nao ideal.
   - "alta": nitida, boa iluminacao e angulo util para pericia.

2. Correspondencia:
   - Informe `corresponde_a_peca` como diagnostico visual.
   - Nao reprove a foto apenas por duvida de correspondencia com a peca.
   - Divergencia forte de peca e tratada pela triagem via `needs_human_review`, nao por este agente.

3. Decisao de aprovacao:
   - Aprovada se a qualidade for "media" ou "alta".
   - Reprovada se a qualidade for "baixa".

Formato de saida:
RETORNE SOMENTE JSON VALIDO (sem texto extra e sem blocos ```):

{{
  "aprovada": true|false,
  "motivo": "breve explicacao se reprovada",
  "qualidade_imagem": "baixa|media|alta",
  "corresponde_a_peca": true|false
}}
"""


def _clean_json_fences(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    return raw


def _normalize_quality(value: object) -> str:
    qualidade = str(value or "").strip().lower()
    if qualidade not in {"baixa", "media", "alta"}:
        return "baixa"
    return qualidade


def run_quality_check(case_id: str, triage_images: list[TriageImage], output_dir: str) -> dict[str, Any]:
    """Avalia a qualidade das imagens triadas."""
    assessments: list[QualityAssessment] = []

    progress_enabled = os.getenv("AGENTE_PROGRESS", "1").strip().lower() not in ("0", "false", "no")
    total = len(triage_images or [])

    for idx, img in enumerate(triage_images, start=1):
        if progress_enabled:
            print(f"[quality] {idx}/{total} {img.image_id} ({img.part_id})", flush=True)

        prompt = build_quality_prompt(img.part_id, img.checklist_damage_reported)
        raw = _clean_json_fences(call_llm_with_image(prompt=prompt, image_path=img.photo_path))

        try:
            result_dict = json.loads(raw)

            qualidade_imagem = _normalize_quality(result_dict.get("qualidade_imagem"))
            corresponde_a_peca = bool(result_dict.get("corresponde_a_peca"))
            motivo = result_dict.get("motivo")

            # Compatibility disagreement is handled by triage (`needs_human_review`).
            # Quality approval only decides whether the image is usable for an expert.
            aprovada = qualidade_imagem in {"media", "alta"}

            if aprovada and (motivo or "").strip() == "":
                motivo = None

            assessment = QualityAssessment(
                image_id=img.image_id,
                aprovada=aprovada,
                motivo=motivo,
                qualidade_imagem=qualidade_imagem,
                corresponde_a_peca=corresponde_a_peca,
            )
            assessments.append(assessment)
        except Exception as e:
            print(f"Erro ao avaliar qualidade da imagem {img.image_id}: {e}")
            # Se o checklist reportou avaria, nao descarte a foto por falha de parsing do LLM.
            if img.checklist_damage_reported is True:
                assessments.append(
                    QualityAssessment(
                        image_id=img.image_id,
                        aprovada=True,
                        motivo=(
                            "Falha ao interpretar JSON do quality check; "
                            "checklist reportou avaria; mantendo para analise/orcamento."
                        ),
                        qualidade_imagem="media",
                        corresponde_a_peca=False,
                    )
                )
            continue

    output = QualityOutput(case_id=case_id, assessments=assessments)

    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "quality_check.json"), "w", encoding="utf-8") as f:
        json.dump(output.model_dump(), f, ensure_ascii=False, indent=2)

    return output.model_dump()

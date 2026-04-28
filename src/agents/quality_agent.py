from __future__ import annotations

import json
import os
from typing import Any

from core.llm_gate_client import call_llm_with_image
from core.schemas import QualityAssessment, QualityOutput, TriageImage


def build_quality_prompt(part_id: str, checklist_damage_reported: bool | None = None) -> str:
    # Retrovisores: o lado (esquerdo/direito) é fácil de errar na triagem.
    # Para qualidade, o que importa é se a foto mostra um retrovisor com nitidez suficiente.
    expected_label = part_id
    if (part_id or "").strip().lower().startswith("retrovisor_"):
        expected_label = "retrovisor (qualquer lado)"

    # Para-choques: fotos da frente/traseira costumam mostrar grade/logo/placa/lanternas,
    # e o LLM pode errar o "mismatch" se o rótulo for literal demais.
    # Aqui ampliamos o rótulo esperado para reduzir falso negativo de correspondência.
    pid = (part_id or "").strip().lower()
    if pid == "parachoque_dianteiro":
        expected_label = "dianteira do carro (para-choque/grade/logo frontal/faróis)"
    elif pid == "parachoque_traseiro":
        expected_label = "traseira do carro (para-choque/placa traseira/lanternas)"

    checklist_hint = ""
    if checklist_damage_reported is True:
        checklist_hint = (
            "\nCONTEXTO EXTRA: o CHECKLIST reportou avaria para esta peça. "
            "Se a foto for minimamente utilizável para análise/orçamento, NÃO reprove por excesso de rigor.\n"
        )

    return f"""
Você é um AGENTE DE QUALIDADE DE IMAGENS AUTOMOTIVAS.

Sua tarefa é avaliar se a imagem fornecida tem qualidade suficiente para uma perícia técnica e se ela realmente mostra a peça indicada.

Peça esperada: {expected_label}
{checklist_hint}

Critérios de Avaliação:
1. Qualidade da Imagem:
   - "baixa": Muito escura, borrada, reflexos excessivos que impedem ver danos, ou muito longe.
   - "media": Visível, mas com algumas limitações (ex: leve reflexo, ângulo não ideal).
   - "alta": Nitidez perfeita, boa iluminação, ângulo ideal para perícia.

2. Correspondência:
    - A imagem realmente mostra a peça "{expected_label}"?

3. Decisão (Aprovação):
     - Regra padrão: Aprovada se Qualidade for "media" ou "alta" E corresponder à peça.
     - EXCEÇÃO (quando o checklist reportou avaria):
             - Aprovada se a foto for utilizável para análise/orçamento (qualidade "media" ou "alta"),
                 mesmo que haja dúvida de correspondência exata da peça.
             - Reprove apenas se a foto for claramente inútil (qualidade "baixa" por estar ilegível) OU não mostrar nada relacionado.

Formato de saída:
RETORNE SOMENTE JSON VÁLIDO (sem texto extra e sem blocos ```):

{{
  "aprovada": true|false,
  "motivo": "breve explicação se reprovada",
  "qualidade_imagem": "baixa|media|alta",
  "corresponde_a_peca": true|false
}}
"""


def run_quality_check(case_id: str, triage_images: list[TriageImage], output_dir: str) -> dict[str, Any]:
    """Avalia a qualidade das imagens triadas."""
    assessments: list[QualityAssessment] = []

    progress_enabled = os.getenv("AGENTE_PROGRESS", "1").strip().lower() not in ("0", "false", "no")
    total = len(triage_images or [])

    for idx, img in enumerate(triage_images, start=1):
        if progress_enabled:
            print(f"[quality] {idx}/{total} {img.image_id} ({img.part_id})", flush=True)
        prompt = build_quality_prompt(img.part_id, img.checklist_damage_reported)
        raw = call_llm_with_image(prompt=prompt, image_path=img.photo_path)

        # limpa possíveis fences ```json
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.replace("```json", "").replace("```", "").strip()

        try:
            result_dict = json.loads(raw)

            aprovada = bool(result_dict["aprovada"])
            qualidade_imagem = result_dict["qualidade_imagem"]
            corresponde_a_peca = bool(result_dict["corresponde_a_peca"])
            motivo = result_dict.get("motivo")

            # Flexibilização: se checklist sinalizou avaria, não deixe a qualidade derrubar
            # fotos potencialmente úteis para orçamento por "mismatch" de peça.
            if img.checklist_damage_reported is True:
                if qualidade_imagem in {"media", "alta"}:
                    aprovada = True
                    if (motivo or "").strip() == "":
                        motivo = "Checklist reportou avaria; mantendo para análise/orçamento."

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
            # Se o checklist reportou avaria, não descarte a foto por falha de parsing do LLM.
            if img.checklist_damage_reported is True:
                assessments.append(
                    QualityAssessment(
                        image_id=img.image_id,
                        aprovada=True,
                        motivo="Falha ao interpretar JSON do quality check; checklist reportou avaria; mantendo para análise/orçamento.",
                        qualidade_imagem="baixa",
                        corresponde_a_peca=False,
                    )
                )
            continue

    output = QualityOutput(case_id=case_id, assessments=assessments)

    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "quality_check.json"), "w", encoding="utf-8") as f:
        json.dump(output.model_dump(), f, ensure_ascii=False, indent=2)

    return output.model_dump()

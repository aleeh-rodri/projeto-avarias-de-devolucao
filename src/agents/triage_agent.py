from __future__ import annotations

import json
import os

from core.config import config as global_config
from core.llm_gate_client import call_llm_with_image
from core.schemas import PART_IDS, TriageImage, TriageOutput
from core.pdf_utils import extract_reldev_avaria_part_ids, extract_checklist_text


def build_prompt(checklist_text: str = "") -> str:
    checklist_context = ""
    if checklist_text:
        checklist_context = f"\nCONTEXTO DO CHECKLIST (peças com AVARIA):\n{checklist_text}\n"

    return f"""
Você é um agente de TRIAGEM DE FOTOS DE VEÍCULOS.
{checklist_context}
Sua tarefa:

Regras:

    IMPORTANTE (orientação e viés do checklist)
    - A foto pode estar ROTACIONADA (90/180/270). Identifique a peça mesmo que esteja "de lado".
    - O CONTEXTO DO CHECKLIST é apenas um sinal fraco: use-o SOMENTE para definir `checklist_damage_reported`.
    - Não escolha um `part_id` só porque aparece no checklist; o `part_id` deve vir do que está visível na imagem.

DICAS PARA DIFERENCIAR (importante)
- parachoque_dianteiro: presença de grade, faróis, logo frontal, entrada de ar frontal.
- parachoque_traseiro: presença de lanternas traseiras, placa traseira, sensor/engate traseiro, escapamento.
- capô: superfície externa do capô (chapa) fechada; se a foto mostra o motor/compartimento com capô aberto, ainda é "capo", mas reduza a confiança se não dá para ver a chapa externa.
- retrovisores: peça pequena lateral externa; não confundir com fotos do motor/compartimento.
    - IMPORTANTE: quando a foto mostrar claramente QUAL LADO do carro, escolha corretamente:
        - retrovisor_esquerdo = lado do motorista (esquerda do veículo)
        - retrovisor_direito = lado do passageiro (direita do veículo)
    - Se você NÃO conseguir inferir o lado com confiança, escolha o mais provável, mas reduza a confidence.

Lista de part_id válidos:
{PART_IDS}

Tipos de view aceitos:
- detalhe (close-up)
- media (medium)
- longe (far)
- panoramica (overview)
- interior (interior)

Formato de saída:
RETORNE SOMENTE JSON VÁLIDO (sem texto extra e sem blocos ```):

{{
  \"part_id\": \"...\",
  \"view\": \"...\",
  \"confidence\": 0.0,
  \"checklist_damage_reported\": true/false
}}
"""


def build_retrovisor_lado_prompt() -> str:
    return """
Você é um classificador de LADO DE RETROVISOR em fotos de veículo.

TAREFA
- Determinar se o retrovisor da foto é ESQUERDO (lado do motorista) ou DIREITO (lado do passageiro).
- Se não for possível determinar com confiança, responda como "incerto".

REGRAS
- Responda SOMENTE JSON válido (sem texto extra e sem blocos ```).
- Não invente: se a foto estiver ambígua, use "incerto".

FORMATO:
{
  "lado": "esquerdo|direito|incerto",
  "confidence": 0.0
}
"""


def _clean_json_fences(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    return raw


def _infer_retrovisor_side(image_path: str) -> tuple[str, float]:
    """Retorna (lado, confidence) onde lado ∈ {esquerdo, direito, incerto}."""
    raw = call_llm_with_image(prompt=build_retrovisor_lado_prompt(), image_path=image_path)
    raw = _clean_json_fences(raw)
    try:
        d = json.loads(raw)
    except Exception:
        return ("incerto", 0.0)

    lado = str(d.get("lado", "") or "").strip().lower()
    conf = d.get("confidence", 0.0)
    try:
        conf_f = float(conf)
    except Exception:
        conf_f = 0.0

    if lado not in {"esquerdo", "direito", "incerto"}:
        lado = "incerto"
    conf_f = max(0.0, min(conf_f, 1.0))
    return (lado, conf_f)


def run_triage(case_id: str, fotos_dir: str, output_dir: str, checklist_path: str | None = None) -> dict:
    """Roda triagem em todas as imagens de `fotos_dir` e salva `triage.json` em `output_dir`."""
    images_out: list[TriageImage] = []
    
    checklist_text = ""
    checklist_part_ids: set[str] | None = None
    if checklist_path and os.path.exists(checklist_path):
        # Extração determinística do RELDEV: evita falsos positivos do modelo.
        try:
            checklist_part_ids = extract_reldev_avaria_part_ids(checklist_path)
        except Exception:
            checklist_part_ids = None

        # Contexto enxuto para o LLM: só lista peças marcadas como avaria
        if checklist_part_ids:
            checklist_text = "\n".join(sorted(checklist_part_ids))
        else:
            # fallback compatível: mantém o texto bruto (normalizado) apenas para logging
            checklist_text = extract_checklist_text(checklist_path)

    progress_enabled = os.getenv("AGENTE_PROGRESS", "1").strip().lower() not in ("0", "false", "no")

    all_files = [f for f in os.listdir(fotos_dir) if isinstance(f, str)]
    photo_files = [
        f
        for f in all_files
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
        and "fotdev" in f.lower()
    ]
    total = len(photo_files)

    for idx, fname in enumerate(photo_files, start=1):

        image_path = os.path.join(fotos_dir, fname)
        image_id = os.path.splitext(fname)[0]

        if progress_enabled:
            print(f"[triage] {idx}/{total} {image_id}", flush=True)

        raw = call_llm_with_image(prompt=build_prompt(checklist_text), image_path=image_path)
        raw = _clean_json_fences(raw)

        try:
            result_dict = json.loads(raw)
            part_id = str(result_dict["part_id"])
            view = str(result_dict["view"])
            base_confidence = float(result_dict["confidence"])

            # Retrovisor: refina o lado com uma micro-inferência dedicada.
            # Importante: não derrubar a confiança abaixo do threshold do orquestrador,
            # senão as fotos de retrovisor são descartadas antes do perito.
            if part_id.strip().lower().startswith("retrovisor_"):
                lado, lado_conf = _infer_retrovisor_side(image_path)
                if lado in {"esquerdo", "direito"}:
                    part_id = f"retrovisor_{lado}"
                    # Usa a confiança do classificador de lado como limite superior,
                    # mas mantém ao menos a confiança mínima do pipeline.
                    cap = lado_conf if lado_conf > 0 else base_confidence
                    base_confidence = min(base_confidence, max(cap, global_config.CONFIANCA_MINIMA))
                else:
                    # incerto: mantém o part_id original, mas reduz levemente a confiança
                    # sem cair abaixo da confiança mínima.
                    base_confidence = max(global_config.CONFIANCA_MINIMA, base_confidence * 0.85)

            # Define checklist_damage_reported de forma determinística quando possível.
            if checklist_part_ids is not None and checklist_part_ids:
                checklist_damage_reported = part_id in checklist_part_ids
            else:
                checklist_damage_reported = result_dict.get("checklist_damage_reported")

            triage_img = TriageImage(
                image_id=image_id,
                photo_path=image_path,
                part_id=part_id,
                view=view,
                confidence=base_confidence,
                checklist_damage_reported=checklist_damage_reported,
            )
            images_out.append(triage_img)
        except Exception as e:
            print(f"Erro ao processar imagem {fname}: {e}")
            continue

    # Pós-processamento: se houver múltiplos retrovisores e todos ficaram no mesmo lado,
    # força diversidade (E/D) com confiança reduzida, evitando colapso do pipeline.
    retro_idxs = [i for i, img in enumerate(images_out) if (img.part_id or "").startswith("retrovisor_")]
    if len(retro_idxs) >= 2:
        sides = {str(images_out[i].part_id).strip().lower() for i in retro_idxs}
        if sides.issubset({"retrovisor_esquerdo"}) or sides.issubset({"retrovisor_direito"}):
            # Ordena por confiança desc para manter o "mais provável" como está.
            ordered = sorted(retro_idxs, key=lambda i: float(images_out[i].confidence), reverse=True)
            first_side = str(images_out[ordered[0]].part_id).strip().lower()
            if first_side not in {"retrovisor_esquerdo", "retrovisor_direito"}:
                first_side = "retrovisor_direito"
            opposite = "retrovisor_esquerdo" if first_side == "retrovisor_direito" else "retrovisor_direito"

            for j, idx in enumerate(ordered[1:], start=1):
                forced_side = opposite if (j % 2 == 1) else first_side
                current = images_out[idx]
                # re-cria o objeto para manter compatibilidade com pydantic/dataclass
                forced_conf = max(
                    global_config.CONFIANCA_MINIMA,
                    min(float(current.confidence), global_config.CONFIANCA_MINIMA + 0.01),
                )
                images_out[idx] = TriageImage(
                    image_id=current.image_id,
                    photo_path=current.photo_path,
                    part_id=forced_side,
                    view=current.view,
                    confidence=forced_conf,
                    checklist_damage_reported=current.checklist_damage_reported,
                )

    output = TriageOutput(
        case_id=case_id, 
        images=images_out,
        checklist_summary=checklist_text[:3000] if checklist_text else None
    )

    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "triage.json"), "w", encoding="utf-8") as f:
        json.dump(output.model_dump(), f, ensure_ascii=False, indent=2)

    return output.model_dump()

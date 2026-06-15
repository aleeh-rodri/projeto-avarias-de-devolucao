from __future__ import annotations

import json
import os
from dataclasses import dataclass

from core.config import config as global_config
from core.llm_gate_client import call_llm_with_image, call_llm_with_reference_images
from core.schemas import PART_IDS, TriageImage, TriageOutput
from core.pdf_utils import extract_reldev_avaria_part_ids, extract_checklist_text


LATERALIZABLE_BASE_PART_IDS = {
    "porta_dianteira",
    "porta_traseira",
    "paralama_dianteiro",
    "paralama_traseiro",
}

try:
    LATERAL_SIDE_SCORE_MIN = float(
        os.getenv(
            "AGENTE_TRIAGE_LATERAL_SIDE_MIN_SCORE",
            os.getenv("AGENTE_TRIAGE_LATERAL_SIDE_MIN_CONF", "0.72"),
        )
    )
except ValueError:
    LATERAL_SIDE_SCORE_MIN = 0.72


@dataclass(frozen=True)
class LateralReferencePaths:
    right: str
    left: str


@dataclass(frozen=True)
class LateralReferenceMatch:
    similarity_score: float
    confidence: float


@dataclass(frozen=True)
class LateralSideDecision:
    lado: str
    confidence: float
    reason: str
    right_match: LateralReferenceMatch | None
    left_match: LateralReferenceMatch | None
    right_error: str | None = None
    left_error: str | None = None


def _base_lateral_part_id(part_id: str) -> str | None:
    pid = (part_id or "").strip().lower()
    if pid in LATERALIZABLE_BASE_PART_IDS:
        return pid

    for suffix in ("_direita", "_esquerda", "_direito", "_esquerdo"):
        if not pid.endswith(suffix):
            continue
        base = pid[: -len(suffix)]
        if base in LATERALIZABLE_BASE_PART_IDS:
            return base

    return None


TRIAGE_BASE_PART_IDS = [
    part_id
    for part_id in PART_IDS
    if _base_lateral_part_id(part_id) is None or part_id in LATERALIZABLE_BASE_PART_IDS
]


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

Para portas e para-lamas, escolha SOMENTE a peca base nesta etapa:
- porta_dianteira
- porta_traseira
- paralama_dianteiro
- paralama_traseiro
O lado dessas pecas sera refinado depois por matching visual.

Lista de part_id validos para a triagem base:
{TRIAGE_BASE_PART_IDS}

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

def build_lateral_side_prompt(base_part_id: str, reference_label: str) -> str:
    return f"""
Você é um verificador visual conservador de correspondência entre uma foto principal e uma grade de miniaturas.

ENTRADA
Você receberá 2 imagens, nesta ordem:
1) Foto principal.
2) Imagem composta com miniaturas de referência do veículo.

PEÇA BASE JÁ IDENTIFICADA PELA TRIAGEM
{base_part_id}

OBJETIVO
Determinar se a foto principal aparece dentro de alguma miniatura da imagem de referência.

IMPORTANTE
Esta tarefa NÃO é classificar o lado do veículo.
Esta tarefa NÃO é dizer se a peça é parecida.
Esta tarefa NÃO é dizer se a foto pertence à mesma região do carro.
Esta tarefa é verificar se existe uma miniatura que corresponde à MESMA FOTO

PROCESSO OBRIGATÓRIO
1) Identifique visualmente a foto principal.
2) Identifique as miniaturas da imagem de referência.
3) Compare a foto principal contra cada miniatura individualmente.
4) Procure correspondência da MESMA FOTO, não apenas da mesma peça ou mesma região.
5) Atribua o similarity_score com base na melhor miniatura encontrada.
6) Se nenhuma miniatura tiver evidência clara de ser a mesma foto, retorne score baixo.

REGRA CENTRAL DE CORRESPONDÊNCIA
Considere correspondência somente quando a miniatura parecer representar a mesma imagem da foto principal.

COMO COMPARAR
Compare a foto principal contra cada miniatura individualmente.

Considere como evidência forte:
- mesmo enquadramento geral
- mesma perspectiva da câmera
- mesma posição da peça dentro da imagem
- mesmos danos, riscos, marcas ou sujeiras
- mesmos reflexos
- mesmas sombras
- mesmo fundo ou entorno visível
- mesmos objetos ao redor
- mesma proporção entre peça, carro e fundo

VARIAÇÕES ACEITAS
- escala menor na miniatura
- compressão diferente
- leve diferença de brilho ou contraste
- pequeno corte nas bordas
- pequena perda de nitidez

NÃO CONSIDERE MATCH
- mesma peça, mas outra foto
- mesmo lado do carro, mas outro ângulo
- mesmo tipo de dano em outro enquadramento
- mesma cor de veículo
- mesmo fundo, mas posição diferente da peça
- peça visualmente parecida
- match baseado em inferência de lado do veículo
- match baseado apenas na peça base informada

ESCALA DE similarity_score
- 0.95 a 1.00: praticamente a mesma imagem; diferença mínima de escala, compressão ou crop
- 0.85 a 0.94: match muito forte; aparenta ser a mesma foto com pequenas variações
- 0.72 a 0.84: possível match; há vários elementos iguais, mas ainda existe alguma incerteza
- 0.50 a 0.71: imagem parecida, mas evidência insuficiente de ser a mesma foto
- 0.00 a 0.49: não corresponde

ESCALA DE confidence
- 0.90 a 1.00: avaliação muito segura
- 0.75 a 0.89: avaliação segura
- 0.60 a 0.74: avaliação moderada
- 0.00 a 0.59: avaliação incerta

REGRA DE CONSERVADORISMO
Se não houver evidência clara de mesma foto, use similarity_score abaixo de 0.72.
Se houver apenas semelhança de peça, lateral, cor, dano ou região do veículo, use similarity_score abaixo de 0.65.
Se estiver em dúvida entre "match" e "parecido", trate como "parecido" e reduza o score.
Não aumente o score por saber que a referência é da lateral {reference_label.upper()}.
Não tente compensar ausência de evidência visual usando contexto.

SAÍDA
Retorne SOMENTE JSON válido, sem texto extra e sem blocos ```.

FORMATO:
{{
  "similarity_score": 0.0,
  "confidence": 0.0
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


def _build_lateralized_part_id(base_part_id: str, lado: str) -> str:
    lado_norm = (lado or "").strip().lower()
    if base_part_id.startswith("porta_"):
        suffix = "direita" if lado_norm == "direita" else "esquerda"
    else:
        suffix = "direito" if lado_norm == "direita" else "esquerdo"
    return f"{base_part_id}_{suffix}"


def _clamp01(value: object) -> float:
    try:
        value_f = float(value)
    except Exception:
        return 0.0
    return max(0.0, min(value_f, 1.0))


def _infer_lateral_reference_match(
    image_path: str,
    base_part_id: str,
    reference_path: str,
    reference_label: str,
) -> LateralReferenceMatch:
    raw = call_llm_with_reference_images(
        prompt=build_lateral_side_prompt(base_part_id, reference_label),
        main_image_path=image_path,
        reference_image_path=reference_path,
    )
    raw = _clean_json_fences(raw)
    d = json.loads(raw)
    return LateralReferenceMatch(
        similarity_score=_clamp01(d.get("similarity_score", 0.0)),
        confidence=_clamp01(d.get("confidence", 0.0)),
    )


def _infer_lateral_reference_match_safely(
    image_path: str,
    base_part_id: str,
    reference_path: str,
    reference_label: str,
) -> tuple[LateralReferenceMatch | None, str | None]:
    try:
        match = _infer_lateral_reference_match(
            image_path=image_path,
            base_part_id=base_part_id,
            reference_path=reference_path,
            reference_label=reference_label,
        )
    except Exception as exc:
        return (None, f"{type(exc).__name__}: {exc}")
    return (match, None)


def _decide_lateral_side(
    right_match: LateralReferenceMatch | None,
    left_match: LateralReferenceMatch | None,
    right_error: str | None = None,
    left_error: str | None = None,
) -> LateralSideDecision:
    right_score = right_match.similarity_score if right_match else 0.0
    left_score = left_match.similarity_score if left_match else 0.0

    if (
        right_match is not None
        and right_score >= LATERAL_SIDE_SCORE_MIN
        and right_score > left_score
    ):
        return LateralSideDecision(
            lado="direita",
            confidence=right_match.confidence,
            reason="right_score_above_threshold",
            right_match=right_match,
            left_match=left_match,
            right_error=right_error,
            left_error=left_error,
        )
    if (
        left_match is not None
        and left_score >= LATERAL_SIDE_SCORE_MIN
        and left_score > right_score
    ):
        return LateralSideDecision(
            lado="esquerda",
            confidence=left_match.confidence,
            reason="left_score_above_threshold",
            right_match=right_match,
            left_match=left_match,
            right_error=right_error,
            left_error=left_error,
        )

    confidences = [
        match.confidence
        for match in (right_match, left_match)
        if match is not None
    ]
    if right_match is None and left_match is None:
        return LateralSideDecision(
            lado="incerto",
            confidence=0.0,
            reason="both_reference_calls_failed",
            right_match=right_match,
            left_match=left_match,
            right_error=right_error,
            left_error=left_error,
        )

    if right_score == left_score and right_score >= LATERAL_SIDE_SCORE_MIN:
        reason = "fallback_ambiguous_equal_scores"
    elif right_score < LATERAL_SIDE_SCORE_MIN and left_score < LATERAL_SIDE_SCORE_MIN:
        reason = "fallback_both_scores_below_threshold"
    else:
        reason = "fallback_ambiguous_scores"

    fallback_lado = "direita" if right_score >= left_score else "esquerda"
    return LateralSideDecision(
        lado=fallback_lado,
        confidence=max(confidences) if confidences else 0.0,
        reason=reason,
        right_match=right_match,
        left_match=left_match,
        right_error=right_error,
        left_error=left_error,
    )


def _infer_lateral_side(
    image_path: str,
    base_part_id: str,
    references: LateralReferencePaths,
) -> LateralSideDecision:
    """Retorna a decisao de lado com matches e erros separados por referencia."""
    right_match, right_error = _infer_lateral_reference_match_safely(
        image_path=image_path,
        base_part_id=base_part_id,
        reference_path=references.right,
        reference_label="direita",
    )
    left_match, left_error = _infer_lateral_reference_match_safely(
        image_path=image_path,
        base_part_id=base_part_id,
        reference_path=references.left,
        reference_label="esquerda",
    )

    return _decide_lateral_side(
        right_match=right_match,
        left_match=left_match,
        right_error=right_error,
        left_error=left_error,
    )


def _extract_lateral_references_once(checklist_path: str, output_dir: str) -> LateralReferencePaths | None:
    refs_dir = os.path.join(output_dir, "checklist_lateral_references")
    try:
        from core.checklist_lateral_cropper import extract_lateral_reference_images

        result = extract_lateral_reference_images(checklist_path, refs_dir)
    except Exception as exc:
        print(f"[triage] Nao foi possivel gerar referencias laterais do checklist: {exc}", flush=True)
        return None

    return LateralReferencePaths(
        right=result.lateral_direita_path,
        left=result.lateral_esquerda_path,
    )


def _checklist_damage_for_part(part_id: str, checklist_part_ids: set[str] | None) -> bool | None:
    if checklist_part_ids is None:
        return None

    pid = (part_id or "").strip().lower()
    if pid in checklist_part_ids:
        return True

    base_part_id = _base_lateral_part_id(pid)
    if base_part_id:
        if base_part_id in checklist_part_ids:
            return True
        if pid == base_part_id:
            return any(p.startswith(f"{base_part_id}_") for p in checklist_part_ids)

    return False


def run_triage(case_id: str, fotos_dir: str, output_dir: str, checklist_path: str | None = None) -> dict:
    """Roda triagem em todas as imagens de `fotos_dir` e salva `triage.json` em `output_dir`."""
    images_out: list[TriageImage] = []
    
    checklist_text = ""
    checklist_part_ids: set[str] | None = None
    lateral_references: LateralReferencePaths | None = None
    if checklist_path and os.path.exists(checklist_path):
        os.makedirs(output_dir, exist_ok=True)
        lateral_references = _extract_lateral_references_once(checklist_path, output_dir)

        # Extração determinística do RELDEV: evita falsos positivos do modelo.
        try:
            checklist_part_ids = extract_reldev_avaria_part_ids(checklist_path)
        except Exception:
            checklist_part_ids = None

        # Contexto enxuto para o LLM: só lista peças marcadas como avaria.
        # Quando o set está vazio, o RELDEV foi lido e não há avarias; não use o
        # texto bruto, pois a legenda "* Avaria:" pode induzir falso positivo.
        if checklist_part_ids is not None:
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

            # Portas e para-lamas: a triagem base identifica a peca; o lado vem do
            # matching visual contra as laterais direita/esquerda recortadas do checklist.
            base_lateral_part_id = _base_lateral_part_id(part_id)
            if base_lateral_part_id:
                part_id = base_lateral_part_id
                if lateral_references is not None:
                    side_decision = _infer_lateral_side(image_path, base_lateral_part_id, lateral_references)
                    if side_decision.lado in {"direita", "esquerda"}:
                        part_id = _build_lateralized_part_id(base_lateral_part_id, side_decision.lado)
                        if side_decision.reason.startswith("fallback_"):
                            # Fallback lateraliza para nao quebrar o fluxo dos peritos,
                            # mas preserva a incerteza com uma pequena penalizacao.
                            base_confidence = min(base_confidence, base_confidence * 0.85)
                        else:
                            base_confidence = min(base_confidence, side_decision.confidence)
                    else:
                        # Mantem a peca base e sinaliza a incerteza reduzindo a confianca.
                        base_confidence = min(base_confidence, base_confidence * 0.85)
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
            if checklist_part_ids is not None:
                checklist_damage_reported = _checklist_damage_for_part(part_id, checklist_part_ids)
            else:
                checklist_damage_reported = result_dict.get("checklist_damage_reported")

            triage_img = TriageImage(
                image_id=image_id,
                photo_path=image_path,
                part_id=part_id,
                view=view,
                confidence=round(base_confidence, 4),
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
                    confidence=round(forced_conf, 4),
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

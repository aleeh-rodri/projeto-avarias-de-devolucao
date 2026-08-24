from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from core.llm_gate_client import call_llm_with_image


BillingDecision = Literal["cobrar", "nao_cobrar", "revisar"]
BillingClassification = Literal["dano_cobravel", "desgaste_leve", "inconclusivo"]
DamageType = Literal["arranhao", "quebra", "trinca", "outro", "incerto"]
DamageDepth = Literal["superficial", "moderada", "profunda", "incerta"]
DamageExtent = Literal["pequena", "media", "grande", "incerta"]


@dataclass(frozen=True)
class BillingAgentConfig:
    confidence_min_automatico: float = 0.70
    use_basic_model: bool = False
    max_completion_tokens: int = 1200


class BillingAgent:
    """Avalia elegibilidade de cobranca depois da analise tecnica do perito.

    MVP atual:
    - somente calotas;
    - nao substitui a analise tecnica do perito;
    - decide entre cobrar / nao_cobrar / revisar;
    - usa foto + resultado estruturado do perito;
    - em caso de erro, ambiguidade ou baixa confianca, retorna revisar.
    """

    def __init__(self, config: BillingAgentConfig | None = None):
        self.config = config or BillingAgentConfig()

    @staticmethod
    def _clean_json_fences(raw: str) -> str:
        value = (raw or "").strip()
        if value.startswith("```"):
            value = value.replace("```json", "").replace("```", "").strip()
        return value

    @staticmethod
    def _clamp01(value: object) -> float:
        try:
            value_f = float(value)
        except Exception:
            return 0.0
        return max(0.0, min(value_f, 1.0))

    @staticmethod
    def _to_bool(value: object) -> bool:
        if value is True:
            return True
        if value is False:
            return False
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "sim", "yes"}:
                return True
            if normalized in {"false", "0", "nao", "não", "no"}:
                return False
        return False

    @staticmethod
    def _normalize_enum(value: object, allowed: set[str], fallback: str) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in allowed else fallback

    @staticmethod
    def _fallback_review(
        *,
        reason: str,
        raw_response: str | None = None,
        confidence: float = 0.0,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "decisao": "revisar",
            "classificacao": "inconclusivo",
            "confidence": max(0.0, min(float(confidence or 0.0), 1.0)),
            "criterios": {
                "tipo_dano": "incerto",
                "profundidade": "incerta",
                "extensao": "incerta",
                "quebra": False,
                "trinca": False,
                "perda_material": False,
            },
            "justificativa": reason,
            "needs_human_review": True,
        }
        if raw_response:
            out["raw_response"] = raw_response
        return out

    @staticmethod
    def _validate_input(analise_perito: dict[str, Any]) -> tuple[bool, str | None]:
        if not isinstance(analise_perito, dict):
            return (False, "Analise do perito invalida ou ausente.")

        peca = str(analise_perito.get("peca") or "").strip().lower()
        if peca != "calota":
            return (False, "BillingAgent MVP aceita somente analises de calota.")

        nivel = str(analise_perito.get("nivel_dano") or "").strip().lower()
        if nivel not in {"sem_dano", "leve", "moderado", "grave"}:
            return (False, "nivel_dano invalido na analise do perito.")

        return (True, None)

    @staticmethod
    def _build_calota_billing_prompt(analise_perito: dict[str, Any]) -> str:
        perito_json = json.dumps(analise_perito, ensure_ascii=False, indent=2)

        return f"""
Voce e um AVALIADOR DE ELEGIBILIDADE DE COBRANCA DE AVARIAS AUTOMOTIVAS.

PAPEL DESTA ETAPA
- Um perito tecnico ja analisou a imagem e identificou a existencia da avaria.
- Sua funcao NAO e substituir o perito tecnico.
- Sua funcao e decidir se a avaria identificada deve gerar cobranca segundo a politica disponivel.
- Uma avaria pode existir visualmente e ainda assim nao ser cobravel.

PECA ANALISADA
CALOTA

ANALISE TECNICA PRODUZIDA PELO PERITO
{perito_json}

OBJETIVO
Classificar a avaria em exatamente uma destas decisoes:
- "cobrar": ha evidencia suficiente de dano que ultrapassa desgaste superficial leve.
- "nao_cobrar": existe avaria visual, mas ela e claramente superficial/leve e compativel com desgaste que nao deve gerar cobranca.
- "revisar": a imagem ou a evidencia nao permite decidir com seguranca entre cobrar e nao cobrar.

POLITICA DISPONIVEL NESTE MVP
- Arranhoes ou riscos claramente superficiais, leves e de pequena extensao podem ser tratados como desgaste leve e nao gerar cobranca.
- Quebra, trinca, falta/perda de material ou dano claramente profundo nao devem ser tratados como simples desgaste superficial.
- Se a profundidade, extensao ou natureza do dano nao puder ser avaliada com seguranca, escolha "revisar".
- Nao invente criterios de cobranca que nao estejam definidos aqui.
- Na duvida entre cobrar e nao cobrar, escolha "revisar".

O QUE OBSERVAR NA FOTO
- tipo predominante de dano;
- profundidade aparente;
- extensao aparente;
- existencia de quebra;
- existencia de trinca;
- existencia de perda de material.

REGRAS IMPORTANTES
- O fato de o perito ter retornado "leve" NAO significa automaticamente "nao_cobrar".
- O fato de o perito ter retornado "moderado" ou "grave" e um sinal tecnico relevante, mas a decisao deve continuar baseada na evidencia visual.
- Nao transforme sujeira, reflexo, sombra ou brilho em dano cobravel.
- Nao altere a peca analisada.
- Nao retorne servico, preco ou item da LPU.

ESCALA DE CONFIDENCE
- 0.90 a 1.00: decisao muito segura.
- 0.75 a 0.89: decisao segura.
- 0.60 a 0.74: alguma incerteza.
- abaixo de 0.60: decisao incerta; prefira "revisar".

RETORNE SOMENTE JSON VALIDO, sem Markdown e sem texto extra:
{{
  "decisao": "cobrar|nao_cobrar|revisar",
  "classificacao": "dano_cobravel|desgaste_leve|inconclusivo",
  "confidence": 0.0,
  "criterios": {{
    "tipo_dano": "arranhao|quebra|trinca|outro|incerto",
    "profundidade": "superficial|moderada|profunda|incerta",
    "extensao": "pequena|media|grande|incerta",
    "quebra": true,
    "trinca": false,
    "perda_material": false
  }},
  "justificativa": "explicacao curta, objetiva e baseada na evidencia visual"
}}
""".strip()

    def _normalize_response(self, data: dict[str, Any]) -> dict[str, Any]:
        decision = self._normalize_enum(
            data.get("decisao"),
            {"cobrar", "nao_cobrar", "revisar"},
            "revisar",
        )
        classification = self._normalize_enum(
            data.get("classificacao"),
            {"dano_cobravel", "desgaste_leve", "inconclusivo"},
            "inconclusivo",
        )
        confidence = self._clamp01(data.get("confidence"))

        criteria_raw = data.get("criterios")
        if not isinstance(criteria_raw, dict):
            criteria_raw = {}

        criteria = {
            "tipo_dano": self._normalize_enum(
                criteria_raw.get("tipo_dano"),
                {"arranhao", "quebra", "trinca", "outro", "incerto"},
                "incerto",
            ),
            "profundidade": self._normalize_enum(
                criteria_raw.get("profundidade"),
                {"superficial", "moderada", "profunda", "incerta"},
                "incerta",
            ),
            "extensao": self._normalize_enum(
                criteria_raw.get("extensao"),
                {"pequena", "media", "grande", "incerta"},
                "incerta",
            ),
            "quebra": self._to_bool(criteria_raw.get("quebra")),
            "trinca": self._to_bool(criteria_raw.get("trinca")),
            "perda_material": self._to_bool(criteria_raw.get("perda_material")),
        }

        justification = str(data.get("justificativa") or "").strip()
        if not justification:
            justification = "Decisao sem justificativa suficiente; revisar manualmente."
            decision = "revisar"
            classification = "inconclusivo"

        # Consistencia minima entre decisao e classificacao.
        if decision == "cobrar":
            classification = "dano_cobravel"
        elif decision == "nao_cobrar":
            classification = "desgaste_leve"
        else:
            classification = "inconclusivo"

        # Sinais fortes de dano nao devem terminar como nao_cobrar.
        strong_damage_signal = (
            criteria["quebra"]
            or criteria["trinca"]
            or criteria["perda_material"]
            or criteria["profundidade"] == "profunda"
        )
        if decision == "nao_cobrar" and strong_damage_signal:
            decision = "revisar"
            classification = "inconclusivo"
            justification = (
                "Resposta inconsistente: havia sinal de quebra/trinca/perda de material "
                "ou dano profundo, mas a decisao veio como nao_cobrar. Encaminhado para revisao."
            )

        # Baixa confianca nunca automatiza uma decisao de negocio.
        if (
            decision in {"cobrar", "nao_cobrar"}
            and confidence < self.config.confidence_min_automatico
        ):
            original_decision = decision
            decision = "revisar"
            classification = "inconclusivo"
            justification = (
                f"Decisao original '{original_decision}' com confidence {confidence:.2f}, "
                f"abaixo do minimo automatico {self.config.confidence_min_automatico:.2f}. "
                f"Justificativa original: {justification}"
            )

        return {
            "decisao": decision,
            "classificacao": classification,
            "confidence": round(confidence, 4),
            "criterios": criteria,
            "justificativa": justification,
            "needs_human_review": decision == "revisar",
        }

    def avaliar_calota(
        self,
        *,
        image_path: str,
        analise_perito: dict[str, Any],
    ) -> dict[str, Any]:
        """Decide se uma avaria de calota deve ser cobrada.

        Retorna sempre um dict estruturado. Qualquer erro vira decisao="revisar".
        """
        valid, reason = self._validate_input(analise_perito)
        if not valid:
            return self._fallback_review(reason=reason or "Entrada invalida.")

        nivel = str(analise_perito.get("nivel_dano") or "").strip().lower()
        if nivel == "sem_dano":
            return {
                "decisao": "nao_cobrar",
                "classificacao": "desgaste_leve",
                "confidence": 1.0,
                "criterios": {
                    "tipo_dano": "incerto",
                    "profundidade": "incerta",
                    "extensao": "incerta",
                    "quebra": False,
                    "trinca": False,
                    "perda_material": False,
                },
                "justificativa": "O perito tecnico classificou a calota como sem_dano; nenhuma cobranca deve ser gerada.",
                "needs_human_review": False,
                "source": "deterministic_sem_dano",
            }

        if not image_path or not str(image_path).strip():
            return self._fallback_review(
                reason="Analise de calota com dano, mas sem foto disponivel para avaliar elegibilidade de cobranca."
            )

        prompt = self._build_calota_billing_prompt(analise_perito)

        try:
            raw = call_llm_with_image(
                prompt=prompt,
                image_path=str(image_path),
                temperature=0,
                max_completion_tokens=self.config.max_completion_tokens,
                use_basic_model=self.config.use_basic_model,
            )
        except Exception as exc:
            return self._fallback_review(
                reason=f"Falha ao chamar o LLM no BillingAgent: {type(exc).__name__}: {exc}"
            )

        cleaned = self._clean_json_fences(raw)
        try:
            parsed = json.loads(cleaned)
        except Exception:
            return self._fallback_review(
                reason="Resposta do BillingAgent nao veio em JSON valido.",
                raw_response=raw,
            )

        if not isinstance(parsed, dict):
            return self._fallback_review(
                reason="Resposta do BillingAgent nao e um objeto JSON.",
                raw_response=raw,
            )

        result = self._normalize_response(parsed)
        result["source"] = "llm_billing_policy"
        return result

    def avaliar_item(
        self,
        *,
        image_path: str,
        analise_perito: dict[str, Any],
    ) -> dict[str, Any]:
        """Ponto de entrada generico para futuras pecas.

        No MVP atual, somente calota esta habilitada.
        """
        peca = str((analise_perito or {}).get("peca") or "").strip().lower()
        if peca == "calota":
            return self.avaliar_calota(
                image_path=image_path,
                analise_perito=analise_perito,
            )

        return self._fallback_review(
            reason=f"Peca '{peca or 'desconhecida'}' ainda nao suportada pelo BillingAgent MVP."
        )

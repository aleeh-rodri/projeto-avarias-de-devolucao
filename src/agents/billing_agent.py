from __future__ import annotations

from typing import Any, Literal


BillingDecision = Literal["cobrar", "nao_cobrar", "revisar"]
BillingClassification = Literal["dano_cobravel", "desgaste_leve", "inconclusivo"]


class BillingAgent:
    """Aplica regras deterministicas de cobranca ao parecer tecnico do perito.

    O agente nao acessa imagem, LLM, servicos, precos ou LPU. Ele recebe somente
    os atributos tecnicos que ja foram extraidos pelo perito de pneus e rodas.
    Neste MVP, apenas calotas sao suportadas.
    """

    _DAMAGE_TYPES = {"sem_dano", "arranhao", "quebra", "trinca", "outro", "incerto"}
    _DEPTHS = {"nao_aplicavel", "superficial", "moderada", "profunda", "incerta"}
    _EXTENTS = {"nao_aplicavel", "pequena", "media", "grande", "incerta"}

    @staticmethod
    def _normalize(value: object) -> str:
        return str(value or "").strip().lower()

    @classmethod
    def _bool_or_invalid(cls, value: object) -> bool | None:
        if value is True or value is False:
            return value
        if isinstance(value, str):
            normalized = cls._normalize(value)
            if normalized in {"true", "1", "sim", "yes"}:
                return True
            if normalized in {"false", "0", "nao", "não", "no"}:
                return False
        return None

    @staticmethod
    def _result(
        *,
        decisao: BillingDecision,
        classificacao: BillingClassification,
        regra: str,
        justificativa: str,
        criterios: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "decisao": decisao,
            "classificacao": classificacao,
            "confidence": 1.0 if decisao != "revisar" else 0.0,
            "regra_aplicada": regra,
            "criterios": criterios,
            "justificativa": justificativa,
            "needs_human_review": decisao == "revisar",
            "source": "deterministic_billing_policy",
        }

    @classmethod
    def _review(
        cls,
        reason: str,
        criterios: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return cls._result(
            decisao="revisar",
            classificacao="inconclusivo",
            regra="entrada_invalida_ou_regra_nao_definida",
            justificativa=reason,
            criterios=criterios or {},
        )

    def avaliar_calota(self, *, analise_perito: dict[str, Any]) -> dict[str, Any]:
        """Decide a cobranca usando exclusivamente o JSON tecnico do perito."""
        if not isinstance(analise_perito, dict):
            return self._review("Analise do perito invalida ou ausente.")

        peca = self._normalize(analise_perito.get("peca"))
        part_id = self._normalize(analise_perito.get("part_id"))
        nivel = self._normalize(analise_perito.get("nivel_dano"))
        tipo = self._normalize(analise_perito.get("tipo_dano"))
        profundidade = self._normalize(analise_perito.get("profundidade"))
        extensao = self._normalize(analise_perito.get("extensao"))

        if peca != "calota":
            return self._review("BillingAgent MVP aceita somente analises de calota.")
        if not part_id:
            return self._review("part_id ausente na analise tecnica da calota.")
        if nivel not in {"sem_dano", "leve", "moderado", "grave"}:
            return self._review("nivel_dano invalido na analise tecnica da calota.")
        if tipo not in self._DAMAGE_TYPES:
            return self._review("tipo_dano invalido na analise tecnica da calota.")
        if profundidade not in self._DEPTHS:
            return self._review("profundidade invalida na analise tecnica da calota.")
        if extensao not in self._EXTENTS:
            return self._review("extensao invalida na analise tecnica da calota.")

        quebra = self._bool_or_invalid(analise_perito.get("quebra"))
        trinca = self._bool_or_invalid(analise_perito.get("trinca"))
        perda_material = self._bool_or_invalid(analise_perito.get("perda_material"))
        if quebra is None or trinca is None or perda_material is None:
            return self._review(
                "quebra, trinca e perda_material devem ser booleanos na analise tecnica."
            )

        criterios = {
            "peca": peca,
            "part_id": part_id,
            "nivel_dano": nivel,
            "tipo_dano": tipo,
            "profundidade": profundidade,
            "extensao": extensao,
            "quebra": quebra,
            "trinca": trinca,
            "perda_material": perda_material,
        }

        sinal_quebra = quebra or tipo == "quebra"
        sinal_trinca = trinca or tipo == "trinca"

        # Dados contraditorios nao devem produzir uma decisao financeira automatica.
        if nivel == "sem_dano" and (sinal_quebra or sinal_trinca or perda_material):
            return self._review(
                "Analise contraditoria: sem_dano acompanhado de quebra, trinca ou perda de material.",
                criterios,
            )

        if nivel == "sem_dano":
            return self._result(
                decisao="nao_cobrar",
                classificacao="desgaste_leve",
                regra="sem_dano_nao_cobra",
                justificativa="Calota classificada pelo perito como sem dano; nao cobrar.",
                criterios=criterios,
            )

        if sinal_quebra:
            return self._result(
                decisao="cobrar",
                classificacao="dano_cobravel",
                regra="quebra_cobra",
                justificativa="O parecer tecnico identificou quebra na calota; cobrar.",
                criterios=criterios,
            )

        if sinal_trinca:
            return self._result(
                decisao="cobrar",
                classificacao="dano_cobravel",
                regra="trinca_cobra",
                justificativa="O parecer tecnico identificou trinca na calota; cobrar.",
                criterios=criterios,
            )

        if nivel == "leve" and tipo == "arranhao" and profundidade == "superficial":
            return self._result(
                decisao="nao_cobrar",
                classificacao="desgaste_leve",
                regra="arranhao_leve_superficial_nao_cobra",
                justificativa="Arranhao leve e superficial na calota; nao cobrar.",
                criterios=criterios,
            )

        return self._review(
            "A combinacao tecnica informada ainda nao possui regra de cobranca definida.",
            criterios,
        )

    def avaliar_item(self, *, analise_perito: dict[str, Any]) -> dict[str, Any]:
        """Ponto de entrada generico para as pecas suportadas pelo billing."""
        if not isinstance(analise_perito, dict):
            return self._review("Analise do perito invalida ou ausente.")
        peca = self._normalize(analise_perito.get("peca"))
        if peca == "calota":
            return self.avaliar_calota(analise_perito=analise_perito)
        return self._review(
            f"Peca '{peca or 'desconhecida'}' ainda nao suportada pelo BillingAgent MVP."
        )

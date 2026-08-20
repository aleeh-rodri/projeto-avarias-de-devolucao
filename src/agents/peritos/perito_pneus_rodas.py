from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from agents.peritos.base_perito import BasePerito
from core.llm_gate_client import call_llm_with_image
from core.lpu import LpuItem, find_services, load_lpu_items
from core.schemas import ExpertConsolidatedOutput, ServiceItem


@dataclass(frozen=True)
class ConfigPeritoPneusRodas:
    caminho_lpu_xlsx: str


class PeritoPneusRodas(BasePerito):
    def __init__(self, config: ConfigPeritoPneusRodas):
        self.config = config
        self.lpu_items = load_lpu_items(config.caminho_lpu_xlsx)

    @staticmethod
    def _wheel_position(part_id: str | None) -> tuple[str, str, str]:
        """Retorna (eixo, lado, part_id normalizado) para uma roda canônica."""
        pid = (part_id or "").strip().lower()
        if not pid.startswith("roda_"):
            return ("", "", pid)
        eixo = "dianteira" if "dianteira" in pid else ("traseira" if "traseira" in pid else "")
        lado = "direita" if pid.endswith("direita") else ("esquerda" if pid.endswith("esquerda") else "")
        return (eixo, lado, pid)

    @staticmethod
    def _parse_llm_json(raw: str) -> dict[str, Any]:
        value = raw.strip()
        if "```" in value:
            value = value.split("```", 2)[1]
            if value.startswith("json"):
                value = value[4:]
        parsed = json.loads(value.strip())
        if not isinstance(parsed, dict):
            raise ValueError("resposta do perito não é um objeto JSON")
        return parsed

    def _select_service(self, peca: str, acao: str, part_id: str | None) -> list[LpuItem]:
        peca_norm = (peca or "").strip().lower()
        if "calota" in peca_norm:
            # Na LPU, calota é um único jogo, independentemente da quantidade avariada.
            keywords = ["calota", "jogo"]
        elif "ferro" in peca_norm:
            keywords = ["roda", "ferro", acao]
        elif "liga" in peca_norm:
            keywords = ["roda", "liga", "leve", acao]
        else:
            keywords = ["roda", acao]

        eixo, lado, _ = self._wheel_position(part_id)
        if eixo:
            keywords.append(eixo)
        if lado:
            keywords.append(lado)

        selected = find_services(
            self.lpu_items,
            keywords,
            perito_filtro="pneus_rodas",
            modo_restrito=True,
            allow_global_fallback=False,
            fuzzy=False,
        )
        if not selected:
            selected = find_services(
                self.lpu_items,
                keywords,
                perito_filtro="pneus_rodas",
                allow_global_fallback=False,
                fuzzy=False,
            )[:1]

        selected = [
            service
            for service in selected
            if "banco" not in (service.descricao or "").lower()
            and "forro" not in (service.descricao or "").lower()
        ]
        return selected[:1]

    def _analyze_one(
        self,
        image_path: str,
        part_id: str | None,
        checklist_summary: str,
        wheel_type: str,
    ) -> dict[str, Any]:
        wheel_type_norm = (wheel_type or "desconhecido").strip().lower()
        if wheel_type_norm == "liga_leve":
            wheel_type_prompt = "roda de liga leve"
            expected_peca = "roda liga leve"
        elif wheel_type_norm == "ferro":
            wheel_type_prompt = "roda de ferro"
            expected_peca = "roda ferro"
        else:
            wheel_type_prompt = "desconhecido"
            expected_peca = "roda"

        prompt = f"""
Voce e um PERITO TECNICO ESPECIALISTA EM RODAS, PNEUS E CALOTAS.

OBJETIVO
- Analise somente a roda identificada pelo PART_ID abaixo.
- O tipo de roda ja foi identificado pelo sistema. Nao tente inferir ou alterar esse tipo pela imagem.
- Analise apenas existencia de dano visivel, severidade e acao tecnica.
- Retorne APENAS JSON valido (sem Markdown, sem texto extra).

PART_ID DA RODA ANALISADA:
{part_id or "desconhecido"}

TIPO DE RODA IDENTIFICADO PELO SISTEMA:
{wheel_type_prompt}

CONTEXTO DO CHECKLIST (use como pista; evidencia visual tem prioridade):
{checklist_summary}

REGRAS SOBRE PECA
- Se o tipo informado for "roda de liga leve", a peca deve ser "roda liga leve". Nao retorne calota.
- Se o tipo informado for "roda de ferro", a peca padrao deve ser "roda ferro".
- Calota so existe em roda de ferro. Retorne "calota" apenas se o dano visivel estiver claramente na calota.

CRITERIOS TECNICOS
- Calota: quebra, falta de material, trinca ou risco profundo implica troca. Calota danificada nao recebe reparo.
- Roda de liga leve: ralado/arranhoes implicam reparo; amassado ou trinca e grave, com troca preferida para trinca clara.
- Roda de ferro: amassado corrigivel implica reparo; trinca evidente implica troca.
- Nao confunda sujeira com dano. Evidencia insuficiente deve ser classificada como sem_dano.

SEVERIDADE
- sem_dano: nada evidente OU foto nao permite avaliar.
- leve: marca superficial/ralado leve sem deformacao.
- moderado: ralada profunda extensa ou deformacao leve.
- grave: deformacao clara, trinca, quebra de calota ou risco de seguranca.

RETORNE APENAS ESTE JSON:
{{
  "peca": "roda liga leve|roda ferro|calota",
  "nivel_dano": "sem_dano|leve|moderado|grave",
  "acao": "reparo|troca",
  "justificativa": "descricao tecnica objetiva baseada na evidencia visual"
}}
"""
        result = self._parse_llm_json(
            call_llm_with_image(
                prompt=prompt,
                image_path=image_path,
                use_basic_model=False,
                max_completion_tokens=2000,
            )
        )

        peca = str(result.get("peca") or expected_peca).strip().lower()
        acao = str(result.get("acao") or "reparo").strip().lower()
        nivel = str(result.get("nivel_dano") or "moderado").strip().lower()

        if wheel_type_norm == "liga_leve":
            peca = "roda liga leve"
        elif wheel_type_norm == "ferro":
            peca = "calota" if "calota" in peca else "roda ferro"

        if peca == "calota":
            acao = "troca"

        if nivel == "sem_dano":
            services: list[LpuItem] = []
        else:
            services = self._select_service(peca, acao, part_id)

        services_out = [ServiceItem(descricao=s.descricao, preco=s.preco) for s in services]
        total = self._calculate_total(services)
        return {
            **ExpertConsolidatedOutput(
                nivel_dano=nivel,
                peca=peca,
                servicos=services_out,
                preco_total=total,
                justificativa=str(result.get("justificativa") or "").strip() or None,
                fotos_analisadas=[image_path],
            ).model_dump(),
            "part_id": part_id,
            "acao": acao,
        }

    @staticmethod
    def _merge_calota_items(items: list[dict[str, Any]]) -> dict[str, Any]:
        """Consolida várias calotas avariadas em uma única cobrança de jogo."""
        first = dict(items[0])
        part_ids: list[str] = []
        photos: list[str] = []
        justifications: list[str] = []
        nivel = "sem_dano"
        rank = {"sem_dano": 0, "leve": 1, "moderado": 2, "grave": 3}

        for item in items:
            pid = str(item.get("part_id") or "").strip()
            if pid and pid not in part_ids:
                part_ids.append(pid)
            for photo in item.get("fotos_analisadas") or []:
                if isinstance(photo, str) and photo not in photos:
                    photos.append(photo)
            justification = str(item.get("justificativa") or "").strip()
            if justification:
                justifications.append(f"{pid or 'roda'}: {justification}")
            item_level = str(item.get("nivel_dano") or "sem_dano").strip().lower()
            if rank.get(item_level, 0) > rank.get(nivel, 0):
                nivel = item_level

        first["nivel_dano"] = nivel
        first["part_id"] = part_ids[0] if len(part_ids) == 1 else None
        first["part_ids"] = part_ids
        first["fotos_analisadas"] = photos
        first["justificativa"] = "; ".join(justifications) or first.get("justificativa")
        return first

    def run(self, image_paths: list[str], **kwargs) -> dict[str, Any]:
        if not image_paths:
            return {"erro": "nenhuma imagem fornecida ao perito de pneus e rodas"}

        checklist_summary = kwargs.get("checklist_summary") or "Nenhuma observação no checklist."
        wheel_type = kwargs.get("wheel_type") or "desconhecido"
        imagens_usadas = kwargs.get("imagens_usadas")

        metadata = imagens_usadas if isinstance(imagens_usadas, list) else []
        analyses: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        seen_part_ids: set[str] = set()

        for index, image_path in enumerate(image_paths):
            meta = metadata[index] if index < len(metadata) and isinstance(metadata[index], dict) else {}
            part_id = str(meta.get("part_id") or "").strip().lower() or None

            # Defesa adicional: mesmo que o chamador envie duas fotos da mesma roda,
            # somente a primeira evidência é analisada e pode gerar cobrança.
            if part_id and part_id in seen_part_ids:
                continue
            if part_id:
                seen_part_ids.add(part_id)

            try:
                analyses.append(
                    self._analyze_one(
                        image_path=image_path,
                        part_id=part_id,
                        checklist_summary=checklist_summary,
                        wheel_type=wheel_type,
                    )
                )
            except Exception as exc:
                errors.append({"part_id": part_id or "", "foto": image_path, "erro": str(exc)})

        if not analyses:
            detail = errors[0]["erro"] if errors else "nenhuma foto distinta por part_id"
            return {"erro": f"falha rodas: {detail}", "erros_analise": errors}

        charged_items = [item for item in analyses if item.get("nivel_dano") != "sem_dano"]
        if not charged_items:
            result = ExpertConsolidatedOutput(
                nivel_dano="sem_dano",
                peca="pneus_rodas",
                servicos=[],
                preco_total=0.0,
                justificativa="Sem danos identificados nas rodas analisadas.",
                fotos_analisadas=[p for item in analyses for p in item.get("fotos_analisadas", [])],
            ).model_dump()
            result["analises"] = analyses
            if errors:
                result["erros_analise"] = errors
            return result

        calota_items = [item for item in charged_items if item.get("peca") == "calota"]
        wheel_items = [item for item in charged_items if item.get("peca") != "calota"]
        consolidated_items = wheel_items + ([self._merge_calota_items(calota_items)] if calota_items else [])

        services_flat: list[dict[str, Any]] = []
        total_numeric = 0.0
        any_non_numeric = False
        for item in consolidated_items:
            services_flat.extend(item.get("servicos") or [])
            price = item.get("preco_total")
            if isinstance(price, (int, float)):
                total_numeric += float(price)
            elif str(price).strip().lower() == "sob consulta":
                any_non_numeric = True

        nivel_final = max(
            (str(item.get("nivel_dano") or "sem_dano") for item in consolidated_items),
            key=self._severity_rank,
        )
        result: dict[str, Any] = {
            "nivel_dano": nivel_final,
            "peca": "pneus_rodas",
            "itens": consolidated_items,
            "analises": analyses,
            "servicos": services_flat,
            "preco_total": "Sob consulta" if any_non_numeric else round(total_numeric, 2),
            "justificativa": "; ".join(
                f"{item.get('part_id') or ', '.join(item.get('part_ids') or [])}: {item.get('justificativa')}"
                for item in consolidated_items
                if item.get("justificativa")
            ),
            "fotos_analisadas": [p for item in analyses for p in item.get("fotos_analisadas", [])],
        }
        if errors:
            result["erros_analise"] = errors
        return result

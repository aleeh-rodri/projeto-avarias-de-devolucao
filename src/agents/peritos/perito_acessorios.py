from __future__ import annotations
import json
import unicodedata
from dataclasses import dataclass
from typing import Any
from core.llm_gate_client import call_llm_with_image
from core.lpu import LpuItem, find_services, load_lpu_items
from core.photo_part_metadata import extract_photo_part_code
from core.schemas import ServiceItem, ExpertConsolidatedOutput
from agents.peritos.base_perito import BasePerito

@dataclass(frozen=True)
class ConfigPeritoAcessorios:
    caminho_lpu_xlsx: str


KEY_RESERVE_PHOTO_CODES = {"7", "8", "174"}


def _clean_json_fences(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    return raw


def _norm_ascii(text: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    return " ".join(normalized.strip().lower().split())


def _is_vehicle_key_piece(text: Any) -> bool:
    normalized = _norm_ascii(text)
    return "chave" in normalized and "roda" not in normalized


def _contains_vehicle_key_service(items: list[LpuItem]) -> bool:
    for item in items:
        desc = _norm_ascii(item.descricao)
        if "chave principal" in desc or "chave reserva" in desc:
            return True
    return False


def _build_key_count_prompt() -> str:
    return """
Voce e um verificador visual de chaves de veiculo.

TAREFA
- Conte quantas chaves fisicas de veiculo aparecem claramente na foto.
- Considere chave canivete, chave com telecomando e chave mecanica.
- Ignore chaveiros, etiquetas, documentos, maos, aneis e cartoes.
- Se duas chaves estiverem presas no mesmo chaveiro, conte 2 chaves.
- Se houver apenas uma chave visivel, retorne key_count=1.
- Se houver duas ou mais chaves visiveis, retorne o total aproximado.
- Se a imagem nao permitir contar com seguranca, use status="incerto".

REGRA DE CONSERVADORISMO
- So retorne status="duas_ou_mais" quando houver evidencia clara de duas ou mais chaves.
- So retorne status="uma" quando houver evidencia clara de exatamente uma chave.

RETORNE SOMENTE JSON VALIDO, sem markdown:
{
  "status": "uma|duas_ou_mais|nenhuma|incerto",
  "key_count": 0,
  "confidence": 0.0,
  "justificativa": "explicacao objetiva baseada na imagem"
}
"""


def _total_price(selected: list[LpuItem]) -> float | str:
    if any(str(s.preco).strip().lower() == "sob consulta" for s in selected):
        return "Sob consulta"
    return sum(float(s.preco) for s in selected if isinstance(s.preco, (int, float)))


class PeritoAcessorios(BasePerito):
    def __init__(self, config: ConfigPeritoAcessorios):
        self.config = config
        self.lpu_items = load_lpu_items(config.caminho_lpu_xlsx)

    def _is_key_reserve_photo(self, image_path: str) -> bool:
        return (extract_photo_part_code(image_path) or "") in KEY_RESERVE_PHOTO_CODES

    def _analyze_key_count(self, image_path: str) -> dict[str, Any]:
        raw = call_llm_with_image(
            prompt=_build_key_count_prompt(),
            image_path=image_path,
            temperature=0,
            max_tokens=400,
        )
        raw = _clean_json_fences(raw)
        try:
            data = json.loads(raw)
        except Exception:
            return {
                "status": "incerto",
                "key_count": 0,
                "confidence": 0.0,
                "justificativa": "Falha ao interpretar JSON da verificacao visual de chaves.",
                "raw_response": raw,
            }

        status = str(data.get("status") or "").strip().lower()
        if status not in {"uma", "duas_ou_mais", "nenhuma", "incerto"}:
            status = "incerto"

        try:
            key_count = int(float(data.get("key_count") or 0))
        except Exception:
            key_count = 0

        try:
            confidence = float(data.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        confidence = max(0.0, min(confidence, 1.0))

        if key_count >= 2 and status != "duas_ou_mais":
            status = "duas_ou_mais"
        elif key_count == 1 and status == "incerto" and confidence >= 0.8:
            status = "uma"

        return {
            "status": status,
            "key_count": key_count,
            "confidence": confidence,
            "justificativa": str(data.get("justificativa") or "").strip(),
        }

    def _run_chave_reserva_nao_tem(self, image_paths: list[str]) -> dict[str, Any]:
        key_paths = [p for p in image_paths if self._is_key_reserve_photo(p)]
        if not key_paths:
            return {
                "nivel_dano": "sem_dano",
                "peca": "chave reserva",
                "servicos": [],
                "preco_total": 0,
                "justificativa": "Checklist marcou chave reserva como nao tem, mas nenhuma foto de chave (ids 7, 8 ou 174) foi encontrada para validacao visual.",
                "fotos_analisadas": [],
            }

        analyses: list[dict[str, Any]] = []
        for path in key_paths:
            analysis = self._analyze_key_count(path)
            analysis["foto"] = path
            analyses.append(analysis)

        two_keys = [
            a for a in analyses
            if a.get("status") == "duas_ou_mais" and float(a.get("confidence") or 0.0) >= 0.7
        ]
        if two_keys:
            chosen = max(two_keys, key=lambda a: float(a.get("confidence") or 0.0))
            return {
                "nivel_dano": "sem_dano",
                "peca": "chave reserva",
                "servicos": [],
                "preco_total": 0,
                "justificativa": (
                    "Checklist marcou chave reserva como nao tem, mas a foto de chave mostra duas ou mais chaves. "
                    "Cobranca de chave reserva invalidada."
                ),
                "fotos_analisadas": [chosen.get("foto")] if chosen.get("foto") else key_paths[:1],
                "validacao_chave_reserva": analyses,
            }

        one_key = [
            a for a in analyses
            if a.get("status") == "uma" and int(a.get("key_count") or 0) == 1 and float(a.get("confidence") or 0.0) >= 0.7
        ]
        if one_key:
            chosen = max(one_key, key=lambda a: float(a.get("confidence") or 0.0))
            selected = find_services(
                self.lpu_items,
                ["chave reserva", "reposicao"],
                perito_filtro="acessorios",
                allow_global_fallback=False,
            )[:1]
            servicos_out = [ServiceItem(descricao=s.descricao, preco=s.preco) for s in selected]
            result = ExpertConsolidatedOutput(
                nivel_dano="reposicao",
                peca="chave reserva",
                servicos=servicos_out,
                preco_total=_total_price(selected),
                justificativa=(
                    "Checklist marcou chave reserva como nao tem e a foto de chave mostra apenas uma chave."
                ),
                fotos_analisadas=[str(chosen.get("foto") or key_paths[0])],
            ).model_dump()
            result.update(
                {
                    "force_include": True,
                    "origin": "checklist_chave_reserva_visual",
                    "validacao_chave_reserva": analyses,
                }
            )
            return result

        return {
            "nivel_dano": "sem_dano",
            "peca": "chave reserva",
            "servicos": [],
            "preco_total": 0,
            "justificativa": "Checklist marcou chave reserva como nao tem, mas a foto de chave nao permitiu confirmar se havia uma ou duas chaves.",
            "fotos_analisadas": key_paths[:1],
            "validacao_chave_reserva": analyses,
        }

    def _run_generico_acessorio(self, image_paths: list[str], **kwargs) -> dict[str, Any]:
        checklist_summary = kwargs.get("checklist_summary", "Nenhuma observação no checklist.")
        
        if kwargs.get("chave_reserva_nao_tem") is True:
            return self._run_chave_reserva_nao_tem(image_paths)

        prompt = f"""
Você é um PERITO TÉCNICO DE ACESSÓRIOS AUTOMOTIVOS.

OBJETIVO
- Identificar SOMENTE itens de acessórios que estejam visivelmente faltantes ou danificados.
- Evitar suposições: se o item não aparece na foto e o checklist não menciona, NÃO conclua que está faltando.
- Retornar SOMENTE JSON válido (sem Markdown, sem texto extra).

CONTEXTO DO CHECKLIST (use como pista; evidência visual tem prioridade):
{checklist_summary}

EXEMPLOS DE ACESSÓRIOS
- chave, manual, antena, kit ferramentas, triângulo, macaco, chave de roda, estepe (se aplicável), tapetes soltos, itens avulsos.

CRITÉRIOS
- "reposicao": item faltante (confirmado por checklist e/ou evidência visual clara de ausência quando deveria estar presente).
- "reparo": item presente porém danificado (quebrado, rasgado, sem funcionamento evidente na imagem).

ANTI-EXCESSO
- Se a imagem não mostra o item com clareza, prefira um resultado conservador e descreva que precisa de foto específica do acessório.

REGRA DE CHAVE DO VEICULO
- Nao cobre chave principal ou chave reserva neste fluxo generico.
- Chave principal/reserva so pode ser cobrada pela rotina especifica de chave reserva, quando o checklist marcar "Nao Tem" e a validacao visual confirmar apenas uma chave.
- Etiqueta, adesivo, chaveiro ou identificador preso a chave nao e dano de chave.

RETORNE SOMENTE ESTE JSON:
{{
    "peca": "nome da peça",
    "acao": "reposicao|reparo",
    "justificativa": "descrição objetiva baseada na evidência visual e checklist como contexto"
}}
"""
        try:
            raw = call_llm_with_image(prompt=prompt, image_path=image_paths[0])
            res = json.loads(raw.strip().replace("```json", "").replace("```", "").strip())
            if _is_vehicle_key_piece(res.get("peca")):
                return {
                    "nivel_dano": "sem_dano",
                    "peca": str(res.get("peca") or "chave"),
                    "servicos": [],
                    "preco_total": 0,
                    "justificativa": (
                        "Chave principal/reserva nao e cobrada pelo fluxo generico de acessorios; "
                        "aplica-se somente a regra especifica de chave reserva."
                    ),
                    "fotos_analisadas": image_paths,
                }
            acao = str(res.get("acao") or "").strip().lower()
            if acao not in {"reposicao", "reparo"}:
                return {
                    "nivel_dano": "sem_dano",
                    "peca": str(res.get("peca") or "acessorio"),
                    "servicos": [],
                    "preco_total": 0,
                    "justificativa": "Acao invalida ou ausente na analise generica de acessorios.",
                    "fotos_analisadas": image_paths,
                }
            selected = find_services(
                self.lpu_items,
                [res["peca"], acao],
                perito_filtro="acessorios",
                allow_global_fallback=False,
            )[:1]
            if _contains_vehicle_key_service(selected):
                return {
                    "nivel_dano": "sem_dano",
                    "peca": str(res.get("peca") or "chave"),
                    "servicos": [],
                    "preco_total": 0,
                    "justificativa": (
                        "Servico de chave principal/reserva bloqueado no fluxo generico de acessorios; "
                        "aplica-se somente a regra especifica de chave reserva."
                    ),
                    "fotos_analisadas": image_paths,
                }
            servicos_out = [ServiceItem(descricao=s.descricao, preco=s.preco) for s in selected]
            return ExpertConsolidatedOutput(
                nivel_dano=acao if selected else "sem_dano", peca=res["peca"], servicos=servicos_out,
                preco_total=_total_price(selected),
                justificativa=res.get("justificativa"), fotos_analisadas=image_paths
            ).model_dump()
        except: return {"erro": "falha acessorios"}

    def _merge_results(self, results: list[dict[str, Any]], image_paths: list[str]) -> dict[str, Any]:
        valid_results = [r for r in results if isinstance(r, dict) and not r.get("erro")]
        charged_items = [
            r
            for r in valid_results
            if isinstance(r.get("servicos"), list) and len(r.get("servicos") or []) > 0
        ]

        if not charged_items:
            justificativas = [
                str(r.get("justificativa") or "").strip()
                for r in valid_results
                if str(r.get("justificativa") or "").strip()
            ]
            out: dict[str, Any] = {
                "nivel_dano": "sem_dano",
                "peca": "acessorios",
                "servicos": [],
                "preco_total": 0,
                "justificativa": "; ".join(justificativas) if justificativas else "Sem danos identificados em acessorios.",
                "fotos_analisadas": image_paths,
            }
            for result in valid_results:
                if result.get("validacao_chave_reserva"):
                    out["validacao_chave_reserva"] = result.get("validacao_chave_reserva")
                    break
            return out

        if len(charged_items) == 1:
            return charged_items[0]

        servicos_flat: list[Any] = []
        fotos_flat: list[str] = []
        preco_total = 0.0
        any_sob_consulta = False
        has_reposicao = False

        for item in charged_items:
            if str(item.get("nivel_dano") or "").strip().lower() == "reposicao":
                has_reposicao = True

            servicos = item.get("servicos")
            if isinstance(servicos, list):
                servicos_flat.extend(servicos)

            fotos = item.get("fotos_analisadas")
            if isinstance(fotos, list):
                fotos_flat.extend([p for p in fotos if isinstance(p, str)])

            pt = item.get("preco_total")
            if isinstance(pt, (int, float)):
                preco_total += float(pt)
            elif str(pt).strip().lower() == "sob consulta":
                any_sob_consulta = True

        fotos_out: list[str] = []
        seen: set[str] = set()
        for path in fotos_flat:
            if path in seen:
                continue
            seen.add(path)
            fotos_out.append(path)

        out = {
            "nivel_dano": "reposicao" if has_reposicao else "reparo",
            "peca": "acessorios",
            "servicos": servicos_flat,
            "preco_total": "Sob consulta" if any_sob_consulta else float(preco_total),
            "justificativa": "; ".join(
                [
                    f"{item.get('peca')}: {item.get('justificativa')}"
                    for item in charged_items
                    if item.get("peca")
                ]
            ),
            "fotos_analisadas": fotos_out,
            "itens": charged_items,
        }
        for result in valid_results:
            if result.get("validacao_chave_reserva"):
                out["validacao_chave_reserva"] = result.get("validacao_chave_reserva")
                break
        return out

    def run(self, image_paths: list[str], **kwargs) -> dict[str, Any]:
        checklist_summary = kwargs.get("checklist_summary", "Nenhuma observacao no checklist.")
        results: list[dict[str, Any]] = []

        if kwargs.get("chave_reserva_nao_tem") is True:
            results.append(self._run_chave_reserva_nao_tem(image_paths))

        generic_paths = [p for p in image_paths if not self._is_key_reserve_photo(p)]
        if generic_paths:
            results.append(
                self._run_generico_acessorio(
                    generic_paths,
                    checklist_summary=checklist_summary,
                    chave_reserva_nao_tem=False,
                )
            )

        return self._merge_results(results, image_paths)

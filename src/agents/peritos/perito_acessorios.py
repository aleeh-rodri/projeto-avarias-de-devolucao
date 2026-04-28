from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any
from core.llm_gate_client import call_llm_with_image
from core.lpu import LpuItem, find_services, load_lpu_items
from core.schemas import ServiceItem, ExpertConsolidatedOutput
from agents.peritos.base_perito import BasePerito

@dataclass(frozen=True)
class ConfigPeritoAcessorios:
    caminho_lpu_xlsx: str

class PeritoAcessorios(BasePerito):
    def __init__(self, config: ConfigPeritoAcessorios):
        self.config = config
        self.lpu_items = load_lpu_items(config.caminho_lpu_xlsx)

    def run(self, image_paths: list[str], **kwargs) -> dict[str, Any]:
        checklist_summary = kwargs.get("checklist_summary", "Nenhuma observação no checklist.")
        
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
            selected = find_services(
                self.lpu_items,
                [res['peca'], res['acao']],
                perito_filtro="acessorios",
                allow_global_fallback=False,
            )[:1]
            servicos_out = [ServiceItem(descricao=s.descricao, preco=s.preco) for s in selected]
            return ExpertConsolidatedOutput(
                nivel_dano="reposicao", peca=res['peca'], servicos=servicos_out,
                preco_total=sum(float(s.preco) for s in selected if isinstance(s.preco, (int, float))),
                justificativa=res['justificativa'], fotos_analisadas=image_paths
            ).model_dump()
        except: return {"erro": "falha acessorios"}

from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any
from core.llm_gate_client import call_llm_with_image
from core.lpu import LpuItem, find_services, load_lpu_items
from core.schemas import ServiceItem, ExpertConsolidatedOutput
from agents.peritos.base_perito import BasePerito

@dataclass(frozen=True)
class ConfigPeritoInterior:
    caminho_lpu_xlsx: str

class PeritoInterior(BasePerito):
    def __init__(self, config: ConfigPeritoInterior):
        self.config = config
        self.lpu_items = load_lpu_items(config.caminho_lpu_xlsx)

    def run(self, image_paths: list[str], **kwargs) -> dict[str, Any]:
        checklist_summary = kwargs.get("checklist_summary", "Nenhuma observação no checklist.")

        # Heurística: quando o checklist explicitamente diz que NÃO há avaria de interior,
        # não devemos cobrar higienização sem evidência muito clara na foto.
        chk_norm = (checklist_summary or "").lower().replace(" ", "")
        checklist_sem_interior = ("interiorbancosnãotem" in chk_norm or "interiorbancosnaotem" in chk_norm) and (
            "tapetecarpetenãotem" in chk_norm or "tapetecarpetenaotem" in chk_norm
        )
        
        prompt = f"""
Você é um PERITO TÉCNICO DE INTERIOR DE VEÍCULOS.

OBJETIVO
- Identificar avarias internas com evidência visual (bancos, tapetes/carpete, forro de teto, painel).
- Evitar falso positivo de higienização: só indicar quando houver sujeira/contaminação claramente visível.
- Retornar APENAS JSON válido (sem Markdown, sem texto extra).

CONTEXTO DO CHECKLIST (use como pista; evidência visual tem prioridade):
{checklist_summary}

DEFINIÇÃO TÉCNICA (o que conta como avaria)
- Banco (tecido/couro): furo, rasgo, costura aberta, queimado, descascado, espuma aparente.
- Tapete/carpete: rasgo, desgaste extremo, mancha/sujeira pesada impregnada.
- Forro teto: rasgo, descolamento, mancha/umidade evidente.
- Painel/console: peça quebrada, faltante, trinca, riscos profundos.

HIGIENIZAÇÃO (quando cabe)
- Indicar "higienizacao" somente se houver sujeira severa evidente (mancha grande, lama, derramamento, mofo) OU lixo/contaminação claramente visível.
- Poeira leve, marca de dedo e sujeira discreta NÃO são higienização.

SEVERIDADE (determinística)
- sem_dano: nada evidente OU foto não permite avaliar.
- leve: risco/arranhão leve interno, sujeira leve (sem higienização), marca discreta.
- moderado: rasgo pequeno, dano localizado que requer reparo (estofaria/peça), mancha significativa.
- grave: rasgo grande, peça quebrada/faltante, dano que indica troca do item.

REGRAS ANTI-EXCESSO
- Se a foto estiver ruim/cortada/escura, responda sem_dano e explique que precisa de foto mais próxima.
- Não use checklist para “criar” avaria sem evidência visual.

RETORNE APENAS ESTE JSON:
{{
    "peca": "banco|tapete|forro teto|interior|painel",
    "nivel_dano": "sem_dano|leve|moderado|grave",
    "acao": "reparo|higienizacao|troca",
    "justificativa": "descrição técnica objetiva baseada na evidência visual e checklist como contexto"
}}
"""
        try:
            raw = call_llm_with_image(prompt=prompt, image_path=image_paths[0])
            raw = raw.strip()
            
            # Limpeza de markdown code blocks
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"): 
                    raw = raw[4:]
            
            res = json.loads(raw.strip())

            # Se o checklist fala explicitamente que não há avarias de interior, evita cobrar
            # higienização por default.
            if checklist_sem_interior and res.get("acao") == "higienizacao":
                return ExpertConsolidatedOutput(
                    nivel_dano="sem_dano",
                    peca=res.get("peca", "interior"),
                    servicos=[],
                    preco_total=0.0,
                    justificativa="Checklist indica sem avarias internas; higienização não cobrada sem evidência clara.",
                    fotos_analisadas=image_paths,
                ).model_dump()
            
            # Se for sem dano, sai cedo
            if res.get("nivel_dano") == "sem_dano":
                return ExpertConsolidatedOutput(
                    nivel_dano="sem_dano", peca=res.get('peca', 'interior'), servicos=[],
                    preco_total=0.0, justificativa="Sem danos identificados.",
                    fotos_analisadas=image_paths
                ).model_dump()

            # Busca na LPU com palavras-chave baseadas na ação e peça
            acao = res.get('acao', 'higienizacao')
            peca = res.get('peca', 'interior')
            
            kws = [peca, acao]
            if acao == "higienizacao": kws = ["higienização"]
            
            # Busca restrita para evitar lixo
            selected = find_services(
                self.lpu_items,
                kws,
                perito_filtro="interior",
                modo_restrito=True,
                allow_global_fallback=False,
            )
            if not selected:
                selected = find_services(
                    self.lpu_items,
                    kws,
                    perito_filtro="interior",
                    allow_global_fallback=False,
                )[:1]

            # Para evitar duplicidade (ex.: duas linhas de higienização), mantém apenas o melhor match.
            if len(selected) > 1:
                selected = selected[:1]
            
            servicos_out = [ServiceItem(descricao=s.descricao, preco=s.preco) for s in selected]
            
            total = 0.0
            for s in selected:
                if isinstance(s.preco, (int, float)): total += float(s.preco)

            return ExpertConsolidatedOutput(
                nivel_dano=res.get('nivel_dano', 'moderado'),
                peca=peca, 
                servicos=servicos_out,
                preco_total=round(total, 2),
                justificativa=res.get('justificativa', ''), 
                fotos_analisadas=image_paths
            ).model_dump()
        except Exception as e:
            return {"erro": f"falha interior: {str(e)}"}

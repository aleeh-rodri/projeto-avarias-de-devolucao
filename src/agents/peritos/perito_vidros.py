from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any
from core.llm_gate_client import call_llm_with_image
from core.lpu import LpuItem, find_services, load_lpu_items
from core.schemas import ServiceItem, ExpertConsolidatedOutput
from agents.peritos.base_perito import BasePerito

@dataclass(frozen=True)
class ConfigPeritoVidros:
    caminho_lpu_xlsx: str

class PeritoVidros(BasePerito):
    def __init__(self, config: ConfigPeritoVidros):
        self.config = config
        self.lpu_items = load_lpu_items(config.caminho_lpu_xlsx)

    def run(self, image_paths: list[str], **kwargs) -> dict[str, Any]:
        checklist_summary = kwargs.get("checklist_summary", "Nenhuma observação no checklist.")
        
        prompt = f"""
Você é um PERITO TÉCNICO ESPECIALISTA EM VIDROS E CRISTAIS AUTOMOTIVOS.

OBJETIVO
- Identificar avarias visíveis em: para-brisa, vidro traseiro, vidro lateral.
- Decidir entre: sem_dano, reparo (quando tecnicamente cabível) ou troca (quando obrigatório).
- Retornar APENAS JSON válido (sem Markdown, sem texto extra).

CONTEXTO DO CHECKLIST (use como pista; evidência visual tem prioridade):
{checklist_summary}

DEFINIÇÕES TÉCNICAS
- Pique: lasca/ponto pequeno (impacto) sem propagação.
- "Estrela"/fissura: rachadura irradiada a partir do impacto.
- Trinca: linha contínua/propagada (tende a não ser reparável).

CRITÉRIOS DE DECISÃO (determinísticos)
1) sem_dano:
    - nenhum dano visível; OU foto não permite avaliar.

2) troca (obrigatória quando houver qualquer um):
    - trinca/fissura/estrela no lado do motorista (campo de visão principal), mesmo que pequena;
    - dano grande/espalhado;
    - dano atingindo ou muito próximo das bordas;
    - múltiplos pontos com propagação (quando visível).

3) reparo (exceção, quando cabível):
    - pique pequeno e isolado fora do campo de visão do motorista;
    - sem trinca propagada e sem indício de dano em borda.

ANTI-EXCESSO
- Não invente dano por reflexo, sujeira, gota d'água ou distorção.
- Se não conseguir localizar com clareza se está no campo do motorista, cite a incerteza na justificativa.

RETORNE APENAS ESTE JSON:
{{
  "peca": "parabrisa|vidro traseiro|vidro lateral",
  "lado": "motorista|passageiro|centro|nao_se_aplica",
  "acao": "reparo|troca|sem_dano",
  "justificativa": "descrição técnica objetiva baseada na evidência visual e checklist como contexto"
}}
"""
        try:
            raw = call_llm_with_image(prompt=prompt, image_path=image_paths[0])
            raw = raw.strip().replace("```json", "").replace("```", "").strip()
            res = json.loads(raw)

            if res.get("acao") == "sem_dano":
                return ExpertConsolidatedOutput(
                    nivel_dano="sem_dano",
                    peca=res.get("peca", "vidro"),
                    servicos=[],
                    preco_total=0.0,
                    justificativa=res.get("justificativa", "Sem danos identificados."),
                    fotos_analisadas=image_paths,
                ).model_dump()
            
            # Regra de negócio: motorista sempre troca se houver dano identificado como troca/reparo significativo
            acao = res['acao']
            if res.get('lado') == 'motorista' and acao == 'reparo':
                acao = 'troca'
                res['justificativa'] += " (Ação alterada para troca por estar no campo de visão do motorista)."

            selected: list[LpuItem] = []
            pecas_a_cotar: list[dict[str, Any]] = []

            # Busca LPU (sem fallback global para evitar serviços errados)
            if acao == "troca":
                # Peças são cotadas à parte: aqui buscamos apenas a mão de obra.
                kws_mo = ["mão de obra", "troca", res.get('peca', 'parabrisa')]
                selected.extend(
                    find_services(
                        self.lpu_items,
                        kws_mo,
                        perito_filtro="vidros",
                        modo_restrito=False,
                        allow_global_fallback=False,
                        fuzzy=False,
                    )[:1]
                )
            else:
                kws = [res.get('peca', 'parabrisa'), acao]
                selected = find_services(
                    self.lpu_items,
                    kws,
                    perito_filtro="vidros",
                    modo_restrito=True,
                    allow_global_fallback=False,
                    fuzzy=False,
                )

            # Dedup preservando ordem
            dedup: list[LpuItem] = []
            seen: set[str] = set()
            for it in selected:
                k = str(it.descricao).strip().lower()
                if k and k not in seen:
                    seen.add(k)
                    dedup.append(it)
            selected = dedup

            # Em troca, NUNCA levar item de reparo junto
            if acao == "troca" and selected:
                selected = [s for s in selected if "reparo" not in (s.descricao or "").lower()]

            # Regra de negócio: peças são cotadas à parte.
            # Se a ação é TROCA, sinalizar que existe peça para cotação manual, sem adicionar como serviço.
            if acao == "troca":
                peca = str(res.get("peca", "vidro") or "vidro")
                pecas_a_cotar.append(
                    {
                        "descricao": peca,
                        "quantidade": 1,
                        "observacao": "Peça deve ser cotada à parte (informar valor manualmente).",
                    }
                )

            servicos_out = [ServiceItem(descricao=s.descricao, preco=s.preco) for s in selected]
            total = sum(float(s.preco) for s in selected if isinstance(s.preco, (int, float)))

            out = ExpertConsolidatedOutput(
                nivel_dano=acao,
                peca=res['peca'],
                servicos=servicos_out,
                preco_total=round(total, 2),
                justificativa=res['justificativa'],
                fotos_analisadas=image_paths
            ).model_dump()
            if pecas_a_cotar:
                out["pecas_a_cotar"] = pecas_a_cotar
            return out
        except Exception as e:
            return {"erro": f"falha vidros: {str(e)}"}

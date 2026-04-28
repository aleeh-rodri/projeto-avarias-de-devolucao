from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any
from core.llm_gate_client import call_llm_with_image
from core.lpu import LpuItem, find_services, load_lpu_items
from core.schemas import ServiceItem, ExpertConsolidatedOutput
from agents.peritos.base_perito import BasePerito

@dataclass(frozen=True)
class ConfigPeritoPneusRodas:
    caminho_lpu_xlsx: str

class PeritoPneusRodas(BasePerito):
    def __init__(self, config: ConfigPeritoPneusRodas):
        self.config = config
        self.lpu_items = load_lpu_items(config.caminho_lpu_xlsx)

    def run(self, image_paths: list[str], **kwargs) -> dict[str, Any]:
        checklist_summary = kwargs.get("checklist_summary", "Nenhuma observação no checklist.")

        imagens_usadas = kwargs.get("imagens_usadas")

        def _expected_wheel_from_part_id(part_id: str | None) -> tuple[str, str, str]:
            """Retorna (eixo, lado, part_id_norm) a partir do part_id canônico."""
            pid = (part_id or "").strip().lower()
            if not pid.startswith("roda_"):
                return ("", "", pid)
            eixo = "dianteira" if "dianteira" in pid else ("traseira" if "traseira" in pid else "")
            lado = "direita" if pid.endswith("direita") else ("esquerda" if pid.endswith("esquerda") else "")
            return (eixo, lado, pid)

        expected_part_id: str | None = None
        if isinstance(imagens_usadas, list) and imagens_usadas:
            # Se todas as imagens forem da mesma roda, usa isso para restringir a LPU.
            pids = [str(m.get("part_id") or "").strip().lower() for m in imagens_usadas if isinstance(m, dict)]
            pids = [p for p in pids if p]
            if pids and len(set(pids)) == 1:
                expected_part_id = pids[0]

        # Heurística importante: no checklist deste caso aparece "Liga Leve Não Tem".
        # Isso reduz falsos positivos de "roda de liga leve" (e evita cobrar reparo de liga leve indevidamente).
        chk_norm = (checklist_summary or "").lower().replace(" ", "")
        liga_leve_nao_tem = "ligalevenãotem" in chk_norm or "ligalevenaotem" in chk_norm

        # Checklist também costuma indicar explicitamente calotas / jogo de calotas.
        chk_norm_ws = (checklist_summary or "").lower()
        menciona_calota = ("calota" in chk_norm_ws) or ("calotas" in chk_norm_ws) or ("jogo de calota" in chk_norm_ws) or ("jogo de calotas" in chk_norm_ws)
        
        prompt = f"""
Você é um PERITO TÉCNICO ESPECIALISTA EM RODAS, PNEUS E CALOTAS.

OBJETIVO
- Identificar avarias visíveis e classificar tecnicamente a peça (calota, roda de ferro, roda de liga leve).
- Decidir ação: reparo (padrão) vs troca (quando fizer sentido técnico/prático).
- Retornar APENAS JSON válido (sem Markdown, sem texto extra).

CONTEXTO DO CHECKLIST (use como pista; evidência visual tem prioridade):
{checklist_summary}

DEFINIÇÕES TÉCNICAS
- Ralada/ralado de guia: desgaste/arranhão na borda/face da roda.
- Amassado: deformação no aro.
- Trinca: ruptura/fissura no aro (risco de segurança).
- Quebra de calota: lasca/falta de material, travas rompidas.

CRITÉRIOS (determinísticos)
1) calota:
    - qualquer quebra, falta de material, trinca ou risco profundo -> ação "troca".
    - risco leve superficial pode ser classificado como leve; ainda assim, se a calota estiver danificada, prefira "troca".

2) roda de liga leve:
    - ralada de guia/arranhões -> ação "reparo".
    - amassado no aro ou trinca -> nível "grave" e ação "reparo" ou "troca" (se a trinca for clara, prefira "troca").

3) roda de ferro:
    - amassado -> "reparo" quando parecer corrigível.
    - trinca evidente -> "troca".

SEVERIDADE
- sem_dano: nada evidente OU foto não permite avaliar.
- leve: marca superficial/ralado leve sem deformação.
- moderado: ralada profunda extensa ou deformação leve.
- grave: deformação clara do aro, trinca, quebra (calota) ou risco de segurança.

ANTI-EXCESSO
- Não confunda sujeira com dano.
- Se não der para identificar o tipo de peça com segurança, escolha o mais provável e descreva a dúvida na justificativa.

RETORNE APENAS ESTE JSON:
{{
  "peca": "roda liga leve|roda ferro|calota",
  "nivel_dano": "sem_dano|leve|moderado|grave",
  "acao": "reparo|troca",
  "justificativa": "descrição técnica objetiva baseada na evidência visual e checklist como contexto"
}}
"""
        try:
            raw = call_llm_with_image(prompt=prompt, image_path=image_paths[0])
            raw = raw.strip()
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"): raw = raw[4:]
            res = json.loads(raw.strip())
            
            peca = res.get('peca', 'roda')
            acao = res.get('acao', 'reparo')

            # Se checklist diz que não tem liga leve, força a peça para calota (conservador).
            peca_norm = (peca or "").lower()
            if (liga_leve_nao_tem and "liga" in peca_norm) or menciona_calota:
                peca = "calota"
                acao = "troca"
                peca_norm = "calota"

            # Calota: não faz sentido "reparo" na prática; força troca.
            if "calota" in peca_norm and acao == "reparo":
                acao = "troca"

            if res.get("nivel_dano") == "sem_dano":
                return ExpertConsolidatedOutput(
                    nivel_dano="sem_dano", peca=peca, servicos=[],
                    preco_total=0.0, justificativa="Sem danos identificados nas rodas.",
                    fotos_analisadas=image_paths
                ).model_dump()

            # Lógica de busca na LPU mais restrita
            if "calota" in peca_norm:
                # Obs: na LPU a descrição é "Calota (VALOR DO JOGO) ..." e pode não conter a palavra "troca".
                kws = ["calota", "jogo"]
            elif "ferro" in peca_norm:
                kws = ["roda", "ferro", acao]
            elif "liga" in peca_norm:
                # evita depender de "de" (roda de liga leve vs roda liga leve)
                kws = ["roda", "liga", "leve", acao]
            else:
                kws = ["roda", acao]

            # Se sabemos qual roda é (ex.: roda_dianteira_direita), restringe por posição.
            eixo, lado, _pid = _expected_wheel_from_part_id(expected_part_id)
            if eixo:
                kws.append(eixo)
            if lado:
                kws.append(lado)
            
            # Tenta busca restrita primeiro forcando o perito_filtro
            selected = find_services(
                self.lpu_items,
                kws,
                perito_filtro="pneus_rodas",
                modo_restrito=True,
                allow_global_fallback=False,
                fuzzy=False,
            )
            if not selected:
                # Se não achar nada no modo restrito do perito, tenta o modo normal MAS AINDA no perito pneus_rodas
                selected = find_services(
                    self.lpu_items,
                    kws,
                    perito_filtro="pneus_rodas",
                    allow_global_fallback=False,
                    fuzzy=False,
                )[:1]

            # A LPU desta workspace tem itens de banco/forro erroneamente marcados como pneus_rodas; filtra na saída
            if selected:
                selected = [
                    s for s in selected
                    if "banco" not in (s.descricao or "").lower()
                    and "forro" not in (s.descricao or "").lower()
                ]

            # Evita pegar múltiplas rodas (dianteira/traseira etc.) por conta de keywords genéricas.
            # Para este perito, sempre retorna no máximo 1 linha de serviço por evidência.
            if selected:
                selected = selected[:1]

            # Se AINDA ASSIM não achar nada, retorna lista vazia para evitar pegar "bancos" como fallback global
            if not selected:
                servicos_out = []
                total = 0.0
            else:
                servicos_out = [ServiceItem(descricao=s.descricao, preco=s.preco) for s in selected]
                total = sum(float(s.preco) for s in selected if isinstance(s.preco, (int, float)))

            return ExpertConsolidatedOutput(
                nivel_dano=res.get('nivel_dano', 'moderado'),
                peca=peca, 
                servicos=servicos_out,
                preco_total=round(total, 2),
                justificativa=res.get('justificativa', ''), 
                fotos_analisadas=image_paths
            ).model_dump()
        except Exception as e:
            return {"erro": f"falha rodas: {str(e)}"}

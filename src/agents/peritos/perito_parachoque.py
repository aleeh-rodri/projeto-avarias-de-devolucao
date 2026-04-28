from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.llm_gate_client import call_llm_with_image
from core.lpu import LpuItem, find_services, load_lpu_items
from core.schemas import BumperExpertOutput, ExpertConsolidatedOutput, ServiceItem
from agents.peritos.base_perito import BasePerito


@dataclass(frozen=True)
class ConfigPeritoParachoque:
    caminho_lpu_xlsx: str


class PeritoParachoque(BasePerito):
    def __init__(self, config: ConfigPeritoParachoque):
        self.config = config
        self.lpu_items = load_lpu_items(config.caminho_lpu_xlsx)

    def run(self, image_paths: list[str], **kwargs) -> dict[str, Any]:
        if not image_paths:
            return {"erro": "imagem invalida"}
        
        checklist_summary = kwargs.get("checklist_summary", "Nenhuma observação no checklist.")
        return run_perito_parachoque_multiplas_fotos(image_paths, self.config, checklist_summary)


def build_bumper_prompt(checklist_summary: str = "") -> str:
     return f"""
Você é um PERITO TÉCNICO ESPECIALISTA EM PARA-CHOQUES (plástico).

OBJETIVO
- Analisar a foto e classificar tecnicamente APENAS o para-choque.
- Ser conservador: só marcar avaria quando houver evidência visual.
- Retornar SOMENTE JSON válido (sem Markdown, sem texto extra).

CONTEXTO DO CHECKLIST (apenas como pista; o que prevalece é o que aparece na foto):
{checklist_summary}

REGRAS TÉCNICAS (definição de avaria e severidade)
1) "sem_dano": não há evidência clara de avaria no para-choque OU a imagem está desfocada/escura/cortada a ponto de impedir avaliação confiável.
    - Nesse caso, descreva em observações: "Imagem ignorada: qualidade/visibilidade insuficiente".

2) "leve" (SOMENTE PINTURA é suficiente; NÃO exigir recuperação):
    - riscos/arranhões superficiais na pintura ou transferência de tinta
    - sem deformação do plástico (sem amassado/abaulamento), sem trinca, sem quebra
    - sem perda de material
    - NÃO confundir reflexo, sujeira, gota d'água ou sombra com risco.

3) "moderado" (exige recuperação/reparo do para-choque + pintura):
    - deformação visível (amassado, vinco, abaulamento) no plástico
    - arranhões profundos com sulco evidente (além da camada de verniz), ou esfolado amplo
    - trinca pequena NÃO atravessante quando houver indício de reparabilidade

4) "grave" (evento severo; tende a troca/serviço pesado):
    - trinca atravessante/irreparável (rachadura aberta) OU quebra com falta de material
    - perda de material relevante (ex.: lasca/furo) ou desalinhamento importante
    - engates/abas rompidos em múltiplos pontos (se visível)

ANTI-EXCESSO
- Não invente danos ocultos.
- Não use o checklist para "criar" avaria sem evidência visual.
- Se houver dúvida real entre sem_dano e leve, escolha "sem_dano" e peça melhor foto nas observações.

COMO PREENCHER OS CAMPOS
- posicao_parachoque: sempre escolha "dianteiro" ou "traseiro" (se estiver incerto, escolha o mais provável e mencione a incerteza nas observações).
- localizacao_avaria: onde está o dano PRINCIPAL (se não der para localizar, use "nao_identificavel").
- tipo_avaria: escolha o tipo predominante.
- observacoes_objetivas: frase curta, objetiva, técnica; cite sinais visuais (ex.: "risco superficial sem deformação", "trinca atravessante com falta de material").

RETORNE SOMENTE ESTE JSON:
{{
  "posicao_parachoque": "dianteiro|traseiro",
  "nivel_dano_parachoque": "sem_dano|leve|moderado|grave",
  "localizacao_avaria": "canto_esquerdo|canto_direito|centro|superior|inferior|nao_identificavel",
  "tipo_avaria": "arranhao|amassado|quebra|trinca|outro",
  "observacoes_objetivas": "curto e direto, baseado apenas na evidência visual e no checklist como contexto"
}}
""".strip()


def _clean_json_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    return raw


def _severity_rank(nivel: str) -> int:
    return {"sem_dano": 0, "leve": 1, "moderado": 2, "grave": 3}.get(nivel, -1)


def _select_lpu_services(
    items: list[LpuItem],
    posicao: str,
    nivel: str,
) -> list[LpuItem]:

    if nivel == "sem_dano":
        return []

    # Keywords em tokens para não depender de frase exata (ex.: "Pintura do para-choque...")
    if posicao == "dianteiro":
        pintura_keywords = ["pintura", "para-choque", "dianteiro"]
    elif posicao == "traseiro":
        pintura_keywords = ["pintura", "para-choque", "traseiro"]
    else:
        pintura_keywords = ["pintura", "para-choque"]

    if nivel == "leve":
        found = find_services(
            items,
            pintura_keywords,
            perito_filtro="parachoque",
            allow_global_fallback=False,
            modo_restrito=True,
            fuzzy=False,
        )
        # remove itens combinados quando posição é específica
        if posicao in {"dianteiro", "traseiro"}:
            found = [f for f in found if "dianteiro e traseiro" not in f.descricao.lower()]
        # fallback dentro do perito (ainda sem fuzzy) caso não bata no modo restrito
        if not found:
            found = find_services(
                items,
                pintura_keywords,
                perito_filtro="parachoque",
                allow_global_fallback=False,
                fuzzy=False,
            )
            if posicao in {"dianteiro", "traseiro"}:
                found = [f for f in found if "dianteiro e traseiro" not in f.descricao.lower()]
        # Preferir serviços que sejam só pintura (sem recuperação/reparo)
        def _rank_pintura(it: LpuItem) -> tuple[int, int, int]:
            d = (it.descricao or "").lower()
            has_pintura = 0 if "pintura" in d else 1
            has_recuperacao = 1 if ("recuper" in d or "reparo" in d) else 0
            has_troca = 1 if "troca" in d else 0
            return (has_pintura, has_recuperacao, has_troca)

        found = sorted(found, key=_rank_pintura)
        return found[:1]

    if nivel == "moderado":
        found = find_services(
            items,
            ["reparo", "peça", "plástica", *pintura_keywords],
            perito_filtro="parachoque",
            allow_global_fallback=False,
            modo_restrito=True,
            fuzzy=False,
        )
        if posicao in {"dianteiro", "traseiro"}:
            found = [f for f in found if "dianteiro e traseiro" not in f.descricao.lower()]
        if not found:
            found = find_services(
                items,
                ["reparo", "para-choque", posicao] if posicao in {"dianteiro", "traseiro"} else ["reparo", "para-choque"],
                perito_filtro="parachoque",
                allow_global_fallback=False,
                fuzzy=False,
            )
        return found[:2]

    if nivel == "grave":
        found = find_services(
            items,
            ["mão", "obra", "troca", "para-choque", *pintura_keywords],
            perito_filtro="parachoque",
            allow_global_fallback=False,
            modo_restrito=True,
            fuzzy=False,
        )
        if posicao in {"dianteiro", "traseiro"}:
            found = [f for f in found if "dianteiro e traseiro" not in f.descricao.lower()]
        if not found:
            found = find_services(
                items,
                ["troca", "para-choque", posicao] if posicao in {"dianteiro", "traseiro"} else ["troca", "para-choque"],
                perito_filtro="parachoque",
                allow_global_fallback=False,
                fuzzy=False,
            )
        return found[:2]

    return []


def run_perito_parachoque_multiplas_fotos(
    image_paths: list[str],
    config: ConfigPeritoParachoque,
    checklist_summary: str = "",
) -> dict:

    lpu_items = load_lpu_items(config.caminho_lpu_xlsx)
    avaliados = []

    progress_enabled = os.getenv("AGENTE_PROGRESS", "1").strip().lower() not in ("0", "false", "no")
    total = len(image_paths or [])

    for idx, p in enumerate(image_paths, start=1):
        if progress_enabled:
            try:
                name = Path(p).stem
            except Exception:
                name = str(p)
            print(f"[perito_parachoque] {idx}/{total} {name}", flush=True)
        raw = call_llm_with_image(prompt=build_bumper_prompt(checklist_summary), image_path=p)
        raw = _clean_json_fences(raw)

        try:
            d = json.loads(raw)
            parsed = BumperExpertOutput(**d)
        except Exception:
            continue

        avaliados.append({
            "posicao": parsed.posicao_parachoque,
            "nivel": parsed.nivel_dano_parachoque,
            "obs": parsed.observacoes_objetivas,
            "localizacao": str(d.get("localizacao_avaria", "") or "").strip().lower(),
            "tipo": str(d.get("tipo_avaria", "") or "").strip().lower(),
            "path": p,
        })

    if not avaliados:
        return {"erro": "imagem invalida"}

    def _best_for(posicao: str) -> dict[str, Any] | None:
        return max(
            (a for a in avaliados if a["posicao"] == posicao),
            default=None,
            key=lambda x: _severity_rank(x["nivel"]),
        )

    melhor_dianteiro = _best_for("dianteiro")
    melhor_traseiro = _best_for("traseiro")

    def _support_count(posicao: str, nivel: str) -> int:
        """Conta quantas fotos para a posição retornaram pelo menos o nível indicado."""
        target = _severity_rank(nivel)
        return sum(
            1
            for a in avaliados
            if a.get("posicao") == posicao and _severity_rank(str(a.get("nivel", "sem_dano"))) >= target
        )

    def _conservative_level(posicao: str, nivel: str) -> str:
        """Evita falso positivo de trinca/deformação em uma única foto."""
        n = (nivel or "").strip().lower()
        if n in {"grave", "moderado"}:
            if _support_count(posicao, n) < 2:
                # Se só uma foto sugeriu reparo/troca, tende a ser leitura errada de junta/sombra.
                return "leve"
        return n

    def _count_avarias_distintas(posicao: str) -> int:
        # Regra: quando há múltiplas avarias distintas no mesmo para-choque, a pintura pode ser cobrada por avaria.
        # Usamos a localização para aproximar "distintas".
        locs: set[str] = set()
        has_any_damage = False
        for a in avaliados:
            if a.get("posicao") != posicao:
                continue
            nivel = str(a.get("nivel", "sem_dano"))
            if nivel == "sem_dano":
                continue
            has_any_damage = True
            loc = str(a.get("localizacao") or "").strip().lower()
            if not loc:
                loc = "nao_identificavel"
            locs.add(loc)

        if not has_any_damage:
            return 0
        # se só veio "nao_identificavel", assume 1
        locs_sem_nao = {l for l in locs if l != "nao_identificavel"}
        count = len(locs_sem_nao) if locs_sem_nao else 1
        # proteção contra explosão por ruído do modelo
        return max(1, min(count, 3))

    itens: list[dict[str, Any]] = []
    servicos_flat: list[ServiceItem] = []
    total_geral = 0.0
    erros_lpu: list[str] = []
    justificativas: list[str] = []

    for posicao, melhor in (("dianteiro", melhor_dianteiro), ("traseiro", melhor_traseiro)):
        if not melhor:
            continue

        fotos_posicao = [a["path"] for a in avaliados if a["posicao"] == posicao and a.get("path")]

        nivel_raw = str(melhor.get("nivel", "sem_dano"))
        nivel = _conservative_level(posicao, nivel_raw)
        obs = str(melhor.get("obs", "")).strip()

        if nivel != (nivel_raw or "").strip().lower() and obs:
            obs = f"{obs} | calibracao: moderado/grave sem suporte >=2 fotos; tratado como leve/pintura"

        selected = _select_lpu_services(lpu_items, posicao, nivel)
        if nivel != "sem_dano" and not selected:
            erros_lpu.append(
                f"Falha LPU: dano '{nivel}' identificado para '{posicao}' mas nenhum serviço foi encontrado na LPU."
            )
            continue

        # Se houver múltiplas avarias distintas na MESMA posição, e o serviço for pintura,
        # é comum cobrar pintura por avaria/região.
        qtd_avarias = _count_avarias_distintas(posicao)
        selected_expanded: list[LpuItem] = []
        for s in selected:
            desc_l = (s.descricao or "").lower()
            is_pintura = "pintura" in desc_l
            is_troca = "troca" in desc_l
            times = qtd_avarias if (is_pintura and not is_troca and qtd_avarias > 1) else 1
            selected_expanded.extend([s] * times)

        servicos_out = [ServiceItem(descricao=s.descricao, preco=s.preco) for s in selected_expanded]
        servicos_flat.extend(servicos_out)

        total_pos = sum(float(s.preco) for s in selected_expanded if isinstance(s.preco, (int, float)))
        total_geral += total_pos

        if obs:
            justificativas.append(f"{posicao}: {nivel} ({obs})")
        else:
            justificativas.append(f"{posicao}: {nivel}")

        itens.append(
            ExpertConsolidatedOutput(
                nivel_dano=nivel,
                peca=f"para-choque {posicao}",
                servicos=servicos_out,
                preco_total=round(total_pos, 2),
                justificativa=f"Dano {nivel}. Obs: {obs}" if obs else f"Dano {nivel}.",
                fotos_analisadas=fotos_posicao,
            ).model_dump()
        )

    if erros_lpu and not itens:
        return {"erro": " | ".join(erros_lpu)}

    # Se houve dano em uma posição e falhou LPU na outra, retorna erro mas preserva o que deu certo.
    if erros_lpu and itens:
        return {
            "erro": " | ".join(erros_lpu),
            "itens": itens,
            "servicos": [s.model_dump() for s in servicos_flat],
            "preco_total": round(total_geral, 2),
            "fotos_analisadas": image_paths,
        }

    if not itens:
        # Fallback: se só retornou sem_dano em tudo, mantém compatibilidade.
        return ExpertConsolidatedOutput(
            nivel_dano="sem_dano",
            peca="para-choques",
            servicos=[],
            preco_total=0.0,
            justificativa="Sem evidência de dano em para-choque nas fotos fornecidas.",
            fotos_analisadas=image_paths,
        ).model_dump()

    nivel_final = max((i["nivel_dano"] for i in itens), key=_severity_rank)

    return {
        "nivel_dano": nivel_final,
        "peca": "para-choques",
        "itens": itens,  # breakdown por posição (dianteiro/traseiro)
        "servicos": [s.model_dump() for s in servicos_flat],  # compatibilidade com ExcelAgent
        "preco_total": round(total_geral, 2),
        "justificativa": "; ".join(justificativas) if justificativas else None,
        "fotos_analisadas": image_paths,
    }

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.llm_gate_client import call_llm_with_image
from core.lpu import LpuItem, find_services, load_lpu_items
from core.schemas import ServiceItem, ExpertConsolidatedOutput
from agents.peritos.base_perito import BasePerito


@dataclass(frozen=True)
class ConfigPeritoLataria:
    caminho_lpu_xlsx: str


class PeritoLataria(BasePerito):
    def __init__(self, config: ConfigPeritoLataria):
        self.config = config
        self.lpu_items = load_lpu_items(config.caminho_lpu_xlsx)

    def run(self, image_paths: list[str], **kwargs) -> dict[str, Any]:
        if not image_paths:
            return {"erro": "imagem invalida"}
        
        checklist_summary = kwargs.get("checklist_summary", "Nenhuma observação no checklist.")

        imagens_usadas = kwargs.get("imagens_usadas")

        def _expected_from_part_id(part_id: str) -> tuple[str, str]:
            pid = (part_id or "").strip().lower()
            if pid == "capo":
                return ("capo", "nao_se_aplica")
            if pid == "teto":
                return ("teto", "nao_se_aplica")
            if pid == "tampa_porta_malas":
                return ("tampa porta-malas", "nao_se_aplica")
            if pid == "parabarro_esquerdo":
                return ("para-barro", "esquerdo")

            if pid == "parabarro_direito":
                return ("para-barro", "direito")
            
            if pid.startswith("retrovisor_"):
                # Não force o lado: triagem pode errar esquerdo/direito.
                # O modelo deve inferir o lado pela imagem quando possível.
                return ("retrovisor", "")
            if pid.startswith("porta_dianteira_"):
                return ("porta dianteira", "direito" if pid.endswith("direita") else "esquerdo")
            if pid.startswith("porta_traseira_"):
                return ("porta traseira", "direito" if pid.endswith("direita") else "esquerdo")
            if pid.startswith("paralama_dianteiro_"):
                return ("paralama dianteiro", "direito" if pid.endswith("direito") else "esquerdo")
            if pid.startswith("paralama_traseiro_"):
                return ("paralama traseiro", "direito" if pid.endswith("direito") else "esquerdo")
            # fallback: mantém o que o modelo disser
            return ("", "")

        expected_part_ids_by_index: list[str | None] = [None] * len(image_paths)
        if isinstance(imagens_usadas, list) and len(imagens_usadas) == len(image_paths):
            for i, meta in enumerate(imagens_usadas):
                if isinstance(meta, dict):
                    expected_part_ids_by_index[i] = str(meta.get("part_id") or "").strip() or None

        def _build_prompt(expected_part_id: str | None, expected_peca: str | None, expected_lado: str | None) -> str:
            expected_block = ""
            if expected_part_id and expected_peca and expected_lado:
                expected_block = f"""

PEÇA ESPERADA (da triagem): {expected_part_id}
Você DEVE avaliar SOMENTE esta peça. Se a foto NÃO mostrar claramente essa peça (ex.: capô aberto mostrando o motor, sem a superfície externa), responda:
- nivel_dano = sem_dano
- justificativa = explique que a peça esperada não está visível/avaliável

Preencha obrigatoriamente:
- peca = \"{expected_peca}\"
- lado = \"{expected_lado}\"
"""

            return f"""
Você é um PERITO TÉCNICO ESPECIALISTA EM FUNILARIA E PINTURA (LATARIA).

OBJETIVO
- Identificar APENAS avarias visíveis na lataria (chapa/pintura) e classificá-las por severidade.
- Ser conservador: se não houver evidência visual suficiente, responda como sem_dano.
- Retornar SOMENTE JSON válido (sem Markdown, sem texto extra).

CONTEXTO DO CHECKLIST (use como pista; a evidência visual tem prioridade):
{checklist_summary}
{expected_block}

PEÇAS ALVO (somente estas):
- Portas, Capô, Teto, Laterais, Para-lamas, Caixa de Ar, Colunas, Tampa/Porta-malas, Retrovisores.

DEFINIÇÕES TÉCNICAS (o que é avaria)
- Arranhão/Risco: marca linear na pintura (pode ter transferência de tinta).
- Amassado/Deformação: variação de forma/volume, vinco, ondulação, sombra de amassado.
- Trinca/Rasgo: ruptura/abertura na chapa (raro; mais comum em plásticos).
- Quebra: normalmente não se aplica à chapa; use "outro" se o dano for diferente.

SEVERIDADE (decisão determinística)
1) sem_dano:
    - não há avaria claramente visível na peça; OU
    - a imagem está desfocada/escura/cortada e não permite identificar peça e dano com confiança; OU
    - a peça esperada não está visível/avaliável.

2) leve (pintura/martelinho; sem funilaria pesada):
    - riscos superficiais sem deformação; ou
    - amassado MUITO leve sem vinco marcado (típico de martelinho).

3) moderado (funilaria/recuperação + pintura):
    - amassado com vinco, deformação evidente, necessidade de repuxo/massa.

4) grave (evento severo; troca/solda/serviço estrutural):
    - rasgo/abertura na chapa, deformação estrutural evidente, ou comprometimento de alinhamento.

REGRAS ANTI-EXCESSO
- Não inclua peças não visíveis/ocultas.
- Não marque dano por reflexo, sujeira, sombra ou água.

COMO PREENCHER
- "peca": nome direto da peça.
- "lado": "esquerdo"/"direito" quando aplicável; senão "nao_se_aplica".
- "localizacao_avaria": onde está o dano principal.
- "tipo_avaria": escolha o tipo predominante.
- "justificativa": objetiva, técnica, cite evidência visual.

REGRA IMPORTANTE (RETROVISOR)
- Se a peça for "retrovisor", o campo "lado" DEVE ser "esquerdo" ou "direito".
- NÃO use "nao_se_aplica" para retrovisor.

RETORNE SOMENTE ESTE JSON:
{{
  "peca": "nome da peca",
  "lado": "esquerdo|direito|nao_se_aplica",
  "nivel_dano": "sem_dano|leve|moderado|grave",
  "localizacao_avaria": "canto_esquerdo|canto_direito|centro|superior|inferior|nao_identificavel",
  "tipo_avaria": "arranhao|amassado|quebra|trinca|outro",
  "justificativa": "descrição técnica baseada na evidência visual e checklist como contexto"
}}
"""
        def _clean_json_fences(raw: str) -> str:
            raw = (raw or "").strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            return raw

        def _severity_rank(nivel: str) -> int:
            return {"sem_dano": 0, "leve": 1, "moderado": 2, "grave": 3}.get((nivel or "").strip().lower(), -1)

        def _acao_from_nivel(nivel: str) -> str:
            n = (nivel or "").strip().lower()
            if n == "leve":
                return "pintura"
            if n == "moderado":
                return "recuperação"
            if n == "grave":
                return "troca"
            return "pintura"

        def _kws_for(peca_detectada: str, lado: str, acao: str) -> list[str]:
            peca_norm = (peca_detectada or "").strip().lower()
            lado_norm = (lado or "").strip().lower()

            kws: list[str] = [peca_detectada, acao]
            if "retrovisor" in peca_norm:
                kws = ["retrovisor", acao]
                if lado_norm == "esquerdo":
                    kws.append("esquerdo")
                elif lado_norm == "direito":
                    kws.append("direito")
            elif "porta" in peca_norm:
                # Importante: não misturar serviços de porta dianteira/traseira.
                # A LPU costuma ter linhas separadas (ex.: "porta dianteira direita" vs "porta traseira direita").
                if "dianteira" in peca_norm:
                    kws = ["porta", "dianteira", acao]
                elif "traseira" in peca_norm:
                    kws = ["porta", "traseira", acao]
                else:
                    kws = ["porta", acao]
                if lado_norm == "esquerdo":
                    kws.append("esquerda")
                elif lado_norm == "direito":
                    kws.append("direita")
            elif "caixa" in peca_norm and "ar" in peca_norm:
                kws = ["caixa de ar", acao]
                if lado_norm == "esquerdo":
                    kws.append("esquerdo")
                elif lado_norm == "direito":
                    kws.append("direito")
            elif "paralama" in peca_norm:
                kws = ["paralama", acao]
                # Use prefixos para casar com "direito/direita" e "esquerdo/esquerda".
                if lado_norm == "esquerdo":
                    kws.append("esquerd")
                elif lado_norm == "direito":
                    kws.append("direit")
            elif "capo" in peca_norm or "capô" in peca_norm:
                kws = ["capo", acao]
            elif "teto" in peca_norm:
                kws = ["teto", acao]
            elif "tampa" in peca_norm and ("malas" in peca_norm or "porta" in peca_norm):
                kws = ["tampa", "porta", "malas", acao]

            elif "para-barro" in peca_norm or "parabarro" in peca_norm or "para barro" in peca_norm:
                kws = ["para", "barro", acao]

                if lado_norm == "esquerdo":
                    kws.append("esquerdo")
                elif lado_norm == "direito":
                    kws.append("direito")

            if selected and ("para-barro" in peca_norm or "parabarro" in peca_norm or "para barro" in peca_norm):
                if lado_norm == "esquerdo":
                    filtered = [
                        s for s in selected
                        if "esquerd" in (s.descricao or "").lower()
                    ]
                    selected = filtered or selected
                elif lado_norm == "direito":
                    filtered = [
                        s for s in selected
                        if "direit" in (s.descricao or "").lower()
                    ]
                    selected = filtered or selected

            return kws

        def _select_lpu_services_for_part(peca_detectada: str, lado: str, nivel: str) -> list[LpuItem]:
            peca_norm = (peca_detectada or "").strip().lower()
            acao = _acao_from_nivel(nivel)
            kws = _kws_for(peca_detectada, lado, acao)

            def _filter_by_intent(candidates: list[LpuItem], acao_in: str) -> list[LpuItem]:
                if not candidates:
                    return []

                acao_key = (acao_in or "").strip().lower()
                # Sem depender de acentos/variações.
                if "recuper" in acao_key:
                    acao_key = "recuperacao"

                def _d(desc: str) -> str:
                    return (desc or "").strip().lower()

                if acao_key == "pintura":
                    # Quando o nível é leve, queremos evitar puxar serviços mais abrangentes
                    # (ex.: "Recuperação e pintura ...") que acabam duplicando a pintura.
                    exclude_tokens = ["recuper", "funilar", "martelinho", "reparo", "repux", "massa"]
                    filtered = [
                        s
                        for s in candidates
                        if not any(t in _d(s.descricao) for t in exclude_tokens)
                    ]
                    return filtered or candidates

                if acao_key == "recuperacao":
                    filtered = [s for s in candidates if "recuper" in _d(s.descricao)]
                    return filtered or candidates

                if acao_key == "troca":
                    filtered = [s for s in candidates if ("troca" in _d(s.descricao) or "substit" in _d(s.descricao))]
                    return filtered or candidates

                return candidates

            def _search_with(kws_in: list[str]) -> list[LpuItem]:
                out = find_services(
                    self.lpu_items,
                    kws_in,
                    perito_filtro="lataria",
                    modo_restrito=True,
                    allow_global_fallback=False,
                )
                if out:
                    return out
                return find_services(
                    self.lpu_items,
                    kws_in,
                    perito_filtro="lataria",
                    allow_global_fallback=False,
                )[:2]

            selected = _search_with(kws)

            # Ajuste por intenção: evita duplicação/serviços indevidos.
            selected = _filter_by_intent(selected, acao)

            # A normalização da LPU troca hífen por espaço: "para-lama" -> "para lama".
            # Se a peça for paralama e o match não parecer específico, tenta a variante "para lama".
            if "paralama" in peca_norm:
                def _looks_like_paralama(svcs: list[LpuItem]) -> bool:
                    for s in svcs or []:
                        desc = (s.descricao or "").lower()
                        if "para-lama" in desc or "para lama" in desc or "paralama" in desc:
                            return True
                    return False

                if not selected or not _looks_like_paralama(selected):
                    kws_alt = [("para lama" if k == "paralama" else k) for k in kws]
                    alt = _search_with(kws_alt)
                    if alt and _looks_like_paralama(alt):
                        selected = alt

            # Portas: se a peça é dianteira/traseira, filtra para não puxar serviço da outra.
            if selected and "porta" in peca_norm:
                if "dianteira" in peca_norm:
                    filtered = [s for s in selected if "dianteir" in (s.descricao or "").lower()]
                    selected = filtered or selected
                elif "traseira" in peca_norm:
                    filtered = [s for s in selected if "traseir" in (s.descricao or "").lower()]
                    selected = filtered or selected

            # Para-lamas: se a peça é dianteiro/traseiro, filtra para não puxar serviço do outro.
            if selected and "paralama" in peca_norm:
                if "dianteiro" in peca_norm:
                    filtered = [s for s in selected if "dianteir" in (s.descricao or "").lower()]
                    selected = filtered or selected
                elif "traseiro" in peca_norm:
                    filtered = [s for s in selected if "traseir" in (s.descricao or "").lower()]
                    selected = filtered or selected

            # Se a peça é retrovisor e o lado é conhecido, não misturar serviços do outro lado.
            lado_norm = (lado or "").strip().lower()
            if selected and "retrovisor" in peca_norm and lado_norm in {"esquerdo", "direito"}:
                filtered_side = [
                    s
                    for s in selected
                    if lado_norm in (s.descricao or "").lower()
                ]
                selected = filtered_side or selected

            # Evita itens genéricos multi-peças quando a peça é específica
            if selected and (
                "porta" in peca_norm
                or "retrovisor" in peca_norm
                or "paralama" in peca_norm
                or "para-barro" in peca_norm
                or "parabarro" in peca_norm
                or "para barro" in peca_norm
                or ("caixa" in peca_norm and "ar" in peca_norm)
            ):
                filtered = [
                    s
                    for s in selected
                    if "pecas grandes" not in (s.descricao or "").lower()
                    and "capo, teto, portas" not in (s.descricao or "").lower()
                ]
                selected = filtered or selected

            # Não misturar itens demais
            if "retrovisor" in peca_norm:
                return selected[:1] if selected else []
            return selected[:2] if selected else []

        avaliados: list[dict[str, Any]] = []
        for idx, p in enumerate(image_paths):
            expected_part_id = expected_part_ids_by_index[idx] if idx < len(expected_part_ids_by_index) else None
            expected_peca, expected_lado = (None, None)
            if expected_part_id:
                ep, el = _expected_from_part_id(expected_part_id)
                expected_peca = ep or None
                expected_lado = el or None

            raw = call_llm_with_image(
                prompt=_build_prompt(expected_part_id, expected_peca, expected_lado),
                image_path=p,
            )
            raw = _clean_json_fences(raw)
            try:
                d = json.loads(raw)
            except Exception:
                continue

            # Se temos part_id esperado, força peca consistente.
            # Lado só é forçado quando for determinístico (não retrovisor).
            if expected_part_id and expected_peca:
                d["peca"] = expected_peca
                if expected_lado:
                    d["lado"] = expected_lado

            def _infer_lado_retrovisor(dct: dict[str, Any], expected_pid: str | None) -> str:
                lado_raw = str(dct.get("lado", "") or "").strip().lower()
                if lado_raw in {"esquerdo", "direito"}:
                    return lado_raw

                # 1) fallback determinístico pelo part_id da triagem (se disponível)
                # Isso evita que o texto do modelo (justificativa) “contamine” o lado
                # quando temos imagens de ambos os retrovisores.
                pid = (expected_pid or "").strip().lower()
                if pid == "retrovisor_esquerdo":
                    return "esquerdo"
                if pid == "retrovisor_direito":
                    return "direito"

                # 2) tenta inferir pelo texto (justificativa/peca/localizacao)
                blob = " ".join(
                    [
                        str(dct.get("peca", "") or ""),
                        str(dct.get("justificativa", "") or ""),
                        str(dct.get("localizacao_avaria", "") or ""),
                    ]
                ).lower()
                if "esquer" in blob or "motorista" in blob:
                    return "esquerdo"
                if "direit" in blob or "passageiro" in blob:
                    return "direito"

                return ""

            peca = str(d.get("peca", "") or "").strip()
            lado = str(d.get("lado", "") or "").strip().lower()
            nivel = str(d.get("nivel_dano", "") or "").strip().lower()
            justificativa = str(d.get("justificativa", "") or "").strip()
            if not peca or not nivel:
                continue

            # Retrovisor: lado não pode ser "nao_se_aplica".
            if "retrovisor" in peca.lower():
                inferred = _infer_lado_retrovisor(d, expected_part_id)
                if inferred:
                    lado = inferred
                elif lado in {"", "nao_se_aplica"}:
                    lado = "nao_identificavel"

            avaliados.append(
                {
                    "peca": peca,
                    "lado": lado,
                    "nivel": nivel,
                    "justificativa": justificativa,
                    "localizacao": str(d.get("localizacao_avaria", "") or "").strip().lower(),
                    "tipo": str(d.get("tipo_avaria", "") or "").strip().lower(),
                    "path": p,
                    "part_id": expected_part_id,
                }
            )

        if not avaliados:
            return {"erro": "imagem invalida"}

        def _part_key(a: dict[str, Any]) -> str:
            peca = str(a.get("peca", "") or "").strip()
            lado = str(a.get("lado", "") or "").strip()
            return f"{peca} {lado}".strip().lower()

        def _count_avarias_distintas(part_key: str) -> int:
            locs: set[str] = set()
            has_any_damage = False
            for a in avaliados:
                if _part_key(a) != part_key:
                    continue
                nivel = str(a.get("nivel", "sem_dano"))
                if nivel == "sem_dano":
                    continue
                has_any_damage = True
                loc = str(a.get("localizacao") or "").strip().lower() or "nao_identificavel"
                locs.add(loc)

            if not has_any_damage:
                return 0
            locs_sem_nao = {l for l in locs if l != "nao_identificavel"}
            count = len(locs_sem_nao) if locs_sem_nao else 1
            return max(1, min(count, 3))

        # Para cada peça+lados, pega o pior nível
        parts = sorted({_part_key(a) for a in avaliados if _part_key(a)})

        itens: list[dict[str, Any]] = []
        servicos_flat: list[ServiceItem] = []
        total_geral = 0.0
        justificativas: list[str] = []

        any_damage = False
        for pk in parts:
            group = [a for a in avaliados if _part_key(a) == pk]
            if not group:
                continue

            best = max(group, key=lambda x: _severity_rank(str(x.get("nivel", ""))))
            nivel = str(best.get("nivel", "sem_dano"))
            peca = str(best.get("peca", ""))
            lado = str(best.get("lado", ""))
            just = str(best.get("justificativa", "")).strip()
            fotos = [a["path"] for a in group if a.get("path")]

            if nivel == "sem_dano":
                continue
            any_damage = True

            selected = _select_lpu_services_for_part(peca, lado, nivel)

            # Se houver múltiplas avarias e a ação for pintura, duplicar apenas pintura.
            qtd_avarias = _count_avarias_distintas(pk)
            acao = _acao_from_nivel(nivel)
            selected_expanded: list[LpuItem] = []
            for s in selected:
                desc_l = (s.descricao or "").lower()
                is_pintura = "pintura" in desc_l
                is_troca = "troca" in desc_l
                times = qtd_avarias if (acao == "pintura" and is_pintura and not is_troca and qtd_avarias > 1) else 1
                selected_expanded.extend([s] * times)

            servicos_out = [ServiceItem(descricao=s.descricao, preco=s.preco) for s in selected_expanded]
            servicos_flat.extend(servicos_out)

            total_part = sum(float(s.preco) for s in selected_expanded if isinstance(s.preco, (int, float)))
            total_geral += total_part

            justificativas.append(f"{pk}: {nivel}" + (f" ({just})" if just else ""))

            itens.append(
                ExpertConsolidatedOutput(
                    nivel_dano=nivel,
                    peca=f"{peca} {lado}".strip(),
                    servicos=servicos_out,
                    preco_total=round(total_part, 2),
                    justificativa=just or None,
                    fotos_analisadas=fotos,
                ).model_dump()
            )

        if not itens:
            # compatibilidade: se não achou dano em nada
            return ExpertConsolidatedOutput(
                nivel_dano="sem_dano",
                peca="lataria",
                servicos=[],
                preco_total=0.0,
                justificativa="Sem evidência de dano em lataria nas fotos fornecidas.",
                fotos_analisadas=image_paths,
            ).model_dump()

        nivel_final = max((i["nivel_dano"] for i in itens), key=_severity_rank)

        # Dedup de serviços (o Excel não deve receber linhas duplicadas idênticas)
        deduped: list[ServiceItem] = []
        seen: set[tuple[str, str]] = set()
        for s in servicos_flat:
            desc = (s.descricao or "").strip().lower()
            preco_key = str(s.preco).strip().lower()
            key = (desc, preco_key)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(s)

        return {
            "nivel_dano": nivel_final,
            "peca": "lataria",
            "itens": itens,
            "servicos": [s.model_dump() for s in deduped],
            "preco_total": round(total_geral, 2),
            "justificativa": "; ".join(justificativas) if justificativas else None,
            "fotos_analisadas": image_paths,
        }

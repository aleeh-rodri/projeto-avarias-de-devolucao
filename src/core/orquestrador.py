from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config import config as global_config
from core.pdf_utils import extract_reldev_avaria_part_ids, extract_reldev_chave_reserva_nao_tem
from core.schemas import TriageOutput, QualityOutput
from agents.triage_agent import run_triage
from agents.quality_agent import run_quality_check
from agents.peritos.perito_parachoque import ConfigPeritoParachoque, PeritoParachoque
from agents.peritos.perito_lataria import ConfigPeritoLataria, PeritoLataria
from agents.peritos.perito_vidros import ConfigPeritoVidros, PeritoVidros
from agents.peritos.perito_pneus_rodas import ConfigPeritoPneusRodas, PeritoPneusRodas
from agents.peritos.perito_interior import ConfigPeritoInterior, PeritoInterior
from agents.peritos.perito_acessorios import ConfigPeritoAcessorios, PeritoAcessorios
from agents.peritos.perito_emblemas import ConfigPeritoEmblemas, PeritoEmblemas

from core.vehicle_metadata import VehicleMetadataCache

# =========================
# CONFIG
# =========================
BASE_DIR = global_config.BASE_DIR
KEY_RESERVE_PHOTO_CODES = {"7", "8", "174"}


@dataclass(frozen=True)
class ConfigOrquestrador:
    caminho_lpu_xlsx: str = global_config.LPU_DEFAULT_PATH
    confianca_minima: float = global_config.CONFIANCA_MINIMA
    preferir_view: tuple[str, ...] = ("detalhe", "media", "panoramica", "longe")
    max_fotos_por_peca: int = global_config.MAX_FOTOS_POR_PECA
    cobrar_somente_checklist: bool = True
    confianca_extra_sem_checklist: float = 0.9


# =========================
# HELPERS
# =========================
def _normalize_path(p: str) -> str:
    return p.replace("\\", os.sep)


def _triage_index(triage_out: TriageOutput) -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for img in (triage_out.images or []):
        idx[img.image_id] = {
            "part_id": img.part_id,
            "photo_path": img.photo_path,
            "confidence": float(img.confidence),
            "checklist_damage_reported": img.checklist_damage_reported,
            "needs_human_review": bool(getattr(img, "needs_human_review", False)),
            "llm_part_validation": getattr(img, "llm_part_validation", None),
            "photo_part_code": getattr(img, "photo_part_code", None),
            "part_id_source": getattr(img, "part_id_source", None),
        }
    return idx


def _is_key_reserve_photo(img: Any) -> bool:
    code = str(getattr(img, "photo_part_code", "") or "").strip()
    if code in KEY_RESERVE_PHOTO_CODES:
        return True

    desc = str(getattr(img, "expected_part_description", "") or "").strip().lower()
    return "chave" in desc and ("reserva" in desc or "titular" in desc or "original" in desc)

def _build_checklist_divergencias(
    triage_idx: dict[str, dict[str, Any]],
    resultados_peritos: dict[str, Any],
) -> list[dict[str, Any]]:
    """Detecta divergências: checklist marcou avaria, mas perito concluiu sem_dano.

    Heurística atual:
    - Se o resultado consolidado do perito tem nivel_dano == 'sem_dano'
    - E alguma imagem usada por ele (imagens_usadas) tem checklist_damage_reported=True
    Então emitimos alerta por (perito, part_id).
    """
    if not triage_idx or not isinstance(resultados_peritos, dict):
        return []

    divergencias: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for perito_key, perito_data in resultados_peritos.items():
        if not isinstance(perito_key, str) or not isinstance(perito_data, dict):
            continue

        resultado = perito_data.get("resultado")
        if not isinstance(resultado, dict) or resultado.get("erro"):
            continue

        nivel = str(resultado.get("nivel_dano") or "").strip().lower()
        if nivel != "sem_dano":
            continue

        imagens_usadas = perito_data.get("imagens_usadas")
        if not isinstance(imagens_usadas, list) or not imagens_usadas:
            continue

        # Agrupa ids/paths por part_id quando checklist apontou avaria
        by_part: dict[str, dict[str, Any]] = {}
        for meta_img in imagens_usadas:
            if not isinstance(meta_img, dict):
                continue
            image_id = str(meta_img.get("image_id") or "").strip()
            part_id = str(meta_img.get("part_id") or "").strip()
            if not image_id or not part_id:
                continue

            tmeta = triage_idx.get(image_id) or {}
            if tmeta.get("checklist_damage_reported") is not True:
                continue

            bucket = by_part.setdefault(part_id, {"image_ids": [], "photo_paths": []})
            bucket["image_ids"].append(image_id)
            photo_path = tmeta.get("photo_path")
            if isinstance(photo_path, str) and photo_path:
                bucket["photo_paths"].append(photo_path)

        for part_id, payload in by_part.items():
            key = (perito_key, part_id)
            if key in seen:
                continue
            seen.add(key)

            divergencias.append(
                {
                    "tipo": "checklist_vs_visao",
                    "perito": perito_key,
                    "part_id": part_id,
                    "mensagem": (
                        "Checklist marcou avaria para esta peça, mas o perito retornou nivel_dano='sem_dano'. "
                        "Recomendado revisar manualmente."
                    ),
                    "image_ids": payload.get("image_ids", []),
                    "photo_paths": payload.get("photo_paths", []),
                }
            )

    return divergencias


def _image_ids_from_photo_paths(paths: Any) -> list[str]:
    if not isinstance(paths, list):
        return []
    out: list[str] = []
    for p in paths:
        if not isinstance(p, str) or not p.strip():
            continue
        try:
            out.append(Path(p).stem)
        except Exception:
            continue
    return out


def _image_ids_from_imagens_usadas(imagens_usadas: Any) -> list[str]:
    if not isinstance(imagens_usadas, list):
        return []
    out: list[str] = []
    for meta in imagens_usadas:
        if not isinstance(meta, dict):
            continue
        image_id = str(meta.get("image_id") or "").strip()
        if image_id:
            out.append(image_id)
    return out


def _should_charge_from_image_ids(
    triage_idx: dict[str, dict[str, Any]],
    image_ids: list[str],
    *,
    cobrar_somente_checklist: bool,
    confianca_extra_sem_checklist: float,
) -> bool:
    if not cobrar_somente_checklist:
        return True
    if not triage_idx or not image_ids:
        return False

    for image_id in image_ids:
        meta = triage_idx.get(image_id)
        if not meta:
            continue
        if meta.get("checklist_damage_reported") is True:
            return True
        try:
            conf = float(meta.get("confidence") or 0.0)
        except Exception:
            conf = 0.0
        if conf > float(confianca_extra_sem_checklist):
            return True
    return False


def _rank_nivel(nivel: str) -> int:
    n = (nivel or "").strip().lower()
    return {
        "sem_dano": 0,
        "leve": 1,
        "moderado": 2,
        "grave": 3,
        "troca": 4,
    }.get(n, 0)


def _apply_billing_policy_to_result(
    resultado: dict[str, Any],
    *,
    triage_idx: dict[str, dict[str, Any]],
    imagens_usadas: Any,
    cobrar_somente_checklist: bool,
    confianca_extra_sem_checklist: float,
) -> dict[str, Any]:
    # Alguns resultados são apenas "alertas" (revisão humana) e não devem ser descartados
    # pelo filtro do checklist, mesmo quando `cobrar_somente_checklist=True`.
    if isinstance(resultado, dict) and resultado.get("force_include") is True:
        return resultado
    """Remove cobranças fora do checklist, exceto quando confidence>threshold.

    Se, após o filtro, não restar nenhuma cobrança, normaliza o resultado para não cobrar
    (servicos=[], preco_total=0, nivel_dano='sem_dano').
    """
    if not isinstance(resultado, dict) or resultado.get("erro"):
        return resultado

    # Se não vamos filtrar, retorna como está.
    if not cobrar_somente_checklist:
        return resultado

    # Itens (breakdown)
    itens = resultado.get("itens")
    if isinstance(itens, list) and itens:
        kept: list[dict[str, Any]] = []
        for it in itens:
            if not isinstance(it, dict):
                continue
            image_ids = _image_ids_from_photo_paths(it.get("fotos_analisadas"))
            if not image_ids:
                image_ids = _image_ids_from_imagens_usadas(imagens_usadas)

            if _should_charge_from_image_ids(
                triage_idx,
                image_ids,
                cobrar_somente_checklist=cobrar_somente_checklist,
                confianca_extra_sem_checklist=confianca_extra_sem_checklist,
            ):
                kept.append(it)

        if not kept:
            # Sem itens cobrados
            resultado["itens"] = []
            resultado["servicos"] = []
            resultado["preco_total"] = 0
            resultado["nivel_dano"] = "sem_dano"
            resultado["justificativa"] = ("[DESCONSIDERADO POR CHECKLIST] " + str(resultado.get("justificativa") or "")).strip()
            return resultado

        # Rebuild consolidado a partir dos itens mantidos
        resultado["itens"] = kept
        servicos_flat: list[Any] = []
        fotos_flat: list[str] = []
        preco_total = 0.0
        any_sob_consulta = False
        nivel_max = "sem_dano"

        for it in kept:
            nivel_it = str(it.get("nivel_dano") or "").strip().lower()
            if _rank_nivel(nivel_it) > _rank_nivel(nivel_max):
                nivel_max = nivel_it

            fotos = it.get("fotos_analisadas")
            if isinstance(fotos, list):
                fotos_flat.extend([p for p in fotos if isinstance(p, str)])

            svcs = it.get("servicos")
            if isinstance(svcs, list):
                servicos_flat.extend(svcs)

            pt = it.get("preco_total")
            if isinstance(pt, (int, float)):
                preco_total += float(pt)
            elif str(pt).strip().lower() == "sob consulta":
                any_sob_consulta = True

        # dedup fotos mantendo ordem
        fotos_seen: set[str] = set()
        fotos_out: list[str] = []
        for p in fotos_flat:
            if p in fotos_seen:
                continue
            fotos_seen.add(p)
            fotos_out.append(p)

        resultado["servicos"] = servicos_flat
        resultado["fotos_analisadas"] = fotos_out
        resultado["nivel_dano"] = nivel_max
        resultado["preco_total"] = "Sob consulta" if any_sob_consulta else float(preco_total)
        # justificativa: evita carregar itens descartados
        resultado["justificativa"] = "; ".join(
            [
                f"{it.get('peca')}: {it.get('nivel_dano')} ({it.get('justificativa')})"
                for it in kept
                if it.get("peca")
            ]
        )
        return resultado

    # Sem breakdown: decide pelo resultado inteiro
    image_ids = _image_ids_from_photo_paths(resultado.get("fotos_analisadas"))
    if not image_ids:
        image_ids = _image_ids_from_imagens_usadas(imagens_usadas)

    allowed = _should_charge_from_image_ids(
        triage_idx,
        image_ids,
        cobrar_somente_checklist=cobrar_somente_checklist,
        confianca_extra_sem_checklist=confianca_extra_sem_checklist,
    )
    if not allowed:
        resultado["servicos"] = []
        resultado["preco_total"] = 0
        if "pecas_a_cotar" in resultado:
            resultado["pecas_a_cotar"] = []
        resultado["nivel_dano"] = "sem_dano"
        resultado["justificativa"] = ("[DESCONSIDERADO POR CHECKLIST] " + str(resultado.get("justificativa") or "")).strip()
        return resultado

    # Filtra pecas_a_cotar também (quando existirem)
    if isinstance(resultado.get("pecas_a_cotar"), list) and resultado.get("pecas_a_cotar"):
        # usa as mesmas imagens do resultado para decidir
        if not allowed:
            resultado["pecas_a_cotar"] = []

    return resultado


def _parts_with_checklist_damage(
    triage_out: TriageOutput,
    checklist_part_ids: set[str] | None = None,
) -> set[str]:
    out: set[str] = set(checklist_part_ids or [])
    for img in (triage_out.images or []):
        if img.checklist_damage_reported is True and img.part_id:
            out.add(str(img.part_id).strip())
    return out


def _charged_parts_from_resultados(
    resultados_peritos: dict[str, Any],
    triage_idx: dict[str, dict[str, Any]],
) -> set[str]:
    """Infere quais part_id efetivamente geraram cobrança (serviço) após filtros."""
    charged: set[str] = set()
    if not isinstance(resultados_peritos, dict) or not triage_idx:
        return charged

    def add_parts_from_photo_paths(photo_paths: Any) -> None:
        image_ids = _image_ids_from_photo_paths(photo_paths)
        for image_id in image_ids:
            meta = triage_idx.get(image_id) or {}
            part_id = str(meta.get("part_id") or "").strip()
            if part_id:
                charged.add(part_id)

    for perito_data in resultados_peritos.values():
        if not isinstance(perito_data, dict):
            continue
        resultado = perito_data.get("resultado")
        if not isinstance(resultado, dict) or resultado.get("erro"):
            continue

        itens = resultado.get("itens")
        if isinstance(itens, list) and itens:
            for it in itens:
                if not isinstance(it, dict):
                    continue
                servicos = it.get("servicos")
                if not isinstance(servicos, list) or not servicos:
                    continue
                add_parts_from_photo_paths(it.get("fotos_analisadas"))
            continue

        servicos = resultado.get("servicos")
        if isinstance(servicos, list) and servicos:
            add_parts_from_photo_paths(resultado.get("fotos_analisadas"))

    return charged


def _build_checklist_fallback_charges(
    triage_out: TriageOutput,
    triage_idx: dict[str, dict[str, Any]],
    resultados_peritos: dict[str, Any],
    checklist_part_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Gera cobranças 'fallback' quando o checklist marcou avaria, mas nenhum serviço foi gerado.

    Importante: como o checklist atual é um sinal binário por peça (checklist_damage_reported),
    o fallback é por part_id e sempre sai com needs_human_review=True.
    """
    checklist_parts = _parts_with_checklist_damage(triage_out, checklist_part_ids=checklist_part_ids)
    if not checklist_parts:
        return []

    charged_parts = _charged_parts_from_resultados(resultados_peritos, triage_idx)
    missing = sorted([p for p in checklist_parts if p not in charged_parts])
    if not missing:
        return []

    # Escolhe uma foto representativa por peça (a de maior confidence) para facilitar a revisão.
    best_photo_by_part: dict[str, str] = {}
    best_conf_by_part: dict[str, float] = {}
    for img in (triage_out.images or []):
        part_id = str(img.part_id or "").strip()
        if not part_id or part_id not in checklist_parts:
            continue
        try:
            conf = float(img.confidence)
        except Exception:
            conf = 0.0
        if part_id not in best_conf_by_part or conf > best_conf_by_part.get(part_id, 0.0):
            best_conf_by_part[part_id] = conf
            best_photo_by_part[part_id] = str(img.photo_path or "").strip()

    out: list[dict[str, Any]] = []
    for part_id in missing:
        photo_path = best_photo_by_part.get(part_id) or ""
        out.append(
            {
                "descricao": f"CHECKLIST — Avaria reportada na peça '{part_id}' (REVISAR)",
                "valor": 0,
                "needs_human_review": True,
                "origin": "checklist_fallback",
                "part_id": part_id,
                "fotos": [photo_path] if photo_path else [],
            }
        )

    return out


def _escolher_melhores_imagens(
    registros: list[dict[str, Any]],
    preferir_view: tuple[str, ...],
    max_itens: int = 0,
) -> list[dict[str, Any]]:
    if not registros:
        return []

    def view_rank(v: str) -> int:
        v = (v or "").strip().lower()
        try:
            return preferir_view.index(v)
        except ValueError:
            return len(preferir_view) + 1

    ordered = sorted(
        registros,
        key=lambda r: (view_rank(str(r.get("view", ""))), -float(r.get("confidence", 0.0))),
    )

    return ordered[:max_itens] if max_itens > 0 else ordered


def _escolher_melhores_imagens_diversificadas_por_peca(
    registros: list[dict[str, Any]],
    preferir_view: tuple[str, ...],
    max_total: int,
    key_field: str = "part_id",
) -> list[dict[str, Any]]:
    """Escolhe imagens tentando cobrir múltiplas peças (ex.: retrovisor_esquerdo/direito).

    Estratégia:
    - Ordena imagens dentro de cada `key_field` pela preferência de view e confiança.
    - Faz um round-robin entre as peças, respeitando `max_total`.

    Observação: se `max_total <= 0`, retorna todas as imagens ordenadas (comportamento antigo).
    """

    if not registros:
        return []

    if max_total <= 0:
        return _escolher_melhores_imagens(registros, preferir_view=preferir_view, max_itens=0)

    def view_rank(v: str) -> int:
        v = (v or "").strip().lower()
        try:
            return preferir_view.index(v)
        except ValueError:
            return len(preferir_view) + 1

    def score(r: dict[str, Any]) -> tuple[int, float]:
        return (view_rank(str(r.get("view", ""))), -float(r.get("confidence", 0.0)))

    # Agrupa por peça (part_id) e ordena por qualidade dentro de cada grupo
    grupos: dict[str, list[dict[str, Any]]] = {}
    for r in registros:
        k = str(r.get(key_field, "") or "").strip().lower() or "__unknown__"
        grupos.setdefault(k, []).append(r)

    for k in list(grupos.keys()):
        grupos[k] = sorted(grupos[k], key=score)

    # Ordena as peças pela melhor imagem de cada uma
    ordem_pecas = sorted(grupos.keys(), key=lambda k: score(grupos[k][0]) if grupos[k] else (999, 0.0))

    selecionadas: list[dict[str, Any]] = []
    # Round-robin entre as peças
    while len(selecionadas) < max_total:
        qualquer = False
        for k in ordem_pecas:
            if not grupos.get(k):
                continue
            selecionadas.append(grupos[k].pop(0))
            qualquer = True
            if len(selecionadas) >= max_total:
                break
        if not qualquer:
            break

    return selecionadas


# =========================
# PIPELINE FUNCIONAL
# =========================
def rodar_orquestrador(
    case_id: str,
    fotos_dir: str,
    output_dir: str,
    config: ConfigOrquestrador,
    checklist_path: str | None = None,
) -> dict[str, Any]:

    # 1) triagem
    triage_raw = run_triage(
        case_id=case_id, 
        fotos_dir=fotos_dir, 
        output_dir=output_dir,
        checklist_path=checklist_path
    )
    triage_out = TriageOutput(**triage_raw)

    # 2) qualidade
    quality_raw = run_quality_check(
        case_id=case_id,
        triage_images=triage_out.images,
        output_dir=output_dir,
    )
    quality_out = QualityOutput(**quality_raw)

    chave_reserva_nao_tem = False
    if checklist_path and os.path.exists(checklist_path):
        try:
            chave_reserva_nao_tem = extract_reldev_chave_reserva_nao_tem(checklist_path)
        except Exception:
            chave_reserva_nao_tem = False

    aprovadas_ids = {a.image_id for a in quality_out.assessments if a.aprovada}

    imagens_filtradas = [
        img
        for img in triage_out.images
        if img.image_id in aprovadas_ids
        and not bool(getattr(img, "needs_human_review", False))
    ]

    # 3) Mapeamento de Peritos
    mapeamento_peritos = {
        "parachoque": {
            "part_ids": {"parachoque_dianteiro", "parachoque_traseiro"},
            "classe": PeritoParachoque,
            "config": ConfigPeritoParachoque(caminho_lpu_xlsx=config.caminho_lpu_xlsx)
        },
        "emblemas": {
            "part_ids": {"parachoque_dianteiro", "parachoque_traseiro", "tampa_porta_malas"},
            "classe": PeritoEmblemas,
            "config": ConfigPeritoEmblemas(caminho_lpu_xlsx=config.caminho_lpu_xlsx)
        },
        "lataria": {
            "part_ids": {
                "capo", "teto", "tampa_porta_malas",
                "porta_dianteira", "porta_dianteira_esquerda", "porta_dianteira_direita",
                "porta_traseira", "porta_traseira_esquerda", "porta_traseira_direita",
                "paralama_dianteiro", "paralama_dianteiro_esquerdo", "paralama_dianteiro_direito",
                "paralama_traseiro", "paralama_traseiro_esquerdo", "paralama_traseiro_direito", "paralama_esquerdo", "paralama_direito",
                "retrovisor_esquerdo", "retrovisor_direito", 
                # novo
                "parabarro_esquerdo", "parabarro_direito",
            },
            "classe": PeritoLataria,
            "config": ConfigPeritoLataria(caminho_lpu_xlsx=config.caminho_lpu_xlsx)
        },
        "vidros": {
            "part_ids": {"parabrisa", "vidro_traseiro"},
            "classe": PeritoVidros,
            "config": ConfigPeritoVidros(caminho_lpu_xlsx=config.caminho_lpu_xlsx)
        },
        "pneus_rodas": {
            "part_ids": {
                "roda_dianteira_esquerda", "roda_dianteira_direita", 
                "roda_traseira_esquerda", "roda_traseira_direita"
            },
            "classe": PeritoPneusRodas,
            "config": ConfigPeritoPneusRodas(caminho_lpu_xlsx=config.caminho_lpu_xlsx)
        },
        "interior": {
            "part_ids": {"interior"},
            "classe": PeritoInterior,
            "config": ConfigPeritoInterior(caminho_lpu_xlsx=config.caminho_lpu_xlsx)
        },
        "acessorios": {
            "part_ids": {"acessorios"},
            "classe": PeritoAcessorios,
            "config": ConfigPeritoAcessorios(caminho_lpu_xlsx=config.caminho_lpu_xlsx)
        }
    }

    resultados_peritos = {}
    preco_total_geral = 0.0
    any_sob_consulta = False

    metadata_cache = VehicleMetadataCache(
        BASE_DIR / "input" / "vehicle_metadata_cache.xlsx"
    )
    vehicle_metadata = metadata_cache.get_vehicle_metadata(case_id)
    wheel_type = vehicle_metadata.get("wheel_type", "desconhecido")
    print(wheel_type)

    progress_enabled = os.getenv("AGENTE_PROGRESS", "1").strip().lower() not in ("0", "false", "no")
    total_peritos = len(mapeamento_peritos)

    for idx_perito, (nome_perito, info) in enumerate(mapeamento_peritos.items(), start=1):
        elegiveis = [
            img for img in imagens_filtradas
            if img.part_id in info["part_ids"]
            and img.confidence >= config.confianca_minima
        ]

        if progress_enabled:
            print(f"[peritos] {idx_perito}/{total_peritos} {nome_perito}: {len(elegiveis)} fotos", flush=True)
        
        registros = [img.model_dump() for img in elegiveis]

        # Lataria: garantir cobertura de retrovisores (até 2 fotos), pois o lado pode ser confundido
        # na triagem e/ou na qualidade.
        if nome_perito == "lataria":
            retrovisores = [r for r in registros if str(r.get("part_id", "")).lower().startswith("retrovisor_")]
            reservadas = _escolher_melhores_imagens(
                retrovisores,
                preferir_view=config.preferir_view,
                max_itens=min(2, config.max_fotos_por_peca),
            )

            usados_ids = {str(r.get("image_id")) for r in reservadas if r.get("image_id")}
            restantes = [r for r in registros if str(r.get("image_id")) not in usados_ids]

            slots_restantes = max(0, config.max_fotos_por_peca - len(reservadas))
            complementares = _escolher_melhores_imagens_diversificadas_por_peca(
                restantes,
                preferir_view=config.preferir_view,
                max_total=slots_restantes,
                key_field="part_id",
            )

            melhores = reservadas + complementares

        # Para-choque: tende a ter poucas peças (dianteiro/traseiro), mas ainda assim queremos diversidade.
        elif nome_perito == "parachoque":
            melhores = _escolher_melhores_imagens_diversificadas_por_peca(
                registros,
                preferir_view=config.preferir_view,
                max_total=config.max_fotos_por_peca,
                key_field="part_id",
            )

        else:
            melhores = _escolher_melhores_imagens(
                registros,
                preferir_view=config.preferir_view,
                max_itens=config.max_fotos_por_peca,
            )

        if nome_perito == "acessorios" and chave_reserva_nao_tem:
            key_registros = [
                img.model_dump()
                for img in (triage_out.images or [])
                if _is_key_reserve_photo(img)
                and not bool(getattr(img, "needs_human_review", False))
            ]
            if key_registros:
                key_melhores = _escolher_melhores_imagens(
                    key_registros,
                    preferir_view=config.preferir_view,
                    max_itens=config.max_fotos_por_peca,
                )
                seen_ids = {str(r.get("image_id") or "") for r in key_melhores}
                complementares = [
                    r
                    for r in melhores
                    if str(r.get("image_id") or "") not in seen_ids
                ]
                melhores = (key_melhores + complementares)[: config.max_fotos_por_peca]

        if melhores:
            image_paths = []
            imagens_usadas = []
            for r in melhores:
                raw_path = str(r.get("photo_path", ""))
                normalized = _normalize_path(raw_path)
                imagem_path = normalized if os.path.exists(normalized) else os.path.join(fotos_dir, os.path.basename(normalized))
                image_paths.append(imagem_path)
                imagens_usadas.append({
                    "image_id": r.get("image_id"),
                    "part_id": r.get("part_id"),
                    "view": r.get("view")
                })

            try:
                instancia_perito = info["classe"](info["config"])
                res = instancia_perito.run(
                    image_paths=image_paths, 
                    checklist_summary=triage_out.checklist_summary,
                    imagens_usadas=imagens_usadas,
                    chave_reserva_nao_tem=chave_reserva_nao_tem,
                    wheel_type=wheel_type,
                )
                
                resultados_peritos[f"perito_{nome_perito}"] = {
                    "imagens_usadas": imagens_usadas,
                    "resultado": res
                }

                # Totais serão calculados após aplicar a política de cobrança.
            except Exception as e:
                print(f"ERRO: Falha ao rodar perito {nome_perito}: {e}")
                resultados_peritos[f"perito_{nome_perito}"] = {
                    "imagens_usadas": imagens_usadas,
                    "resultado": {"erro": f"falha inesperada: {str(e)}"}
                }
        else:
            resultados_peritos[f"perito_{nome_perito}"] = {"resultado": {"erro": "nenhuma imagem elegivel"}}

    # 4) consolidação (+ política de cobrança)
    triage_idx = _triage_index(triage_out)
    has_triage = bool(triage_idx)

    if has_triage and config.cobrar_somente_checklist:
        for perito_key, perito_data in resultados_peritos.items():
            if not isinstance(perito_data, dict):
                continue
            imagens_usadas = perito_data.get("imagens_usadas")
            resultado = perito_data.get("resultado")
            if isinstance(resultado, dict) and not resultado.get("erro"):
                perito_data["resultado"] = _apply_billing_policy_to_result(
                    resultado,
                    triage_idx=triage_idx,
                    imagens_usadas=imagens_usadas,
                    cobrar_somente_checklist=config.cobrar_somente_checklist,
                    confianca_extra_sem_checklist=config.confianca_extra_sem_checklist,
                )

    # Recalcular totais (já com filtro aplicado)
    preco_total_geral = 0.0
    any_sob_consulta = False
    for perito_data in resultados_peritos.values():
        if not isinstance(perito_data, dict):
            continue
        resultado = perito_data.get("resultado")
        if not isinstance(resultado, dict) or resultado.get("erro"):
            continue
        pt = resultado.get("preco_total")
        if isinstance(pt, (int, float)):
            preco_total_geral += float(pt)
        elif str(pt).strip().lower() == "sob consulta":
            any_sob_consulta = True

    divergencias_checklist = _build_checklist_divergencias(triage_idx, resultados_peritos) if has_triage else []

    cobrancas_checklist_fallback: list[dict[str, Any]] = []
    checklist_part_ids: set[str] | None = None
    if checklist_path and os.path.exists(checklist_path):
        try:
            checklist_part_ids = extract_reldev_avaria_part_ids(checklist_path)
        except Exception:
            checklist_part_ids = None

    if has_triage or checklist_part_ids:
        cobrancas_checklist_fallback = _build_checklist_fallback_charges(
            triage_out,
            triage_idx,
            resultados_peritos,
            checklist_part_ids=checklist_part_ids,
        )

    laudo = {
        "case_id": case_id,
        "triagem": {
            "total_imagens": len(triage_out.images),
            "imagens_aprovadas_qualidade": len(imagens_filtradas),
            "chave_reserva_nao_tem": chave_reserva_nao_tem,
        },
        "peritos": resultados_peritos,
        "divergencias_checklist": divergencias_checklist,
        "cobrancas_checklist_fallback": cobrancas_checklist_fallback,
        "preco_total_geral": "Sob consulta" if any_sob_consulta else preco_total_geral
    }

    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "laudo.json"), "w", encoding="utf-8") as f:
        json.dump(laudo, f, ensure_ascii=False, indent=2)

    return laudo


# =========================
# CLASSE (COMPATIBILIDADE)
# =========================
class Orquestrador:
    def __init__(self, config: ConfigOrquestrador | None = None):
        self.config = config or ConfigOrquestrador()

    def run(self, case_id: str, fotos_dir: str, output_dir: str) -> dict[str, Any]:
        return rodar_orquestrador(
            case_id=case_id,
            fotos_dir=fotos_dir,
            output_dir=output_dir,
            config=self.config,
        )


# =========================
# UTIL
# =========================
def caminho_lpu_padrao_orquestrador() -> str:
    return caminho_lpu_padrao()

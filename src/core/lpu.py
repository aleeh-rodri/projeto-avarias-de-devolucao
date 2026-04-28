from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from difflib import get_close_matches
import unicodedata
import re

import pandas as pd


@dataclass(frozen=True)
class LpuItem:
    descricao: str
    preco: float | str
    perito: str = "outros"


def _normalize(text: str) -> str:
    text = str(text).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"[^\w\s-]", " ", text)
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_lpu_items(xlsx_path: str | Path) -> list[LpuItem]:
    path = Path(xlsx_path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo LPU não encontrado: {path}")

    df = pd.read_excel(path, sheet_name=0)

    cols = {c: _normalize(c) for c in df.columns}

    def pick_col(candidates: Iterable[str]) -> str | None:
        for original, norm in cols.items():
            if any(c in norm for c in candidates):
                return original
        return None

    # Preferir a coluna de descrição textual da carta ("descr") antes de cair em
    # colunas genéricas como "tipo de serviços".
    desc_col = pick_col(["descr"])
    if desc_col is None:
        desc_col = pick_col(["item", "proced", "servi"])
    price_col = pick_col(["preco", "preço", "valor", "vlr", "r$"])
    perito_col = pick_col(["perito"])

    if desc_col is None or price_col is None:
        raise ValueError(
            f"Não foi possível identificar colunas de descrição/preço. Colunas: {list(df.columns)}"
        )

    out: list[LpuItem] = []

    for _, row in df.iterrows():
        desc = row.get(desc_col)
        price = row.get(price_col)
        perito = row.get(perito_col) if perito_col else "outros"

        if pd.isna(desc) or str(desc).strip() == "":
            continue
        if pd.isna(price):
            continue

        desc = str(desc).strip()
        perito = str(perito).strip().lower() if not pd.isna(perito) else "outros"
        price_str = str(price).strip()

        if _normalize(price_str) in {"sob consulta", "sob_consulta"}:
            out.append(LpuItem(descricao=desc, preco="Sob consulta", perito=perito))
            continue

        try:
            out.append(LpuItem(descricao=desc, preco=float(price), perito=perito))
            continue
        except Exception:
            pass

        try:
            s = price_str.replace("R$", "").replace(" ", "")
            s = s.replace(".", "").replace(",", ".")
            out.append(LpuItem(descricao=desc, preco=float(s), perito=perito))
        except Exception:
            out.append(LpuItem(descricao=desc, preco=price_str, perito=perito))

    return out


def find_services(
    items: list[LpuItem],
    keywords: list[str],
    perito_filtro: str | None = None,
    fuzzy: bool = True,
    modo_restrito: bool = False,
    allow_global_fallback: bool = True,
) -> list[LpuItem]:

    original_items = items
    if perito_filtro:
        items = [it for it in items if it.perito == perito_filtro]
    
    # Se filtrou e não sobrou nada, tenta buscar no global (fallback)
    if not items and perito_filtro and allow_global_fallback:
        items = original_items

    kws = [_normalize(k) for k in keywords if k]
    matched: list[tuple[float, LpuItem]] = []

    # match direto com pontuação
    for it in items:
        desc_norm = _normalize(it.descricao)
        matches_count = sum(1 for k in kws if k in desc_norm)
        
        if modo_restrito:
            # No modo restrito, pelo menos as palavras principais devem estar presentes
            if matches_count == len(kws):
                matched.append((float(matches_count), it))
        elif matches_count > 0:
            # Atribui peso maior se a primeira palavra (geralmente a peça) bater
            score = float(matches_count)
            if kws and kws[0] in desc_norm:
                score += 0.5
            matched.append((score, it))

    # Ordena pelo score decrescente
    matched.sort(key=lambda x: x[0], reverse=True)
    out_items = [m[1] for m in matched]

    # fuzzy fallback se nada foi encontrado
    if not out_items and fuzzy:
        index = {_normalize(it.descricao): it for it in items}
        for kw in kws:
            hits = get_close_matches(kw, index.keys(), n=2, cutoff=0.55)
            for h in hits:
                out_items.append(index[h])

    # dedup mantendo a ordem de score
    seen: set[str] = set()
    final_out: list[LpuItem] = []

    for it in out_items:
        key = _normalize(it.descricao)
        if key not in seen:
            seen.add(key)
            final_out.append(it)

    return final_out

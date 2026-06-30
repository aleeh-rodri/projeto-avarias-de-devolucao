from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pdfminer.high_level import extract_text

from core.schemas import PART_IDS


KNOWN_PART_IDS = set(PART_IDS)


def extract_checklist_text(pdf_path: str, *, normalize_whitespace: bool = True) -> str:
    """Extrai texto do PDF do checklist.

    Por compatibilidade com o pipeline antigo, o default é normalizar whitespace.
    Para debugging/extrações mais fiéis, use `normalize_whitespace=False`.
    """
    if not os.path.exists(pdf_path):
        return ""
    try:
        text = extract_text(pdf_path)
        if not normalize_whitespace:
            return text
        return " ".join(text.split())
    except Exception as e:
        print(f"Erro ao ler PDF {pdf_path}: {e}")
        return ""


@dataclass(frozen=True)
class ChecklistRow:
    descricao: str
    item: str
    registro: str
    page: int


@dataclass(frozen=True)
class ChecklistAvariaItem:
    descricao: str
    item: str
    raw_label: str
    part_id: str | None
    page: int


_RE_SPACES = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _RE_SPACES.sub(" ", (s or "").strip().lower())


def _norm_ascii(s: str) -> str:
    text = unicodedata.normalize("NFKD", str(s or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return _RE_SPACES.sub(" ", text.strip().lower())


def is_chave_reserva_nao_tem_row(row: ChecklistRow) -> bool:
    """Identifica a linha do RELDEV que marca Chave Reserva como Nao Tem."""
    descricao = _norm_ascii(row.descricao)
    item = _norm_ascii(row.item)
    registro = _norm_ascii(row.registro)

    return (
        "itens de conferencia" in descricao
        and "chave reserva" in item
        and registro == "nao tem"
    )


def _map_checklist_row_to_part_id(descricao: str, item: str) -> str | None:
    """Mapeia (descrição, item) do RELDEV para um part_id canônico do projeto."""
    d = _norm(descricao)
    it = _norm(item)
    combined = f"{d} {it}".strip()

    # Capô / Teto
    if "capo" in combined or "capô" in combined:
        return "capo"
    if re.search(r"\bteto\b", combined):
        return "teto"

    # Para-brisa
    if "para-brisa" in combined or "parabrisa" in combined or "pára-brisa" in combined:
        return "parabrisa"

    # Para-choque (usa descrição para inferir posição)
    if "para-choque" in combined or "parachoque" in combined:
        if "dianteira" in d or "dianteira" in it:
            return "parachoque_dianteiro"
        if "traseira" in d or "traseira" in it:
            return "parachoque_traseiro"
        # sem posição clara
        return None

    # Rodas (quando vier explícito)
    if "roda" in combined:
        if "dianteira" in combined and "direit" in combined:
            return "roda_dianteira_direita"
        if "dianteira" in combined and "esquerd" in combined:
            return "roda_dianteira_esquerda"
        if "traseira" in combined and "direit" in combined:
            return "roda_traseira_direita"
        if "traseira" in combined and "esquerd" in combined:
            return "roda_traseira_esquerda"
        return None

    # Portas
    if "porta" in combined:
        if "dianteira" in combined and "direit" in combined:
            return "porta_dianteira_direita"
        if "dianteira" in combined and "esquerd" in combined:
            return "porta_dianteira_esquerda"
        if "traseira" in combined and "direit" in combined:
            return "porta_traseira_direita"
        if "traseira" in combined and "esquerd" in combined:
            return "porta_traseira_esquerda"
        return None

    # Para-lama
    if "paralama" in combined or "para-lama" in combined:
        # No RELDEV, muitas vezes aparece apenas 'Para-Lama Esquerdo/Direito'.
        # Na prática (lataria), isso se refere ao paralama dianteiro.
        if "esquerd" in combined and "dianteir" not in combined and "traseir" not in combined:
            return "paralama_dianteiro_esquerdo"
        if "direit" in combined and "dianteir" not in combined and "traseir" not in combined:
            return "paralama_dianteiro_direito"

        if "dianteir" in combined and "direit" in combined:
            return "paralama_dianteiro_direito"
        if "dianteir" in combined and "esquerd" in combined:
            return "paralama_dianteiro_esquerdo"
        if "traseir" in combined and "direit" in combined:
            return "paralama_traseiro_direito"
        if "traseir" in combined and "esquerd" in combined:
            return "paralama_traseiro_esquerdo"
        return None

    # Retrovisor
    if "retrovisor" in combined:
        if "direit" in combined:
            return "retrovisor_direito"
        if "esquerd" in combined:
            return "retrovisor_esquerdo"
        return None
    
    if "para-barro" in combined or "para barro" in combined or "parabarro" in combined:
        if "esquerd" in combined:
            return "parabarro_esquerdo"
        if "direit" in combined:
            return "parabarro_direito"
        return None
    
    # Caixa de ar
    if "caixa" in combined and "ar" in combined:
        if "esquerd" in combined:
            return "caixa_ar_esquerda"
        if "direit" in combined:
            return "caixa_ar_direita"
        return None
    
    # Coluna
    if "coluna" in combined:
        if "esquerd" in combined:
            return "coluna_esquerda"
        if "direit" in combined:
            return "coluna_direita"
        return None

    return None


def _raw_checklist_label(descricao: str, item: str) -> str:
    desc = (descricao or "").strip()
    it = (item or "").strip()
    if desc and it:
        return f"{desc} - {it}"
    return desc or it


def extract_reldev_rows(pdf_path: str | Path) -> list[ChecklistRow]:
    """Extrai a tabela DESCRIÇÃO/ITEM/REGISTRO do RELDEV de forma determinística.

    Implementação baseada em PyMuPDF (fitz), agrupando spans por linha (Y) e
    classificando por coluna (X) a partir do cabeçalho.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return []

    try:
        import fitz  # PyMuPDF
    except Exception:
        return []

    out: list[ChecklistRow] = []

    with fitz.open(str(pdf_path)) as doc:
        for page_index in range(len(doc)):
            page = doc[page_index]
            d = page.get_text("dict")
            spans: list[dict] = []
            for b in d.get("blocks", []):
                for ln in b.get("lines", []):
                    for sp in ln.get("spans", []):
                        txt = str(sp.get("text") or "").strip()
                        if not txt:
                            continue
                        x0, y0, x1, y1 = sp.get("bbox")
                        spans.append({"text": txt, "x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1)})

            if not spans:
                continue

            # localizar cabeçalho da tabela
            header_candidates = [s for s in spans if _norm(s["text"]) in {"descrição", "descricao", "item", "registro"}]
            if not header_candidates:
                continue

            # agrupar por linha (y0) e achar uma linha que contenha os 3 cabeçalhos
            header_line_y: float | None = None
            header_spans: list[dict] = []
            for cand in sorted(header_candidates, key=lambda s: (s["y0"], s["x0"])):
                y = cand["y0"]
                group = [s for s in header_candidates if abs(s["y0"] - y) <= 2.5]
                keys = {_norm(s["text"]) for s in group}
                if {"item", "registro"}.issubset(keys) and ("descrição" in keys or "descricao" in keys):
                    header_line_y = y
                    header_spans = group
                    break

            if header_line_y is None:
                continue

            # determinar X das colunas a partir do cabeçalho
            def pick_x(label_variants: set[str]) -> float:
                c = [s for s in header_spans if _norm(s["text"]) in label_variants]
                c.sort(key=lambda s: s["x0"])
                return float(c[0]["x0"]) if c else 0.0

            x_desc = pick_x({"descrição", "descricao"})
            x_item = pick_x({"item"})
            x_reg = pick_x({"registro"})
            xs = sorted([x_desc, x_item, x_reg])
            if len(xs) != 3:
                continue
            b1 = (xs[0] + xs[1]) / 2.0
            b2 = (xs[1] + xs[2]) / 2.0

            # filtrar spans abaixo do header
            body = [s for s in spans if s["y0"] > header_line_y + 3]
            if not body:
                continue

            # parar ao chegar na legenda do rodapé (Avaria: ...)
            stop_ys = [s["y0"] for s in body if "avaria:" in _norm(s["text"]) or "observa" in _norm(s["text"])]
            stop_y = min(stop_ys) if stop_ys else None
            if stop_y is not None:
                body = [s for s in body if s["y0"] < stop_y]

            # agrupar spans por linha (y0)
            body.sort(key=lambda s: (s["y0"], s["x0"]))
            rows: list[tuple[float, list[dict]]] = []
            for s in body:
                placed = False
                for idx, (ry, items) in enumerate(rows):
                    if abs(s["y0"] - ry) <= 2.5:
                        items.append(s)
                        placed = True
                        break
                if not placed:
                    rows.append((s["y0"], [s]))

            for _, items in rows:
                # agrupa por coluna
                col_desc: list[str] = []
                col_item: list[str] = []
                col_reg: list[str] = []
                for sp in sorted(items, key=lambda s: s["x0"]):
                    txt = str(sp["text"]).strip()
                    if not txt:
                        continue
                    if sp["x0"] < b1:
                        col_desc.append(txt)
                    elif sp["x0"] < b2:
                        col_item.append(txt)
                    else:
                        col_reg.append(txt)

                desc = " ".join(col_desc).strip()
                item = " ".join(col_item).strip()
                reg = " ".join(col_reg).strip()

                # ignora linhas vazias/cabeçalhos repetidos
                if not desc and not item and not reg:
                    continue
                if _norm(desc) in {"descrição", "descricao"} and _norm(item) == "item":
                    continue

                out.append(ChecklistRow(descricao=desc, item=item, registro=reg, page=page_index + 1))

    return out


def extract_reldev_avaria_items(pdf_path: str | Path) -> list[ChecklistAvariaItem]:
    """Extrai todas as linhas marcadas como AVARIA no RELDEV."""
    rows = extract_reldev_rows(pdf_path)
    out: list[ChecklistAvariaItem] = []
    for r in rows:
        if _norm(r.registro) != "avaria":
            continue
        part_id = _map_checklist_row_to_part_id(r.descricao, r.item)
        if part_id and part_id not in KNOWN_PART_IDS:
            part_id = None
        out.append(
            ChecklistAvariaItem(
                descricao=r.descricao,
                item=r.item,
                raw_label=_raw_checklist_label(r.descricao, r.item),
                part_id=part_id,
                page=r.page,
            )
        )
    return out


def extract_reldev_avaria_part_ids(pdf_path: str | Path) -> set[str]:
    """Extrai um conjunto de part_ids marcados como AVARIA no RELDEV.

    Retorna set vazio se não conseguir extrair com confiança.
    """
    out: set[str] = set()
    for item in extract_reldev_avaria_items(pdf_path):
        if item.part_id:
            out.add(item.part_id)
    return out


def extract_reldev_chave_reserva_nao_tem(pdf_path: str | Path) -> bool:
    """Retorna True quando o RELDEV marca Itens De Conferencia / Chave Reserva / Nao Tem."""
    return any(is_chave_reserva_nao_tem_row(row) for row in extract_reldev_rows(pdf_path))

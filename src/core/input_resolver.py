from __future__ import annotations

from pathlib import Path


def resolve_fotos_dir(case_dir: Path) -> Path:
    """Resolve a pasta de fotos dentro do case.

    Suporta variações comuns de naming/case:
    - Fotos / fotos / FOTOS
    - Pasta_Fotos / pasta_fotos

    Retorna um Path (mesmo que não exista) para manter compatibilidade com
    códigos que checam `exists()` depois.
    """
    candidatos = [
        case_dir / "Fotos",
        case_dir / "fotos",
        case_dir / "FOTOS",
        case_dir / "Pasta_Fotos",
        case_dir / "pasta_fotos",
        case_dir / "PASTA_FOTOS",
    ]

    for candidato in candidatos:
        if candidato.exists() and candidato.is_dir():
            return candidato

    if case_dir.exists() and case_dir.is_dir():
        for child in case_dir.iterdir():
            if not child.is_dir():
                continue
            nome = child.name.strip().lower()
            if nome in {"fotos", "pasta_fotos"}:
                return child

    # fallback conservador (mantém compatibilidade com fluxo antigo)
    return candidatos[0]


def resolve_pdf_dir(case_dir: Path) -> Path:
    """Resolve a pasta de PDFs dentro do case."""
    candidatos = [
        case_dir / "PDF",
        case_dir / "pdf",
        case_dir / "Pasta_PDF",
        case_dir / "pasta_pdf",
        case_dir / "PASTA_PDF",
    ]

    for candidato in candidatos:
        if candidato.exists() and candidato.is_dir():
            return candidato

    if case_dir.exists() and case_dir.is_dir():
        for child in case_dir.iterdir():
            if not child.is_dir():
                continue
            nome = child.name.strip().lower()
            if nome in {"pdf", "pasta_pdf"}:
                return child

    # fallback para manter comportamento previsível
    return candidatos[0]


def resolve_checklist_pdf(case_dir: Path) -> Path | None:
    """Encontra o checklist no novo formato.

    Regra: o checklist fica dentro da pasta de PDF e o nome do arquivo contém
    "RELDEV" (case-insensitive).

    Retorna o Path do PDF encontrado, ou None.
    """
    pdf_dir = resolve_pdf_dir(case_dir)
    if not pdf_dir.exists() or not pdf_dir.is_dir():
        return None

    # Procura de forma tolerante: pode estar em subpastas.
    candidatos = [
        p
        for p in pdf_dir.rglob("*.pdf")
        if p.is_file() and ("reldev" in p.name.lower())
    ]

    if not candidatos:
        return None

    # Preferência determinística: nome (case-insensitive), depois caminho.
    candidatos.sort(key=lambda p: (p.name.lower(), str(p).lower()))
    return candidatos[0]

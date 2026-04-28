from __future__ import annotations

import fitz
import argparse
import json
from dataclasses import dataclass
from pathlib import Path


TITLE_RIGHT = "LATERAL DIREITA"
TITLE_LEFT = "LATERAL ESQUERDA"
TITLE_END = "PNEUS/RODA/CALOTA"


@dataclass(frozen=True)
class SelectedPage:
    page_number_1based: int
    width_px: int
    height_px: int
    image_path: str


@dataclass(frozen=True)
class TextAnchor:
    text: str
    page_number_1based: int
    y_top_px: float


def _normalize(text: str) -> str:
    return (text or "").strip().upper()


def _find_text_anchors(page: "fitz.Page", text: str, page_number_1based: int, zoom: float) -> list[TextAnchor]:
    import fitz  # type: ignore

    anchors: list[TextAnchor] = []
    rects = page.search_for(text)
    for rect in rects:
        if not isinstance(rect, fitz.Rect):
            continue
        anchors.append(
            TextAnchor(
                text=text,
                page_number_1based=page_number_1based,
                y_top_px=rect.y0 * zoom,
            )
        )
    return anchors


def run_test(checklist_pdf: Path, out_dir: Path, zoom: float) -> dict:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("PyMuPDF (fitz) nao esta disponivel neste ambiente.") from exc

    try:
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Pillow (PIL) nao esta disponivel neste ambiente.") from exc

    out_dir.mkdir(parents=True, exist_ok=True)

    selected_pages: list[SelectedPage] = []
    anchors_right: list[TextAnchor] = []
    anchors_end: list[TextAnchor] = []

    with fitz.open(checklist_pdf) as doc:
        # Desconsidera as duas primeiras paginas e inicia a busca a partir da pagina 3.
        for page_idx in range(2, len(doc)):
            page = doc.load_page(page_idx)
            page_num = page_idx + 1

            has_right = len(page.search_for(TITLE_RIGHT)) > 0
            has_left = len(page.search_for(TITLE_LEFT)) > 0
            if not (has_right or has_left):
                continue

            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            img_path = out_dir / f"pagina_{page_num}.png"
            pix.save(img_path)

            selected_pages.append(
                SelectedPage(
                    page_number_1based=page_num,
                    width_px=pix.width,
                    height_px=pix.height,
                    image_path=str(img_path),
                )
            )

            anchors_right.extend(_find_text_anchors(page, TITLE_RIGHT, page_num, zoom))
            anchors_end.extend(_find_text_anchors(page, TITLE_END, page_num, zoom))

    if not selected_pages:
        raise RuntimeError(
            "Nenhuma pagina encontrada com os titulos 'LATERAL DIREITA' ou 'LATERAL ESQUERDA'."
        )

    pages_sorted = sorted(selected_pages, key=lambda p: p.page_number_1based)
    widths = [p.width_px for p in pages_sorted]
    heights = [p.height_px for p in pages_sorted]

    combined_width = max(widths)
    combined_height = sum(heights)
    combined = Image.new("RGB", (combined_width, combined_height), color=(255, 255, 255))

    page_y_offset: dict[int, int] = {}
    y_cursor = 0
    for page in pages_sorted:
        img = Image.open(page.image_path).convert("RGB")
        combined.paste(img, (0, y_cursor))
        page_y_offset[page.page_number_1based] = y_cursor
        y_cursor += page.height_px

    combined_path = out_dir / "paginas_laterais_unidas.png"
    combined.save(combined_path)

    right_abs: list[tuple[int, float]] = []
    for a in anchors_right:
        if a.page_number_1based in page_y_offset:
            right_abs.append((a.page_number_1based, page_y_offset[a.page_number_1based] + a.y_top_px))

    if not right_abs:
        raise RuntimeError(
            "Nenhuma ocorrencia de 'LATERAL DIREITA' foi encontrada nas paginas selecionadas."
        )

    right_abs.sort(key=lambda x: (x[0], x[1]))
    start_page, start_y = right_abs[0]

    end_candidates: list[float] = []
    for a in anchors_end:
        if a.page_number_1based not in page_y_offset:
            continue
        abs_y = page_y_offset[a.page_number_1based] + a.y_top_px
        if abs_y > start_y:
            end_candidates.append(abs_y)

    end_y = min(end_candidates) if end_candidates else float(combined_height)

    start_y_int = max(0, int(start_y))
    end_y_int = min(combined_height, int(end_y))

    if end_y_int <= start_y_int:
        raise RuntimeError(
            "Recorte invalido: ponto final ficou antes do inicio. Verifique titulos no PDF."
        )

    cropped = combined.crop((0, start_y_int, combined_width, end_y_int))
    cropped_path = out_dir / "recorte_lateral_direita_ate_pneus.png"
    cropped.save(cropped_path)

    payload = {
        "checklist_pdf": str(checklist_pdf),
        "zoom": zoom,
        "titulos": {
            "inicio": TITLE_RIGHT,
            "paginas_selecionadas_por": [TITLE_RIGHT, TITLE_LEFT],
            "fim": TITLE_END,
        },
        "paginas_selecionadas": [
            {
                "page_number_1based": p.page_number_1based,
                "image_path": p.image_path,
                "width_px": p.width_px,
                "height_px": p.height_px,
            }
            for p in pages_sorted
        ],
        "imagem_unida_path": str(combined_path),
        "recorte_path": str(cropped_path),
        "recorte": {
            "start_page_1based": start_page,
            "start_y_px_in_combined": start_y_int,
            "end_y_px_in_combined": end_y_int,
            "end_rule": "titulo_pneus" if end_candidates else "fim_da_imagem_unida",
        },
    }

    report_path = out_dir / "saida_teste_recorte_laterais.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seleciona paginas com 'LATERAL DIREITA'/'LATERAL ESQUERDA', "
            "une em uma unica imagem e recorta de 'LATERAL DIREITA' ate antes de 'PNEUS/RODA/CALOTA'."
        )
    )
    parser.add_argument("--checklist", required=True, help="Caminho do checklist PDF")
    parser.add_argument(
        "--out-dir",
        default="./output_teste_recorte_laterais",
        help="Diretorio de saida para imagens e JSON",
    )
    parser.add_argument(
        "--zoom",
        type=float,
        default=2.0,
        help="Zoom da renderizacao das paginas (padrao: 2.0)",
    )
    args = parser.parse_args()

    checklist_pdf = Path(args.checklist).expanduser().resolve()
    if not checklist_pdf.exists():
        print(f"ERRO: checklist nao encontrado: {checklist_pdf}")
        return 1

    out_dir = Path(args.out_dir).expanduser().resolve()

    try:
        result = run_test(checklist_pdf=checklist_pdf, out_dir=out_dir, zoom=args.zoom)
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO durante o teste: {type(exc).__name__}: {exc}")
        return 1

    print("OK: teste concluido")
    print(f"Checklist: {result['checklist_pdf']}")
    print(f"Paginas selecionadas: {[p['page_number_1based'] for p in result['paginas_selecionadas']]}")
    print(f"Imagem unida: {result['imagem_unida_path']}")
    print(f"Recorte: {result['recorte_path']}")
    print(
        "Regra de fim do recorte: "
        f"{result['recorte']['end_rule']} (y={result['recorte']['end_y_px_in_combined']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

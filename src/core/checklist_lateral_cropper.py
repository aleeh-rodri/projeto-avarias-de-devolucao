from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz


TITLE_RIGHT = "LATERAL DIREITA"
TITLE_LEFT = "LATERAL ESQUERDA"
TITLE_END = "PNEUS/RODA/CALOTA"


@dataclass(frozen=True)
class TextAnchor:
    text: str
    page_number_1based: int
    y_top_px: float


@dataclass(frozen=True)
class RenderedPage:
    page_number_1based: int
    width_px: int
    height_px: int
    image: "Image.Image"


@dataclass(frozen=True)
class LateralCropResult:
    checklist_pdf: str
    lateral_direita_path: str
    lateral_esquerda_path: str
    start_page_1based: int
    end_page_1based: int
    zoom: float


def _find_text_anchors(page: "fitz.Page", text: str, page_number_1based: int, zoom: float) -> list[TextAnchor]:
    import fitz  # type: ignore

    anchors: list[TextAnchor] = []
    for rect in page.search_for(text):
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


def _pick_first_anchor_after(anchors: list[TextAnchor], page_number_1based: int, y_top_px: float) -> TextAnchor | None:
    candidates = [
        anchor
        for anchor in anchors
        if (anchor.page_number_1based > page_number_1based)
        or (anchor.page_number_1based == page_number_1based and anchor.y_top_px > y_top_px)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda anchor: (anchor.page_number_1based, anchor.y_top_px))


def extract_lateral_reference_images(
    checklist_pdf_path: str | Path,
    output_dir: str | Path,
    *,
    zoom: float = 2.0,
) -> LateralCropResult:
    """Gera duas imagens do checklist: lateral direita e lateral esquerda."""
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("PyMuPDF (fitz) nao esta disponivel neste ambiente.") from exc

    try:
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Pillow (PIL) nao esta disponivel neste ambiente.") from exc

    checklist_pdf = Path(checklist_pdf_path).expanduser().resolve()
    if not checklist_pdf.exists():
        raise FileNotFoundError(f"Checklist PDF nao encontrado: {checklist_pdf}")

    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    anchors_right: list[TextAnchor] = []
    anchors_left: list[TextAnchor] = []
    anchors_end: list[TextAnchor] = []

    with fitz.open(checklist_pdf) as doc:
        for page_idx in range(2, len(doc)):
            page = doc.load_page(page_idx)
            page_num = page_idx + 1
            anchors_right.extend(_find_text_anchors(page, TITLE_RIGHT, page_num, zoom))
            anchors_left.extend(_find_text_anchors(page, TITLE_LEFT, page_num, zoom))
            anchors_end.extend(_find_text_anchors(page, TITLE_END, page_num, zoom))

        if not anchors_right:
            raise RuntimeError("Nenhuma ocorrencia de 'LATERAL DIREITA' foi encontrada no PDF.")

        anchors_right.sort(key=lambda anchor: (anchor.page_number_1based, anchor.y_top_px))
        anchors_left.sort(key=lambda anchor: (anchor.page_number_1based, anchor.y_top_px))
        anchors_end.sort(key=lambda anchor: (anchor.page_number_1based, anchor.y_top_px))

        right_anchor = anchors_right[0]
        left_anchor = _pick_first_anchor_after(
            anchors_left,
            right_anchor.page_number_1based,
            right_anchor.y_top_px,
        )
        if left_anchor is None:
            raise RuntimeError(
                "Nenhuma ocorrencia de 'LATERAL ESQUERDA' foi encontrada depois de 'LATERAL DIREITA'."
            )

        end_anchor = _pick_first_anchor_after(
            anchors_end,
            left_anchor.page_number_1based,
            left_anchor.y_top_px,
        )
        if end_anchor is None:
            raise RuntimeError(
                "Nenhuma ocorrencia de 'PNEUS/RODA/CALOTA' foi encontrada depois de 'LATERAL ESQUERDA'."
            )

        start_page_idx = right_anchor.page_number_1based - 1
        end_page_idx = end_anchor.page_number_1based - 1

        rendered_pages: list[RenderedPage] = []
        for page_idx in range(start_page_idx, end_page_idx + 1):
            page = doc.load_page(page_idx)
            page_num = page_idx + 1
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            rendered_pages.append(
                RenderedPage(
                    page_number_1based=page_num,
                    width_px=pix.width,
                    height_px=pix.height,
                    image=image,
                )
            )

    if not rendered_pages:
        raise RuntimeError("Nenhuma pagina foi renderizada para o recorte das laterais.")

    combined_width = max(page.width_px for page in rendered_pages)
    combined_height = sum(page.height_px for page in rendered_pages)
    combined = Image.new("RGB", (combined_width, combined_height), color=(255, 255, 255))

    page_y_offset: dict[int, int] = {}
    y_cursor = 0
    for page in rendered_pages:
        combined.paste(page.image, (0, y_cursor))
        page_y_offset[page.page_number_1based] = y_cursor
        y_cursor += page.height_px

    right_start_y = int(page_y_offset[right_anchor.page_number_1based] + right_anchor.y_top_px)
    left_start_y = int(page_y_offset[left_anchor.page_number_1based] + left_anchor.y_top_px)
    end_start_y = int(page_y_offset[end_anchor.page_number_1based] + end_anchor.y_top_px)

    if left_start_y <= right_start_y:
        raise RuntimeError("Recorte invalido: 'LATERAL ESQUERDA' ficou antes de 'LATERAL DIREITA'.")
    if end_start_y <= left_start_y:
        raise RuntimeError("Recorte invalido: 'PNEUS/RODA/CALOTA' ficou antes de 'LATERAL ESQUERDA'.")

    lateral_direita = combined.crop((0, right_start_y, combined_width, left_start_y))
    lateral_esquerda = combined.crop((0, left_start_y, combined_width, end_start_y))

    lateral_direita_path = out_dir / "recorte_lateral_direita.png"
    lateral_esquerda_path = out_dir / "recorte_lateral_esquerda.png"
    lateral_direita.save(lateral_direita_path)
    lateral_esquerda.save(lateral_esquerda_path)

    return LateralCropResult(
        checklist_pdf=str(checklist_pdf),
        lateral_direita_path=str(lateral_direita_path),
        lateral_esquerda_path=str(lateral_esquerda_path),
        start_page_1based=right_anchor.page_number_1based,
        end_page_1based=end_anchor.page_number_1based,
        zoom=zoom,
    )

from __future__ import annotations

from pathlib import Path

from core.vehicle_metadata import VehicleMetadataCache


VALID_TEMPLATE_TYPES = {"GF", "MEOO"}


def normalize_template_type(value: str | None) -> str:
    v = str(value or "").strip().upper()

    if v in VALID_TEMPLATE_TYPES:
        return v

    return "GF"


class ExcelTemplateResolver:
    def __init__(
        self,
        *,
        metadata_xlsx_path: str | Path,
        templates_dir: str | Path,
    ):
        self.metadata_cache = VehicleMetadataCache(metadata_xlsx_path)
        self.templates_dir = Path(templates_dir)

    def get_template_type(self, placa: str) -> str:
        metadata = self.metadata_cache.get_vehicle_metadata(placa)
        return normalize_template_type(metadata.get("template_type"))

    def resolve_template_path(self, placa: str) -> Path:
        template_type = self.get_template_type(placa)

        template_map = {
            "GF": self.templates_dir / "template_excel_GF.xlsx",
            "MEOO": self.templates_dir / "template_excel_MEOO.xlsx",
        }

        template_path = template_map[template_type]

        if not template_path.exists():
            raise FileNotFoundError(
                f"Template Excel não encontrado para tipo {template_type}: {template_path}"
            )

        return template_path
from __future__ import annotations

import sys
from pathlib import Path

# Ajuste de path para permitir imports de `src/` quando rodado via terminal
THIS = Path(__file__).resolve()
SRC = THIS.parents[2]
sys.path.insert(0, str(SRC))

from core.lpu import load_lpu_items, find_services

REPO_ROOT = SRC.parent
LPU_XLSX = REPO_ROOT / "LPU.xlsx"

print("LPU:", LPU_XLSX, "exists=", LPU_XLSX.exists())
items = load_lpu_items(LPU_XLSX)
lataria = [it for it in items if it.perito == "lataria"]
print("items:", len(items), "lataria:", len(lataria))

for kws in (["paralama", "pintura", "direit"], ["para lama", "pintura", "direit"]):
    hits = find_services(items, kws, perito_filtro="lataria", modo_restrito=False, allow_global_fallback=False)[:10]
    print("\nKWS:", kws)
    for h in hits:
        print("-", h.descricao)

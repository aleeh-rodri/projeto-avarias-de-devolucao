from abc import ABC, abstractmethod
from typing import Any
from core.lpu import LpuItem

class BasePerito(ABC):
    @abstractmethod
    def run(self, image_paths: list[str], **kwargs) -> dict[str, Any]:
        pass

    def _severity_rank(self, nivel: str) -> int:
        ordem = {"sem_dano": 0, "leve": 1, "moderado": 2, "grave": 3}
        return ordem.get((nivel or "").strip().lower(), -1)

    def _calculate_total(self, selected_services: list[LpuItem]) -> float | str:
        total_numeric = 0.0
        any_non_numeric = False
        for s in selected_services:
            if isinstance(s.preco, (int, float)):
                total_numeric += float(s.preco)
            else:
                any_non_numeric = True

        if any_non_numeric:
            return "Sob consulta"
        return round(total_numeric, 2)

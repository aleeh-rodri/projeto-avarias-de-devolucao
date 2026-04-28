from __future__ import annotations

import os
import sys

# garante imports a partir de `src/`
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from core.llm_gate_client import call_llm_with_image


def main() -> None:
    prompt = 'Responda somente com JSON válido: {"ok": true}'
    out = call_llm_with_image(
        prompt=prompt,
        image_path="Fotos/case_id/fe3475ac-2b61-a507-bb51-aa6a2da63b39-v1.jpg",
        max_tokens=80,
    )
    print(out)


if __name__ == "__main__":
    main()

from __future__ import annotations

import base64
import uuid
from pathlib import Path

import requests

from core.llm_gate_client import _get_config, _guess_mime_type


def main() -> None:
    cfg = _get_config()
    img = Path("Fotos/case_id/fe3475ac-2b61-a507-bb51-aa6a2da63b39-v1.jpg")
    if not img.exists():
        raise FileNotFoundError(img)

    b = img.read_bytes()
    mime = _guess_mime_type(str(img))
    b64 = base64.b64encode(b).decode("utf-8")

    payload = {
        "model": cfg.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Responda apenas com JSON: {\"ok\": true}"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 80,
        "response_format": {"type": "text"},
    }

    headers = {
        "Content-Type": "application/json",
        "X-Correlation-Id": str(uuid.uuid4()),
    "api_key": cfg.api_key,
    }
    if cfg.x_user_id:
        headers["X-User-Id"] = cfg.x_user_id
    if cfg.client_id:
        headers["client_id"] = cfg.client_id

    url = f"{cfg.base_url}{cfg.route}"
    r = requests.post(url, json=payload, headers=headers, timeout=cfg.timeout_s, verify=cfg.verify_ssl)
    print("URL:", url)
    print("Status:", r.status_code)
    print("Body (first 4000 chars):")
    print(r.text[:4000])


if __name__ == "__main__":
    main()

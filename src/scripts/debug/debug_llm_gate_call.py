from __future__ import annotations

import argparse
import base64
import sys
import uuid
from pathlib import Path
from typing import Any

import requests


BASE_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.llm_gate_client import _get_config, _guess_mime_type


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Teste manual de chamada ao LLM Gate (v2/chat/completions).")
    parser.add_argument(
        "--image",
        required=True,
        help="Caminho da imagem local (ex.: input/CUW7H90/Fotos/FOTDEV_15.jpg).",
    )
    parser.add_argument(
        "--prompt",
        default='Responda apenas com JSON valido: {"ok": true}',
        help="Prompt de teste enviado junto com a imagem.",
    )
    parser.add_argument("--max-tokens", type=int, default=120, help="max_tokens do payload.")
    parser.add_argument("--temperature", type=float, default=0.0, help="temperature do payload.")
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Ignora variaveis de proxy do ambiente para esta chamada.",
    )
    return parser.parse_args()


def _build_payload(*, model: str, prompt: str, data_url: str, max_tokens: int, temperature: float) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": {"type": "text"},
    }


def main() -> None:
    args = _parse_args()
    cfg = _get_config()

    img = Path(args.image)
    if not img.exists():
        raise FileNotFoundError(f"Imagem nao encontrada: {img.resolve()}")

    img_bytes = img.read_bytes()
    mime = _guess_mime_type(str(img))
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")
    data_url = f"data:{mime};base64,{img_b64}"

    url = f"{cfg.base_url}{cfg.route}"
    payload = _build_payload(
        model=cfg.model,
        prompt=args.prompt,
        data_url=data_url,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
    )

    base_headers = {
        "Content-Type": "application/json",
        "X-Correlation-Id": str(uuid.uuid4()),
    }
    if cfg.x_user_id:
        base_headers["X-User-Id"] = cfg.x_user_id
    if cfg.client_id:
        base_headers["client_id"] = cfg.client_id

    auth_variants = [
        {"api_key": cfg.api_key},
        {"API_KEY": cfg.api_key},
        {"X-API-Key": cfg.api_key},
    ]

    session = requests.Session()
    if args.no_proxy:
        session.trust_env = False

    last_response: requests.Response | None = None
    last_error: Exception | None = None
    used_auth_header = "none"

    for auth in auth_variants:
        headers = dict(base_headers)
        headers.update(auth)
        used_auth_header = next(iter(auth.keys()))
        try:
            response = session.post(
                url,
                json=payload,
                headers=headers,
                timeout=cfg.timeout_s,
                verify=cfg.verify_ssl,
            )
            last_response = response
            if response.status_code < 400:
                break
        except Exception as exc:  # noqa: BLE001
            last_error = exc

    print("URL:", url)
    print("Imagem:", str(img.resolve()))
    print("Auth header usado:", used_auth_header)
    print("Verify SSL:", cfg.verify_ssl)
    print("No proxy:", args.no_proxy)

    if last_response is None and last_error is not None:
        print("Erro de conexao:", repr(last_error))
        raise last_error

    if last_response is None:
        raise RuntimeError("Falha inesperada: nenhuma resposta e nenhum erro capturado.")

    print("Status:", last_response.status_code)
    print("Response headers:", dict(last_response.headers))
    print("Body (first 4000 chars):")
    print(last_response.text[:4000])


if __name__ == "__main__":
    main()

"""Cliente mínimo para chamar o LLM Gate (Vision) com uma imagem.

Foco: destravar o Agente de Triagem 1.

Formato esperado (conforme teu exemplo curl):
- POST https://.../llm-gate/v2/chat/completions
- Headers: Content-Type, X-Correlation-Id, API_KEY
- Body: { model, messages: [{role:'user', content:[text, image_url]}], max_tokens, ... }

Configuração via `AGENTE_AVARIAS_DEVOLUCAO/.env`.
"""

from __future__ import annotations

import base64
import io
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning
from PIL import Image as PILImage
from PIL import ImageOps
from core.config import config


@dataclass(frozen=True)
class LLMGateConfig:
	base_url: str
	route: str
	api_key: str
	model: str
	timeout_s: int = 120
	verify_ssl: bool = True
	x_user_id: str | None = None
	client_id: str | None = None


def _get_config() -> LLMGateConfig:
	# Usa a nova estrutura de config centralizada
	base_url = os.getenv("LLM_GATE_BASE_URL", "").strip()
	route = os.getenv("LLM_GATE_ROUTE", "").strip()
	api_key = config.LLM_GATE_API_KEY
	model = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()
	x_user_id = os.getenv("LLM_GATE_X_USER_ID", "").strip() or None
	client_id = os.getenv("LLM_GATE_CLIENT_ID", "").strip() or None
	verify_ssl_env = os.getenv("LLM_GATE_VERIFY_SSL", "true").strip().lower()
	verify_ssl = verify_ssl_env not in ("0", "false", "no")

	if not base_url:
		raise RuntimeError("LLM_GATE_BASE_URL não configurado no .env")
	if not route:
		raise RuntimeError("LLM_GATE_ROUTE não configurado no .env")
	if not api_key:
		raise RuntimeError("LLM_GATE_API_KEY (ou API_KEY) não configurado no .env")

	return LLMGateConfig(
		base_url=base_url.rstrip("/"),
		route=route if route.startswith("/") else f"/{route}",
		api_key=api_key,
		model=model,
		verify_ssl=verify_ssl,
		x_user_id=x_user_id,
		client_id=client_id,
	)


def _guess_mime_type(image_path: str) -> str:
	ext = Path(image_path).suffix.lower()
	if ext in (".jpg", ".jpeg"):
		return "image/jpeg"
	if ext == ".png":
		return "image/png"
	return "application/octet-stream"


def _read_image_bytes_auto_orient(image_path: str) -> tuple[bytes, str]:
	"""Lê a imagem aplicando EXIF orientation quando disponível.

	Motivação: algumas fotos chegam "deitadas" (90°) e dependem do EXIF Orientation.
	Quando enviamos bytes puros em base64, o consumidor pode ignorar EXIF.
	"""
	mime = _guess_mime_type(image_path)
	try:
		with PILImage.open(image_path) as img:
			img = ImageOps.exif_transpose(img)
			out = io.BytesIO()

			# Re-encode mantendo formato quando possível
			if mime == "image/png":
				img.save(out, format="PNG")
				return (out.getvalue(), "image/png")

			# default jpeg
			if img.mode in ("RGBA", "LA"):
				img = img.convert("RGB")
			img.save(out, format="JPEG", quality=92, optimize=True)
			return (out.getvalue(), "image/jpeg")
	except Exception:
		# Fallback: bytes originais
		return (Path(image_path).read_bytes(), mime)


def call_llm_with_image(
	*,
	prompt: str,
	image_path: str,
	temperature: float = 0,
	max_tokens: int = 400,
) -> str:
	"""Chama o LLM Gate (v2/chat/completions) com prompt + imagem.

	Payload/headers alinhados ao exemplo curl e ao `antigo_agente_avarias.py`.
	Esperado: resposta estilo OpenAI em `choices[0].message.content`.
	"""
	cfg = _get_config()
	# Quando o ambiente usa TLS sem CA confiável (ex.: NP), o requests/urllib3
	# emite `InsecureRequestWarning` a cada chamada e polui o console.
	# Mantemos a opção de desabilitar via env (default: silenciar quando verify_ssl=False).
	if cfg.verify_ssl is False:
		suppress = os.getenv("LLM_GATE_SUPPRESS_INSECURE_WARNING", "true").strip().lower() not in ("0", "false", "no")
		if suppress:
			urllib3.disable_warnings(InsecureRequestWarning)
	img_bytes, mime = _read_image_bytes_auto_orient(image_path)
	img_b64 = base64.b64encode(img_bytes).decode("utf-8")

	url = f"{cfg.base_url}{cfg.route}"
	payload: dict[str, Any] = {
		"model": cfg.model,
		"messages": [
			{
				"role": "user",
				"content": [
					{"type": "text", "text": prompt},
					{
						"type": "image_url",
						"image_url": {"url": f"data:{mime};base64,{img_b64}"},
					},
				],
			}
		],
		"max_tokens": max_tokens,
		"temperature": temperature,
		"response_format": {"type": "text"},
	}

	headers: dict[str, str] = {
		"Content-Type": "application/json",
		"X-Correlation-Id": str(uuid.uuid4()),
	}

	# Auth: o gateway pode exigir o header exatamente como `api_key`.
	# Vamos tentar variações para compatibilidade.
	auth_variants = [
		{"api_key": cfg.api_key},
		{"API_KEY": cfg.api_key},
		{"X-API-Key": cfg.api_key},
	]
	if cfg.x_user_id:
		headers["X-User-Id"] = cfg.x_user_id
	if cfg.client_id:
		headers["client_id"] = cfg.client_id

	last_exc: Exception | None = None
	for add_headers in auth_variants:
		merged = dict(headers)
		merged.update(add_headers)
		try:
			resp = requests.post(
				url,
				json=payload,
				headers=merged,
				timeout=cfg.timeout_s,
				verify=cfg.verify_ssl,
			)
			if resp.status_code != 200:
				print(f"DEBUG: Status {resp.status_code} - body: {resp.text}")
			resp.raise_for_status()
			data = resp.json()
			break
		except Exception as e:  # noqa: BLE001
			last_exc = e
	else:
		# se todas falharem, sobe a última
		raise last_exc  # type: ignore[misc]

	try:
		return data["choices"][0]["message"]["content"]
	except Exception as e:  # noqa: BLE001
		raise RuntimeError(f"Resposta inesperada do LLM Gate: {data}") from e


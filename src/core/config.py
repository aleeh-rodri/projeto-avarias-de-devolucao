import os
from pathlib import Path

from dotenv import load_dotenv

# Carrega o .env do projeto (AGENTE_AVARIAS_DEVOLUCAO/.env) independentemente do CWD.
_PROJECT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_DIR / ".env")

class Config:
    # API Keys e URLs
    LLM_GATE_URL = os.getenv("LLM_GATE_URL", "http://localhost:8000/v1/chat/completions")
    LLM_GATE_API_KEY = os.getenv("LLM_GATE_API_KEY") or os.getenv("API_KEY") or ""
    
    # Caminhos padrão
    BASE_DIR = Path(__file__).parent.parent.parent
    LPU_DEFAULT_PATH = os.getenv("LPU_DEFAULT_PATH", str(BASE_DIR / "LPU.xlsx"))
    
    # Configurações de Orquestração
    CONFIANCA_MINIMA = float(os.getenv("CONFIANCA_MINIMA", "0.6"))
    MAX_FOTOS_POR_PECA = int(os.getenv("MAX_FOTOS_POR_PECA", "3"))

config = Config()

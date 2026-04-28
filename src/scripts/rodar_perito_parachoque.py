import json
import os
import sys

# Garante que o diretório `src` esteja no sys.path para permitir importação de `agents.*`
SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Ajusta o CWD para `src` para manter paths relativos previsíveis
if SRC_DIR != os.getcwd():
    os.chdir(SRC_DIR)

from agents.peritos.perito_parachoque import (
    ConfigPeritoParachoque,
    PeritoParachoque,
)
from core.config import config

CASE_ID = "case_id"
BASE_DIR = os.path.dirname(SRC_DIR)

# Exemplo: roda o perito numa imagem específica
image_path = os.path.join(
    BASE_DIR,
    "Fotos",
    CASE_ID,
    "3f393c37-8850-1010-6638-da546782f260-v1.jpg",
)

perito = PeritoParachoque(ConfigPeritoParachoque(caminho_lpu_xlsx=config.LPU_DEFAULT_PATH))
result = perito.run(image_paths=[image_path])

print(json.dumps(result, ensure_ascii=False, indent=2))

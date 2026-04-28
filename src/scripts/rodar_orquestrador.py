import json
import os
import sys

# Garante que o diretório `src` esteja no sys.path para permitir importação de módulos
SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Ajusta o CWD para `src` para manter paths relativos previsíveis
if SRC_DIR != os.getcwd():
    os.chdir(SRC_DIR)

from core.orquestrador import (
    ConfigOrquestrador,
    rodar_orquestrador,
)
from core.config import config

CASE_ID = "case_id"
BASE_DIR = os.path.dirname(SRC_DIR)

# Agora o ConfigOrquestrador já vem com defaults do core.config
resultado = rodar_orquestrador(
    case_id=CASE_ID,
    fotos_dir=os.path.join(BASE_DIR, "Fotos", CASE_ID),
    output_dir=os.path.join(BASE_DIR, "output", CASE_ID),
    config=ConfigOrquestrador(),
)

print(json.dumps(resultado, ensure_ascii=False, indent=2))

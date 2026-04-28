import os
import sys
from pathlib import Path


# Garante que o diretório `src` esteja no sys.path para permitir importação de `agents.*`
SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Ajusta o CWD para `src` para manter paths relativos previsíveis
if SRC_DIR != os.getcwd():
    os.chdir(SRC_DIR)

from agents.triage_agent import run_triage
from core.input_resolver import resolve_checklist_pdf, resolve_fotos_dir

PLACA = "RUY5J95"
BASE_DIR = os.path.dirname(SRC_DIR)

# O checklist fica dentro da pasta PDF e contém "RELDEV" no nome
CASE_DIR = Path(BASE_DIR) / "input" / PLACA
CHECKLIST_PDF = resolve_checklist_pdf(CASE_DIR)

FOTOS_DIR = str(resolve_fotos_dir(CASE_DIR))

run_triage(
    case_id=PLACA,
    fotos_dir=FOTOS_DIR,
    output_dir=os.path.join(BASE_DIR, "output", PLACA),
    checklist_path=str(CHECKLIST_PDF) if CHECKLIST_PDF else None
)

"""Schemas e constantes compartilhadas.

Neste MVP, a triagem (TriageAgent) precisa de uma lista canônica de `part_id`
para forçar o LLM a escolher somente valores válidos.
"""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field

# Lista canônica de partes (alinhada ao `Spec.md`)
PART_IDS: list[str] = [
	"parachoque_dianteiro",
	"parachoque_traseiro",
	"capo",
	"teto",
	"tampa_porta_malas",
	"porta_dianteira",
	"porta_dianteira_esquerda",
	"porta_dianteira_direita",
	"porta_traseira",
	"porta_traseira_esquerda",
	"porta_traseira_direita",
	"paralama_dianteiro",
	"paralama_dianteiro_esquerdo",
	"paralama_dianteiro_direito",
	"paralama_traseiro",
	"paralama_traseiro_esquerdo",
	"paralama_traseiro_direito",
	"retrovisor_esquerdo",
	"retrovisor_direito",
	"parabrisa",
	"vidro_traseiro",
	"roda_dianteira_esquerda",
	"roda_dianteira_direita",
	"roda_traseira_esquerda",
	"roda_traseira_direita",
	"interior",
	"acessorios",
    #novas partes
    "farol_esquerdo",
    "farol_direito",
    "lanterna_esquerda",
    "lanterna_direita",
    "parabarro_esquerdo", #adicionado
    "parabarro_direito", #adicionado
    "paralama_esquerdo", #adicionado
    "paralama_direito", #adicionado
    "motor",
]

# Peças que podem existir no checklist/Excel,
# mesmo que ainda não tenham perito automático.
CHECKLIST_PART_IDS: list[str] = sorted(set(PART_IDS + [
    "traseira",
    "dianteira",
    "grade_dianteira",
    "lateral_esquerda",
    "lateral_direita",
]))

class TriageImage(BaseModel):
    image_id: str
    photo_path: str
    part_id: str
    view: str
    confidence: float
    checklist_damage_reported: bool | None = Field(None, description="Indica se o checklist aponta avaria para esta peça")

    # Novo: origem determinística do part_id
    part_id_source: str | None = None

    # Novo: primeiro número depois de FOTDEV_
    photo_part_code: str | None = None

    # Novo: descrição textual da planilha, se existir
    expected_part_description: str | None = None

    # Novo: resultado bruto/estruturado da validação do LLM
    llm_part_validation: dict[str, Any] | None = None

    # Novo: quando LLM indicar divergência forte
    needs_human_review: bool = False

class TriageOutput(BaseModel):
    case_id: str
    images: list[TriageImage]
    checklist_summary: str | None = Field(None, description="Resumo das avarias encontradas no checklist")

class QualityAssessment(BaseModel):
    image_id: str
    aprovada: bool
    motivo: str | None = None
    qualidade_imagem: Literal["baixa", "media", "alta"]
    corresponde_a_peca: bool

class QualityOutput(BaseModel):
    case_id: str
    assessments: list[QualityAssessment]

class BumperExpertOutput(BaseModel):
    posicao_parachoque: Literal["dianteiro", "traseiro"]
    nivel_dano_parachoque: Literal["sem_dano", "leve", "moderado", "grave"]
    localizacao_avaria: Literal["canto_esquerdo", "canto_direito", "centro", "superior", "inferior", "nao_identificavel"] | None = None
    tipo_avaria: Literal["arranhao", "amassado", "quebra", "trinca", "outro"] | None = None
    observacoes_objetivas: str

class DoorExpertOutput(BaseModel):
    posicao_porta: Literal["dianteira_esquerda", "dianteira_direita", "traseira_esquerda", "traseira_direita"]
    nivel_dano_porta: Literal["sem_dano", "leve", "moderado", "grave"]
    observacoes_objetivas: str

class FenderExpertOutput(BaseModel):
    posicao_paralama: Literal["dianteiro_esquerdo", "dianteiro_direito", "traseiro_esquerdo", "traseiro_direito"]
    nivel_dano_paralama: Literal["sem_dano", "leve", "moderado", "grave"]
    observacoes_objetivas: str

class HoodExpertOutput(BaseModel):
    nivel_dano_capo: Literal["sem_dano", "leve", "moderado", "grave"]
    observacoes_objetivas: str

class ServiceItem(BaseModel):
    descricao: str
    preco: float | str

class ExpertConsolidatedOutput(BaseModel):
    nivel_dano: str = Field(..., description="Nível de dano consolidado (sem_dano, leve, moderado, grave)")
    peca: str = Field(..., description="Nome da peça analisada")
    servicos: list[ServiceItem]
    preco_total: float | str
    justificativa: str | None = None
    fotos_analisadas: list[str] = Field(default_factory=list)
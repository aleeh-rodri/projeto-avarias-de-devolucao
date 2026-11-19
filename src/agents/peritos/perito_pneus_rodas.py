from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from agents.peritos.base_perito import BasePerito
from core.llm_gate_client import call_llm_with_image, call_llm_with_images
from core.lpu import LpuItem, find_services, load_lpu_items
from core.schemas import ExpertConsolidatedOutput, ServiceItem


MAX_FOTOS_POR_RODA_COM_AVARIA = 3


@dataclass(frozen=True)
class ConfigPeritoPneusRodas:
    caminho_lpu_xlsx: str


class PeritoPneusRodas(BasePerito):
    def __init__(self, config: ConfigPeritoPneusRodas):
        self.config = config
        self.lpu_items = load_lpu_items(config.caminho_lpu_xlsx)

    @staticmethod
    def _wheel_position(part_id: str | None) -> tuple[str, str, str]:
        """Retorna (eixo, lado, part_id normalizado) para uma roda canônica."""
        pid = (part_id or "").strip().lower()
        if not pid.startswith("roda_"):
            return ("", "", pid)
        eixo = "dianteira" if "dianteira" in pid else ("traseira" if "traseira" in pid else "")
        lado = "direita" if pid.endswith("direita") else ("esquerda" if pid.endswith("esquerda") else "")
        return (eixo, lado, pid)

    @staticmethod
    def _parse_llm_json(raw: str) -> dict[str, Any]:
        value = raw.strip()
        if "```" in value:
            value = value.split("```", 2)[1]
            if value.startswith("json"):
                value = value[4:]
        parsed = json.loads(value.strip())
        if not isinstance(parsed, dict):
            raise ValueError("resposta do perito não é um objeto JSON")
        return parsed

    @staticmethod
    def _to_bool(value: object) -> bool:
        if value is True:
            return True
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "sim", "yes"}
        return False

    @classmethod
    def _billing_criteria(
        cls,
        result: dict[str, Any],
        *,
        nivel: str,
        peca: str = "",
    ) -> dict[str, Any]:
        """Normaliza os fatos visuais que alimentam o billing determinístico."""
        if nivel == "sem_dano":
            return {
                "tipo_dano": "sem_dano",
                "profundidade": "nao_aplicavel",
                "extensao": "nao_aplicavel",
                "quebra": False,
                "nivel_quebra": "nao_aplicavel",
                "trinca": False,
                "perda_material": False,
            }

        tipo = str(result.get("tipo_dano") or "incerto").strip().lower()
        tipos = (
            {"bolha", "rasgo", "corte", "incerto"}
            if peca == "pneu"
            else {"arranhao", "amassado", "quebra", "trinca", "outro", "incerto"}
        )
        if tipo not in tipos:
            tipo = "incerto"

        profundidade = str(result.get("profundidade") or "incerta").strip().lower()
        if profundidade not in {"superficial", "moderada", "profunda", "incerta"}:
            profundidade = "incerta"

        extensao = str(result.get("extensao") or "incerta").strip().lower()
        if extensao not in {"pequena", "media", "grande", "incerta"}:
            extensao = "incerta"

        quebra = cls._to_bool(result.get("quebra")) or tipo == "quebra"
        nivel_quebra = "nao_aplicavel"
        if quebra:
            nivel_quebra = str(result.get("nivel_quebra") or "incerta").strip().lower()
            if nivel_quebra not in {"pequena", "media", "grande", "incerta"}:
                nivel_quebra = "incerta"

        return {
            "tipo_dano": tipo,
            "profundidade": profundidade,
            "extensao": extensao,
            "quebra": quebra,
            "nivel_quebra": nivel_quebra,
            "trinca": cls._to_bool(result.get("trinca")) or tipo == "trinca",
            "perda_material": cls._to_bool(result.get("perda_material")),
        }

    def _select_service(self, peca: str, acao: str, part_id: str | None) -> list[LpuItem]:
        peca_norm = (peca or "").strip().lower()
        if peca_norm == "pneu":
            # A LPU usa pneu dianteiro/esquerdo, enquanto o part_id usa roda
            # dianteira/esquerda. Nao usar fallback parcial para outra posicao.
            eixo, lado, _ = self._wheel_position(part_id)
            if not eixo or not lado:
                return []
            posicao_pneu = {
                "dianteira": "dianteiro", "traseira": "traseiro",
                "direita": "direito", "esquerda": "esquerdo",
            }
            return find_services(
                self.lpu_items,
                ["pneu", "troca", posicao_pneu[eixo], posicao_pneu[lado]],
                perito_filtro="pneus_rodas",
                modo_restrito=True,
                allow_global_fallback=False,
                fuzzy=False,
            )[:1]
        if "calota" in peca_norm:
            # Na LPU, calota é um único jogo, independentemente da quantidade avariada.
            keywords = ["calota", "jogo"]
        elif "ferro" in peca_norm:
            keywords = ["roda", "ferro", acao]
        elif "liga" in peca_norm:
            keywords = ["roda", "liga", "leve", acao]
        else:
            keywords = ["roda", acao]

        eixo, lado, _ = self._wheel_position(part_id)
        if eixo:
            keywords.append(eixo)
        if lado:
            keywords.append(lado)

        selected = find_services(
            self.lpu_items,
            keywords,
            perito_filtro="pneus_rodas",
            modo_restrito=True,
            allow_global_fallback=False,
            fuzzy=False,
        )
        if not selected:
            selected = find_services(
                self.lpu_items,
                keywords,
                perito_filtro="pneus_rodas",
                allow_global_fallback=False,
                fuzzy=False,
            )

        selected = [
            service
            for service in selected
            if "banco" not in (service.descricao or "").lower()
            and "forro" not in (service.descricao or "").lower()
            and ("calota" if "calota" in peca_norm else "roda") in (service.descricao or "").lower()
            and (
                peca_norm != "roda ferro"
                or acao != "reparo"
                or "reparo" in (service.descricao or "").lower()
            )
        ]
        return selected[:1]

    def _analyze_wheel(
        self,
        image_paths: list[str],
        part_id: str | None,
        checklist_summary: str,
        wheel_type: str,
    ) -> list[dict[str, Any]]:
        wheel_type_norm = (wheel_type or "desconhecido").strip().lower()
        if wheel_type_norm == "liga_leve":
            wheel_type_prompt = "roda de liga leve"
            expected_peca = "roda liga leve"
        elif wheel_type_norm == "ferro":
            wheel_type_prompt = "roda de ferro"
            expected_peca = "roda ferro"
        else:
            wheel_type_prompt = "desconhecido"
            expected_peca = "roda"

        prompt = f"""
Voce e um PERITO TECNICO ESPECIALISTA EM RODAS, PNEUS E CALOTAS.
Sua tarefa e relatar FATOS VISUAIS, nao decidir se o cliente deve ser cobrado.

OBJETIVO E METODO DE INSPECAO
- Analise a roda, o pneu e, se presente e avaliavel, a calota da posicao informada.
- As {len(image_paths)} foto(s) anexadas sao da MESMA posicao. Inspecione todas em conjunto, buscando confirmar o mesmo detalhe por angulos diferentes quando possivel.
- Uma imagem sem dano visivel nao elimina um dano nitido em outra imagem. Reflexos, sombras ou sujeira isolados nao sao prova suficiente de dano.
- Para cada componente: (1) identifique a superficie observada, (2) procure evidencias positivas de dano, (3) descarte aparencias normais e artefatos, (4) caracterize tipo/profundidade/extensao, (5) atribua severidade e acao tecnica.
- Gere no maximo UMA analise por componente (roda, calota, pneu). Se houver varios danos na mesma peca, use como tipo_dano principal o mais relevante tecnicamente e descreva os demais na justificativa.
- Nao omita a analise de um pneu avaliavel por encontrar dano na roda ou calota.
- O tipo de roda e um metadado fixo fornecido pelo sistema: nao o altere por aparencia.
- Priorize a evidencia das fotos sobre o checklist. Nao invente avarias, medidas, localizacoes ou caracteristicas nao visiveis.
- Retorne APENAS JSON valido (sem Markdown nem texto adicional).

PART_ID DA RODA ANALISADA:
{part_id or "desconhecido"}

TIPO DE RODA IDENTIFICADO PELO SISTEMA:
{wheel_type_prompt}

CONTEXTO DO CHECKLIST (apenas uma pista; nao equivale a evidencia visual):
{checklist_summary}

IDENTIFICACAO DOS COMPONENTES
- Roda de liga leve: use peca="roda liga leve" para dano no metal. Nao informe calota para roda de liga leve.
- Roda de ferro: use peca="roda ferro" para dano no metal estrutural da roda.
- Calota: cobertura normalmente plastica instalada sobre roda de ferro. Use peca="calota" SOMENTE quando a cobertura esta visivel e voce consegue distinguir o dano como sendo dela. Calota nao visivel nao significa automaticamente calota ausente ou quebrada.
- Pneu: use peca="pneu" para dano na borracha, independentemente da roda.
- Retorne analises apenas para componentes visiveis/avaliaveis; nao crie componente invisivel por suposicao.

CRITERIOS VISUAIS GERAIS (APLICAR ANTES DE CLASSIFICAR)
- Evidencia positiva de arranhao/ralado: marca linear ou area abrasiva com alteracao coerente de textura, pintura ou acabamento, e limites visiveis na superficie da peca.
- Nao classifique automaticamente como avaria: poeira, barro, reflexos, brilho especular, sombra, agua, inscricoes, linhas de moldagem, geometria normal ou diferencas de iluminacao.
- Arranhao superficial: alteracao aparente de brilho, tinta ou acabamento sem sulco profundo, ruptura ou deformacao demonstravel.
- Arranhao moderado: abrasao ou sulco mais nitido, com alteracao localizada do acabamento, mas sem evidencias claras de fratura ou deformacao estrutural.
- Arranhao profundo: sulco ou abrasao pronunciada, possivel exposicao de substrato e bordas bem marcadas. NAO presuma profundidade fisica real apenas pela cor; se nao for possivel distinguir, profundidade="incerta".
- Extensao pequena/media/grande e RELATIVA a area visivel do componente: pequena=localizada; media=ocupa uma regiao perceptivel; grande=atinge parcela ampla ou multiplas regioes. Nao estime centimetros ou porcentagens.
- Se a marca e visivel mas nao e possivel estimar profundidade ou extensao, utilize "incerta" no campo correspondente; nao aumente severidade apenas pela incerteza.
- Trinca: linha de ruptura com indicios de abertura ou descontinuidade material; nao confundir com frisos, contornos, juncao de pecas ou sombras.
- Quebra: parte rompida, fragmentada ou destacada, com descontinuidade identificavel. Perda_material=true SOMENTE se houver ausencia visivel de fragmento/material.
- Deformacao/amassado: mudanca anormal da forma/geometria da peca, demonstrada por contorno, borda ou superficie deslocada; nao confundir perspectiva, curvatura original ou reflexo com amassado.
- Se ha sinal de dano, mas tipo exato e duvidoso, prefira tipo_dano="incerto" e justifique a duvida. Sem evidencia positiva de dano, use tipo_dano="sem_dano".

CALOTA - CRITERIOS ESPECIFICOS
- Inspecione borda externa, face frontal, raios/desenhos, pontos de fixacao visiveis e areas proximas a borda.
- Risco/ralado leve: marcas finas ou superficiais de atrito, sem fratura, sem falta de material e sem alteracao clara do formato. Registre o dano mesmo se parecer leve; nao omita por possivel politica de nao cobranca.
- Ralado moderado: abrasao mais marcada, extensa ou com sulco aparente, sem evidencias de quebra ou perda de material.
- Risco profundo: sulco pronunciado ou abrasao severa observavel; pode coexistir com perda de acabamento, mas NAO equivale automaticamente a quebra.
- Trinca: linha de fratura no plastico; marque trinca=true, sem necessariamente marcar quebra=true.
- Quebra: segmento da calota rompido/descontinuo; marque quebra=true. Perda de fragmento visivel exige perda_material=true; sem evidencia de fragmento ausente, nao presuma perda.
- Diferencie arranhao de rachadura pela presenca de descontinuidade/fratura, e nao somente pela cor escura da marca.
- Calota com dano visivel: acao tecnica="troca" (nao existe reparo de calota neste fluxo). Isso NAO determina cobranca; danos leves continuam descritos normalmente.

RODA DE LIGA LEVE - CRITERIOS ESPECIFICOS
- Inspecione borda/aro, face externa e raios. Diferencie danos no METAL de danos no PNEU encostado na borda.
- Ralado de guia: abrasao ou arranhoes acompanhando a circunferencia da borda, com desgaste de acabamento/metal, mas contorno da borda preservado; tipo_dano="arranhao".
- Arranhao superficial: marca de acabamento/pintura sem sulco marcante nem geometria alterada; nivel_dano em geral "leve".
- Abrasao localizada mais intensa ou area maior de ralado, ainda sem deformacao/trinca: tipo_dano="arranhao", nivel_dano em geral "moderado" conforme evidencia.
- Amassado: alteracao visivel do formato da borda/aro/raio, como trecho achatado, ondulado ou deslocado; tipo_dano="amassado" SOMENTE com evidencias de geometria alterada.
- Trinca: linha clara de ruptura do metal, distinta de marcas de usinagem, emendas de reflexo e riscos superficiais; tipo_dano="trinca", trinca=true, nivel_dano="grave".
- Ralado/arranhao sem ruptura estrutural: acao="reparo". Trinca claramente visivel: acao="troca". Deformacao nitida: nivel_dano="grave"; em caso de incerteza sobre possibilidade de reparo, nao invente integridade estrutural e explicite a limitacao na justificativa.

RODA DE FERRO - CRITERIOS ESPECIFICOS
- Analise o metal da roda, especialmente aro/borda exposta, face, furos e regiao visivel de fixacao. Nao confunda relevo estampado original e aberturas de ventilacao com dano.
- Riscos e marcas de atrito: descontinuidade de pintura/acabamento; classifique "arranhao" quando houver marca abrasiva, sem formato alterado.
- Oxidacao/mancha: manchas de cor ou ferrugem superficial NAO sao automaticamente trinca, quebra ou amassado; nao transforme corrosao em tipo estrutural sem evidencia.
- Amassado: borda ou face realmente entortada, achatada ou ondulada em relacao a seu formato esperado; confirme pelo contorno, nao apenas por sombras/reflexos. Use tipo_dano="amassado".
- Trinca/ruptura: linha de abertura real no metal, nao um friso ou marca de pintura; trinca=true, tipo_dano="trinca", nivel_dano="grave", acao="troca".
- Arranhao sem deformacao: acao="reparo". Para roda de ferro com amassado, acao="reparo" conforme regra tecnica atual do sistema; na justificativa informe se a deformacao parece acentuada.

PNEU - CRITERIOS ESPECIFICOS
- Inspecione a lateral visivel, o ombro e partes da banda de rodagem que estejam efetivamente visiveis.
- Tipos permitidos para dano em pneu: SOMENTE "bolha", "rasgo" ou "corte". Nao invente outro tipo; quando houver indicio ambiguo de avaria, use "incerto" e descreva a limitacao.
- Bolha: protuberancia LOCALIZADA e anormal na borracha, com saliencia do contorno, distinta do formato arredondado natural do pneu, relevo de letras e reflexos. Nao confunda sombra com saliencia.
- Corte: incisao/fenda com aparencia de abertura na borracha, diferente de sulco regular de rodagem, canaleta do desenho ou linha de moldagem.
- Rasgo: ruptura irregular, com bordas abertas, lasca ou retalho da borracha; nao confundir marcas de sujeira ou relevo normal.
- Sulcos simetricos, letras em alto relevo, indicadores do fabricante, marcas de molde, sujeira e desgaste de uso NAO configuram automaticamente bolha, corte ou rasgo.
- Se a evidencia confirmar bolha, corte ou rasgo, acao="troca". Nunca recomende reparo para estes tipos nesta politica de analise. Nao estime marca, medidas ou preco; a LPU cuida dos servicos.
- Se nao houver evidencia visual positiva desses danos na regiao avaliavel, retorne sem_dano para pneu. Nao afirme que regioes ocultas estao integras.

SEVERIDADE (POR EVIDENCIA VISUAL, NAO POR COBRANCA)
- sem_dano: nenhum dano identificado na regiao avaliavel OU imagens insuficientes para afirmar um dano (limitacao do contrato atual; declare a insuficiencia na justificativa).
- leve: arranhoes/ralados superficiais pequenos, sem trinca, quebra, perda de material ou deformacao identificavel.
- moderado: abrasao/arranhao mais intenso ou extenso, sulco visivel ou dano localizado relevante sem prova clara de ruptura estrutural; deformacao discreta, quando inequivoca, pode se enquadrar aqui em roda de ferro.
- grave: trinca, quebra evidente, deformacao importante, perda de material relevante ou danos confirmados no pneu do tipo bolha/corte/rasgo.
- Em calota, trinca/quebra geralmente e grave; um risco superficial continua leve mesmo que a acao tecnica seja troca.
- Em roda de liga leve, deformacao nitida ou trinca e grave; arranhao sem deformacao geralmente nao e grave.
- Em pneu, bolha/corte/rasgo confirmados sao graves pela natureza do dano, independentemente do comprimento aparente.

CONSISTENCIA DO JSON
- Se nivel_dano="sem_dano": tipo_dano="sem_dano", profundidade="nao_aplicavel", extensao="nao_aplicavel", quebra=false, nivel_quebra="nao_aplicavel", trinca=false, perda_material=false. Mantenha acao="reparo" apenas como valor neutro exigido pelo contrato atual; o sistema nao gera servico em sem_dano.
- Se tipo_dano="quebra", quebra=true; se tipo_dano="trinca", trinca=true. Se quebra=false, nivel_quebra="nao_aplicavel"; se quebra=true, classifique nivel_quebra em pequena/media/grande/incerta.
- Use profundidade="nao_aplicavel" quando o conceito nao servir para o dano (ex.: bolha ou amassado). Use "incerta" quando aplicavel, mas nao inferivel.
- Use extensao="nao_aplicavel" somente em sem_dano; para danos visiveis use pequena/media/grande ou incerta conforme evidencia.
- Acao em calota sempre "troca" e em pneu com dano confirmado sempre "troca". Essa acao descreve conduta tecnica e NAO autoriza cobranca.
- Justificativa: indique componente, local visivel, sinais observados e motivo para distinguir dano de sujeira/reflexo/formato original; explicite incertezas, se houver. Nao alegue observacoes nao verificaveis nas imagens.

RETORNE APENAS ESTE JSON:
{{
 "analises": [{{
  "peca": "roda liga leve|roda ferro|calota|pneu",
  "nivel_dano": "sem_dano|leve|moderado|grave",
  "tipo_dano": "sem_dano|arranhao|amassado|quebra|trinca|bolha|rasgo|corte|outro|incerto",
  "profundidade": "nao_aplicavel|superficial|moderada|profunda|incerta",
  "extensao": "nao_aplicavel|pequena|media|grande|incerta",
  "quebra": false,
  "nivel_quebra": "nao_aplicavel|pequena|media|grande|incerta",
  "trinca": false,
  "perda_material": false,
  "acao": "reparo|troca",
  "justificativa": "descricao tecnica objetiva baseada na evidencia visual"
 }}]
}}
"""
        call_options = {
            "prompt": prompt,
            "use_basic_model": False,
            "max_completion_tokens": 2000,
        }
        if len(image_paths) == 1:
            raw = call_llm_with_image(image_path=image_paths[0], **call_options)
        else:
            raw = call_llm_with_images(image_paths=image_paths, **call_options)
        result = self._parse_llm_json(raw)
        # Aceita tambem o objeto unico usado nas respostas anteriores.
        results = result.get("analises", [result])
        if not isinstance(results, list) or not results or not all(isinstance(item, dict) for item in results):
            raise ValueError("analises deve conter objetos de parecer tecnico")
        analyses = [
            self._build_analysis(item, image_paths, part_id, wheel_type_norm, expected_peca)
            for item in results
        ]
        pecas = [item["peca"] for item in analyses]
        if len(pecas) != len(set(pecas)):
            raise ValueError("resposta contem analises duplicadas da mesma peca")
        return analyses

    def _build_analysis(
        self,
        result: dict[str, Any],
        image_paths: list[str],
        part_id: str | None,
        wheel_type_norm: str,
        expected_peca: str,
    ) -> dict[str, Any]:
        peca = str(result.get("peca") or expected_peca).strip().lower()
        acao = str(result.get("acao") or "reparo").strip().lower()
        nivel = str(result.get("nivel_dano") or "moderado").strip().lower()

        if peca == "pneu":
            acao = "troca"
        elif wheel_type_norm == "liga_leve":
            peca = "roda liga leve"
        elif wheel_type_norm == "ferro":
            peca = "calota" if "calota" in peca else "roda ferro"

        if peca == "calota":
            acao = "troca"

        billing_criteria = self._billing_criteria(result, nivel=nivel, peca=peca)
        if peca == "roda ferro" and billing_criteria["tipo_dano"] == "amassado":
            acao = "reparo"

        if nivel == "sem_dano":
            services: list[LpuItem] = []
        else:
            services = self._select_service(peca, acao, part_id)

        services_out = [ServiceItem(descricao=s.descricao, preco=s.preco) for s in services]
        total = self._calculate_total(services)
        return {
            **ExpertConsolidatedOutput(
                nivel_dano=nivel,
                peca=peca,
                servicos=services_out,
                preco_total=total,
                justificativa=str(result.get("justificativa") or "").strip() or None,
                fotos_analisadas=image_paths,
            ).model_dump(),
            "part_id": part_id,
            "acao": acao,
            **billing_criteria,
        }

    @staticmethod
    def _merge_calota_items(items: list[dict[str, Any]]) -> dict[str, Any]:
        """Consolida várias calotas avariadas em uma única cobrança de jogo."""
        first = dict(items[0])
        part_ids: list[str] = []
        photos: list[str] = []
        justifications: list[str] = []
        nivel = "sem_dano"
        rank = {"sem_dano": 0, "leve": 1, "moderado": 2, "grave": 3}

        for item in items:
            pid = str(item.get("part_id") or "").strip()
            if pid and pid not in part_ids:
                part_ids.append(pid)
            for photo in item.get("fotos_analisadas") or []:
                if isinstance(photo, str) and photo not in photos:
                    photos.append(photo)
            justification = str(item.get("justificativa") or "").strip()
            if justification:
                justifications.append(f"{pid or 'roda'}: {justification}")
            item_level = str(item.get("nivel_dano") or "sem_dano").strip().lower()
            if rank.get(item_level, 0) > rank.get(nivel, 0):
                nivel = item_level

        first["nivel_dano"] = nivel
        first["part_id"] = part_ids[0] if len(part_ids) == 1 else None
        first["part_ids"] = part_ids
        first["fotos_analisadas"] = photos
        first["justificativa"] = "; ".join(justifications) or first.get("justificativa")
        return first

    def run(self, image_paths: list[str], **kwargs) -> dict[str, Any]:
        if not image_paths:
            return {"erro": "nenhuma imagem fornecida ao perito de pneus e rodas"}

        checklist_summary = kwargs.get("checklist_summary") or "Nenhuma observação no checklist."
        wheel_type = kwargs.get("wheel_type") or "desconhecido"
        imagens_usadas = kwargs.get("imagens_usadas")

        metadata = imagens_usadas if isinstance(imagens_usadas, list) else []
        analyses: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}

        for index, image_path in enumerate(image_paths):
            meta = metadata[index] if index < len(metadata) and isinstance(metadata[index], dict) else {}
            part_id = str(meta.get("part_id") or "").strip().lower() or None

            # Sem part_id, não há evidência de que duas fotos sejam da mesma roda.
            key = part_id or f"sem_part_id_{index}"
            groups.setdefault(key, []).append((image_path, meta))

        for group in groups.values():
            part_id = str(group[0][1].get("part_id") or "").strip().lower() or None
            marked = any(meta.get("checklist_damage_reported") is True for _, meta in group)
            limit = MAX_FOTOS_POR_RODA_COM_AVARIA if marked and part_id else 1
            wheel_photos = list(dict.fromkeys(path for path, _ in group))[:limit]

            try:
                analyses.extend(
                    self._analyze_wheel(
                        image_paths=wheel_photos,
                        part_id=part_id,
                        checklist_summary=checklist_summary,
                        wheel_type=wheel_type,
                    )
                )
            except Exception as exc:
                errors.append({
                    "part_id": part_id or "", "foto": wheel_photos[0],
                    "fotos": wheel_photos, "erro": str(exc),
                })

        if not analyses:
            detail = errors[0]["erro"] if errors else "nenhuma foto distinta por part_id"
            return {"erro": f"falha rodas: {detail}", "erros_analise": errors}

        charged_items = [deepcopy(item) for item in analyses if item.get("nivel_dano") != "sem_dano"]
        if not charged_items:
            result = ExpertConsolidatedOutput(
                nivel_dano="sem_dano",
                peca="pneus_rodas",
                servicos=[],
                preco_total=0.0,
                justificativa="Sem danos identificados nas rodas, pneus e calotas analisados.",
                fotos_analisadas=list(dict.fromkeys(p for item in analyses for p in item.get("fotos_analisadas", []))),
            ).model_dump()
            result["analises"] = analyses
            if errors:
                result["erros_analise"] = errors
            return result

        calota_items = [item for item in charged_items if item.get("peca") == "calota"]
        wheel_items = [item for item in charged_items if item.get("peca") != "calota"]
        consolidated_items = wheel_items + ([self._merge_calota_items(calota_items)] if calota_items else [])

        services_flat: list[dict[str, Any]] = []
        total_numeric = 0.0
        any_non_numeric = False
        for item in consolidated_items:
            services_flat.extend(item.get("servicos") or [])
            price = item.get("preco_total")
            if isinstance(price, (int, float)):
                total_numeric += float(price)
            elif str(price).strip().lower() == "sob consulta":
                any_non_numeric = True

        nivel_final = max(
            (str(item.get("nivel_dano") or "sem_dano") for item in consolidated_items),
            key=self._severity_rank,
        )
        result: dict[str, Any] = {
            "nivel_dano": nivel_final,
            "peca": "pneus_rodas",
            "itens": consolidated_items,
            "analises": analyses,
            "servicos": services_flat,
            "preco_total": "Sob consulta" if any_non_numeric else round(total_numeric, 2),
            "justificativa": "; ".join(
                f"{item.get('part_id') or ', '.join(item.get('part_ids') or [])}: {item.get('justificativa')}"
                for item in consolidated_items
                if item.get("justificativa")
            ),
            "fotos_analisadas": list(dict.fromkeys(p for item in analyses for p in item.get("fotos_analisadas", []))),
        }
        if errors:
            result["erros_analise"] = errors
        return result

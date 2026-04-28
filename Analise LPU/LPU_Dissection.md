# Análise e Dissecação da LPU (Lista de Preços Unitários)

Este documento contém a análise detalhada da LPU atual e uma proposta de estruturação para otimizar o trabalho dos Agentes Peritos.

## 1. Categorias Identificadas na LPU Atual

A LPU atual engloba uma vasta gama de serviços que podem ser agrupados nas seguintes macro-categorias:

### A. Reposição de Itens e Acessórios
- Bateria, Chaves (Principal/Reserva), Macaco, Triângulo, Acendedor, Tapetes, Manual, Antena, Placas.

### B. Funilaria Pesada (Com Solda)
- Substituição de peças fixas: Teto, Laterais, Colunas, Caixa de ar.

### C. Recuperação e Pintura (Grandes Peças)
- Capô, Teto, Portas, Tampa do porta-malas, Laterais.

### D. Funilaria de Para-choque
- Recuperação, Pintura (incluindo troca), Reparo de peça plástica, Mão de obra de troca, Reparo de molduras.

### E. Funilaria em Peças Pequenas
- Para-lamas, Caixa de ar (sem solda).

### F. Estética e Limpeza
- Polimento (Utilitários/Pequenos), Higienização (Interna/Severa), Lavagem de motor, Retirada de adesivos.

### G. Vidros (Mão de Obra e Reparo)
- Para-brisa, Vidros laterais, Vidro traseiro, Teto solar.

### H. Rodas e Calotas
- Troca de Roda de Ferro (Aros 13-15), Reparo de Roda de Ferro, Reparo de Roda de Liga Leve, Troca de Calotas.

### I. Interior
- Reparos em bancos (rasgados/furados), Forro de teto.

### J. Martelinho de Ouro
- Pequenos amassados em diversas peças (Capô, Teto, Portas, Para-lamas, Colunas, Laterais).

---

## 2. Proposta de Divisão por "Agente Perito"

Para que o sistema de agentes funcione de forma modular, a LPU deve ser filtrada para cada perito. Abaixo, a sugestão de mapeamento:

| Perito | Peças / Serviços Abrangidos | Palavras-chave para Filtro na LPU |
| :--- | :--- | :--- |
| **Perito Para-choque** | Para-choque Dianteiro, Para-choque Traseiro, Molduras de Para-choque | "para-choque", "moldura plástica do para-choque", "pintura para-choque" |
| **Perito Lataria (Pintura/Recup)** | Portas, Capô, Teto, Laterais, Para-lamas, Caixa de Ar, Colunas | "pintura porta", "recuperação e pintura", "martelinho", "lateral" |
| **Perito Vidros** | Para-brisa, Vidros Laterais, Vidro Traseiro, Teto Solar | "vidro", "para-brisa" |
| **Perito Pneus/Rodas** | Rodas de Ferro, Rodas de Liga Leve, Calotas | "roda", "calota", "aro" |
| **Perito Interior** | Bancos, Tapetes, Forro de Teto, Higienização | "banco", "higienização", "tapete", "forro teto" |
| **Perito Acessórios/Itens** | Chaves, Manual, Antena, Placa, Bateria, Macaco | "chave", "manual", "antena", "placa", "bateria" |

---

## 3. Dissecação Específica: Perito Para-choque

O usuário solicitou que os serviços relacionados ao para-choque sejam claramente identificados. Na estrutura da LPU corrigida, todos os itens abaixo pertencem ao **Perito Para-choque**:

1. **Pintura**:
   - `Pintura para-choque dianteiro` (R$ 320,00)
   - `Pintura para-choque traseiro` (R$ 320,00)
   - `Pintura para-choque (troca de peça)` (R$ 320,00)
2. **Reparo Plástico**:
   - `Reparo peça plástica do para-choque dianteiro` (R$ 270,00)
   - `Reparo peça plástica do para-choque traseiro` (R$ 270,00)
3. **Recuperação**:
   - `Recuperação e pintura para-choque dianteiro` (R$ 400,00)
   - `Recuperação e pintura para-choque traseiro` (R$ 400,00)
4. **Mão de Obra de Troca**:
   - `Mão de obra troca do para-choque dianteiro` (R$ 140,00)
   - `Mão de obra troca do para-choque traseiro` (R$ 140,00)
5. **Molduras**:
   - `Reparo moldura plástica do para-choque dianteiro esquerdo/direito` (R$ 220,00)
   - `Reparo moldura plástica do para-choque traseiro esquerdo/direito` (R$ 220,00)

---

## 4. Próximos Passos Sugeridos

1. **Enriquecimento da Origem**: Adicionar uma coluna real na planilha `LPU.xlsx` chamada `PERITO_RESPONSAVEL`.
2. **Mapeamento Automático**: Atualizar o script `lpu.py` para carregar essa nova coluna, permitindo que cada agente filtre apenas o que lhe compete.
3. **Tratativa de Sinergia**: Definir regras de quando um perito deve chamar outro (ex: dano no para-choque que afetou a lateral).

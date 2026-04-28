import pandas as pd
import os
import unicodedata
import re

def _normalize(text):
    if pd.isna(text): return ""
    text = str(text).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"[^\w\s-]", " ", text)
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text

def classificar_perito(descricao):
    desc = _normalize(descricao)
    
    if any(k in desc for k in ["para choque", "parachoque", "moldura", "bumper"]):
        return "parachoque"
    if any(k in desc for k in ["vidro", "para brisa", "parabrisa", "retrovisor"]):
        # Retrovisor às vezes é lataria, às vezes vidro. No orquestrador está em lataria.
        # Mas 'vidro' é forte.
        if "retrovisor" in desc: return "lataria"
        return "vidros"
    if any(k in desc for k in ["roda", "calota", "aro", "pneu"]):
        return "pneus_rodas"
    if any(k in desc for k in ["banco", "tapete", "higienizacao", "forro teto", "interior"]):
        return "interior"
    if any(k in desc for k in ["chave", "manual", "antena", "placa", "bateria", "acendedor", "triangulo", "macaco"]):
        return "acessorios"
    if any(k in desc for k in ["pintura", "recuperacao", "martelinho", "capo", "teto", "porta", "lateral", "para lama", "paralama", "caixa de ar", "coluna"]):
        return "lataria"
    
    return "outros"

def main():
    path = r"c:\Users\144796\OneDrive - Localiza\Documentos\Projetos VSCode\Agente avarias de devolucao v2\AGENTE_AVARIAS_DEVOLUCAO\LPU.xlsx"
    print(f"Lendo LPU em: {path}")
    df = pd.read_excel(path)
    
    desc_col = "DESCRIÇÃO PARA CARTA DE AVARIAS"
    if desc_col not in df.columns:
        # Tenta achar a coluna de descrição
        for col in df.columns:
            if "DESCR" in col.upper():
                desc_col = col
                break
                
    print(f"Usando coluna de descrição: {desc_col}")
    df['perito'] = df[desc_col].apply(classificar_perito)
    
    print("Contagem por perito:")
    print(df['perito'].value_counts())
    
    df.to_excel(path, index=False)
    print("LPU atualizada com sucesso!")

if __name__ == "__main__":
    main()

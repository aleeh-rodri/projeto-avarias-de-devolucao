from google.cloud import bigquery

client = bigquery.Client(project="ana-prd-dados-hub")

def normalize_wheel_type(valor: str) -> str:
    v = (valor or "").lower()

    if "liga leve" in v or "liga" in v:
        return "liga_leve"

    if "aco" in v or "aço" in v:
        return "ferro"

    return "desconhecido"

def get_vehicle_wheel_type(placa: str) -> str:
    query = f"""
    SELECT
      attr.valor_atributo
    FROM `lclz-dados.corporativo_master_data.veiculo`,
    UNNEST(atributos_modelo) AS attr
    WHERE attr.nome_atributo = 'WHEEL'
      AND placa = @placa
    LIMIT 1
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("placa", "STRING", placa)
        ]
    )

    rows = client.query(query, job_config=job_config).result()

    for row in rows:
        return normalize_wheel_type(row.valor_atributo)

    return "desconhecido"
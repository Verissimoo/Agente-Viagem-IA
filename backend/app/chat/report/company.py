"""Constantes da empresa exibidas no PDF (footer, watermark, header).

Edite aqui se mudarem CNPJ, endereço ou redes — não fica espalhado no template.
"""

COMPANY = {
    "name": "PassagensComDesconto",
    "legal_name": "PassagensComDesconto",
    "cadastur": "CADASTUR",
    "cnpj": "62.830.477/0001-51",
    "website": "passagenscomdesconto.com.br",
    "instagram": "@passagenscomdesconto",
    "support_24h": True,
}

# Quote nº prefixo (ex: PCD-80514). Hash curto do quote.id pra ficar único.
QUOTE_PREFIX = "PCD-"

# Validade default da cotação em horas (alinhado ao texto "fechamento em 24h")
QUOTE_VALIDITY_HOURS = 24

# Bullets fixos de "O que está incluso" — alguns são condicionais (ver função em pdf)
INCLUDED_ITEMS_BASE = [
    "Passagem aérea {trip_type} — {airline}",
    "Todas as taxas aeroportuárias inclusas",
    "Assessoria completa durante todo o trajeto",
    "Atendimento personalizado 24 horas",
]

# Disclaimers padrão
DISCLAIMERS = [
    "Valores sujeitos a alteração até a efetivação da compra. "
    "Recomendamos o fechamento em até 24h para garantir o preço cotado.",
    "Cancelamentos e remarcações sujeitos às regras da {airline}, conforme tarifa {fare_type}.",
    "Pagamentos via cartão de crédito podem estar sujeitos a taxas adicionais. "
    "Consulte as opções de parcelamento disponíveis.",
]


def quote_number(quote_id: str) -> str:
    """PCD-XXXX a partir do hash do quote_id (4 chars hex maiúsculos).

    4 chars = 65k combinações, suficiente pro display. Espaço apertado
    no header do PDF — manter curto evita quebra de linha.
    """
    # Pega só hex digits do quote_id pra não trazer hifens/letras estranhas
    cleaned = "".join(c for c in (quote_id or "0000") if c.isalnum()).upper()
    short = cleaned[:4] or "0000"
    return f"{QUOTE_PREFIX}{short}"

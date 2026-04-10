import json
from miles_app.buscamilhas_offer_parser import extract_rows_from_buscamilhas

raw = {
  "Status": { "Erro": False, "Sucesso": True, "Alerta": [] },
  "Trechos": {
    "BSBGRU": {
      "Origem": "BSB",
      "Destino": "GRU",
      "Data": "30/04/2026",
      "Voos": [
        {
          "Companhia": "LATAM",
          "Sentido": "ida",
          "Origem": "BSB",
          "Destino": "GRU",
          "Embarque": "30/04/2026 06:00",
          "Desembarque": "30/04/2026 07:30",
          "Duracao": "01:30",
          "NumeroVoo": "LA3020",
          "NumeroConexoes": 0,
          "Conexoes": [],
          "Milhas": [
            {
              "Adulto": 7000,
              "TotalAdulto": 7000,
              "TaxaEmbarque": 59.90,
              "TipoMilhas": "LIGHT ECONOMY",
              "LimiteBagagem": 0
            },
            {
              "Adulto": 8500,
              "TotalAdulto": 8500,
              "TaxaEmbarque": 59.90,
              "TipoMilhas": "PLUS ECONOMY",
              "LimiteBagagem": 1
            }
          ]
        }
      ]
    }
  }
}

rows = extract_rows_from_buscamilhas(raw, "LATAM", "OW")
print(json.dumps(rows, indent=2, ensure_ascii=False))

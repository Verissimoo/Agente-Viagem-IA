# Relatório de Bug — Companhia AMERICAN (API BuscaMilhas)

**Data/Hora do teste:** 2026-04-20 16:51  
**Endpoint:** `http://apiv2.buscamilhas.com`  
**Rota testada:** GRU → MIA  
**Data de ida:** 19/06/2026 (2026-06-19)  

---

## Problema Observado

Ao realizar uma busca de milhas para a companhia **AMERICAN Airlines**, a API retorna um erro HTTP **500** com a mensagem:

```
{
  "statusCode": 500,
  "error": "Internal Server Error",
  "message": "searchBody is not defined"
}
```

O erro **`searchBody is not defined`** sugere que o handler interno da companhia AMERICAN no servidor BuscaMilhas não está reconhecendo o corpo da requisição.

---

## Requisição Enviada

**Método:** `POST`  
**URL:** `http://apiv2.buscamilhas.com`  
**Headers:**
```json
{
  "Content-Type": "application/json",
  "Accept": "application/json"
}
```

**Body (payload completo):**
```json
{
  "Companhias": [
    "AMERICAN"
  ],
  "TipoViagem": 0,
  "Trechos": [
    {
      "Origem": "GRU",
      "Destino": "MIA",
      "DataIda": "19/06/2026"
    }
  ],
  "Classe": "economica",
  "Adultos": 1,
  "Criancas": 0,
  "Bebes": 0,
  "Chave": "5bb896aa...",
  "Senha": "****",
  "SomenteMilhas": true,
  "SomentePagante": false,
  "Internacional": 1
}
```

> Nota: `Chave` exibida truncada por segurança. Credenciais válidas foram utilizadas no teste.

---

## Resposta Recebida

**HTTP Status Code:** `500`  
**Tempo de resposta:** `0.71s`  

**Body da resposta:**
```json
{
  "statusCode": 500,
  "error": "Internal Server Error",
  "message": "searchBody is not defined"
}
```

---

## Comparação com Companhia que Funciona (TAP)

Para confirmar que o problema é específico da AMERICAN, a mesma estrutura de payload enviada para **TAP** retorna sucesso:

```json
{
  "Status": {
    "Erro": false,
    "Sucesso": true,
    "Alerta": []
  },
  "Trechos": {
    "GRULIS": {
      "Voos": [ ... ]
    }
  }
}
```

---

## Outras Rotas Testadas com AMERICAN

| Origem | Destino | Data | HTTP | Erro |
|--------|---------|------|------|------|
| GRU | MIA | 20/05/2026 | 500 | searchBody is not defined |
| GRU | MIA | 19/06/2026 | 500 | searchBody is not defined |
| GRU | JFK | 19/06/2026 | 500 | searchBody is not defined |
| GRU | DFW | 19/06/2026 | 500 | searchBody is not defined |

O erro ocorre em **todas as rotas e datas** testadas para a companhia AMERICAN.

---

## Informações do Ambiente

- **Linguagem:** Python 3.12  
- **Biblioteca HTTP:** `requests` (POST com `application/json`)  
- **Sistema:** Windows 10/11  
- **Data do teste:** 2026-04-20 16:51  

---

## Solicitação ao Suporte

Por favor, verificar:

1. Se o handler de busca para a companhia **AMERICAN** no endpoint `http://apiv2.buscamilhas.com` está operacional
2. Por que a variável interna `searchBody` não está sendo definida ao receber requisições para AMERICAN
3. Se há algum campo adicional necessário no payload especificamente para a AMERICAN (não documentado)
4. Se o problema é do ambiente de produção ou do ambiente de desenvolvimento/homologação

**Chave de acesso:** `5bb896aa...` (disponibilizada completa mediante solicitação segura)

---

*Relatório gerado automaticamente pelo sistema Agente de Cotação PcD*

"""Provider AwardTool — award multi-programa via API interna (conta Pro própria).

A coleta é feita dirigindo o app deles com Playwright (login Cognito por
formulário + disparo do crawl em tempo real), interceptando as respostas de
`/search_result_v2` e DECODIFICANDO o campo `ciphered_data_v3` (ofuscação
keyless: shift de char -1 → reverse → swap a↔b/y↔h → base64 → zlib).

Cobre todos os programas pedidos (Aeroplan, LifeMiles, Flying Blue, Finnair,
Iberia, Qatar, British, Alaska, Copa, …). Sem credencial (`AWARDTOOL_ENABLED=0`
ou e-mail/senha ausentes) o adapter retorna [] e não derruba o pipeline.

ATENÇÃO: o ToS do AwardTool proíbe automação — uso gentil + cache obrigatórios
(risco de ban da conta). Ver memory/awardtool-scraping.md.
"""
from backend.app.providers.awardtool.adapter import AwardToolAdapter
from backend.app.providers.awardtool.cipher import decode_v3

__all__ = ["AwardToolAdapter", "decode_v3"]

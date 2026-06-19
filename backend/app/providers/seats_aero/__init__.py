"""Provider seats.aero — agregador de award space (Aeroplan, Lifemiles,
Flying Blue no piloto; Avios/Qatar/Alaska/Finnair/Copa e mais ~20 programas
prontos em PROGRAM_DISPLAY, ligáveis via SEATS_AERO_SOURCES).

Integrado via Partner API oficial (base `https://seats.aero/partnerapi`),
autenticada por API KEY ESTÁTICA no header `Partner-Authorization`. O login
por magic-link do site é irrelevante para a API. Sem `SEATS_AERO_API_KEY` o
adapter retorna `[]` silenciosamente — não derruba o orchestrator.

Fluxo: /search (availability multi-programa, só milhas) → /trips/{id}
(horários + taxas). Taxa NÃO é normalizada (ausente p/ vários programas);
fica em `risk_notes`. Docs: https://developers.seats.aero/

Nota de licença: o plano Pro é licenciado para uso pessoal; uso comercial
(ferramenta B2B) exige aprovação escrita — solicitar via support@seats.aero.
"""
from backend.app.providers.seats_aero.adapter import SeatsAeroAdapter

__all__ = ["SeatsAeroAdapter"]

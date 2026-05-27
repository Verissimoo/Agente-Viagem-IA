"""Provider seats.aero — agregador de award space para Aeroplan, Lifemiles,
Avios (Iberia/British/Qatar/Aer Lingus), Smiles, LATAM Pass, Azul Fidelidade,
Copa ConnectMiles e mais 20+ programas.

Esqueleto preparado para a integração quando a API key for fornecida.
Sem chave (env `SEATS_AERO_API_KEY` ausente), o adapter retorna `[]`
silenciosamente — não derruba o orchestrator.

Plano comercial necessário: **Pro+ ($24.99/mês)** que dá acesso à API REST.
Onboarding: https://seats.aero/pro
Docs API: https://seats.aero/api/docs (acessível após login Pro+)
"""
from backend.app.providers.seats_aero.adapter import SeatsAeroAdapter

__all__ = ["SeatsAeroAdapter"]

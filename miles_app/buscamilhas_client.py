from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(key)
    if v is None or str(v).strip() == "":
        return default
    return v


def _sleep_rps(rps: float):
    if rps and rps > 0:
        time.sleep(1.0 / float(rps))


# ------------------------------------------------------------------
# Constantes
# ------------------------------------------------------------------
BUSCAMILHAS_ENDPOINT = "http://apiv2.buscamilhas.com"

TIPO_VIAGEM_OW = 0   # somente ida
TIPO_VIAGEM_RT = 1   # ida e volta

COMPANHIAS_NACIONAIS = ["LATAM", "GOL", "AZUL"]
COMPANHIAS_INTERNACIONAIS = ["TAP", "IBERIA", "AMERICAN AIRLINES", "INTERLINE"]
COMPANHIAS_TODAS = COMPANHIAS_NACIONAIS + COMPANHIAS_INTERNACIONAIS


# ------------------------------------------------------------------
# Client
# ------------------------------------------------------------------
@dataclass
class BuscaMilhasClient:
    chave: str
    senha: str
    endpoint: str = BUSCAMILHAS_ENDPOINT
    connect_timeout: int = 10
    read_timeout: int = 60
    max_attempts: int = 3
    rps: float = 0.0

    def __post_init__(self):
        self.endpoint = (self.endpoint or "").rstrip("/")
        if not self.chave:
            raise RuntimeError("BUSCAMILHAS_CHAVE não configurada no .env")
        if not self.senha:
            raise RuntimeError("BUSCAMILHAS_SENHA não configurada no .env")
        self._session = requests.Session()

    def search(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                if self.rps:
                    _sleep_rps(self.rps)

                r = self._session.post(
                    self.endpoint,
                    json=payload,
                    headers=headers,
                    timeout=(self.connect_timeout, self.read_timeout),
                )

                if r.status_code == 429:
                    raise RuntimeError(f"BuscaMilhas HTTP 429 (rate limit). Body: {r.text[:2000]}")

                if r.status_code >= 500:
                    raise RuntimeError(f"BuscaMilhas HTTP {r.status_code} (server). Body: {r.text[:2000]}")

                if r.status_code >= 400:
                    raise RuntimeError(f"BuscaMilhas HTTP {r.status_code}. Body: {r.text[:2000]}")

                return r.json() if r.content else {}

            except Exception as e:
                last_err = e
                time.sleep(min(1.5 * attempt, 6.0))

        raise RuntimeError(f"Falha BuscaMilhas após {self.max_attempts} tentativas: {last_err}")


# ------------------------------------------------------------------
# Build payload
# ------------------------------------------------------------------
def build_payload(
    companhia: str,
    origem: str,
    destino: str,
    data_ida: str,            # formato DD/MM/AAAA
    data_volta: Optional[str] = None,  # formato DD/MM/AAAA, None = somente ida
    adultos: int = 1,
    criancas: int = 0,
    bebes: int = 0,
    classe: str = "economica",
    somente_milhas: bool = True,
    somente_pagante: bool = False,
    internacional: bool = False,
    chave: str = "",
    senha: str = "",
) -> Dict[str, Any]:
    tipo_viagem = TIPO_VIAGEM_RT if data_volta else TIPO_VIAGEM_OW

    trecho: Dict[str, Any] = {
        "Origem": origem.upper(),
        "Destino": destino.upper(),
        "DataIda": data_ida,
    }
    if data_volta:
        trecho["DataVolta"] = data_volta

    return {
        "Companhias": [companhia.upper()],
        "TipoViagem": tipo_viagem,
        "Trechos": [trecho],
        "Classe": classe.lower(),
        "Adultos": int(adultos or 1),
        "Criancas": int(criancas or 0),
        "Bebes": int(bebes or 0),
        "Chave": chave,
        "Senha": senha,
        "SomenteMilhas": somente_milhas,
        "SomentePagante": somente_pagante,
        "Internacional": 1 if internacional else 0,
    }


# ------------------------------------------------------------------
# Função de alto nível — uma companhia por chamada (regra da API)
# ------------------------------------------------------------------
def search_flights_buscamilhas(
    companhia: str,
    origem: str,
    destino: str,
    data_ida: str,            # DD/MM/AAAA
    data_volta: Optional[str] = None,
    adultos: int = 1,
    criancas: int = 0,
    bebes: int = 0,
    classe: str = "economica",
    somente_milhas: bool = True,
    somente_pagante: bool = False,
    internacional: bool = False,
) -> Dict[str, Any]:
    """
    Realiza uma busca na API Busca Milhas para UMA companhia.
    Credenciais lidas do .env: BUSCAMILHAS_CHAVE e BUSCAMILHAS_SENHA.
    """
    load_dotenv(override=False)

    chave = _env("BUSCAMILHAS_CHAVE", "") or ""
    senha = _env("BUSCAMILHAS_SENHA", "") or ""
    endpoint = _env("BUSCAMILHAS_ENDPOINT", BUSCAMILHAS_ENDPOINT) or BUSCAMILHAS_ENDPOINT
    connect_timeout = int(_env("BUSCAMILHAS_CONNECT_TIMEOUT", "10") or "10")
    read_timeout = int(_env("BUSCAMILHAS_READ_TIMEOUT", "60") or "60")
    max_attempts = int(_env("BUSCAMILHAS_MAX_ATTEMPTS", "3") or "3")
    rps = float(_env("BUSCAMILHAS_RPS", "0") or "0")

    client = BuscaMilhasClient(
        chave=chave,
        senha=senha,
        endpoint=endpoint,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        max_attempts=max_attempts,
        rps=rps,
    )

    payload = build_payload(
        companhia=companhia,
        origem=origem,
        destino=destino,
        data_ida=data_ida,
        data_volta=data_volta,
        adultos=adultos,
        criancas=criancas,
        bebes=bebes,
        classe=classe,
        somente_milhas=somente_milhas,
        somente_pagante=somente_pagante,
        internacional=internacional,
        chave=chave,
        senha=senha,
    )

    return client.search(payload)





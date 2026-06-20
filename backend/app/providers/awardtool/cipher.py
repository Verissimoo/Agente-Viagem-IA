"""Decodifica o campo `ciphered_data_v3` das respostas do AwardTool.

A ofuscação é KEYLESS (não é criptografia — só obscurece os resultados pra
dificultar scraping). Reverso confirmado contra dados reais (2026-06-19):

    decode:  v3 → (char-1) → reverse → swap(a↔b, y↔h) → base64-decode → zlib-inflate → JSON

Os metadados (user_info, routes_check, …) usam `ciphered_data` = base64+zlib
puro; só os RESULTADOS de busca usam o `_v3` acima.
"""
from __future__ import annotations

import base64
import json
import zlib
from typing import Any

# swap é a sua própria inversa (a↔b, y↔h)
_SWAP = str.maketrans({"a": "b", "b": "a", "y": "h", "h": "y"})


def decode_v3(ciphered: str) -> Any:
    """`ciphered_data_v3` → objeto JSON. Levanta em entrada inválida."""
    shifted = "".join(chr(ord(c) - 1) for c in ciphered)
    reversed_ = shifted[::-1]
    swapped = reversed_.translate(_SWAP)
    raw = base64.b64decode(swapped + "=" * (-len(swapped) % 4))
    return json.loads(zlib.decompress(raw))


def encode_v3(obj: Any) -> str:
    """Inversa de `decode_v3` — usada só em testes (gera fixtures cifradas)."""
    raw = zlib.compress(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    b64 = base64.b64encode(raw).decode("ascii")
    swapped = b64.translate(_SWAP)          # swap (auto-inversa)
    reversed_ = swapped[::-1]               # reverse
    return "".join(chr(ord(c) + 1) for c in reversed_)  # char +1

"""Verificador de configuração — roda depois de colar as credenciais.

Uso:
    .venv\\Scripts\\python.exe -m backend.app.chat.verify_env

Testa:
1. DATABASE_URL conecta no Neon Postgres.
2. ANTHROPIC_API_KEY responde no Claude.
3. NEON_AUTH_* tem JWKS endpoint acessível (Stack Auth no ar).
"""
from __future__ import annotations

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _ok(label: str) -> None:
    print(f"  [OK]   {label}")


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f" - {detail}" if detail else ""))


def check_database() -> bool:
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        _fail("DATABASE_URL", "vazio no .env")
        return False
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='chat'"
            )
            count = cur.fetchone()[0]
        _ok(f"DATABASE_URL conecta no Neon - schema chat tem {count} tabelas")
        return True
    except Exception as e:
        _fail("DATABASE_URL", str(e))
        return False


def check_anthropic() -> bool:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        _fail("ANTHROPIC_API_KEY", "vazio no .env")
        return False
    if not key.startswith("sk-ant-"):
        _fail("ANTHROPIC_API_KEY", "formato incomum (esperado sk-ant-...)")
        return False
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            messages=[{"role": "user", "content": "diga 'ok'"}],
        )
        text = resp.content[0].text if resp.content else ""
        _ok(f"ANTHROPIC_API_KEY funciona - modelo respondeu '{text[:30]}'")
        return True
    except Exception as e:
        _fail("ANTHROPIC_API_KEY", str(e))
        return False


def check_neon_auth() -> bool:
    jwks_url = os.getenv("NEON_AUTH_JWKS_URL", "").strip()
    project_id = os.getenv("NEON_AUTH_PROJECT_ID", "").strip()

    if not jwks_url and not project_id:
        _ok("Neon Auth nao configurado - usando DevAuthProvider (ok pra dev)")
        return True

    if not jwks_url and project_id:
        jwks_url = (
            f"https://api.stack-auth.com/api/v1/projects/{project_id}"
            "/.well-known/jwks.json"
        )

    try:
        import json
        import urllib.request
        with urllib.request.urlopen(jwks_url, timeout=10) as resp:
            if resp.status != 200:
                _fail("Neon Auth", f"JWKS retornou {resp.status}")
                return False
            data = resp.read()
            payload = json.loads(data)
            n_keys = len(payload.get("keys", []))
        _ok(f"Neon Auth ativo - JWKS expoe {n_keys} chave(s) publica(s)")
        return True
    except Exception as e:
        _fail("Neon Auth", f"JWKS inacessivel: {e}")
        return False


def main() -> int:
    print("Verificacao de ambiente do chat product")
    print("-" * 50)
    results = [
        check_database(),
        check_anthropic(),
        check_neon_auth(),
    ]
    print("-" * 50)
    ok = sum(results)
    print(f"{ok}/{len(results)} checks passaram")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

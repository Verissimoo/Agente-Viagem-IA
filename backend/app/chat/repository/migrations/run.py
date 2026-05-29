"""Aplicador idempotente de migrations.

Uso:
    python -m backend.app.chat.repository.migrations.run

Lê `DATABASE_URL` do ambiente (carrega .env se python-dotenv estiver disponível)
e aplica os arquivos .sql em ordem alfabética. Cada arquivo é executado
inteiro dentro de uma transação. As migrations já são idempotentes
(IF NOT EXISTS), então rodar várias vezes é seguro.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def main() -> int:
    try:
        import psycopg
    except ImportError:
        print("psycopg não instalado. Rode: pip install 'psycopg[binary,pool]'", file=sys.stderr)
        return 2

    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL ausente — configure no .env antes de rodar.", file=sys.stderr)
        return 2

    migrations_dir = Path(__file__).parent
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        print("Nenhuma migration encontrada.", file=sys.stderr)
        return 1

    host = dsn.split('@')[-1].split('/')[0]
    print(f"Aplicando {len(files)} migration(s) em {host}")

    for path in files:
        sql = path.read_text(encoding="utf-8")
        print(f"  - {path.name}")
        try:
            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
        except Exception as e:
            print(f"    FALHOU: {e}", file=sys.stderr)
            return 1

    print("OK — migrations aplicadas com sucesso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

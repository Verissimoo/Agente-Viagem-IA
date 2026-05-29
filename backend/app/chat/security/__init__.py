"""Segurança do produto chat.

Camadas independentes que rodam em pontos específicos do pipeline:

- `input_filter`: sanitiza mensagem do usuário ANTES de entrar no LLM
  (tamanho, controle chars, escapes que confundem o tokenizer).
- `jailbreak`: detecta padrões de prompt injection. Não é defesa única —
  use sempre em conjunto com instruções fortes no system prompt.
- `output_filter`: sanitiza resposta do LLM ANTES de entregar ao usuário,
  removendo nomes de providers e jargão técnico que vazariam o "como" da
  cotação. O vendedor não precisa saber de Skiplagged/Kayak/Buscamilhas etc.
- `rate_limit`: limites por usuário (mensagens/min, buscas/h).
- `audit`: trilha pra investigação. Toda decisão de defesa loga aqui.
"""
from backend.app.chat.security.audit import AuditLogger, get_audit_logger
from backend.app.chat.security.input_filter import (
    InputViolation,
    sanitize_user_message,
)
from backend.app.chat.security.jailbreak import JailbreakResult, detect_jailbreak
from backend.app.chat.security.output_filter import sanitize_assistant_output
from backend.app.chat.security.rate_limit import RateLimiter, get_rate_limiter

__all__ = [
    "AuditLogger",
    "InputViolation",
    "JailbreakResult",
    "RateLimiter",
    "detect_jailbreak",
    "get_audit_logger",
    "get_rate_limiter",
    "sanitize_assistant_output",
    "sanitize_user_message",
]

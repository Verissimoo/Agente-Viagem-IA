"""Envio de e-mail transacional — SMTP por env, com degradação graciosa.

Se as variáveis SMTP não estiverem configuradas (dev local), NÃO falha: apenas
loga o link de reset (útil pra testar sem servidor de e-mail). Em produção,
configure `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/`SMTP_FROM`.

Nunca levanta exceção pro chamador — falha de e-mail não pode derrubar o
endpoint de reset (o usuário não deve saber se o e-mail existe, e um SMTP fora
do ar não pode virar 500).
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _smtp_config() -> dict:
    return {
        "host": os.getenv("SMTP_HOST", "").strip(),
        "port": int(os.getenv("SMTP_PORT", "587") or "587"),
        "user": os.getenv("SMTP_USER", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", "").strip(),
        "from_addr": (os.getenv("SMTP_FROM", "") or os.getenv("SMTP_USER", "")).strip(),
        "use_tls": os.getenv("SMTP_USE_TLS", "1") not in ("0", "false", "False", ""),
    }


def smtp_enabled() -> bool:
    cfg = _smtp_config()
    return bool(cfg["host"] and cfg["from_addr"])


def _send(to_email: str, subject: str, text_body: str, html_body: str) -> bool:
    """Envia via SMTP. Retorna True se enviou, False se caiu no modo log/erro."""
    cfg = _smtp_config()
    if not smtp_enabled():
        logger.info("SMTP não configurado — e-mail NÃO enviado. Assunto=%r para=%s",
                    subject, to_email)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = to_email
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    try:
        if cfg["port"] == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=ctx, timeout=15) as s:
                if cfg["user"]:
                    s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as s:
                if cfg["use_tls"]:
                    s.starttls(context=ssl.create_default_context())
                if cfg["user"]:
                    s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        logger.info("E-mail enviado para %s (assunto=%r)", to_email, subject)
        return True
    except Exception as e:
        logger.warning("Falha ao enviar e-mail para %s: %s", to_email, e)
        return False


_COMPANY = "Passagens com Desconto"


def send_password_reset_email(to_email: str, reset_url: str) -> bool:
    """Manda o link de redefinição de senha. Sempre loga o link (rastreio); se
    SMTP estiver off, o link no log é a forma de testar em dev."""
    logger.info("Link de reset para %s: %s", to_email, reset_url)
    subject = f"{_COMPANY} — redefinição de senha"
    text_body = (
        f"Você (ou alguém) pediu para redefinir a senha da sua conta {_COMPANY}.\n\n"
        f"Abra o link abaixo para criar uma nova senha (expira em 1 hora):\n"
        f"{reset_url}\n\n"
        f"Se não foi você, ignore este e-mail — sua senha continua a mesma."
    )
    html_body = f"""\
<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto">
  <h2 style="color:#1a73e8">Redefinição de senha</h2>
  <p>Você (ou alguém) pediu para redefinir a senha da sua conta
     <strong>{_COMPANY}</strong>.</p>
  <p>
    <a href="{reset_url}"
       style="display:inline-block;padding:12px 20px;background:#1a73e8;color:#fff;
              text-decoration:none;border-radius:6px">Criar nova senha</a>
  </p>
  <p style="color:#666;font-size:13px">O link expira em 1 hora. Se não foi você,
     ignore este e-mail — sua senha continua a mesma.</p>
</div>"""
    return _send(to_email, subject, text_body, html_body)

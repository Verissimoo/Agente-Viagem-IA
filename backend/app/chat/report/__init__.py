"""Gerador de relatório PDF da cotação aprovada.

API pública:
    from backend.app.chat.report import generate_quote_pdf
    pdf_bytes = generate_quote_pdf(quote, user, offer)
"""
from backend.app.chat.report.generator import generate_quote_pdf

__all__ = ["generate_quote_pdf"]

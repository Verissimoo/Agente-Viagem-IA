import sys
import os
from datetime import date

# Adicionar o diretório atual ao path para importar os módulos
sys.path.append(os.getcwd())

from miles_app.iata_resolver import resolve_city_to_iatas, normalize_city_key
from pcd.nlp.intent_parser import parse_intent_regex, clean_text_ptbr

def test_cleaning():
    print("--- Testando clean_text_ptbr ---")
    tests = [
        "Quero uma passagem de Brasília para Lisboa",
        "Gostaria de um voo de SP para Miami",
        "Preciso de uma cotação de BSB para LIS",
        "Uma passagem de Natal para Londres",
        "Brasília para Lisboa"
    ]
    for t in tests:
        res = clean_text_ptbr(t)
        print(f"Original: {t}")
        print(f"Cleaned : {res}\n")

def test_iata():
    print("--- Testando IATA Resolver ---")
    tests = [
        "Brasília", "São Paulo", "Natal", "Londres", "Bogotá", "Medellín", "Toquio", "Tokyo", "NYC", "GRU"
    ]
    for t in tests:
        res = resolve_city_to_iatas(t)
        norm = normalize_city_key(t)
        print(f"Input: {t:12} | Key: {norm:15} | Res: {res}")

def test_parser():
    print("\n--- Testando intent_parser_regex (Refinado) ---")
    tests = [
        "Quero uma passagem de Brasília para Lisboa dia 20/06/2026",
        "Brasília para Lisboa 20/06/2026",
        "Quero um voo de BSB para LIS dia 20/06/2026",
        "Gostaria de um voo de SP para Miami com 3 dias de flexibilidade",
        "Bogotá para Medellín com volta flexivel",
        "Sao Paulo -> Lisboa flex 3 dias"
    ]
    for t in tests:
        intent = parse_intent_regex(t)
        print(f"Texto: {t}")
        print(f"  Origem : {intent.origin_city} ({intent.origin_iata})")
        print(f"  Destino: {intent.destination_city} ({intent.destination_iata})")
        print(f"  Flex   : {intent.flex_days} | Volta Flex: {intent.flex_return}")
        print(f"  Confid.: {intent.confidence} | Notas: {intent.notes}")
        
    print("\n--- Verificação Anti-IATA Falso ---")
    bad_input = "Quero uma passagem de brasilia para lisboa"
    intent = parse_intent_regex(bad_input)
    if intent.origin_iata == "UMA":
        print("❌ FALHA: 'UMA' detectado como IATA.")
    else:
        print(f"✅ SUCESSO: IATA Origem: {intent.origin_iata}")

if __name__ == "__main__":
    test_cleaning()
    test_iata()
    test_parser()

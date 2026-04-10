import json
from miles_app.buscamilhas_offer_parser import extract_rows_from_buscamilhas
raw = json.load(open('debug_dumps/test_azul_both.json'))
rows = extract_rows_from_buscamilhas(raw, 'AZUL', 'OW')
print(f'Total rows: {len(rows)}')
print('Miles rows:', len([r for r in rows if r.get('IsMiles')]))
print('Money rows:', len([r for r in rows if not r.get('IsMiles')]))

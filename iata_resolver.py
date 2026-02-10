from __future__ import annotations

import re
import unicodedata

def _normalize(s: str) -> str:
    s = (s or "").strip().upper()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\s+", " ", s)
    return s

_RAW_CITY_TO_IATAS = {
    # Brasil (principais + variações)
    "BRASILIA": ["BSB"],
    "BSB": ["BSB"],
    "SAO": ["CGH", "GRU", "VCP"],
    "SAO PAULO": ["CGH", "GRU", "VCP"],
    "CONGONHAS": ["CGH"],
    "GUARULHOS": ["GRU"],
    "CAMPINAS": ["VCP"],
    "RIO": ["SDU", "GIG"],
    "RIO DE JANEIRO": ["SDU", "GIG"],
    "GALEAO": ["GIG"],
    "SANTOS DUMONT": ["SDU"],
    "BELO HORIZONTE": ["CNF", "PLU"],
    "CONFINS": ["CNF"],
    "PAMPULHA": ["PLU"],
    "VITORIA": ["VIX"],
    "CURITIBA": ["CWB"],
    "PORTO ALEGRE": ["POA"],
    "FLORIANOPOLIS": ["FLN"],
    "SALVADOR": ["SSA"],
    "RECIFE": ["REC"],
    "FORTALEZA": ["FOR"],
    "NATAL": ["NAT"],
    "MANAUS": ["MAO"],
    "BELEM": ["BEL"],
    "GOIANIA": ["GYN"],
    "CUIABA": ["CGB"],
    "CAMPO GRANDE": ["CGR"],
    "MACEIO": ["MCZ"],
    "ARACAJU": ["AJU"],
    "TERESINA": ["THE"],
    "SAO LUIS": ["SLZ"],
    "PORTO VELHO": ["PVH"],
    "RIO BRANCO": ["RBR"],
    "BOA VISTA": ["BVB"],
    "MACAPA": ["MCP"],
    "PALMAS": ["PMW"],
    "FOZ DO IGUACU": ["IGU"],
    "LONDRINA": ["LDB"],
    "MARINGA": ["MGF"],
    "NAVEGANTES": ["NVT"],
    "JOINVILLE": ["JOI"],
    "CHAPECO": ["XAP"],
    "CAXIAS DO SUL": ["CXJ"],
    "PORTO SEGURO": ["BPS"],
    "ILHEUS": ["IOS"],
    "VITORIA DA CONQUISTA": ["VDC"],
    "RIBEIRAO PRETO": ["RAO"],
    "UBERLANDIA": ["UDI"],

    # América do Sul (principais internacionais por país)
    "BUENOS AIRES": ["EZE", "AEP"],
    "EZEIZA": ["EZE"],
    "AEROPARQUE": ["AEP"],
    "CORDOBA": ["COR"],
    "MENDOZA": ["MDZ"],
    "SANTIAGO": ["SCL"],
    "LIMA": ["LIM"],
    "CUSCO": ["CUZ"],
    "BOGOTA": ["BOG"],
    "MEDELLIN": ["MDE"],
    "CALI": ["CLO"],
    "CARTAGENA": ["CTG"],
    "QUITO": ["UIO"],
    "GUAYAQUIL": ["GYE"],
    "ASUNCION": ["ASU"],
    "MONTEVIDEO": ["MVD"],
    "PUNTA DEL ESTE": ["PDP"],
    "LA PAZ": ["LPB"],
    "SANTA CRUZ": ["VVI"],
    "CARACAS": ["CCS"],
    "GEORGETOWN": ["GEO"],
    "PARAMARIBO": ["PBM"],
    "CAYENNE": ["CAY"],

    # Chile/Peru/Colombia/Ecuador/Argentina aliases comuns
    "SANTIAGO DE CHILE": ["SCL"],
    "BOGOTÁ": ["BOG"],
    "MEDELLÍN": ["MDE"],
    "GUAYAQUIL (EC)": ["GYE"],
    "LIMA (PE)": ["LIM"],

    # América Central (apenas aeroportos internacionais principais) + México
    "MEXICO CITY": ["MEX"],
    "CIUDAD DE MEXICO": ["MEX"],
    "CANCUN": ["CUN"],
    "GUADALAJARA": ["GDL"],
    "MONTERREY": ["MTY"],
    "TIJUANA": ["TIJ"],
    "PUERTO VALLARTA": ["PVR"],
    "LOS CABOS": ["SJD"],

    "GUATEMALA CITY": ["GUA"],
    "CIUDAD DE GUATEMALA": ["GUA"],
    "BELIZE CITY": ["BZE"],
    "SAN SALVADOR": ["SAL"],
    "TEGUCIGALPA": ["TGU"],
    "SAN PEDRO SULA": ["SAP"],
    "MANAGUA": ["MGA"],
    "SAN JOSE": ["SJO"],
    "SAN JOSE (CR)": ["SJO"],
    "LIBERIA (CR)": ["LIR"],
    "PANAMA CITY": ["PTY"],
    "CIUDAD DE PANAMA": ["PTY"],

    # Estados Unidos (cobertura “profissional” e bem ampla)
    "NEW YORK": ["JFK", "LGA", "EWR"],
    "NYC": ["JFK", "LGA", "EWR"],
    "JFK": ["JFK"],
    "LAGUARDIA": ["LGA"],
    "NEWARK": ["EWR"],

    "LOS ANGELES": ["LAX"],
    "LAX": ["LAX"],
    "SAN FRANCISCO": ["SFO", "OAK", "SJC"],
    "SFO": ["SFO"],
    "SAN JOSE (US)": ["SJC"],
    "OAKLAND": ["OAK"],

    "MIAMI": ["MIA", "FLL"],
    "FORT LAUDERDALE": ["FLL"],
    "ORLANDO": ["MCO"],
    "TAMPA": ["TPA"],
    "ATLANTA": ["ATL"],
    "CHICAGO": ["ORD", "MDW"],
    "O HARE": ["ORD"],
    "MIDWAY": ["MDW"],

    "WASHINGTON": ["IAD", "DCA", "BWI"],
    "WASHINGTON DC": ["IAD", "DCA", "BWI"],
    "DULLES": ["IAD"],
    "REAGAN": ["DCA"],
    "BALTIMORE": ["BWI"],

    "BOSTON": ["BOS"],
    "PHILADELPHIA": ["PHL"],
    "CHARLOTTE": ["CLT"],
    "DETROIT": ["DTW"],
    "MINNEAPOLIS": ["MSP"],
    "SEATTLE": ["SEA"],
    "DENVER": ["DEN"],
    "DALLAS": ["DFW", "DAL"],
    "FORT WORTH": ["DFW"],
    "HOUSTON": ["IAH", "HOU"],
    "PHOENIX": ["PHX"],
    "LAS VEGAS": ["LAS"],
    "SAN DIEGO": ["SAN"],
    "SALT LAKE CITY": ["SLC"],
    "PORTLAND (US)": ["PDX"],
    "AUSTIN": ["AUS"],
    "NASHVILLE": ["BNA"],
    "NEW ORLEANS": ["MSY"],
    "RALEIGH": ["RDU"],
    "CLEVELAND": ["CLE"],
    "PITTSBURGH": ["PIT"],
    "CINCINNATI": ["CVG"],
    "KANSAS CITY": ["MCI"],
    "ST LOUIS": ["STL"],
    "INDIANAPOLIS": ["IND"],
    "COLUMBUS (OH)": ["CMH"],
    "MILWAUKEE": ["MKE"],
    "SACRAMENTO": ["SMF"],
    "SAN ANTONIO": ["SAT"],
    "DALLAS LOVE FIELD": ["DAL"],
    "OAKLAND (US)": ["OAK"],

    "HONOLULU": ["HNL"],
    "ANCHORAGE": ["ANC"],

    "BOISE": ["BOI"],
    "SPOKANE": ["GEG"],
    "ALBUQUERQUE": ["ABQ"],
    "EL PASO": ["ELP"],
    "TUCSON": ["TUS"],
    "OKLAHOMA CITY": ["OKC"],
    "TULSA": ["TUL"],
    "MEMPHIS": ["MEM"],
    "BIRMINGHAM (US)": ["BHM"],
    "JACKSONVILLE": ["JAX"],
    "SAVANNAH": ["SAV"],
    "CHARLESTON (SC)": ["CHS"],
    "NORFOLK": ["ORF"],
    "RICHMOND": ["RIC"],
    "HARTFORD": ["BDL"],
    "BUFFALO": ["BUF"],
    "ROCHESTER (NY)": ["ROC"],
    "SYRACUSE": ["SYR"],
    "PROVIDENCE": ["PVD"],
    "NEW HAVEN": ["HVN"],

    "AUGUSTA (GA)": ["AGS"],
    "LOUISVILLE": ["SDF"],
    "LEXINGTON": ["LEX"],
    "OMAHA": ["OMA"],
    "DES MOINES": ["DSM"],
    "SIOUX FALLS": ["FSD"],
    "BISMARCK": ["BIS"],
    "FARGO": ["FAR"],
    "BILLINGS": ["BIL"],
    "MISSOULA": ["MSO"],

    "RENO": ["RNO"],
    "PALM SPRINGS": ["PSP"],
    "SANTA ANA": ["SNA"],
    "LONG BEACH": ["LGB"],
    "ONTARIO (CA)": ["ONT"],

    # Europa (principais por país)
    "LISBOA": ["LIS"],
    "LISBON": ["LIS"],
    "PORTO": ["OPO"],
    "MADRID": ["MAD"],
    "BARCELONA": ["BCN"],
    "VALENCIA": ["VLC"],
    "MALAGA": ["AGP"],
    "PARIS": ["CDG", "ORY"],
    "CHARLES DE GAULLE": ["CDG"],
    "ORLY": ["ORY"],
    "LYON": ["LYS"],
    "NICE": ["NCE"],
    "MARSEILLE": ["MRS"],

    "LONDON": ["LHR", "LGW", "STN", "LTN", "LCY"],
    "LONDRES": ["LHR", "LGW", "STN", "LTN", "LCY"],
    "HEATHROW": ["LHR"],
    "GATWICK": ["LGW"],
    "STANSTED": ["STN"],
    "LUTON": ["LTN"],
    "LONDON CITY": ["LCY"],
    "MANCHESTER": ["MAN"],
    "EDINBURGH": ["EDI"],
    "DUBLIN": ["DUB"],

    "AMSTERDAM": ["AMS"],
    "BRUSSELS": ["BRU"],
    "FRANKFURT": ["FRA"],
    "MUNICH": ["MUC"],
    "BERLIN": ["BER"],
    "HAMBURG": ["HAM"],
    "DUSSELDORF": ["DUS"],

    "ROME": ["FCO", "CIA"],
    "ROMA": ["FCO", "CIA"],
    "FIUMICINO": ["FCO"],
    "MILAN": ["MXP", "LIN", "BGY"],
    "MILAO": ["MXP", "LIN", "BGY"],
    "VENICE": ["VCE"],
    "VENZA": ["VCE"],
    "NAPLES": ["NAP"],

    "ZURICH": ["ZRH"],
    "GENEVA": ["GVA"],
    "VIENNA": ["VIE"],
    "PRAGUE": ["PRG"],
    "WARSAW": ["WAW"],
    "KRAKOW": ["KRK"],
    "BUDAPEST": ["BUD"],
    "ATHENS": ["ATH"],
    "THESSALONIKI": ["SKG"],
    "ISTANBUL": ["IST", "SAW"],
    "ANKARA": ["ESB"],
    "LISBON (PT)": ["LIS"],

    "COPENHAGEN": ["CPH"],
    "STOCKHOLM": ["ARN"],
    "OSLO": ["OSL"],
    "HELSINKI": ["HEL"],

    "BUCHAREST": ["OTP"],
    "SOFIA": ["SOF"],
    "ZAGREB": ["ZAG"],
    "BELGRADE": ["BEG"],
    "KIEV": ["KBP"],
    "KYIV": ["KBP"],

    # Oriente Médio (principais hubs)
    "DUBAI": ["DXB", "DWC"],
    "DXB": ["DXB"],
    "ABU DHABI": ["AUH"],
    "DOHA": ["DOH"],
    "RIYADH": ["RUH"],
    "JEDDAH": ["JED"],
    "DAMMAM": ["DMM"],
    "TEL AVIV": ["TLV"],
    "KUWAIT": ["KWI"],
    "BAHRAIN": ["BAH"],
    "MUSCAT": ["MCT"],
    "AMMAN": ["AMM"],
    "BEIRUT": ["BEY"],
    "CAIRO": ["CAI"],

    # Japão (somente Japão como você pediu)
    "TOKYO": ["HND", "NRT"],
    "TOQUIO": ["HND", "NRT"],
    "HANEDA": ["HND"],
    "NARITA": ["NRT"],
    "OSAKA": ["KIX", "ITM"],
    "KANSAI": ["KIX"],
    "ITAMI": ["ITM"],
    "NAGOYA": ["NGO"],
    "CENTRAIR": ["NGO"],
    "FUKUOKA": ["FUK"],
    "SAPPORO": ["CTS"],
    "NEW CHITOSE": ["CTS"],
    "OKINAWA": ["OKA"],
    "NAHA": ["OKA"],
    "SENDAI": ["SDJ"],
    "HIROSHIMA": ["HIJ"],
    "KAGOSHIMA": ["KOJ"],
    "NAGASAKI": ["NGS"],
    "KUMAMOTO": ["KMJ"],
    "TAKAMATSU": ["TAK"],
    "NIIGATA": ["KIJ"],
    "KOBE": ["UKB"],
}

CITY_TO_IATAS: dict[str, list[str]] = {}
for k, v in _RAW_CITY_TO_IATAS.items():
    key = _normalize(k)
    if key not in CITY_TO_IATAS:
        CITY_TO_IATAS[key] = []
    for code in v:
        c = _normalize(code)
        if re.fullmatch(r"[A-Z]{3}", c) and c not in CITY_TO_IATAS[key]:
            CITY_TO_IATAS[key].append(c)

def resolve_place_to_iatas(place: str) -> list[str]:
    place_n = _normalize(place)

    if re.fullmatch(r"[A-Z]{3}", place_n):
        return [place_n]

    if place_n in CITY_TO_IATAS:
        return CITY_TO_IATAS[place_n]

    for city, iatas in CITY_TO_IATAS.items():
        if city and city in place_n:
            return iatas

    raise ValueError(f"Não consegui mapear '{place}' para aeroportos. Adicione no CITY_TO_IATAS.")


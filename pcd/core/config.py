import os

class Config:
    # 1 para ativado, 0 para desativado
    PCD_OFFLINE = int(os.getenv("PCD_OFFLINE", "0"))
    
    # Customização de ranking
    COST_PER_MILE_BRL = float(os.getenv("COST_PER_MILE_BRL", "0.015"))
    CONNECTION_PENALTY_BRL = float(os.getenv("CONNECTION_PENALTY_BRL", "80.0"))

config = Config()

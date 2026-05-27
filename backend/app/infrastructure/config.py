"""Runtime configuration loaded from environment variables."""
import os


class Config:
    PCD_OFFLINE = int(os.getenv("PCD_OFFLINE", "0"))
    COST_PER_MILE_BRL = float(os.getenv("COST_PER_MILE_BRL", "0.015"))
    CONNECTION_PENALTY_BRL = float(os.getenv("CONNECTION_PENALTY_BRL", "80.0"))


config = Config()

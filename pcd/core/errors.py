class PcdError(Exception):
    """Classe base para erros do PCD"""
    pass

class OfflineModeError(PcdError):
    """Levantado quando uma chamada de rede é tentada em modo offline sem fixtures"""
    def __init__(self, source: str):
        self.source = source
        super().__init__(f"Modo Offline Ativado: Chamada de rede bloqueada para a fonte '{source}'. Use --use-fixtures ou desative PCD_OFFLINE.")

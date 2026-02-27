from abc import ABC, abstractmethod
from typing import List
from pcd.core.schema import SearchRequest, UnifiedOffer

class BaseSearchAdapter(ABC):
    """
    Interface base para os adapters de busca de voo.
    """

    @abstractmethod
    def search(self, request: SearchRequest, use_fixtures: bool = False) -> List[UnifiedOffer]:
        """
        Executa a busca e retorna uma lista de UnifiedOffer.
        Se use_fixtures=True, o adapter deve tentar usar dados cacheados 
        para evitar chamadas de rede reais (útil para testes).
        """
        pass

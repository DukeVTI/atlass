from abc import ABC, abstractmethod
from typing import Any

class Tool(ABC):
    """
    Abstract base class for all Atlas Layer 4 Tools.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """The tool's identifier name (e.g., 'send_email')"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """The tool's description for Claude"""
        pass

    @property
    @abstractmethod
    def schema(self) -> dict:
        """
        Claude-compatible JSON schema describing the inputs.
        """
        pass

    @property
    def is_destructive(self) -> bool:
        """
        Flag indicating if the tool modifies state externally (e.g., send email, delete file).
        Defaults to False. Destructive tools interact directly with the Confirmation Gate.
        """
        return False

    @abstractmethod
    async def run(self, **kwargs) -> Any:
        """
        Execute the tool. Must return a string natively handling any exceptions.
        """
        pass

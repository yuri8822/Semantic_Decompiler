"""
Common interface every provider file must implement.
"""

from abc import ABC, abstractmethod


class BaseProvider(ABC):
    @abstractmethod
    def complete(self, system: str, user: str, pass_num: int = 1) -> str:
        """Send a system + user prompt and return the model's text response."""
        raise NotImplementedError

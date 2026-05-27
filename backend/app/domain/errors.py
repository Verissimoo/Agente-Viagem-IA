"""Domain-level exceptions."""


class AppError(Exception):
    """Base class for application errors."""


class OfflineModeError(AppError):
    """Raised when a network call is attempted with `PCD_OFFLINE=1` and no fixture available."""

    def __init__(self, source: str):
        self.source = source
        super().__init__(
            f"Offline mode: network call blocked for source '{source}'. "
            "Use fixtures or unset PCD_OFFLINE."
        )

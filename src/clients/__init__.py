"""Desktop client helpers.

Avoid importing optional network dependencies when accessing the pure
release-notification policy.
"""

__all__ = ["SmartUpdateChecker"]


def __getattr__(name):
    if name == "SmartUpdateChecker":
        from src.clients.smart_update_client import SmartUpdateChecker
        return SmartUpdateChecker
    raise AttributeError(name)

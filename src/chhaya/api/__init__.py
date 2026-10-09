"""HTTP surface for Chhaya: one orchestrator call per endpoint, no logic."""

from chhaya.api.app import app_factory, create_app

__all__ = ["app_factory", "create_app"]

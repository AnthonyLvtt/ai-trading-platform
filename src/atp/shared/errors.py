from __future__ import annotations


class ATPError(Exception):
    """Base exception for technical/application failures, not business states."""


class ValidationError(ATPError):
    """Input or value validation failed."""


class DomainError(ATPError):
    """A domain contract was violated."""


class InfrastructureError(ATPError):
    """Infrastructure prevented a reliable operation."""


class ConfigurationError(ATPError):
    """Configuration is missing, invalid, or unsafe."""


class ReconciliationRequired(InfrastructureError):
    """The observed outcome is uncertain and requires reconciliation."""

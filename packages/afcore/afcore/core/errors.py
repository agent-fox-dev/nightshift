"""Exception hierarchy for agent-fox.

Defines a base AgentFoxError with optional structured context,
and specific subclasses for each error category in the system.

Requirements: 01-REQ-4.1, 01-REQ-4.2, 01-REQ-4.3
"""

from __future__ import annotations

from typing import Any


class AgentFoxError(Exception):
    """Base exception for all agent-fox errors."""

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.context = context


class ConfigError(AgentFoxError): ...


class PlanError(AgentFoxError): ...


class WorkspaceError(AgentFoxError): ...


class RefConflictError(WorkspaceError):
    """Git ref directory/file conflict (non-retryable).

    Raised when ``git branch`` fails because an existing ref is a
    filesystem path-prefix of the target ref (or vice versa).  For
    example, ref ``feature/spec/0`` (a file) prevents creation of
    ``feature/spec/0/reviewer/pre-flight`` (which requires ``0`` to be a
    directory).  Retrying is futile — the conflicting ref must be
    deleted first.

    Requirements: #745
    """


class IntegrationError(AgentFoxError):
    """Error during workspace integration (harvest/merge).

    Attributes:
        retryable: Whether the error is retryable. Defaults to True for
            backward compatibility. Set to False for workspace-state errors
            (e.g. divergent untracked files) that cannot be resolved by
            re-running the same session.

    Requirements: 118-REQ-3.1
    """

    def __init__(self, message: str, *, retryable: bool = True, **context: Any) -> None:
        super().__init__(message, **context)
        self.retryable = retryable


class SecurityError(AgentFoxError): ...


class KnowledgeStoreError(AgentFoxError): ...


class FatalAPIError(AgentFoxError):
    """Non-recoverable model-API error (billing, authentication, access).

    Raised when the Anthropic API rejects a call for a reason that
    retrying — or falling back to a degraded code path — cannot fix:
    the account is out of credit, the API key is invalid, or the
    credentials are not permitted to use the endpoint.

    Callers must let this propagate so the daemon aborts with a clear
    message instead of silently continuing with every subsequent AI call
    failing the same way.
    """

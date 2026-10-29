class PolicyError(Exception):
    """Request blocked by the outbound security policy."""


class DNSError(Exception):
    """Hostname could not be resolved."""


class ApprovalRequired(Exception):
    """State-changing request without explicit approval."""


class AllProvidersFailed(Exception):
    """Every LLM provider failed before the first token."""


class StreamInterrupted(Exception):
    """Provider failed after output began."""

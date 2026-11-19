"""Custom exception classes for mnIPLab (Mininet-IPLab).

This module defines domain-specific exceptions to provide clear, actionable error
messages for network configuration and runtime issues.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence


class mnIPLabError(Exception):
    """Base exception for all mnIPLab errors.
    
    All other mnIPLab exceptions inherit from this, allowing users to catch
    all mnIPLab-specific errors with a single except clause.
    """
    pass


class ConfigurationError(mnIPLabError):
    """Raised when network or router configuration is invalid.
    
    Examples:
        - Invalid protocol name
        - Missing required configuration parameter
        - Invalid IP address format
        - FRR configuration template error
    """
    pass


class RouterError(mnIPLabError):
    """Raised when router initialization or operation fails.
    
    Examples:
        - Failed to start FRR daemons
        - Failed to enable a protocol daemon
        - Router not found in network
        - FRR configuration directory not accessible
    """
    pass


class FRRError(mnIPLabError):
    """Raised when FRR (Free Range Routing) operations fail.
    
    Examples:
        - FRR init script not found
        - Failed to start/stop FRR daemons
        - Configuration file not writable
        - Daemon already running or port in use
    """
    pass


class ExaBGPError(mnIPLabError):
    """Raised when ExaBGP operations fail.
    
    Examples:
        - ExaBGP binary not found
        - Failed to write ExaBGP configuration
        - ExaBGP process failed to start
    """
    pass


class ExaBGPControlError(ExaBGPError):
    """Raised when a Speaker's daemon does not answer a command, or answers ``error``.

    ExaBGP's ``error`` line carries no reason — the daemon's log has it — so
    the message names the command that failed. Examples:

        - The FIFO pair is missing or nobody reads it (daemon not running)
        - Five seconds of silence with no ``done``
        - ``error`` or ``shutdown`` in answer to a command
    """

    def __init__(self, message: str, *, command: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.command = command


class NetworkError(mnIPLabError):
    """Raised when network operations fail.
    
    Examples:
        - Failed to add node to network
        - Failed to initialize network-wide state
        - Network already started
        - Network cleanup failed
    """
    pass


class HostError(mnIPLabError):
    """Raised when host configuration or operation fails.
    
    Examples:
        - Invalid IP address for host
        - Failed to configure interface
        - Failed to set IPv6 address
        - Host not found in network
    """
    pass


class LinkError(mnIPLabError):
    """Raised when link configuration or operation fails.
    
    Examples:
        - Failed to create or move a required host-side link
        - Invalid interface configuration
        - Failed to add IP address to interface
        - IPv6 address configuration error
        - Link not found between nodes
    """
    pass


class CLIError(mnIPLabError):
    """Raised when CLI operations fail.
    
    Examples:
        - Failed to initialize hosts file
        - Failed to update hosts entries
        - Network not started
        - Command execution error
    """
    pass


class AuthenticationError(mnIPLabError):
    """Raised when web server authentication fails.
    
    Examples:
        - Invalid username or password
        - Session token expired
        - Unauthorized access attempt
    """
    pass


class WebServerError(mnIPLabError):
    """Raised when web server operations fail.
    
    Examples:
        - Failed to start web server
        - Port already in use
        - Failed to create WebSocket handler
        - PTY session creation failed
    """
    pass


class ValidationError(mnIPLabError):
    """Raised when configuration validation fails.

    Examples:
        - Invalid setting value
        - Type mismatch in configuration
        - Out of range value
        - Required field missing
    """
    pass


class NamespaceError(mnIPLabError):
    """Raised when a Node's private namespaces cannot be established.

    Examples:
        - The Namespace Anchor PID could not be recovered from procfs
        - The anchor shell's argv[0] does not match what discovery expects
    """
    pass


class NamespaceGuardError(NamespaceError):
    """Raised when a Mininet fallback would silently resolve to the wrong namespace.

    Distinct from an ordinary link failure so a future reader is pointed at
    the namespace mismatch rather than at link creation. Examples:

        - Mininet's ``netns 1`` fallback is about to run with a private-PID
          Node's shell as the caller, where ``1`` names the Node itself
          rather than the root namespace
    """
    pass


class DNSControlError(mnIPLabError):
    """Raised when an Authoritative DNS control operation fails.

    Examples:
        - Knot control socket is unreachable or not yet created
        - Control socket timeout or broken connection
        - Knot daemon returned an unexpected error response
    """

    def __init__(
        self,
        message: str,
        *,
        command: Optional[str] = None,
        error_code: Optional[str] = None,
        zone: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.command = command
        self.error_code = error_code
        self.zone = zone
        self.data = data or {}


class DNSValidationError(DNSControlError, ValidationError):
    """Raised when Knot rejects a zone or record transaction due to semantic validation.

    Examples:
        - Adding a CNAME record to an owner that already holds an A record
        - Submitting malformed record data or invalid domain name syntax
        - Missing mandatory apex records (SOA, NS)
    """
    pass




class ResolverControlError(mnIPLabError):
    """Raised when a Resolver control-channel operation fails.

    Examples:
        - Unbound's UNIX control socket is unreachable or not yet created
        - The control socket timed out mid-command
        - Unbound answered a ``UBCT1`` command with an ``error`` line
    """

    def __init__(
        self,
        message: str,
        *,
        command: Optional[str] = None,
        socket_path: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.command = command
        self.socket_path = socket_path


class LabUnavailableError(mnIPLabError):
    """Raised when a call reaches a Lab whose process is gone.

    The message names the Lab — its Lab Example and key — and why it cannot
    answer, so the diagnostic reaches the browser as a statement about that
    Lab rather than a broken socket.
    """
    pass


class KrillError(mnIPLabError):
    """Raised when a Krill API call fails or Krill cannot be reached.

    Examples:
        - Krill refused a ROA delta, e.g. ``ca-roa-delta-error`` for a prefix
          the Hosted CA does not hold
        - Krill's unix socket is missing, refusing connections, or silent

    Attributes:
        status: The HTTP status Krill answered with, or None when no answer
            came back.
        label: Krill's error label (``ca-roa-delta-error``), empty when Krill
            sent none.
        message: Krill's message text.
        body: The parsed error JSON, when Krill sent one.
    """

    def __init__(
        self,
        status: Optional[int],
        label: str,
        message: str,
        body: Optional[Any] = None,
    ) -> None:
        super().__init__(f"{label}: {message}" if label else message)
        self.status = status
        self.label = label
        self.message = message
        self.body = body


class RPKIObjectError(mnIPLabError, ValueError):
    """Raised when Krill refuses a ROA or ASPA change at a Hosted CA.

    Examples:
        - Removing a ROA the Hosted CA never signed
        - Adding a ROA the Hosted CA has already signed
        - Signing an ASPA that Krill does not accept for its customer

    A ValueError too, so the Lab Prompt prints it as an error the same way it
    prints a refused input.
    """
    pass


class RPKINotHeldError(RPKIObjectError):
    """Raised when a Hosted CA is asked to sign a ROA for a prefix it does not hold.

    Attributes:
        hosted_ca: The Autonomous System number of the Hosted CA that refused.
        prefixes: The prefixes the Hosted CA does not hold.
    """

    def __init__(self, message: str, *, hosted_ca: int, prefixes: Sequence[str]) -> None:
        super().__init__(message)
        self.hosted_ca = hosted_ca
        self.prefixes = list(prefixes)


class RoutinatorError(mnIPLabError, ValueError):
    """Raised when a Validator's Routinator does not answer its HTTP endpoint.

    An empty or non-JSON answer means Routinator is not running, or has not
    bound its endpoint yet. A ValueError too, so the Lab Prompt prints it as
    an error the same way it prints a refused input.
    """
    pass

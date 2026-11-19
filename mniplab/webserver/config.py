"""Configuration module for mnIPLab web server."""

import os
import secrets
import string
import warnings
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Set, Tuple

from dotenv import dotenv_values
from pydantic import AliasChoices, BaseModel, Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mniplab.constants import env_file_path

if TYPE_CHECKING:
    from .logins import LoginStore

# Default configuration values
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8050
DEFAULT_USERNAME = "admin"
# The passwords earlier releases shipped as the default; production refuses them.
KNOWN_DEFAULT_PASSWORDS = ("changeme", "changeme123")
DEFAULT_CORS_ORIGINS = ["*"]

# Fixed once, at import: pydantic reads `env_file` at class definition, and
# the deprecation check below must look at the same file Settings did.
ENV_FILE = env_file_path()

# Page loads a minute the Terminal reconnect budget must cover.  A reload opens
# a fresh set of Terminals, so the budget has to be a multiple of the per-Session
# cap or ordinary reloading trips the rate limiter; see
# Settings.validate_reconnect_budget_outruns_the_cap.
RELOADS_PER_MINUTE = 3

# How two validators' refusals begin, so Server settings can recognise them
# and put them in the dialog's words (SettingsStore._plain_reason).
PING_INTERVAL_REFUSAL = "terminal_ping_interval must be less than terminal_idle_timeout"
RECONNECT_BUDGET_REFUSAL = "ws_rate_limit must be at least"

# Letters and digits a reader cannot confuse when copying from a console.
_PASSWORD_ALPHABET = "".join(
    c for c in string.ascii_letters + string.digits if c not in "0OoIl1"
)


def generate_password() -> str:
    """Return a random login password for a server started without one.

    It always holds a letter and a digit, so it passes the same strength
    check as a password a person sets.

    Returns:
        A 16-character password.
    """
    while True:
        candidate = "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(16))
        if any(c.isalpha() for c in candidate) and any(c.isdigit() for c in candidate):
            return candidate


def _env_var_names_that_are_set() -> Set[str]:
    """Return every env var name set in the process env or in the env file.

    Settings itself reads from exactly these two places (`SettingsConfigDict`
    below); a deprecation check needs to look at them directly because
    `AliasChoices` resolves an aliased field to a single value and does not
    expose which alias supplied it.  Collected in one pass rather than probed
    per name, so checking a table of renames does not re-read ``.env`` once per
    entry.  Upper-cased, matching pydantic-settings' case-insensitive default.

    Returns:
        The names that are set, upper-cased.
    """
    names = {key.upper() for key in os.environ}
    names.update(
        key.upper()
        for key, value in dotenv_values(ENV_FILE).items()
        if value is not None
    )
    return names


class Settings(BaseSettings):
    """Web server configuration settings.
    
    All settings can be overridden via environment variables with MNIPLAB_ prefix.
    Example: MNIPLAB_USERNAME=learner MNIPLAB_PASSWORD=lab123

    The env file is ``.env`` in the working directory unless
    ``MNIPLAB_ENV_FILE`` names another; pydantic fixes the path here, at
    class definition, so the variable has to be set before this module is
    imported.
    """
    
    model_config = SettingsConfigDict(
        env_prefix="MNIPLAB_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        # Lets a field with an explicit validation_alias (the Terminal limits
        # below) still be constructed by its Python name, e.g.
        # Settings(terminal_per_session_cap=5) in tests — without this,
        # AliasChoices makes the field name itself stop working as a
        # constructor kwarg.
        populate_by_name=True,
    )

    # The logins added in Server settings; only `serve` opens a store.
    _login_store: Optional["LoginStore"] = PrivateAttr(default=None)
    
    # Server configuration
    host: str = Field(default=DEFAULT_HOST, description="Server bind address")
    port: int = Field(default=DEFAULT_PORT, gt=0, le=65535, description="Server port (1-65535)")
    enable_web: bool = Field(default=True, description="Enable web server")
    use_secure_cookies: bool = Field(
        default=False,
        description="Set Secure flag on session cookies (enable when serving over HTTPS)"
    )
    
    # Authentication
    username: str = Field(default=DEFAULT_USERNAME, min_length=1, description="Login username")
    password: str = Field(
        default_factory=generate_password,
        min_length=1,
        description="Login password; a random one is generated at each start when unset",
    )
    users: str = Field(
        default="",
        description=(
            "Multi-user credentials: 'user:pass:role,...' (roles: instructor, learner). "
            "When set, overrides MNIPLAB_USERNAME/PASSWORD. "
            "Example: MNIPLAB_USERS='alice:Pass123:instructor,bob:Pass456:learner'"
        )
    )
    # Terminal limits
    terminal_per_session_cap: int = Field(
        default=5,
        ge=1,
        le=100,
        validation_alias=AliasChoices(
            "MNIPLAB_TERMINAL_PER_SESSION_CAP",
            "MNIPLAB_PTY_PER_SESSION_CAP",
            "MNIPLAB_PTY_PER_CLIENT_CAP",
        ),
        description=(
            "Max Terminals per Session. Both older spellings are still read: "
            "PTY_PER_SESSION_CAP, and before it PTY_PER_CLIENT_CAP, which "
            "measured the same thing under a name that changed meaning three "
            "times in January 2026 while the value stayed 5"
        ),
    )
    terminal_global_cap: int = Field(
        default=100,
        ge=1,
        validation_alias=AliasChoices(
            "MNIPLAB_TERMINAL_GLOBAL_CAP", "MNIPLAB_PTY_GLOBAL_CAP"
        ),
        description="Max live Terminals across the whole host, independent of Session",
    )
    terminal_idle_timeout: int = Field(
        default=300,
        gt=0,
        validation_alias=AliasChoices(
            "MNIPLAB_TERMINAL_IDLE_TIMEOUT", "MNIPLAB_PTY_IDLE_TIMEOUT"
        ),
        description="Seconds a Terminal may go unused before it auto-disconnects",
    )
    terminal_ping_interval: int = Field(
        default=15,
        gt=0,
        validation_alias=AliasChoices(
            "MNIPLAB_TERMINAL_PING_INTERVAL", "MNIPLAB_PTY_PING_INTERVAL"
        ),
        description="Seconds between a Terminal's WebSocket keepalive pings",
    )

    # Session limits
    session_idle_timeout: int = Field(
        default=9000,
        gt=0,
        description=(
            "Seconds a Session may go unseen before it is treated as dead "
            "(default 9000, i.e. 2.5 hours). Its Terminals are closed with it."
        ),
    )
    
    session_per_user_cap: int = Field(
        default=0,
        ge=0,
        description=(
            "Most Sessions one account may hold at once (0, the default, disables "
            "the cap entirely). At the cap a login evicts that account's "
            "least-recently-seen Session and closes its Terminals. Off by default "
            "because one credential may legitimately serve a whole class."
        ),
    )

    activity_idle_threshold: int = Field(
        default=300,
        gt=0,
        description=(
            "Seconds a Group may go without Session or Terminal activity "
            "before its Activity State reads Idle (default 300, i.e. 5 "
            "minutes). Derived at request time; nothing is stored."
        ),
    )

    # Rate limiting
    login_rate_limit: int = Field(default=5, ge=1, description="Wrong passwords per minute per login name before that login is locked")
    ws_attempt_limit: int = Field(
        default=120,
        ge=1,
        description=(
            "Terminal connection attempts a Session may make per minute, "
            "counted whatever the outcome. The ceiling that bounds a storm of "
            "attempts the server refuses, which ws_rate_limit deliberately "
            "does not charge for. Must be at least ws_rate_limit"
        ),
    )
    ws_rate_limit: int = Field(
        default=30,
        ge=1,
        description=(
            "Terminal connections a Session may open per minute. Only a "
            "connection that actually yields a Terminal is counted, so a "
            "Terminal refused for the cap costs nothing. Must be at least "
            "RELOADS_PER_MINUTE x terminal_per_session_cap"
        ),
    )
    
    # CORS settings
    cors_origins: List[str] = Field(
        default=DEFAULT_CORS_ORIGINS,
        description="CORS allowed origins (restrict in production)"
    )

    # How many Labs the server runs at once.  Read at startup only: a running
    # class is never switched under, and a restart comes up with no Lab
    # alive.
    lab_mode: Literal['single', 'multi'] = Field(
        default='single',
        description="Lab mode: 'single' (one Lab for everyone, the default) or "
                    "'multi' (a Lab per Group). Read at startup only."
    )
    # The most Labs alive at once in Multi-Lab Mode; Single-Lab Mode is a
    # cap of one whatever this says.  It refuses Group and Instructor alike.
    lab_cap: int = Field(
        default=4, ge=1,
        description="Max Labs alive at once in Multi-Lab Mode (Instructor included)"
    )
    # Seconds allowed for a Lab Example's builder to execute and report ready
    # before timing out.
    lab_builder_timeout: float = Field(
        default=60.0,
        gt=0,
        description="Seconds allowed for a Lab to build and report ready before timing out",
    )

    # Environment mode
    environment: Literal['development', 'production'] = Field(
        default='development',
        description="Runtime environment: 'development' or 'production'. "
                    "Production mode enforces stricter security checks."
    )
    allow_default_password: bool = Field(
        default=False,
        description="Allow default password in production (set true only for demos)"
    )
    
    @field_validator('password')
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """Reject weak passwords; production's checks are in validate_production_guardrails."""
        # Minimum length check
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        
        # Require at least one letter and one number for better security
        has_letter = any(c.isalpha() for c in v)
        has_digit = any(c.isdigit() for c in v)
        if not (has_letter and has_digit):
            raise ValueError(
                "Password must contain at least one letter and one number\n"
                "Example: 'mypass123' or 'Lab2024'"
            )
        
        return v
    
    @model_validator(mode='after')
    def validate_production_guardrails(self) -> 'Settings':
        """Refuse an old default password and a CORS wildcard in production.

        Runs after every field is read: ``environment`` is declared below
        ``password`` and ``cors_origins``, so a field validator on either
        never saw it and let both through.
        """
        if self.environment != 'production':
            return self
        if self.password in KNOWN_DEFAULT_PASSWORDS and not self.allow_default_password:
            raise ValueError(
                f"Default password '{self.password}' not allowed in production.\n"
                "Set MNIPLAB_PASSWORD environment variable or\n"
                "use MNIPLAB_ALLOW_DEFAULT_PASSWORD=true for development."
            )
        if "*" in self.cors_origins:
            raise ValueError(
                "CORS wildcard (*) not allowed in production.\n"
                "Specify explicit origins:\n"
                "  MNIPLAB_CORS_ORIGINS='http://lab.example.com,https://lab.example.com'"
            )
        return self

    @model_validator(mode='after')
    def validate_timeout_relationships(self) -> 'Settings':
        """Ensure ping interval is less than idle timeout.

        Ties ``terminal_ping_interval`` to ``terminal_idle_timeout`` only — it
        does not reference ``terminal_per_session_cap``, so renaming that field
        left this validator unchanged.
        """
        if self.terminal_ping_interval >= self.terminal_idle_timeout:
            raise ValueError(PING_INTERVAL_REFUSAL)
        return self

    @model_validator(mode='after')
    def validate_reconnect_budget_outruns_the_cap(self) -> 'Settings':
        """Ensure a Learner can reload the page without being locked out.

        Every Terminal costs one connection, so a full set costs the cap, and a
        reload spends a second set opening what the reload just dropped.  A
        budget merely larger than the cap therefore buys one page load a
        minute: at ``WS_RATE_LIMIT=10`` with a cap of 5, a second reload was
        refused for the rest of the minute, and the Learner was told they were
        reconnecting too fast when they had simply reloaded twice.

        ``RELOADS_PER_MINUTE`` sets how many page loads a minute the budget
        must cover, which is the smallest number that makes the limit about
        connection storms rather than about ordinary use.
        """
        floor = RELOADS_PER_MINUTE * self.terminal_per_session_cap
        if self.ws_rate_limit < floor:
            raise ValueError(
                f"{RECONNECT_BUDGET_REFUSAL} {floor} "
                f"({RELOADS_PER_MINUTE} x terminal_per_session_cap of "
                f"{self.terminal_per_session_cap}), so that reloading the page "
                f"does not exhaust it; got {self.ws_rate_limit}"
            )
        return self

    @model_validator(mode='after')
    def validate_attempt_ceiling_clears_the_reconnect_budget(self) -> 'Settings':
        """Ensure the storm ceiling cannot fire before the reconnect budget.

        Every served Terminal is also an attempt, so a ceiling below
        ``ws_rate_limit`` would refuse connections the reconnect budget still
        allowed — and refuse them with the wrong reason, which is the confusion
        this pair of limits exists to end.
        """
        if self.ws_attempt_limit < self.ws_rate_limit:
            raise ValueError(
                f"ws_attempt_limit must be at least ws_rate_limit "
                f"({self.ws_rate_limit}), since every served Terminal is also "
                f"an attempt; got {self.ws_attempt_limit}"
            )
        return self

    @classmethod
    def renamed_env_names(cls) -> Dict[str, Tuple[str, ...]]:
        """Return each renamed setting's current env var name and its old ones.

        Read off the fields' own ``AliasChoices`` rather than restated in a
        table beside them.  A hand-kept copy is a second source of truth that
        drifts: adding an alias and forgetting the table would leave the value
        working while the deprecation warning silently stopped firing, which
        is the exact silent break this warning exists to prevent.

        Deriving it also makes the blame order true by construction — pydantic
        takes the first alias it finds a value for, so the first superseded
        name present is the one that supplied the value.

        Returns:
            Each current name mapped to its superseded spellings, in the order
            pydantic consults them.
        """
        renamed: Dict[str, Tuple[str, ...]] = {}
        for field in cls.model_fields.values():
            alias = field.validation_alias
            if not isinstance(alias, AliasChoices):
                continue
            names = [choice for choice in alias.choices if isinstance(choice, str)]
            if len(names) > 1:
                renamed[names[0]] = tuple(names[1:])
        return renamed

    @model_validator(mode='after')
    def warn_on_deprecated_env_names(self) -> 'Settings':
        """Warn when a superseded env var name is what supplied a value.

        ``AliasChoices`` resolves each renamed field silently, so by the time
        any validator runs, the winning value carries no trace of which env var
        supplied it.  The only way to tell is to look at the same two places
        Settings itself reads from — the process environment and ``.env`` —
        directly, and warn only when the current name is absent and a
        superseded one is present.  Without this, renaming a setting would be
        the same silent break that let MNIPLAB_SESSION_SECRET sit dead in
        `.env` for three months.
        """
        present = _env_var_names_that_are_set()
        for current, superseded in self.renamed_env_names().items():
            if current in present:
                continue
            for old_name in superseded:
                if old_name in present:
                    warnings.warn(
                        f"{old_name} is deprecated; use {current} instead. "
                        f"{old_name} will stop being read in a future release.",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                    break
        return self

    @property
    def password_was_generated(self) -> bool:
        """Whether the single login's password was generated for this run.

        True when neither the environment nor ``.env`` set MNIPLAB_PASSWORD.
        """
        return "password" not in self.model_fields_set

    def use_login_store(self, store: Optional["LoginStore"]) -> None:
        """Let the login registry read the logins added in Server settings.

        Only ``serve`` opens a login store; without one the registry is the
        ``.env`` accounts alone.

        Args:
            store: A ``LoginStore``, or None to read ``.env`` accounts only.
        """
        self._login_store = store

    @property
    def login_store(self) -> Optional["LoginStore"]:
        """The logins added in Server settings, or None outside ``serve``."""
        return self._login_store

    def get_user_registry(self) -> Dict[str, dict]:
        """Return {username: {password, role}} for every login the server accepts.

        The ``.env`` accounts come first.  Under ``serve`` the logins added
        in Server settings follow, each a Learner marked ``added``; a name
        in both, possible only by a hand edit, is the ``.env`` account.
        """
        registry = self._env_user_registry()
        if self._login_store is not None:
            for name, password in self._login_store.passwords().items():
                registry.setdefault(name, {"password": password, "role": "learner", "added": True})
        return registry

    def _env_user_registry(self) -> Dict[str, dict]:
        """Return {username: {password, role}} for the accounts set in ``.env``.

        When MNIPLAB_USERS is set, parses 'user:pass:role,...' entries.
        Falls back to the single MNIPLAB_USERNAME/PASSWORD pair (role: instructor).
        """
        if not self.users.strip():
            return {self.username: {"password": self.password, "role": "instructor"}}

        valid_roles = {"instructor", "learner"}
        registry: Dict[str, dict] = {}
        for entry in self.users.split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(":", 2)
            if len(parts) != 3:
                continue
            uname, passwd, role = parts[0].strip(), parts[1].strip(), parts[2].strip().lower()
            if not uname or not passwd:
                continue
            if role not in valid_roles:
                role = "learner"
            registry[uname] = {"password": passwd, "role": role}

        if not registry:
            return {self.username: {"password": self.password, "role": "instructor"}}
        return registry


# Global settings instance
settings = Settings()


def check_config_warnings() -> None:
    """Print concise warnings about default configuration values in production mode.
    
    This function is called from start_server() to provide clean configuration
    warnings without pydantic validation stack traces.
    
    Warnings are only shown if MNIPLAB_ENVIRONMENT is set to 'production'.
    For lab/development environments (default), no warnings are displayed.
    
    Users can customize these values via .env file or environment variables:
    - MNIPLAB_HOST
    - MNIPLAB_PASSWORD
    """
    from mininet.log import warn
    
    # Only show warnings in production mode
    if settings.environment == 'development':
        return
    
    warnings = []
    
    if settings.host not in ['localhost', '127.0.0.1', '::1']:
        warnings.append(f"Binding to public address ({settings.host}). Set MNIPLAB_HOST=127.0.0.1 in .env for security.")
    
    if settings.password in KNOWN_DEFAULT_PASSWORDS:
        warnings.append(f"Using default password '{settings.password}'. Set MNIPLAB_PASSWORD in .env.")
    
    
    for msg in warnings:
        warn(f"*** Warning: {msg}\n")

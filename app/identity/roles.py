ORGANIZATION_STATUSES = ("active", "suspended")
USER_STATUSES = ("active", "suspended")
MEMBERSHIP_STATUSES = ("active", "suspended")
SERVICE_ACCOUNT_STATUSES = ("active", "revoked")

ORGANIZATION_ROLES = ("owner", "developer", "billing_viewer")
SERVICE_ACCOUNT_ROLES = ("developer", "billing_viewer")
PLATFORM_ROLES = ("platform_operator",)

AUDIT_ACTOR_TYPES = ("user", "service_account", "api_key", "system")
USER_ACTION_TOKEN_PURPOSES = ("email_verification", "password_reset")
ROUTING_STRATEGIES = ("balanced", "cost", "latency", "quality")

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class PortalSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterAccountRequest(PortalSchema):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=200)
    password: SecretStr = Field(min_length=12, max_length=1_024)
    organization_name: str = Field(min_length=1, max_length=200)
    organization_slug: str = Field(min_length=3, max_length=63)


class RegisterAccountResponse(PortalSchema):
    user_id: UUID
    organization_id: UUID
    verification_required: bool = True
    verification_token: str | None = None


class VerifyEmailRequest(PortalSchema):
    token: SecretStr = Field(min_length=16, max_length=1_024)


class LoginRequest(PortalSchema):
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=12, max_length=1_024)


class PasswordResetRequestBody(PortalSchema):
    email: str = Field(min_length=3, max_length=320)


class PasswordResetRequestResponse(PortalSchema):
    message: str
    reset_token: str | None = None


class PasswordResetConfirmRequest(PortalSchema):
    token: SecretStr = Field(min_length=16, max_length=1_024)
    new_password: SecretStr = Field(min_length=12, max_length=1_024)


class OrganizationSummary(PortalSchema):
    id: UUID
    slug: str
    name: str
    role: str


class BrowserSessionResponse(PortalSchema):
    user_id: UUID
    email: str
    display_name: str
    active_organization: OrganizationSummary
    csrf_token: str
    expires_at: str


class CurrentAccountResponse(PortalSchema):
    user_id: UUID
    email: str
    display_name: str
    active_organization_id: UUID
    organizations: list[OrganizationSummary]


class MessageResponse(PortalSchema):
    message: str

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

OrganizationRole = Literal["owner", "developer", "billing_viewer"]
MembershipStatus = Literal["active", "suspended"]


class MemberSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateInvitationRequest(MemberSchema):
    email: str = Field(min_length=3, max_length=320)
    role: OrganizationRole


class InvitationResponse(MemberSchema):
    id: UUID
    email: str
    role: OrganizationRole
    expires_at: str
    invitation_token: str


class AcceptInvitationRequest(MemberSchema):
    token: SecretStr = Field(min_length=16, max_length=1_024)


class AcceptedInvitationResponse(MemberSchema):
    organization_id: UUID
    membership_id: UUID
    role: OrganizationRole


class OrganizationMemberResponse(MemberSchema):
    membership_id: UUID
    user_id: UUID
    email: str
    display_name: str
    role: OrganizationRole
    status: MembershipStatus


class OrganizationMembersResponse(MemberSchema):
    data: list[OrganizationMemberResponse]


class UpdateOrganizationMemberRequest(MemberSchema):
    role: OrganizationRole
    status: MembershipStatus

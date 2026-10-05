"""
api/routers/roles.py
~~~~~~~~~~~~~~~~~~~~
The Role Dossier: one page of evidence about one role, for the signed-in user.

Thin by design - everything is composed in ``services/dossier.py``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.auth import get_current_user
from services import dossier, referral

router = APIRouter(prefix="/roles", tags=["Role Dossier"])


async def get_current_user_id(user: Annotated[dict[str, Any], Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim"
        )
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]


@router.get("/{job_id}/dossier")
async def role_dossier(job_id: str, user_id: CurrentUserId) -> dict[str, Any]:
    """
    Is this role open, is it for me, what is my way in, am I ready.

    Each of the four sections is ``null`` when it could not be loaded; the
    page renders whatever is present.
    """
    result = await dossier.build_dossier(job_id=job_id, user_id=user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return result


class ReferralAskRequest(BaseModel):
    recipient_name: str | None = Field(None, max_length=120)
    recipient_title: str | None = Field(None, max_length=120)
    relationship: str | None = Field(
        None, description=f"One of: {', '.join(referral.RELATIONSHIPS)}", max_length=40
    )


@router.post("/{job_id}/referral-ask")
async def referral_ask(
    job_id: str, body: ReferralAskRequest, user_id: CurrentUserId
) -> dict[str, Any]:
    """
    Draft a message asking someone for a referral to this role.

    Nothing is sent: the draft is returned for the user to edit and send from
    their own account. ``generated`` is false when a template was returned
    because the model was unavailable.
    """
    result = await referral.draft(
        job_id=job_id,
        user_id=user_id,
        recipient_name=body.recipient_name,
        recipient_title=body.recipient_title,
        relationship=body.relationship,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return result


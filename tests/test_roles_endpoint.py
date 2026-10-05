"""
tests/test_roles_endpoint.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
HTTP surface of the Role Dossier.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

JOB_ID = str(uuid.uuid4())
URL = f"/api/v1/roles/{JOB_ID}/dossier"
DOSSIER = {
    "role": {"id": JOB_ID, "title": "Backend Engineer"},
    "liveness": None,
    "fit": None,
    "way_in": None,
    "readiness": None,
}


async def test_dossier_requires_a_signed_in_user(api_client):
    response = await api_client.get(URL)
    assert response.status_code in (401, 403)


async def test_dossier_is_returned_for_the_signed_in_user(auth_client):
    with patch("services.dossier.build_dossier", AsyncMock(return_value=DOSSIER)) as build:
        response = await auth_client.get(URL)
    assert response.status_code == 200
    assert response.json() == DOSSIER
    assert build.await_args.kwargs["job_id"] == JOB_ID
    assert build.await_args.kwargs["user_id"]


async def test_unknown_role_is_a_404(auth_client):
    with patch("services.dossier.build_dossier", AsyncMock(return_value=None)):
        response = await auth_client.get(URL)
    assert response.status_code == 404

"""
services/outreach/errors.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
The ways an Outreach call can be refused.

Each error carries the HTTP status the router will answer with, so the services
stay free of FastAPI and the router needs one handler for all of them:

* ``NotFound`` (404): no such thing, *or not yours*. A thread, person or touch
  that belongs to another user is indistinguishable from one that does not
  exist, so an id reveals nothing. It is never a 403.
* ``Conflict`` (409): a cap was reached, or the request repeats something that
  already exists.
* ``Invalid`` (422): a value is out of bounds, in the wrong state or unknown.
"""

from __future__ import annotations


class OutreachError(Exception):
    """Base class: a refused request with a status and a message for the user."""

    status_code: int = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFound(OutreachError):  # noqa: N818 - reads as the condition, like StopIteration
    status_code = 404


class Conflict(OutreachError):  # noqa: N818 - reads as the condition, like StopIteration
    status_code = 409


class Invalid(OutreachError):  # noqa: N818 - reads as the condition, like StopIteration
    status_code = 422

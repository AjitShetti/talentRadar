"""
services/practice/errors.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
What the Practice services raise. The router turns each into a status code:
``PracticeConflict`` is 409, ``PracticeInvalid`` is 422 and ``PracticeNotFound`` is 404;
``PracticeRateLimited`` (a ``PracticeConflict``) is 429. The message is written for the person using the page, so it is safe to show.
"""

from __future__ import annotations


class PracticeConflict(Exception):  # noqa: N818 - names the condition, as RateLimited does
    """The request is fine but clashes with what is stored (already done, already started)."""


class PracticeInvalid(Exception):  # noqa: N818 - names the condition, as RateLimited does
    """The request cannot be accepted as given (a bad repo link, a closed window, a wrong repo)."""


class PracticeNotFound(Exception):  # noqa: N818 - names the condition, as RateLimited does
    """The thing asked about does not exist, or is not the caller's to see."""


class PracticeRateLimited(PracticeConflict):
    """Too many saves this minute. A conflict to any caller that only knows conflicts.

    Only ``challenges.save_submission`` raises it. The router answers it with 429 and a
    ``Retry-After`` header, where every other ``PracticeConflict`` is a 409.
    """

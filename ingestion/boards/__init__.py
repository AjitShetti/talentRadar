"""
ingestion/boards
~~~~~~~~~~~~~~~~
One reader per applicant-tracking system, for the job-board roster.

Each module's ``read_board(client, ref)`` makes the request for one
:class:`domain.job_boards.BoardRef` and returns a :class:`BoardRead` holding every
role on it. ``READERS`` is the one table the sweep, the probe and live search look a
reader up in; a module is added to it when its reader exists.
"""

from __future__ import annotations

from domain.job_boards import AtsName
from ingestion.boards import ashby, greenhouse, lever
from ingestion.boards.base import BoardRead, Reader, get_json

READERS: dict[AtsName, Reader] = {
    "greenhouse": greenhouse.read_board,
    "ashby": ashby.read_board,
    "lever": lever.read_board,
}

__all__ = ["READERS", "BoardRead", "Reader", "get_json"]

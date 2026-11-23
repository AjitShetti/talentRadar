"""
services/job_boards
~~~~~~~~~~~~~~~~~~~
The I/O behind the job-board roster: writing what one board lists into ``jobs``,
the discovery steps that find boards, and the paced sweep that reads them.

The rules (which boards exist, when to read one again, what counts as India) are
pure and live in ``domain/job_boards.py``; each ATS's request and parsing lives
in ``ingestion/boards``. Design:
docs/superpowers/specs/2026-11-23-job-board-roster-design.md.
"""

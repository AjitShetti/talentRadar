"""
services/events
~~~~~~~~~~~~~~~
The I/O behind Events: one fetcher and parser per source (Luma iCal, GDG,
Eventbrite), the store that upserts what they find, and the reads the API and
the Overview day plan use.

Every parser is a thin, pure function over a recorded payload; the rules
(IST, relevance, identity) live in ``domain/``. Every network call goes
through :func:`services.events.http.get_bounded`, so each one identifies
itself, times out and stops reading at the same size cap.
"""

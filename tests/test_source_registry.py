"""
tests/test_source_registry.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Contract tests for the live-source registry.

The registry is the single list of scrapers that the search fan-out, the
health service and the fixture contract tests all read. These tests pin the
properties every consumer depends on:

* a source is uniquely named, so health counters cannot collide
* the fetch callable has the one shared signature the fan-out calls
* browser-only sources are declared as such, so a 512 MB instance can exclude
  them without knowing anything about the individual scraper
* the free-tier roster contains no browser source at all
"""

from __future__ import annotations

import inspect

import pytest

from ingestion.sources.registry import (
    LiveSource,
    default_live_sources,
    get_live_source,
    live_source_registry,
)


def test_registry_is_not_empty():
    assert len(live_source_registry()) > 0


def test_source_names_are_unique():
    names = [s.name for s in live_source_registry()]
    assert len(names) == len(set(names)), f"duplicate source names: {names}"


def test_every_source_has_a_usable_shape():
    for source in live_source_registry():
        assert isinstance(source, LiveSource)
        assert source.name and source.name.islower()
        assert source.timeout_seconds > 0
        assert source.tier in {"ats", "board", "stealth"}
        assert callable(source.fetch)


def test_fetch_callables_share_one_signature():
    """The fan-out calls every source the same way; nothing may deviate."""
    for source in live_source_registry():
        params = list(inspect.signature(source.fetch).parameters)
        assert params[:3] == ["query", "location", "is_remote"], (
            f"{source.name} takes {params}, but the fan-out passes "
            "(query, location, is_remote)"
        )


def test_stealth_sources_are_declared_browser_requiring():
    for source in live_source_registry():
        if source.tier == "stealth":
            assert source.requires_browser is True


def test_default_roster_excludes_browser_sources():
    """
    The deployed instance has 512 MB and cannot launch a browser. The default
    roster must therefore be browser-free without needing a feature flag to
    save it.
    """
    for source in default_live_sources(enable_stealth=False):
        assert source.requires_browser is False


def test_enabling_stealth_adds_browser_sources():
    without = {s.name for s in default_live_sources(enable_stealth=False)}
    with_stealth = {s.name for s in default_live_sources(enable_stealth=True)}
    assert without < with_stealth


def test_get_live_source_by_name():
    first = live_source_registry()[0]
    assert get_live_source(first.name) is first


def test_get_live_source_rejects_unknown_name():
    with pytest.raises(KeyError):
        get_live_source("no-such-source")

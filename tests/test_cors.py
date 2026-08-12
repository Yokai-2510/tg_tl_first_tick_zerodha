"""
CORS origin matching — the thing that silently breaks a fresh frontend deploy.

The API allow-lists origins exactly, so a browser on an origin that is not listed
is blocked before the request is even sent, and the failure looks like the backend
being down. Preview deployments make an exact list unmaintainable (a new hostname
per push on Vercel, per branch on Cloudflare Pages and Netlify), so wildcard
entries compile to an anchored regex.
"""

from __future__ import annotations

import re

import pytest

from backend.api.server import DEV_ORIGIN, cors_settings


def matches(origins: list[str], origin: str) -> bool:
    """Reproduce Starlette's decision for one Origin header."""
    exact, regex = cors_settings(origins)
    if "*" in exact or origin in exact:
        return True
    return bool(regex and re.compile(regex).fullmatch(origin))


def test_empty_config_falls_back_to_the_dev_server():
    exact, regex = cors_settings([])
    assert exact == [DEV_ORIGIN] and regex is None


def test_exact_origins_pass_through_unchanged():
    exact, regex = cors_settings(["https://a.vercel.app", "http://localhost:5173"])
    assert exact == ["https://a.vercel.app", "http://localhost:5173"]
    assert regex is None, "no wildcards means no regex at all"


def test_trailing_slash_and_blanks_are_tolerated():
    # An Origin header never has a trailing slash, so a configured one would
    # never match and the operator would have no idea why.
    exact, _ = cors_settings(["https://a.pages.dev/", "  ", ""])
    assert exact == ["https://a.pages.dev"]
    assert matches(["https://a.pages.dev/"], "https://a.pages.dev")


def test_bare_star_keeps_its_allow_any_meaning():
    exact, regex = cors_settings(["*"])
    assert exact == ["*"] and regex is None


@pytest.mark.parametrize("origin", [
    "https://main.first-tick.pages.dev",
    "https://feature-x.first-tick.pages.dev",
])
def test_wildcard_admits_preview_hostnames(origin):
    assert matches(["https://*.first-tick.pages.dev"], origin)


@pytest.mark.parametrize("origin", [
    "https://first-tick.pages.dev.evil.com",   # suffix appended
    "http://main.first-tick.pages.dev",        # wrong scheme
    "https://main.other-project.pages.dev",    # different project
    "https://evil.com/main.first-tick.pages.dev",
])
def test_wildcard_is_anchored_and_cannot_be_widened(origin):
    assert not matches(["https://*.first-tick.pages.dev"], origin)


def test_star_never_spans_a_slash():
    # The guarantee that makes the regex safe: a path separator cannot be
    # swallowed, so an attacker-controlled host with our domain in its PATH fails.
    _, regex = cors_settings(["https://*.pages.dev"])
    assert "[^/]+" in regex
    assert not re.compile(regex).fullmatch("https://evil.com/x.pages.dev")


def test_dots_are_escaped_not_treated_as_any_char():
    assert not matches(["https://*.pages.dev"], "https://axpagesxdev")


def test_exact_and_wildcard_entries_coexist():
    exact, regex = cors_settings(
        ["http://localhost:5173", "https://*.vercel.app", "https://*.pages.dev"]
    )
    assert exact == ["http://localhost:5173"]
    for o in ("http://localhost:5173", "https://x.vercel.app", "https://y.pages.dev"):
        assert matches(
            ["http://localhost:5173", "https://*.vercel.app", "https://*.pages.dev"], o
        ), o
    assert regex.count("|") == 1


def test_wildcard_only_config_does_not_silently_gain_localhost():
    exact, regex = cors_settings(["https://*.pages.dev"])
    assert exact == [], "the dev fallback must not be added when a pattern exists"
    assert regex is not None

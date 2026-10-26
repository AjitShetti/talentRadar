"""
tests/test_company_map_website.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The company website reader: ``extract_site_facts`` on recorded homepages and
small inline cards, and ``read_site`` against an ``httpx.MockTransport`` so
nothing here touches the network.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest

from services.company_map import website
from services.company_map.types import SiteFacts
from services.company_map.website import (
    MAX_BODY_BYTES,
    MAX_REDIRECTS,
    USER_AGENT,
    extract_site_facts,
    read_site,
)

FIXTURES = Path(__file__).parent / "fixtures" / "company_map"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ── extract_site_facts: recorded homepages ──────────────────────────────────


@pytest.mark.parametrize(
    ("fixture", "base_url", "description_start"),
    [
        ("site_zerodha.html", "https://zerodha.com/", "Online stock brokerage platform"),
        ("site_groww.html", "https://groww.in/", "Start Online Investing in Stocks"),
        ("site_cred.html", "https://cred.club/", "financial & lifestyle experiences"),
    ],
)
def test_fixture_descriptions_are_extracted(
    fixture: str, base_url: str, description_start: str
) -> None:
    facts = extract_site_facts(_fixture(fixture), base_url)
    assert facts.description is not None
    assert facts.description.startswith(description_start)
    assert "\n" not in facts.description
    assert facts.title
    assert 0 < len(facts.text) <= 4000


def test_fixture_registered_address_is_found_in_a_long_disclaimer() -> None:
    facts = extract_site_facts(_fixture("site_zerodha.html"), "https://zerodha.com/")
    assert facts.pincode == "560078"
    assert facts.address is not None
    assert facts.address.startswith("#153/154, 4th Cross, Dollars Colony")
    assert "Bengaluru - 560078" in facts.address
    assert "complaints" not in facts.address
    assert len(facts.address) <= 300


def test_fixture_footer_address_spans_are_one_block() -> None:
    facts = extract_site_facts(_fixture("site_groww.html"), "https://groww.in/")
    assert facts.pincode == "560103"
    assert facts.address is not None
    assert facts.address.startswith("Vaishnavi Tech Park")
    assert "Bellandur, Bengaluru" in facts.address


def test_page_with_no_address_gives_none() -> None:
    facts = extract_site_facts(_fixture("site_cred.html"), "https://cred.club/")
    assert facts.address is None
    assert facts.pincode is None


def test_fixture_links_are_same_site_about_and_contact_only() -> None:
    facts = extract_site_facts(_fixture("site_zerodha.html"), "https://zerodha.com/")
    assert "https://zerodha.com/about/" in facts.links
    assert "https://zerodha.com/contact/" in facts.links
    assert "https://zerodha.com/about/philosophy" in facts.links
    assert len(facts.links) == len(set(facts.links))
    assert all(link.startswith("https://zerodha.com/") for link in facts.links)


def test_relative_about_link_is_made_absolute() -> None:
    facts = extract_site_facts(_fixture("site_groww.html"), "https://groww.in/")
    assert facts.links == ("https://groww.in/about-us", "https://groww.in/careers")


def test_fixture_company_social_link_is_not_a_person() -> None:
    for name, url in [
        ("site_zerodha.html", "https://zerodha.com/"),
        ("site_groww.html", "https://groww.in/"),
        ("site_cred.html", "https://cred.club/"),
    ]:
        assert extract_site_facts(_fixture(name), url).people == ()


# ── extract_site_facts: inline cases ────────────────────────────────────────

_FOOTER = """
<html><head><title>Acme</title>
<meta name="description" content="  Acme builds
   payroll software.  ">
<style>.x{color:red}</style></head>
<body>
<nav><a href="/pricing">Pricing</a> Menu text that is noise</nav>
<main><h1>Payroll for India</h1><p>Run payroll in minutes.</p></main>
<script>var secret = "do not show";</script>
<footer>
  <div class="address">
    <p>Acme Technologies Pvt Ltd</p>
    <p>4th Floor, 80 Feet Road, Koramangala 4th Block</p>
    <p>Bengaluru, Karnataka 560034</p>
  </div>
  <a href="/contact-us#form">Contact</a>
  <a href="https://acme.in/about">About</a>
  <a href="https://other.example/about">Partner</a>
  <a href="mailto:hi@acme.in">Mail</a>
</footer>
</body></html>
"""


def test_footer_address_with_560034_is_found() -> None:
    facts = extract_site_facts(_FOOTER, "https://acme.in/")
    assert facts.pincode == "560034"
    assert facts.address is not None
    assert "Koramangala 4th Block" in facts.address
    assert "560034" in facts.address


def test_text_drops_script_style_and_nav_and_flattens_meta() -> None:
    facts = extract_site_facts(_FOOTER, "https://acme.in/")
    assert facts.description == "Acme builds payroll software."
    assert facts.title == "Acme"
    assert "do not show" not in facts.text
    assert "color:red" not in facts.text
    assert "noise" not in facts.text
    assert "Run payroll in minutes." in facts.text


def test_links_keep_same_site_about_and_contact_without_fragments() -> None:
    facts = extract_site_facts(_FOOTER, "https://www.acme.in/")
    assert facts.links == ("https://www.acme.in/contact-us", "https://acme.in/about")


def test_og_description_is_the_fallback() -> None:
    html = '<html><head><meta property="og:description" content="From OG."></head></html>'
    assert extract_site_facts(html, "https://a.in/").description == "From OG."


def test_text_is_capped_at_4000_characters() -> None:
    html = "<html><body>" + "<p>word word word</p>" * 2000 + "</body></html>"
    assert len(extract_site_facts(html, "https://a.in/").text) <= 4000


def test_pincode_without_city_is_not_an_address() -> None:
    html = "<footer><p>Plot 5, Sector 2, Pune 560034 is not ours</p></footer>"
    facts = extract_site_facts(html, "https://a.in/")
    assert facts.address is None
    assert facts.pincode is None


_TEAM = """
<section class="team">
  <div class="card">
    <h3>Priya Raman</h3>
    <p>Co-founder &amp; CEO</p>
    <a href="https://www.linkedin.com/in/priya-raman-123/?trk=x">
      <img alt="" src="/li.svg"></a>
    <a href="https://twitter.com/priyaraman">
      <img alt="" src="/x.svg"></a>
  </div>
  <div class="card">
    <h3>Arjun Mehta</h3>
    <p>Chief Technology Officer</p>
    <a href="https://x.com/arjunm">Arjun Mehta</a>
  </div>
  <div class="card">
    <h3>Kavya Iyer</h3>
    <p>Senior Software Engineer</p>
    <a href="https://www.linkedin.com/in/kavya-iyer/">LinkedIn</a>
  </div>
  <div class="card">
    <p>Founder</p>
    <a href="https://www.linkedin.com/in/someone-unnamed/">LinkedIn</a>
  </div>
</section>
"""


def test_cofounder_ceo_card_yields_a_founder_with_its_linkedin_url() -> None:
    facts = extract_site_facts(_TEAM, "https://acme.in/team")
    priya = [p for p in facts.people if p.name == "Priya Raman"]
    assert len(priya) == 1
    person = priya[0]
    assert person.kind == "founder"
    assert person.title == "Co-founder & CEO"
    assert person.linkedin_url == "https://www.linkedin.com/in/priya-raman-123/"
    assert person.x_url == "https://x.com/priyaraman"
    assert person.source_url == "https://acme.in/team"
    assert person.source_kind == "site"


def test_chief_without_founder_is_a_leader() -> None:
    facts = extract_site_facts(_TEAM, "https://acme.in/team")
    arjun = [p for p in facts.people if p.name == "Arjun Mehta"]
    assert len(arjun) == 1
    assert arjun[0].kind == "leader"
    assert arjun[0].x_url == "https://x.com/arjunm"
    assert arjun[0].linkedin_url is None


def test_team_member_without_a_role_word_is_not_a_person() -> None:
    facts = extract_site_facts(_TEAM, "https://acme.in/team")
    assert all(p.name != "Kavya Iyer" for p in facts.people)


def test_role_card_with_no_name_is_skipped_never_guessed() -> None:
    facts = extract_site_facts(_TEAM, "https://acme.in/team")
    assert {p.name for p in facts.people} == {"Priya Raman", "Arjun Mehta"}
    assert all("someone-unnamed" not in (p.linkedin_url or "") for p in facts.people)


def test_plain_team_member_link_alone_yields_no_person() -> None:
    html = '<ul><li><a href="https://www.linkedin.com/in/rahul-k/">Rahul K</a></li></ul>'
    assert extract_site_facts(html, "https://a.in/").people == ()


def test_role_word_far_away_in_a_big_container_does_not_attach() -> None:
    filler = "<p>" + "We build things for customers across India. " * 20 + "</p>"
    html = (
        "<div><h2>Our CEO says hello</h2>"
        + filler
        + '<a href="https://www.linkedin.com/in/random-person/">Random Person</a></div>'
    )
    assert extract_site_facts(html, "https://a.in/").people == ()


# ── read_site ───────────────────────────────────────────────────────────────

_HOME = (
    "<html><head><meta name='description' content='Acme home.'></head><body>"
    "<p>Payroll.</p><a href='/contact'>Contact</a></body></html>"
)
_CONTACT = "<html><body><address>12 Residency Road, Bengaluru 560025</address></body></html>"

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _html(body: str | bytes) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=body)


def _redirect(location: str, status: int = 301) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


_PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Every host resolves to a public address unless a test says otherwise."""
    answers: dict[str, list[str]] = {}

    async def resolve(host: str, port: int) -> list[str]:
        return answers.get(host, [_PUBLIC_IP])

    monkeypatch.setattr(website, "_resolve", resolve, raising=False)
    return answers


async def test_read_site_returns_none_when_robots_disallows_root() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return _html(_HOME)

    async with _client(handler) as client:
        assert await read_site(client, "https://acme.in/") is None
    assert seen == ["/robots.txt"]


async def test_read_site_respects_a_rule_for_our_agent_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: TalentRadar\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
            )
        return _html(_HOME)

    async with _client(handler) as client:
        assert await read_site(client, "https://acme.in/") is None


async def test_read_site_404_robots_allows_and_reads_contact_for_address() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="not found")
        if request.url.path == "/":
            return _html(_HOME)
        if request.url.path == "/contact":
            return _html(_CONTACT)
        return httpx.Response(500)

    async with _client(handler) as client:
        facts = await read_site(client, "https://acme.in/")

    assert isinstance(facts, SiteFacts)
    assert facts.description == "Acme home."
    assert facts.pincode == "560025"
    assert facts.address == "12 Residency Road, Bengaluru 560025"
    assert [r.url.path for r in seen] == ["/robots.txt", "/", "/contact"]
    assert all(r.headers["user-agent"] == USER_AGENT for r in seen)


async def test_read_site_skips_contact_page_when_home_has_an_address() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return _html(_FOOTER)

    async with _client(handler) as client:
        facts = await read_site(client, "https://acme.in/")

    assert facts is not None and facts.pincode == "560034"
    # The about page is read for people and addresses; the contact page is not needed.
    assert seen == ["/robots.txt", "/", "/about"]


async def test_read_site_contact_page_disallowed_by_robots_is_not_fetched() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /contact\n")
        return _html(_HOME)

    async with _client(handler) as client:
        facts = await read_site(client, "https://acme.in/")

    assert facts is not None and facts.address is None
    assert seen == ["/robots.txt", "/"]


async def test_read_site_robots_server_error_means_do_not_fetch() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(503)
        return _html(_HOME)

    async with _client(handler) as client:
        assert await read_site(client, "https://acme.in/") is None
    assert seen == ["/robots.txt"]


async def test_read_site_non_html_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7")

    async with _client(handler) as client:
        assert await read_site(client, "https://acme.in/") is None


async def test_read_site_error_status_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(500, headers={"content-type": "text/html"}, content=b"oops")

    async with _client(handler) as client:
        assert await read_site(client, "https://acme.in/") is None


async def test_read_site_transport_error_returns_none_never_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with _client(handler) as client:
        assert await read_site(client, "https://acme.in/") is None


async def test_read_site_garbage_url_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _html(_HOME)

    async with _client(handler) as client:
        assert await read_site(client, "not a url") is None


async def test_read_site_body_over_300kb_is_truncated_not_fully_read() -> None:
    chunk = b"<p>" + b"x" * 8_189 + b"</p>"  # 8 KiB
    total_chunks = 200  # 1.6 MB on offer
    sent = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal sent
        yield b"<html><head><meta name='description' content='Big.'></head><body>"
        for _ in range(total_chunks):
            sent += len(chunk)
            yield chunk
        sent += 100
        yield b"<p>Tail, Bengaluru 560001</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=body())

    async with _client(handler) as client:
        facts = await read_site(client, "https://acme.in/")

    assert facts is not None
    assert facts.description == "Big."
    assert facts.address is None  # the tail past the cap was never read
    assert sent <= MAX_BODY_BYTES + 2 * len(chunk)
    assert sent < total_chunks * len(chunk)


# ── read_site: redirects and refused hosts ──────────────────────────────────


async def test_redirect_to_another_host_obeys_that_hosts_robots() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "acme.in":
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return _redirect("https://acme-global.com/")
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return _html(_HOME)

    async with _client(handler) as client:
        assert await read_site(client, "https://acme.in/") is None
    assert seen == [
        "https://acme.in/robots.txt",
        "https://acme.in/",
        "https://acme-global.com/robots.txt",
    ]


async def test_redirect_to_linkedin_is_refused_before_any_linkedin_request() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "acme.in":
            return _redirect("https://www.linkedin.com/company/x")
        return _html(_HOME)

    async with _client(handler) as client:
        assert await read_site(client, "https://acme.in/") is None
    assert seen == ["https://acme.in/robots.txt", "https://acme.in/"]


async def test_robots_redirect_to_linkedin_is_refused() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "acme.in":
            return _redirect("https://www.linkedin.com/robots.txt")
        return httpx.Response(404)

    async with _client(handler) as client:
        assert await read_site(client, "https://acme.in/") is None
    assert seen == ["https://acme.in/robots.txt"]


async def test_contact_page_redirect_to_linkedin_keeps_the_homepage_only() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/contact":
            return _redirect("https://in.linkedin.com/company/acme")
        return _html(_HOME)

    async with _client(handler) as client:
        facts = await read_site(client, "https://acme.in/")
    assert facts is not None and facts.description == "Acme home."
    assert facts.address is None
    assert seen == ["https://acme.in/robots.txt", "https://acme.in/", "https://acme.in/contact"]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com",
        "https://linkedin.com/company/acme",
        "https://lnkd.in/abc123",
        "https://sites.google.com/view/acme",
        "https://twitter.com/acme",
        "https://github.com/acme",
        "https://www.crunchbase.com/organization/acme",
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://169.254.169.254/",
        "http://[::1]/",
        "http://192.168.1.1/",
        "http://224.0.0.1/",
        "http://localhost/",
        "http://intranet.local/",
        "http://metadata.google.internal/",
        "http://example.com:8080/",
        "https://example.com:8443/",
        "ftp://example.com/",
        "http://user@example.com/",
    ],
)
async def test_refused_urls_make_no_request_at_all(url: str) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _html(_HOME)

    async with _client(handler) as client:
        assert await read_site(client, url) is None
    assert seen == []


async def test_host_resolving_to_a_private_address_is_refused(
    _public_dns: dict[str, list[str]],
) -> None:
    _public_dns["sneaky.in"] = ["10.1.2.3"]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _html(_HOME)

    async with _client(handler) as client:
        assert await read_site(client, "https://sneaky.in/") is None
    assert seen == []


async def test_redirect_to_a_host_resolving_privately_is_refused(
    _public_dns: dict[str, list[str]],
) -> None:
    _public_dns["internal.acme.in"] = ["169.254.169.254"]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return _redirect("http://internal.acme.in/latest/meta-data/")

    async with _client(handler) as client:
        assert await read_site(client, "https://acme.in/") is None
    assert seen == ["https://acme.in/robots.txt", "https://acme.in/"]


async def test_redirect_to_an_ip_literal_is_refused() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return _redirect("http://169.254.169.254/latest/meta-data/")

    async with _client(handler) as client:
        assert await read_site(client, "https://acme.in/") is None
    assert seen == ["https://acme.in/robots.txt", "https://acme.in/"]


async def test_more_than_max_redirects_is_none() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        hop = int(request.url.path.strip("/") or 0)
        return _redirect(f"/{hop + 1}", status=302)

    async with _client(handler) as client:
        assert await read_site(client, "https://acme.in/") is None
    assert MAX_REDIRECTS == 3
    assert seen == ["/robots.txt", "/", "/1", "/2", "/3"]


async def test_exactly_max_redirects_is_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        hop = int(request.url.path.strip("/") or 0)
        if hop < MAX_REDIRECTS:
            return _redirect(f"/{hop + 1}", status=302)
        return _html(_FOOTER)

    async with _client(handler) as client:
        facts = await read_site(client, "https://acme.in/")
    assert facts is not None and facts.pincode == "560034"


async def test_redirect_hop_disallowed_by_robots_is_not_requested() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        if request.url.path == "/":
            return _redirect("/private/home")
        return _html(_HOME)

    async with _client(handler) as client:
        assert await read_site(client, "https://acme.in/") is None
    assert seen == ["/robots.txt", "/"]


async def test_http_to_https_redirect_reads_the_site_and_its_contact_page() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.scheme == "http":
            return _redirect(str(request.url.copy_with(scheme="https")))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/contact":
            return _html(_CONTACT)
        return _html(_HOME)

    async with _client(handler) as client:
        facts = await read_site(client, "http://acme.in/")

    assert facts is not None and facts.pincode == "560025"
    assert seen == [
        "http://acme.in/robots.txt",
        "https://acme.in/robots.txt",
        "http://acme.in/",
        "https://acme.in/",
        "https://acme.in/contact",
    ]


async def test_www_redirect_uses_the_final_hosts_robots_for_the_contact_page() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "acme.in":
            if request.url.path == "/robots.txt":
                return httpx.Response(404)  # the bare host allows everything
            return _redirect("https://www.acme.in/")
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /contact\n")
        if request.url.path == "/contact":
            return _html(_CONTACT)
        return _html(_HOME)

    async with _client(handler) as client:
        facts = await read_site(client, "https://acme.in/")

    assert facts is not None and facts.description == "Acme home."
    assert facts.address is None
    assert seen == [
        "https://acme.in/robots.txt",
        "https://acme.in/",
        "https://www.acme.in/robots.txt",
        "https://www.acme.in/",
    ]


async def test_robots_is_fetched_once_per_host() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/":
            return _redirect("/home")
        if request.url.path == "/contact":
            return _html(_CONTACT)
        return _html(_HOME)

    async with _client(handler) as client:
        facts = await read_site(client, "https://acme.in/")

    assert facts is not None and facts.pincode == "560025"
    assert seen == ["/robots.txt", "/", "/home", "/contact"]

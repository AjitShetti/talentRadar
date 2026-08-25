"""
tests/test_company_intel.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for the Company Intelligence directory.

The most important thing covered here is the *anti-fabrication* invariant: the
panel shows recruiter names and email addresses, and none of them may be
invented. Two things enforce that and both are tested:

* the seeded catalogue must not contain a single email address or person's name
* the sourced web lookup must only surface addresses that literally appear in
  fetched page text, never a guess assembled from a naming pattern

Everything here runs without a database or a network.
"""

from __future__ import annotations

import json

import pytest

from scripts.seed_companies import CATALOGUE_DIR, load_catalogue
from services.companies import TIER_LABELS, TIER_ORDER, _tier_rank
from services.company_contacts import (
    VALID_KINDS,
    _classify,
    _extract,
    _root_domain,
    is_broker_source,
    is_hiring_page,
)

REQUIRED_FIELDS = ("name", "domain", "website_url", "tier", "industry",
                   "office_cities", "description", "tech_stack")


@pytest.fixture(scope="module")
def catalogue() -> list[dict]:
    return load_catalogue("bengaluru")


# ─────────────────────────────────────────────────────────────────────────────
# Catalogue integrity
# ─────────────────────────────────────────────────────────────────────────────

class TestCatalogue:
    def test_catalogue_is_not_empty(self, catalogue):
        assert len(catalogue) > 50, "the Bengaluru directory should be substantial"

    def test_every_record_has_the_required_fields(self, catalogue):
        for record in catalogue:
            missing = [f for f in REQUIRED_FIELDS if not record.get(f)]
            assert not missing, f"{record.get('name')} is missing {missing}"

    def test_domains_are_unique(self, catalogue):
        domains = [r["domain"] for r in catalogue]
        dupes = {d for d in domains if domains.count(d) > 1}
        assert not dupes, f"duplicate domains would collide on upsert: {dupes}"

    def test_every_tier_is_known(self, catalogue):
        unknown = {r["tier"] for r in catalogue} - set(TIER_ORDER)
        assert not unknown, f"tiers with no label or sort position: {unknown}"

    def test_every_company_lists_the_city_it_is_filed_under(self, catalogue):
        for record in catalogue:
            assert "Bengaluru" in record["office_cities"], (
                f"{record['name']} is in bengaluru.json but does not list a Bengaluru office"
            )

    def test_github_org_is_a_slug_not_a_url(self, catalogue):
        # The slug is resolved live against the GitHub API; a URL would 404 there
        # and silently hide the open-source panel.
        for record in catalogue:
            org = record.get("github_org")
            if org:
                assert "/" not in org and "github.com" not in org, (
                    f"{record['name']} has a URL in github_org: {org!r}"
                )

    def test_founded_years_are_plausible(self, catalogue):
        for record in catalogue:
            year = record.get("founded_year")
            if year is not None:
                assert 1700 < year <= 2026, f"{record['name']} founded_year={year}"


class TestCatalogueCarriesNoFabricatedContacts:
    """
    The catalogue seeds *published channels only*. A person's name or an email
    address in here would be a claim about a real individual that nobody
    verified, so both are banned outright at the data layer.
    """

    def test_no_seeded_contact_has_an_email(self, catalogue):
        offenders = [
            (r["name"], c["email"])
            for r in catalogue for c in (r.get("contacts") or [])
            if c.get("email")
        ]
        assert not offenders, f"catalogue must not seed email addresses: {offenders}"

    def test_no_seeded_contact_names_a_person(self, catalogue):
        offenders = [
            (r["name"], c["name"])
            for r in catalogue for c in (r.get("contacts") or [])
            if c.get("name")
        ]
        assert not offenders, f"catalogue must not seed personal names: {offenders}"

    def test_every_seeded_contact_cites_a_source(self, catalogue):
        for record in catalogue:
            for contact in record.get("contacts") or []:
                assert contact.get("source_url"), (
                    f"{record['name']} has a contact with no source_url"
                )
                assert contact.get("kind") in VALID_KINDS

    def test_no_seeded_contact_claims_to_be_verified(self, catalogue):
        for record in catalogue:
            for contact in record.get("contacts") or []:
                assert not contact.get("verified", False), (
                    f"{record['name']}: only a human may mark a contact verified"
                )

    def test_catalogue_file_contains_no_email_addresses_at_all(self):
        raw = (CATALOGUE_DIR / "bengaluru.json").read_text(encoding="utf-8")
        payload = json.loads(raw)
        assert payload["companies"], "catalogue parsed but is empty"
        assert "@" not in raw.replace("@claude", ""), (
            "an '@' in the catalogue usually means an email address slipped in"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Directory ordering
# ─────────────────────────────────────────────────────────────────────────────

class TestTierOrdering:
    def test_every_tier_has_a_display_label(self):
        assert set(TIER_ORDER) <= set(TIER_LABELS)

    def test_big_tech_sorts_ahead_of_services(self):
        assert _tier_rank("big_tech") < _tier_rank("services")

    def test_unknown_tier_sorts_last(self):
        assert _tier_rank("something_else") == len(TIER_ORDER)
        assert _tier_rank(None) == len(TIER_ORDER)


# ─────────────────────────────────────────────────────────────────────────────
# Contact extraction — the anti-fabrication rules
# ─────────────────────────────────────────────────────────────────────────────

class TestRootDomain:
    @pytest.mark.parametrize("value,expected", [
        ("https://www.razorpay.com/jobs", "razorpay.com"),
        ("razorpay.com", "razorpay.com"),
        ("https://careers.zerodha.com", "careers.zerodha.com"),
        ("", ""),
        (None, ""),
    ])
    def test_normalises_to_a_bare_host(self, value, expected):
        assert _root_domain(value) == expected

    def test_does_not_eat_a_leading_w_from_the_name(self):
        # lstrip("www.") would turn this into "ework.com".
        assert _root_domain("https://wework.com") == "wework.com"


class TestClassify:
    """
    Two ways an address qualifies and no others: a talent local part, or a
    company-domain address found on a page that is actually about hiring.
    """

    @pytest.mark.parametrize("email", [
        "careers@razorpay.com", "jobs@acme.com", "hiring@acme.com",
        "talent.acquisition@acme.com", "hr@acme.com", "campus@acme.com",
        "recruitment@acme.com", "internships@acme.com",
    ])
    def test_talent_localparts_are_careers_inboxes(self, email):
        assert _classify(email, "acme.com") == "careers_inbox"

    def test_talent_inbox_needs_no_hiring_context(self):
        # "careers@" announces itself; where it was found does not change that.
        assert _classify("careers@acme.com", "acme.com", hiring_context=False) == "careers_inbox"

    def test_personal_company_address_on_a_hiring_page_is_a_recruiter(self):
        assert _classify("priya.n@acme.com", "acme.com", hiring_context=True) == "recruiter"

    def test_personal_company_address_off_a_hiring_page_is_rejected(self):
        # This is the grievance-officer / PR-lead case: a real person on a real
        # company page who has nothing to do with hiring.
        assert _classify("priya.n@acme.com", "acme.com", hiring_context=False) is None

    def test_subdomain_of_the_company_counts(self):
        assert _classify("ravi@careers.acme.com", "acme.com", hiring_context=True) == "recruiter"

    @pytest.mark.parametrize("email", [
        "no-reply@acme.com", "noreply@acme.com", "privacy@acme.com",
        "legal@acme.com", "press@acme.com", "support@acme.com",
        "sales@acme.com", "security@acme.com", "billing@acme.com",
    ])
    def test_non_talent_function_addresses_are_rejected(self, email):
        assert _classify(email, "acme.com", hiring_context=True) is None

    @pytest.mark.parametrize("email", [
        "usa-sales@acme.com", "nodal-officer@acme.com", "pr-storytellers@acme.com",
        "customer.care@acme.com", "grievance.redressal@acme.com",
    ])
    def test_noise_is_matched_on_whole_tokens_not_just_prefixes(self, email):
        # An earlier prefix-only rule let "usa-sales@" and "nodal-officer@"
        # through and labelled them recruiters.
        assert _classify(email, "acme.com", hiring_context=True) is None

    @pytest.mark.parametrize("email", [
        "first.last@acme.com", "last.first@acme.com", "john.doe@acme.com",
        "jane.doe@acme.com", "doe.john@acme.com", "first.l@acme.com",
        "john.d@acme.com", "first@acme.com", "john@acme.com",
        "firstname@acme.com", "yourname@acme.com",
    ])
    def test_email_format_placeholders_are_rejected(self, email):
        # Contact brokers publish these as the company's email *pattern*. They
        # are precisely what a naming-pattern guess produces, so they must never
        # be presented as a real person's address.
        assert _classify(email, "acme.com", hiring_context=True) is None

    def test_random_third_party_address_is_rejected(self):
        # A personal address on someone else's domain is not this company's
        # recruiter just because it appeared on the same page.
        assert _classify("someone@gmail.com", "acme.com", hiring_context=True) is None

    def test_placeholder_domains_are_rejected(self):
        assert _classify("careers@example.com", "acme.com") is None
        assert _classify("careers@yourcompany.com", "acme.com") is None


class TestBrokerAndHiringPageGates:
    @pytest.mark.parametrize("url", [
        "https://leadiq.com/c/razorpay/5a1d98b9",
        "https://contactout.com/Prakhar-Gupta-394264521",
        "https://www.rocketreach.co/acme-email-format",
        "https://apollo.io/companies/acme",
    ])
    def test_contact_brokers_are_refused_as_sources(self, url):
        assert is_broker_source(url) is True

    @pytest.mark.parametrize("url", [
        "https://razorpay.com/jobs/",
        "https://acme.com/about",
        None,
        "",
    ])
    def test_ordinary_pages_are_allowed_as_sources(self, url):
        assert is_broker_source(url) is False

    @pytest.mark.parametrize("url", [
        "https://acme.com/careers",
        "https://acme.com/jobs/backend-engineer",
        "https://acme.com/join-us",
        "https://acme.com/life-at-acme",
        "https://boards.greenhouse.io/acme",
    ])
    def test_hiring_pages_are_recognised(self, url):
        assert is_hiring_page(url) is True

    @pytest.mark.parametrize("url", [
        "https://acme.com/privacy",
        "https://acme.com/grievance-redressal",
        "https://acme.com/newsroom/contact-us",
    ])
    def test_non_hiring_pages_are_not(self, url):
        assert is_hiring_page(url) is False

    def test_title_can_establish_hiring_context(self):
        assert is_hiring_page("https://acme.com/p/1234", "We are hiring engineers") is True


class TestExtract:
    HIRING = {"hiring_context": True}

    def test_pulls_a_published_careers_inbox_out_of_page_text(self):
        page = "Write to us at careers@acme.com for open roles."
        assert _extract(page, "acme.com") == [("careers@acme.com", "careers_inbox")]

    def test_lowercases_and_strips_trailing_punctuation(self):
        page = "Contact Careers@Acme.com."
        assert _extract(page, "acme.com") == [("careers@acme.com", "careers_inbox")]

    def test_returns_nothing_when_the_page_publishes_nothing(self):
        page = "Acme is hiring engineers in Bengaluru. Apply on our careers page."
        assert _extract(page, "acme.com", **self.HIRING) == []

    def test_never_invents_an_address_from_a_name_on_the_page(self):
        # The single most important case: a page naming a recruiter but printing
        # no address must yield no address. Guessing "priya.sharma@acme.com"
        # here would be a confident, plausible, unverified claim about a person.
        page = "Priya Sharma, Talent Partner at Acme, is hiring for the platform team."
        assert _extract(page, "acme.com", **self.HIRING) == []

    def test_a_real_recruiter_address_on_a_hiring_page_is_kept(self):
        page = "For this role, reach Priya at priya.sharma@acme.com."
        assert _extract(page, "acme.com", **self.HIRING) == [
            ("priya.sharma@acme.com", "recruiter")
        ]

    def test_the_same_address_off_a_hiring_page_is_dropped(self):
        page = "For this role, reach Priya at priya.sharma@acme.com."
        assert _extract(page, "acme.com", hiring_context=False) == []

    def test_filters_noise_but_keeps_the_real_contact(self):
        page = "no-reply@acme.com | careers@acme.com | legal@acme.com"
        assert _extract(page, "acme.com", **self.HIRING) == [
            ("careers@acme.com", "careers_inbox")
        ]

    def test_a_broker_style_pattern_block_yields_nothing(self):
        # Verbatim shape of what LeadIQ prints for a company.
        page = ("Acme email format: first.last@acme.com, first@acme.com, "
                "john.doe@acme.com, f.last@acme.com")
        assert _extract(page, "acme.com", **self.HIRING) == []

    def test_tolerates_empty_input(self):
        assert _extract("", "acme.com") == []
        assert _extract(None, "acme.com") == []

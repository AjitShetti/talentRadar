"""Published addresses read from a company's own pages and from GitHub profiles.

Fictional people at fictional domains only. Domain filtering (is the address on the
company's own domain?) happens where the address is stored, not here.
"""

from __future__ import annotations

from services.company_map.people import parse_github_user
from services.company_map.types import PersonCandidate
from services.company_map.website import extract_site_facts

BASE = "https://fictional.test/team"

TEAM = """
<html><body>
  <main>
    <div class="person">
      <h3>Asha Example</h3><p>Co-founder &amp; CEO</p>
      <a href="https://www.linkedin.com/in/asha-example">LinkedIn</a>
      <a href="mailto:asha@fictional.test?subject=Hi">Email</a>
    </div>
    <div class="person">
      <h3>Ravi Sample</h3><p>CTO</p>
      <a href="https://www.linkedin.com/in/ravi-sample">LinkedIn</a>
      <span>ravi.sample@fictional.test</span>
    </div>
    <div class="person">
      <h3>Meera Dummy</h3><p>Chief Design Officer</p>
      <a href="https://www.linkedin.com/in/meera-dummy">LinkedIn</a>
      <span>meera [at] fictional.test</span>
    </div>
  </main>
  <footer>
    <a href="mailto:careers@fictional.test">Join us</a>
    <a href="mailto:hello@fictional.test">Say hello</a>
    <p>Write to noreply@fictional.test for nothing.</p>
    <p>Hiring: Talent@Fictional.test</p>
  </footer>
</body></html>
"""


def _people_by_name() -> dict[str, PersonCandidate]:
    facts = extract_site_facts(TEAM, BASE)
    return {p.name: p for p in facts.people}


def test_mailto_in_person_card_attaches_to_person() -> None:
    asha = _people_by_name()["Asha Example"]
    assert asha.email == "asha@fictional.test"
    assert asha.email_source_url == BASE


def test_visible_text_address_in_card_attaches() -> None:
    ravi = _people_by_name()["Ravi Sample"]
    assert ravi.email == "ravi.sample@fictional.test"


def test_obfuscated_at_not_parsed() -> None:
    meera = _people_by_name()["Meera Dummy"]
    assert meera.email is None


def test_hiring_inbox_collected_as_hiring() -> None:
    facts = extract_site_facts(TEAM, BASE)
    addresses = [e.address for e in facts.emails]
    assert addresses == ["careers@fictional.test", "talent@fictional.test"]
    assert all(e.source_url == BASE for e in facts.emails)


def test_drop_prefixes_and_person_addresses_not_in_site_emails() -> None:
    addresses = {e.address for e in extract_site_facts(TEAM, BASE).emails}
    assert "hello@fictional.test" not in addresses
    assert "noreply@fictional.test" not in addresses
    assert "asha@fictional.test" not in addresses


def test_github_user_email_read() -> None:
    person = parse_github_user({"login": "pk", "name": "P K", "email": "pk@fictional.test"})
    assert person.email == "pk@fictional.test"
    assert person.email_source_url == "https://github.com/pk"


def test_github_user_without_email_is_none() -> None:
    person = parse_github_user({"login": "pk", "email": None})
    assert person.email is None
    assert person.email_source_url is None


def test_github_user_with_malformed_email_is_none() -> None:
    person = parse_github_user({"login": "pk", "email": "not an address"})
    assert person.email is None

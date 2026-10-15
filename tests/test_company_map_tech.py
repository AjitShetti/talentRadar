"""
tests/test_company_map_tech.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The company map lists tech employers only (decision 2026-10-15). The rule is
``domain.company_taxonomy.is_tech``: a positive signal from tech words, vetoed
by a clear non-tech word unless a strong tech word is also present.

The parametrised sample is real, from the Wikidata import of 2026-10-15.
"""

from __future__ import annotations

import pytest

from domain.company_taxonomy import is_tech

NOT_TECH = [
    ("Christ University", "Private autonomous university in Bangalore"),
    ("Manipal Hospitals", "healthcare network in India"),
    ("Shriram Properties", "Indian real estate company"),
    ("Vijaya Bank", "former Indian banking group"),
    ("United Breweries Group", "Indian alcohol and lifestyle business group"),
    ("FLYeasy", "Indian airline"),
    ("Novo Nordisk (India)", "Pharmaceutical company in Bengaluru, India"),
    ("California Burrito", "A fast casual Mexican restaurant chain"),
    ("Colormyles", "Indian home utility brand known for innovative mosquito nets"),
    ("Indian Telephone Industries Limited", "company"),
]

TECH = [
    ("Vxceed", "Indian enterprise software company"),
    ("HealthifyMe", "Indian digital health and wellness company"),
    ("Sarvam AI", "Indian artificial intelligence company"),
    ("Infosys", "Indian multinational technology company"),
    ("Supertails", "Indian pet care company (e-commerce, pet pharmacy…)"),
    ("Yulu", "Indian micromobility company"),
    ("Mesrai", "AI code review platform for engineering teams"),
    ("Cuemath", "educational company based in India"),
    ("Wisemonk", "employer of record company in India"),
    ("EDS Technologies", "largest CAD/CAM/CAE reseller in India"),
    ("Theranautilus", "deep-tech, nanotechnology and healthcare"),
    ("MyTravaly", "Global travel technology company"),
]


@pytest.mark.parametrize(("name", "description"), NOT_TECH)
def test_a_non_tech_employer_is_not_tech(name: str, description: str):
    assert is_tech(name=name, description=description, industry_labels=(), sector=None) is False


@pytest.mark.parametrize(("name", "description"), TECH)
def test_a_tech_employer_is_tech(name: str, description: str):
    assert is_tech(name=name, description=description, industry_labels=(), sector=None) is True


class TestVeto:
    def test_a_bank_that_calls_itself_digital_is_tech(self):
        assert is_tech(
            name="Acme Bank", description="Digital-first bank", industry_labels=(), sector=None
        )

    def test_a_plain_bank_is_not(self):
        assert not is_tech(
            name="Acme Bank", description="Indian bank", industry_labels=(), sector="fintech"
        )

    def test_a_pharma_company_with_a_tech_platform_is_tech(self):
        assert is_tech(
            name="Acme",
            description="Pharmaceutical company with an online pharmacy platform",
            industry_labels=(),
            sector=None,
        )

    def test_a_technical_university_is_not_tech(self):
        # "technical" is not a tech word: it names a kind of school.
        assert not is_tech(
            name="Acme Technical University",
            description="Technical university in Bengaluru",
            industry_labels=(),
            sector=None,
        )

    def test_an_institute_of_technology_is_not_tech(self):
        assert not is_tech(
            name="Acme Institute of Technology",
            description="Engineering college in Bengaluru",
            industry_labels=(),
            sector=None,
        )


class TestOtherSignals:
    def test_a_wikidata_industry_label_counts(self):
        assert is_tech(
            name="Acme",
            description="company",
            industry_labels=("software industry",),
            sector=None,
        )

    def test_a_software_sector_is_a_weak_yes(self):
        assert is_tech(name="Acme", description=None, industry_labels=(), sector="saas")

    def test_a_sector_cannot_outvote_a_non_tech_word(self):
        # sector_from_text reads "health" in "healthcare network" and "bank" in
        # "banking group"; the sector alone must not list a hospital or a bank.
        assert not is_tech(
            name="Manipal Hospitals",
            description="healthcare network in India",
            industry_labels=(),
            sector="healthtech",
        )
        assert not is_tech(
            name="Vijaya Bank",
            description="former Indian banking group",
            industry_labels=(),
            sector="fintech",
        )

    def test_a_tech_name_is_a_weak_yes(self):
        assert is_tech(name="Acme Technologies", description=None, industry_labels=(), sector=None)

    def test_nothing_to_go_on_is_not_tech(self):
        assert not is_tech(name="Acme", description=None, industry_labels=(), sector=None)
        assert not is_tech(name="Acme", description="company", industry_labels=(), sector="other")

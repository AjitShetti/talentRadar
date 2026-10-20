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


class TestEcosystemAndTechSources:
    """Ruling 2026-10-15: VCs and accelerators stay on the map (it has a VC type),
    and a company Y Combinator or GitHub put there is tech by its source."""

    @pytest.mark.parametrize(
        "description",
        [
            "Venture capital firm investing in early-stage Indian companies",
            "early stage VC fund",
            "seed accelerator programme",
        ],
    )
    def test_investors_and_accelerators_are_tech_ecosystem(self, description: str):
        assert is_tech(name="Axilor Ventures", description=description, sector=None)

    @pytest.mark.parametrize("source", ["yc", "github_org"])
    def test_a_row_from_a_tech_by_nature_source_is_tech_with_no_text(self, source: str):
        from services.company_map.tech import classify
        from storage.models import Company

        row = Company(name="Quiet Co", extra_metadata={"provenance": {"name": source}})
        assert classify(row) is True

    def test_a_directory_description_is_read(self):
        from services.company_map.tech import classify
        from storage.models import Company

        row = Company(
            name="Quiet Co",
            extra_metadata={
                "provenance": {"name": "hiring_list"},
                "yc_description": "Indian payments software",
            },
        )
        assert classify(row) is True

    def test_a_hiring_list_row_alone_is_not_enough(self):
        from services.company_map.tech import classify
        from storage.models import Company

        row = Company(
            name="Third Wave Coffee", extra_metadata={"provenance": {"name": "hiring_list"}}
        )
        assert classify(row) is False


class TestHomepageTextIsWeak:
    """Review C1 (2026-10-15): every homepage says "online", "app" or "digital".
    A homepage can make a silent row tech only with a specific tech word, and
    never outvotes what Wikidata, a curator or the name says."""

    @pytest.mark.parametrize(
        ("name", "description", "site_text"),
        [
            ("Narayana Health", "Indian hospital chain", "Book a doctor appointment online"),
            ("Christ University", "Private autonomous university", "Apply online for admissions"),
            ("Canara Bank", "Indian public sector bank", "Internet banking and mobile app"),
            ("Toit", "brewery in Bangalore", "Order online on our app"),
            ("IISc", "public research university", "science and technology research with AI"),
        ],
    )
    def test_a_homepage_never_outvotes_a_non_tech_source(self, name, description, site_text):
        assert not is_tech(name=name, description=description, sector=None, site_text=site_text)

    @pytest.mark.parametrize(
        "site_text",
        [
            "Shop online for backpacks and jackets",
            "India's favourite digital gold app",
            "Order on our website or app",
        ],
    )
    def test_generic_web_words_on_a_homepage_are_not_enough(self, site_text):
        assert not is_tech(name="Wildcraft", description=None, sector=None, site_text=site_text)

    @pytest.mark.parametrize(
        "site_text",
        ["An AI platform for lawyers", "Payments SaaS for merchants", "Cloud cost automation"],
    )
    def test_a_specific_tech_word_on_a_homepage_is_enough(self, site_text):
        assert is_tech(name="Quiet Co", description=None, sector=None, site_text=site_text)

    def test_a_homepage_cannot_override_a_non_tech_name(self):
        assert not is_tech(
            name="Third Wave Coffee",
            description=None,
            sector=None,
            site_text="Order coffee online with our AI-powered app",
        )


class TestTechSourcesOnlyForTheirOwnRows:
    """Review I3: a GitHub org merged into a bank's row does not make the bank tech;
    a directory-created row is tech unless its own text says otherwise."""

    def _row(self, provenance: dict, **metadata):
        from storage.models import Company

        return Company(
            name=metadata.pop("name", "Quiet Co"),
            extra_metadata={"provenance": provenance, **metadata},
        )

    def test_a_github_field_on_a_wikidata_bank_is_not_tech(self):
        from services.company_map.tech import classify

        row = self._row(
            {"name": "wikidata", "github_org": "github_org"},
            name="Canara Bank",
            wikidata_description="Indian public sector bank",
        )
        assert classify(row) is False

    def test_a_github_created_foundation_is_not_tech(self):
        from services.company_map.tech import classify

        row = self._row(
            {"name": "github_org"},
            github_org_description="Bengaluru community foundation for open learning",
        )
        assert classify(row) is False

    def test_a_yc_created_row_with_a_bland_line_is_tech(self):
        from services.company_map.tech import classify

        row = self._row({"name": "yc"}, yc_description="Helping Indian families save")
        assert classify(row) is True

    def test_a_site_line_never_makes_a_hospital_tech_and_the_verdict_can_go_back(self):
        from services.company_map.tech import apply_tech
        from storage.models import Company

        row = Company(
            name="Narayana Health",
            is_tech=True,
            summary="Book online",
            summary_source="site",
            extra_metadata={
                "provenance": {"name": "wikidata"},
                "wikidata_description": "Indian hospital chain",
                "site": {"description": "Book a doctor appointment online"},
            },
        )
        assert apply_tech(row) is False
        assert row.is_tech is False


@pytest.mark.parametrize(
    "description",
    [
        "ride-hailing app",
        "building foundation models for Indian languages",
        "Teaches coding to children",
        "developer tools for API testing",
    ],
)
def test_review_m1_misses_are_now_tech(description: str):
    assert is_tech(name="Quiet Co", description=description, sector=None)

"""Tests for services/llm.py's resume-structure-extraction normalizer.

Models given the exact-keys contract for extract_resume_structure() still
drift toward synonym field names ("position" for "title", "details" for
"bullets") or wrap the payload differently. _normalize_resume_document()
is the defensive layer that catches that drift so the editor doesn't
silently render blank fields."""

from services.llm import _normalize_resume_document


def test_normalizes_experience_synonym_keys() -> None:
    raw = {
        "schema_version": 1,
        "personal": {"full_name": "John Smith", "email": "john@example.com"},
        "sections": [
            {
                "id": "exp1", "type": "experience", "title": "Experience", "visible": True, "order": 1,
                "items": [{
                    "position": "Senior PM", "company": "Widget Co", "date_range": "2019-Present",
                    "details": ["Launched 3 major features used by 2M users", "Grew retention by 18%"],
                }],
            },
        ],
    }
    doc = _normalize_resume_document(raw)

    exp_section = next(s for s in doc["sections"] if s["type"] == "experience")
    item = exp_section["items"][0]
    assert item["title"] == "Senior PM"
    assert item["company"] == "Widget Co"
    assert item["dates"] == "2019-Present"
    assert item["bullets"] == ["Launched 3 major features used by 2M users", "Grew retention by 18%"]


def test_normalizes_education_synonym_keys() -> None:
    raw = {"sections": [{
        "type": "education", "items": [{"institution": "UT Austin", "degree_name": "BBA", "duration": "2011-2015"}],
    }]}
    doc = _normalize_resume_document(raw)

    edu = doc["sections"][0]["items"][0]
    assert edu["school"] == "UT Austin"
    assert edu["degree"] == "BBA"
    assert edu["dates"] == "2011-2015"


def test_normalizes_flat_skills_list_into_a_category() -> None:
    raw = {"sections": [{
        "type": "skills", "items": [{"name": "SQL"}, {"name": "Figma"}, {"name": "Roadmapping"}],
    }]}
    doc = _normalize_resume_document(raw)

    skills = doc["sections"][0]["items"]
    assert len(skills) == 1
    assert skills[0]["category"] == "Skills"
    assert skills[0]["items"] == ["SQL", "Figma", "Roadmapping"]


def test_unwraps_a_document_nested_under_a_wrapper_key() -> None:
    raw = {"resume": {"personal": {"full_name": "Ada Lovelace"}, "sections": []}}
    doc = _normalize_resume_document(raw)

    assert doc["personal"]["full_name"] == "Ada Lovelace"


def test_unwraps_a_single_item_list_payload() -> None:
    raw = [{"personal": {"full_name": "Grace Hopper"}, "sections": []}]
    doc = _normalize_resume_document(raw)

    assert doc["personal"]["full_name"] == "Grace Hopper"


def test_unrecognized_section_type_falls_back_to_custom() -> None:
    raw = {"sections": [{"type": "hobbies", "title": "Hobbies", "items": [{"heading": "Chess", "bullets": ["National rank 12"]}]}]}
    doc = _normalize_resume_document(raw)

    assert doc["sections"][0]["type"] == "custom"
    assert doc["sections"][0]["items"][0]["heading"] == "Chess"


def test_drops_sections_and_items_with_no_real_content() -> None:
    raw = {"sections": [
        {"type": "experience", "items": [{"title": "", "company": "", "bullets": []}]},
        {"type": "projects", "items": []},
    ]}
    doc = _normalize_resume_document(raw)

    assert doc["sections"] == []


def test_links_without_a_url_are_dropped() -> None:
    raw = {"personal": {"links": [{"label": "GitHub", "url": ""}, {"label": "LinkedIn", "url": "linkedin.com/in/x"}]}}
    doc = _normalize_resume_document(raw)

    assert doc["personal"]["links"] == [{"label": "LinkedIn", "url": "linkedin.com/in/x"}]

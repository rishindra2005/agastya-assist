"""Tests for Agastya Assist portal playbooks and loader."""

from pathlib import Path
import tempfile
import pytest
import yaml

from agastya.portals import Playbook, load_playbooks, match_playbook
from agastya.profile.fields import FIELDS

EXPECTED_PLAYBOOK_IDS = {
    "itr_register",
    "itr1_prefill",
    "itr_refund_status",
    "pan_new_49a",
    "pan_correction",
    "pan_aadhaar_link",
    "pan_instant_epan",
    "voter_form6",
    "voter_form8",
    "voter_search",
    "voter_epic_download",
    "mca_spice_name",
    "mca_din_dir3",
    "mca_company_master",
    "itr_fixture",
}


@pytest.fixture(scope="module")
def playbooks() -> dict[str, Playbook]:
    """Load default playbooks once for testing."""
    return load_playbooks()


def test_all_playbooks_load(playbooks: dict[str, Playbook]):
    """Verify all 15 required playbooks load and match expected IDs."""
    assert len(playbooks) >= 15
    for expected_id in EXPECTED_PLAYBOOK_IDS:
        assert expected_id in playbooks, f"Missing expected playbook: {expected_id}"
        pb = playbooks[expected_id]
        assert isinstance(pb, Playbook)
        assert pb.id == expected_id


def test_playbook_ids_unique(playbooks: dict[str, Playbook]):
    """Ensure all playbook IDs are strictly unique."""
    ids = [pb.id for pb in playbooks.values()]
    assert len(ids) == len(set(ids))


def test_playbook_structure_and_fields(playbooks: dict[str, Playbook]):
    """Verify each playbook satisfies all schema, type, and field catalog rules."""
    for pb_id, pb in playbooks.items():
        assert pb.portal.strip(), f"{pb_id}: portal is empty"
        assert pb.title_en.strip(), f"{pb_id}: title_en is empty"
        assert pb.title_hi.strip(), f"{pb_id}: title_hi is empty"
        assert pb.live_url.startswith("https://") or pb.live_url.startswith("http://"), (
            f"{pb_id}: invalid live_url '{pb.live_url}'"
        )
        assert isinstance(pb.requires_login, bool), f"{pb_id}: requires_login must be boolean"

        # Field keys MUST come from agastya.profile.fields.FIELDS
        for req_field in pb.required_fields:
            assert req_field in FIELDS, (
                f"{pb_id}: required_field '{req_field}' not in FIELDS"
            )

        for opt_field in pb.optional_fields:
            assert opt_field in FIELDS, (
                f"{pb_id}: optional_field '{opt_field}' not in FIELDS"
            )

        for hint_key, hint_vals in pb.field_hints.items():
            assert hint_key in FIELDS, (
                f"{pb_id}: field_hints key '{hint_key}' not in FIELDS"
            )
            assert isinstance(hint_vals, list)
            assert len(hint_vals) > 0, f"{pb_id}: field_hints[{hint_key}] is empty"

        # Steps: 4-12 imperative sentences
        assert 4 <= len(pb.steps) <= 12, (
            f"{pb_id}: steps count ({len(pb.steps)}) not in [4, 12]"
        )
        for step in pb.steps:
            assert isinstance(step, str) and step.strip(), f"{pb_id}: invalid step '{step}'"

        # stop_before: must list real final buttons
        assert len(pb.stop_before) >= 1, f"{pb_id}: stop_before is empty"
        for btn in pb.stop_before:
            assert isinstance(btn, str) and btn.strip()

        # keywords: must include English, Hindi, and Hinglish keywords
        assert len(pb.keywords) >= 3, f"{pb_id}: keywords list too short"
        for kw in pb.keywords:
            assert isinstance(kw, str) and kw.strip()


def test_fixture_files_exist_when_set(playbooks: dict[str, Playbook]):
    """Verify fixture files exist on disk for all playbooks defining a fixture."""
    expected_fixtures = {
        "pan_new_49a": "pan_49a.html",
        "voter_form6": "voter_form6.html",
        "mca_spice_name": "mca_din.html",
        "itr_fixture": "itr_login_and_form.html",
    }

    for pb_id, expected_filename in expected_fixtures.items():
        assert pb_id in playbooks
        pb = playbooks[pb_id]
        assert pb.fixture is not None, f"{pb_id} should specify a fixture"
        assert expected_filename in pb.fixture
        p = Path(pb.fixture)
        if not p.is_file():
            # Check relative to tests/fixtures/forms/
            p = Path("tests/fixtures/forms") / expected_filename
        assert p.is_file(), f"Fixture file {pb.fixture} does not exist on disk"

    # Other playbooks should have fixture as None
    non_fixture_ids = EXPECTED_PLAYBOOK_IDS - set(expected_fixtures.keys())
    for pb_id in non_fixture_ids:
        assert playbooks[pb_id].fixture is None, f"{pb_id} should have fixture=None"


def test_required_matches(playbooks: dict[str, Playbook]):
    """Test required query matches specified in the prompt contract."""
    # 1. 'mujhe naya pan card chahiye' -> pan_new_49a first
    matches_pan = match_playbook("mujhe naya pan card chahiye", playbooks)
    assert matches_pan, "No match for 'mujhe naya pan card chahiye'"
    assert matches_pan[0].id == "pan_new_49a", (
        f"Expected pan_new_49a first, got {matches_pan[0].id}"
    )

    # 2. 'वोटर आईडी बनवाना है' -> voter_form6 first
    matches_voter = match_playbook("वोटर आईडी बनवाना है", playbooks)
    assert matches_voter, "No match for 'वोटर आईडी बनवाना है'"
    assert matches_voter[0].id == "voter_form6", (
        f"Expected voter_form6 first, got {matches_voter[0].id}"
    )

    # 3. 'company name reserve' -> mca_spice_name
    matches_mca = match_playbook("company name reserve", playbooks)
    assert matches_mca, "No match for 'company name reserve'"
    assert matches_mca[0].id == "mca_spice_name", (
        f"Expected mca_spice_name first, got {matches_mca[0].id}"
    )

    # 4. 'refund status' -> itr_refund_status
    matches_refund = match_playbook("refund status", playbooks)
    assert matches_refund, "No match for 'refund status'"
    assert matches_refund[0].id == "itr_refund_status", (
        f"Expected itr_refund_status first, got {matches_refund[0].id}"
    )


def test_additional_intent_matches(playbooks: dict[str, Playbook]):
    """Test additional multilingual and Hinglish queries."""
    # Hinglish pan
    assert match_playbook("pan card banana", playbooks)[0].id == "pan_new_49a"

    # Voter search
    assert match_playbook("search voter list", playbooks)[0].id == "voter_search"

    # Voter correction
    assert match_playbook("voter id correction", playbooks)[0].id == "voter_form8"

    # ITR filing
    assert match_playbook("file itr 1", playbooks)[0].id == "itr1_prefill"

    # Link pan aadhaar
    assert match_playbook("link pan with aadhaar", playbooks)[0].id == "pan_aadhaar_link"

    # Instant e-pan
    assert match_playbook("instant epan", playbooks)[0].id == "pan_instant_epan"

    # MCA DIN KYC
    assert match_playbook("dir 3 kyc", playbooks)[0].id == "mca_din_dir3"

    # MCA company master data
    assert match_playbook("view company master data", playbooks)[0].id == "mca_company_master"

    # Unmatched query returns empty list
    assert match_playbook("zzzz non matching query xyz 9999", playbooks) == []


def test_validation_missing_key():
    """Verify loader raises ValueError naming the file when a required key is missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_file = Path(tmpdir) / "invalid_missing_key.yaml"
        bad_file.write_text(yaml.dump({"id": "sample"}), encoding="utf-8")

        with pytest.raises(ValueError) as excinfo:
            load_playbooks(tmpdir)

        assert "invalid_missing_key.yaml" in str(excinfo.value)
        assert "missing required key" in str(excinfo.value)


def test_validation_unknown_field_key():
    """Verify loader raises ValueError naming the file when a field is not in FIELDS."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_file = Path(tmpdir) / "invalid_field.yaml"
        data = {
            "id": "sample",
            "portal": "Test Portal",
            "title_en": "Test",
            "title_hi": "परीक्षण",
            "live_url": "https://example.gov.in",
            "fixture": None,
            "requires_login": False,
            "required_fields": ["fake_nonexistent_field"],
            "optional_fields": [],
            "field_hints": {},
            "steps": ["Step 1.", "Step 2.", "Step 3.", "Step 4."],
            "stop_before": ["Submit"],
            "keywords": ["test"],
        }
        bad_file.write_text(yaml.dump(data), encoding="utf-8")

        with pytest.raises(ValueError) as excinfo:
            load_playbooks(tmpdir)

        assert "invalid_field.yaml" in str(excinfo.value)
        assert "fake_nonexistent_field" in str(excinfo.value)
        assert "not in FIELDS" in str(excinfo.value)


def test_validation_nonexistent_fixture():
    """Verify loader raises ValueError naming the file when fixture file does not exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_file = Path(tmpdir) / "invalid_fixture.yaml"
        data = {
            "id": "sample",
            "portal": "Test Portal",
            "title_en": "Test",
            "title_hi": "परीक्षण",
            "live_url": "https://example.gov.in",
            "fixture": "this_file_does_not_exist_at_all.html",
            "requires_login": False,
            "required_fields": ["pan"],
            "optional_fields": [],
            "field_hints": {},
            "steps": ["Step 1.", "Step 2.", "Step 3.", "Step 4."],
            "stop_before": ["Submit"],
            "keywords": ["test"],
        }
        bad_file.write_text(yaml.dump(data), encoding="utf-8")

        with pytest.raises(ValueError) as excinfo:
            load_playbooks(tmpdir)

        assert "invalid_fixture.yaml" in str(excinfo.value)
        assert "this_file_does_not_exist_at_all.html" in str(excinfo.value)


def test_validation_duplicate_id():
    """Verify loader raises ValueError when two playbooks share the same ID."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data = {
            "id": "dup_id",
            "portal": "Test Portal",
            "title_en": "Test",
            "title_hi": "परीक्षण",
            "live_url": "https://example.gov.in",
            "fixture": None,
            "requires_login": False,
            "required_fields": ["pan"],
            "optional_fields": [],
            "field_hints": {},
            "steps": ["Step 1.", "Step 2.", "Step 3.", "Step 4."],
            "stop_before": ["Submit"],
            "keywords": ["test"],
        }
        f1 = Path(tmpdir) / "a.yaml"
        f2 = Path(tmpdir) / "b.yaml"
        f1.write_text(yaml.dump(data), encoding="utf-8")
        f2.write_text(yaml.dump(data), encoding="utf-8")

        with pytest.raises(ValueError) as excinfo:
            load_playbooks(tmpdir)

        assert "dup_id" in str(excinfo.value)
        assert "duplicate id" in str(excinfo.value)


def test_validation_invalid_directory():
    """Verify loader raises ValueError when directory does not exist."""
    with pytest.raises(ValueError) as excinfo:
        load_playbooks("/nonexistent/directory/path/12345")
    assert "Playbooks directory does not exist" in str(excinfo.value)

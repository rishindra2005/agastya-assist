"""Tests for local static HTML form fixtures.

Validates that all fixture forms match real Indian government form structures,
contain required field names, action URLs, buttons, and accessibility/robustness attributes.
"""

from html.parser import HTMLParser
from pathlib import Path
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "forms"


class FormParser(HTMLParser):
    """HTML parser to inspect form tags, input elements, selects, buttons, and labels."""

    def __init__(self):
        super().__init__()
        self.forms: list[dict] = []
        self.inputs: list[dict] = []
        self.selects: list[dict] = []
        self.buttons: list[dict] = []
        self.labels: list[dict] = []
        self.text_content: list[str] = []
        self._current_select: dict | None = None
        self._current_button: dict | None = None
        self._current_label: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attr_dict = {k: v or "" for k, v in attrs}
        if tag == "form":
            self.forms.append({
                "action": attr_dict.get("action", ""),
                "method": attr_dict.get("method", "").upper(),
                "id": attr_dict.get("id", ""),
            })
        elif tag == "input":
            self.inputs.append({
                "tag": "input",
                "name": attr_dict.get("name", ""),
                "id": attr_dict.get("id", ""),
                "type": attr_dict.get("type", "text"),
                "value": attr_dict.get("value", ""),
                "placeholder": attr_dict.get("placeholder", ""),
                "aria_label": attr_dict.get("aria-label", ""),
                "required": "required" in attr_dict,
                "readonly": "readonly" in attr_dict,
            })
        elif tag == "select":
            select_info = {
                "tag": "select",
                "name": attr_dict.get("name", ""),
                "id": attr_dict.get("id", ""),
                "required": "required" in attr_dict,
                "options": [],
            }
            self.selects.append(select_info)
            self._current_select = select_info
        elif tag == "option":
            if self._current_select is not None:
                self._current_select["options"].append(attr_dict.get("value", ""))
        elif tag == "button":
            button_info = {
                "tag": "button",
                "id": attr_dict.get("id", ""),
                "name": attr_dict.get("name", ""),
                "type": attr_dict.get("type", "submit"),
                "formaction": attr_dict.get("formaction", ""),
                "text": "",
            }
            self.buttons.append(button_info)
            self._current_button = button_info
        elif tag == "label":
            label_info = {
                "for": attr_dict.get("for", ""),
                "text": "",
            }
            self.labels.append(label_info)
            self._current_label = label_info

    def handle_data(self, data: str):
        cleaned = data.strip()
        if cleaned:
            self.text_content.append(cleaned)
            if self._current_button is not None:
                self._current_button["text"] += cleaned
            if self._current_label is not None:
                self._current_label["text"] += cleaned

    def handle_endtag(self, tag: str):
        if tag == "select":
            self._current_select = None
        elif tag == "button":
            self._current_button = None
        elif tag == "label":
            self._current_label = None


def parse_fixture(filename: str) -> FormParser:
    path = FIXTURES_DIR / filename
    assert path.exists(), f"Fixture file {filename} does not exist at {path}"
    content = path.read_text(encoding="utf-8")
    parser = FormParser()
    parser.feed(content)
    return parser


def test_form_success_fixture():
    parser = parse_fixture("form_success.html")
    full_text = " ".join(parser.text_content)
    assert "Form submitted" in full_text


def test_pan_49a_fixture():
    parser = parse_fixture("pan_49a.html")

    # Form points to form_success.html
    assert len(parser.forms) == 1
    assert parser.forms[0]["action"] == "form_success.html"

    # Select fields
    select_names = {s["name"]: s for s in parser.selects}
    assert "application_type" in select_names
    assert "category" in select_names
    assert "gender" in select_names
    assert "state" in select_names
    assert "proof_of_identity" in select_names
    assert "proof_of_address" in select_names
    assert "proof_of_dob" in select_names

    # Check that category has INDIVIDUAL
    assert "INDIVIDUAL" in select_names["category"]["options"]

    # Check that all states (at least 36 states and UTs) are in state select
    assert len(select_names["state"]["options"]) >= 36

    # Input fields
    input_names = {inp["name"] for inp in parser.inputs}
    assert "title" in input_names  # radio
    assert "last_name" in input_names
    assert "first_name" in input_names
    assert "middle_name" in input_names
    assert "dob" in input_names
    assert "father_last_name" in input_names
    assert "father_first_name" in input_names
    assert "father_middle_name" in input_names
    assert "mother_last_name" in input_names
    assert "mother_first_name" in input_names
    assert "mother_middle_name" in input_names
    assert "mobile" in input_names
    assert "email" in input_names
    assert "aadhaar" in input_names

    # Address inputs
    assert "flat_no" in input_names
    assert "premises" in input_names
    assert "road" in input_names
    assert "area" in input_names
    assert "city" in input_names
    assert "pincode" in input_names

    # Source of income checkboxes
    assert "source_of_income" in input_names

    # Check buttons
    button_texts = [b["text"] for b in parser.buttons]
    assert any("Save Draft" in t for t in button_texts)
    assert any("Next" in t for t in button_texts)
    assert any("Submit" in t for t in button_texts)
    assert any("Pay Now" in t for t in button_texts)

    # Submit button has type=submit
    submit_buttons = [b for b in parser.buttons if "Submit" in b["text"]]
    assert any(b["type"] == "submit" for b in submit_buttons)

    # Test robustness: fields with only placeholder/aria-label (no <label for=>)
    label_fors = {lbl["for"] for lbl in parser.labels if lbl["for"]}
    assert "last_name" in label_fors
    assert "middle_name" not in label_fors  # only placeholder/aria-label
    assert "aadhaar" not in label_fors      # only placeholder/aria-label

    aadhaar_input = next(inp for inp in parser.inputs if inp["name"] == "aadhaar")
    assert aadhaar_input["aria_label"] != ""
    assert aadhaar_input["placeholder"] != ""
    assert aadhaar_input["required"] is True

    # Required fields
    required_inputs = {inp["name"] for inp in parser.inputs if inp["required"]}
    assert "last_name" in required_inputs
    assert "first_name" in required_inputs
    assert "mobile" in required_inputs
    assert "email" in required_inputs
    assert "flat_no" in required_inputs
    assert "pincode" in required_inputs


def test_voter_form6_fixture():
    parser = parse_fixture("voter_form6.html")

    # Form action points to form_success.html
    assert len(parser.forms) == 1
    assert parser.forms[0]["action"] == "form_success.html"

    # Select fields
    select_names = {s["name"]: s for s in parser.selects}
    assert "state" in select_names
    assert "district" in select_names
    assert "assembly_constituency" in select_names
    assert "relative_type" in select_names
    assert "gender" in select_names

    # Input fields
    input_names = {inp["name"] for inp in parser.inputs}
    assert "first_name" in input_names
    assert "surname" in input_names
    assert "relative_name" in input_names
    assert "mobile" in input_names
    assert "email" in input_names
    assert "aadhaar_number" in input_names
    assert "dob" in input_names

    # Address fields
    assert "house_no" in input_names
    assert "street_area" in input_names
    assert "town_village" in input_names
    assert "post_office" in input_names
    assert "pincode" in input_names
    assert "tehsil_taluka" in input_names
    assert "address_district" in input_names
    assert "address_state" in input_names

    # Declaration checkbox
    assert "declaration" in input_names
    decl_input = next(inp for inp in parser.inputs if inp["name"] == "declaration")
    assert decl_input["type"] == "checkbox"
    assert decl_input["required"] is True

    # Buttons
    button_texts = [b["text"] for b in parser.buttons]
    assert any("Send OTP" in t for t in button_texts)
    assert any("Preview" in t for t in button_texts)
    assert any("Submit" in t for t in button_texts)

    # Submit button type
    submit_buttons = [b for b in parser.buttons if "Submit" in b["text"]]
    assert any(b["type"] == "submit" for b in submit_buttons)

    # Robustness: fields with aria-label / placeholder without <label for=>
    label_fors = {lbl["for"] for lbl in parser.labels if lbl["for"]}
    assert "first_name" in label_fors
    assert "surname" not in label_fors
    assert "aadhaar_number" not in label_fors

    surname_input = next(inp for inp in parser.inputs if inp["name"] == "surname")
    assert surname_input["aria_label"] != ""
    assert surname_input["placeholder"] != ""


def test_itr_login_and_form_fixture():
    parser = parse_fixture("itr_login_and_form.html")

    # All form actions point to form_success.html
    assert len(parser.forms) >= 2
    for form in parser.forms:
        assert form["action"] == "form_success.html"

    # Login box fields
    input_names = {inp["name"]: inp for inp in parser.inputs}
    assert "user_id" in input_names
    assert "password" in input_names
    assert input_names["password"]["type"] == "password"

    # Personal info fields
    assert "pan" in input_names
    assert input_names["pan"]["readonly"] is True
    assert input_names["pan"]["value"] != ""

    assert "name" in input_names
    assert "dob" in input_names
    assert "aadhaar" in input_names
    assert "mobile" in input_names
    assert "email" in input_names

    # Address fields
    assert "flat_door_no" in input_names
    assert "area" in input_names
    assert "city" in input_names
    assert "pincode" in input_names

    # Nature of employment select
    select_names = {s["name"]: s for s in parser.selects}
    assert "nature_of_employment" in select_names
    assert "state" in select_names

    # Bank details
    assert "bank_account_number" in input_names
    assert "ifsc_code" in input_names

    # Buttons
    button_texts = [b["text"] for b in parser.buttons]
    assert any("Login" in t for t in button_texts)
    assert any("Continue" in t for t in button_texts)
    assert any("Save Draft" in t for t in button_texts)
    assert any("Proceed to Verification" in t for t in button_texts)
    assert any("e-Verify" in t for t in button_texts)

    # e-Verify button type=submit
    everify_buttons = [b for b in parser.buttons if "e-Verify" in b["text"]]
    assert any(b["type"] == "submit" for b in everify_buttons)

    # Robustness: fields with only placeholder/aria-label
    label_fors = {lbl["for"] for lbl in parser.labels if lbl["for"]}
    assert "password" not in label_fors
    assert "aadhaar" not in label_fors
    assert "ifsc_code" not in label_fors


def test_mca_din_fixture():
    parser = parse_fixture("mca_din.html")

    # Form action points to form_success.html
    assert len(parser.forms) == 1
    assert parser.forms[0]["action"] == "form_success.html"

    # Input fields
    input_names = {inp["name"]: inp for inp in parser.inputs}
    assert "proposed_name_1" in input_names
    assert "proposed_name_2" in input_names
    assert "din" in input_names
    assert "director_name" in input_names
    assert "email" in input_names
    assert "mobile" in input_names

    # Required inputs
    assert input_names["proposed_name_1"]["required"] is True
    assert input_names["director_name"]["required"] is True

    # Select fields
    select_names = {s["name"]: s for s in parser.selects}
    assert "company_type" in select_names
    assert "company_class" in select_names
    assert "company_category" in select_names
    assert "industrial_activity" in select_names

    # Buttons
    button_texts = [b["text"] for b in parser.buttons]
    assert any("Save" in t for t in button_texts)
    assert any("Check Name" in t for t in button_texts)
    assert any("Submit" in t for t in button_texts)

    submit_buttons = [b for b in parser.buttons if "Submit" in b["text"]]
    assert any(b["type"] == "submit" for b in submit_buttons)

    # Robustness: proposed_name_2 and din have no <label for=>
    label_fors = {lbl["for"] for lbl in parser.labels if lbl["for"]}
    assert "proposed_name_1" in label_fors
    assert "proposed_name_2" not in label_fors
    assert "din" not in label_fors

    assert input_names["proposed_name_2"]["placeholder"] != ""
    assert input_names["proposed_name_2"]["aria_label"] != ""

"""Tests for pattern-based field value extraction in agastya.profile.extract."""

import pytest

from agastya.profile.extract import devanagari_to_ascii, extract_values


TABLE_TEST_CASES = [
    # 1. PAN standard uppercase
    ("My PAN is ABCDE1234F for taxation", ["pan"], {"pan": "ABCDE1234F"}),
    # 2. PAN lowercase with spaces
    ("pan number is abcde 1234 f", ["pan"], {"pan": "ABCDE1234F"}),
    # 3. Aadhaar continuous 12 digits
    ("Aadhaar is 123456789012", ["aadhaar"], {"aadhaar": "123456789012"}),
    # 4. Aadhaar with spaces
    ("Aadhaar: 1234 5678 9012", ["aadhaar"], {"aadhaar": "123456789012"}),
    # 5. Aadhaar with dashes
    ("Aadhaar card 1234-5678-9012.", ["aadhaar"], {"aadhaar": "123456789012"}),
    # 6. Aadhaar with Devanagari digits
    ("मेरा आधार नंबर १२३४ ५६७८ ९०१२ है", ["aadhaar"], {"aadhaar": "123456789012"}),
    # 7. Mobile standard 10 digits
    ("mobile number is 9876543210", ["mobile"], {"mobile": "9876543210"}),
    # 8. Mobile with space
    ("mobile is 98765 43210", ["mobile"], {"mobile": "9876543210"}),
    # 9. Mobile with +91 prefix
    ("contact me at +91 98765 43210", ["mobile"], {"mobile": "9876543210"}),
    # 10. Mobile with 0 prefix
    ("phone is 09876543210", ["mobile"], {"mobile": "9876543210"}),
    # 11. Mobile with Devanagari digits
    ("मोबाइल नंबर ९८७६५४३२१० है", ["mobile"], {"mobile": "9876543210"}),
    # 12. Email standard
    ("email me at test.user@example.com please", ["email"], {"email": "test.user@example.com"}),
    # 13. Email with Indian TLD
    ("reach me at a@b.in.", ["email"], {"email": "a@b.in"}),
    # 14. Pincode 6 digits
    ("pincode is 110001", ["pincode"], {"pincode": "110001"}),
    # 15. Pincode with Devanagari digits
    ("पिन कोड ११०००१", ["pincode"], {"pincode": "110001"}),
    # 16. Pincode alongside Aadhaar
    ("aadhaar 1234 5678 9012 and pincode 560001", ["pincode"], {"pincode": "560001"}),
    # 17. Pincode not extracted from Aadhaar alone
    ("mera aadhaar 1234 5678 9012", ["pincode"], {}),
    # 18. Bank IFSC standard
    ("my IFSC code is SBIN0001234", ["bank_ifsc"], {"bank_ifsc": "SBIN0001234"}),
    # 19. Bank IFSC lowercase with spaces
    ("bank ifsc is hdfc 0 001234", ["bank_ifsc"], {"bank_ifsc": "HDFC0001234"}),
    # 20. EPIC voter ID standard
    ("Voter card ABC1234567", ["epic_number"], {"epic_number": "ABC1234567"}),
    # 21. EPIC voter ID with space
    ("epic number abc 1234567", ["epic_number"], {"epic_number": "ABC1234567"}),
    # 22. DOB DD/MM/YYYY
    ("born on 05/03/2001", ["date_of_birth"], {"date_of_birth": "05/03/2001"}),
    # 23. DOB DD-MM-YYYY
    ("DOB: 5-3-2001", ["date_of_birth"], {"date_of_birth": "05/03/2001"}),
    # 24. DOB English month name
    ("I was born on 5 March 2001", ["date_of_birth"], {"date_of_birth": "05/03/2001"}),
    # 25. DOB Hindi month name
    ("मेरी जन्म तिथि 5 मार्च 2001 है", ["date_of_birth"], {"date_of_birth": "05/03/2001"}),
    # 26. DOB Hindi month name with Devanagari digits
    ("जन्म तिथि ५ मार्च २००१", ["date_of_birth"], {"date_of_birth": "05/03/2001"}),
    # 27. DOB Hindi August (अगस्त)
    ("जन्म 15 अगस्त 1947 को हुआ था", ["date_of_birth"], {"date_of_birth": "15/08/1947"}),
    # 28. DOB Hindi January (जनवरी)
    ("26 जनवरी 1950", ["date_of_birth"], {"date_of_birth": "26/01/1950"}),
    # 29. DOB Hindi October (अक्टूबर)
    ("2 अक्टूबर 1969", ["date_of_birth"], {"date_of_birth": "02/10/1969"}),
    # 30. DOB Hindi December (दिसंबर)
    ("31 दिसंबर 1999", ["date_of_birth"], {"date_of_birth": "31/12/1999"}),
    # 31. DOB YYYY-MM-DD
    ("date of birth 2001-03-05", ["date_of_birth"], {"date_of_birth": "05/03/2001"}),
    # 32. Mixed sentence with mobile and email
    (
        "mera mobile 98765 43210 aur email a@b.in hai",
        ["mobile", "email"],
        {"mobile": "9876543210", "email": "a@b.in"},
    ),
]


@pytest.mark.parametrize("answer,fields,expected", TABLE_TEST_CASES)
def test_extract_values_table(answer: str, fields: list[str], expected: dict[str, str]) -> None:
    result = extract_values(answer, fields)
    assert result == expected


def test_mixed_sentence_mobile_and_email() -> None:
    sentence = "mera mobile 98765 43210 aur email a@b.in hai"
    res = extract_values(sentence, ["mobile", "email"])
    assert res == {"mobile": "9876543210", "email": "a@b.in"}


def test_free_text_fields_never_guessed() -> None:
    answer = "मेरा नाम राहुल शर्मा है और जन्म 5 मार्च 2001 है, पता 12 MG Road"
    res = extract_values(answer, ["full_name", "date_of_birth", "address_line1"])
    # full_name and address_line1 must NOT be extracted; date_of_birth must be extracted
    assert "full_name" not in res
    assert "address_line1" not in res
    assert res.get("date_of_birth") == "05/03/2001"


def test_all_hindi_months() -> None:
    months = [
        ("15 जनवरी 2000", "15/01/2000"),
        ("15 फ़रवरी 2000", "15/02/2000"),
        ("15 फरवरी 2000", "15/02/2000"),
        ("15 मार्च 2000", "15/03/2000"),
        ("15 अप्रैल 2000", "15/04/2000"),
        ("15 मई 2000", "15/05/2000"),
        ("15 जून 2000", "15/06/2000"),
        ("15 जुलाई 2000", "15/07/2000"),
        ("15 अगस्त 2000", "15/08/2000"),
        ("15 सितंबर 2000", "15/09/2000"),
        ("15 सितम्बर 2000", "15/09/2000"),
        ("15 अक्टूबर 2000", "15/10/2000"),
        ("15 अक्तूबर 2000", "15/10/2000"),
        ("15 नवंबर 2000", "15/11/2000"),
        ("15 नवम्बर 2000", "15/11/2000"),
        ("15 दिसंबर 2000", "15/12/2000"),
        ("15 दिसम्बर 2000", "15/12/2000"),
    ]
    for text, expected in months:
        res = extract_values(text, ["date_of_birth"])
        assert res.get("date_of_birth") == expected, f"Failed for {text}"


def test_devanagari_digits_conversion() -> None:
    assert devanagari_to_ascii("०१२३४५६७८९") == "0123456789"
    assert devanagari_to_ascii("पिन ११०००१") == "पिन 110001"


def test_empty_inputs() -> None:
    assert extract_values("", ["pan"]) == {}
    assert extract_values("ABCDE1234F", []) == {}
    assert extract_values("", []) == {}

from src.common.location_filters import ALLOWED_ONSITE_COUNTRIES, is_globally_remote, matched_onsite_country


def test_plain_remote_passes():
    assert is_globally_remote("Remote", "We're a fully distributed team.") is True


def test_non_remote_location_fails():
    assert is_globally_remote("Chicago, IL", "") is False


def test_remote_us_only_in_location_is_restricted():
    assert is_globally_remote("Remote (US Only)", "") is False


def test_remote_with_restriction_in_description_is_restricted():
    desc = "This is a remote role. Must be located in the United States."
    assert is_globally_remote("Remote", desc) is False


def test_remote_with_unrelated_us_mention_still_passes():
    # "US" appearing without a restriction phrase shouldn't trip the filter.
    desc = "We serve clients across the US, EU, and APAC."
    assert is_globally_remote("Remote", desc) is True


def test_none_inputs_do_not_crash():
    assert is_globally_remote(None, None) is False


def test_matched_onsite_country_matches_location_text():
    assert matched_onsite_country("Karachi, Pakistan", None) == "Pakistan"


def test_matched_onsite_country_falls_back_to_description():
    assert matched_onsite_country("On-site", "This role is based in our Doha, Qatar office.") == "Qatar"


def test_matched_onsite_country_is_case_insensitive():
    assert matched_onsite_country("singapore", None) == "Singapore"


def test_matched_onsite_country_saudi_arabia_abbreviation():
    assert matched_onsite_country("Riyadh, KSA", None) == "Saudi Arabia"


def test_matched_onsite_country_returns_none_when_no_match():
    assert matched_onsite_country("Berlin, Germany", "A great team in Berlin.") is None


def test_matched_onsite_country_none_inputs_do_not_crash():
    assert matched_onsite_country(None, None) is None


def test_allowed_onsite_countries_matches_the_requested_list():
    # Direct request 2026-09-11 — regression guard against a future edit
    # accidentally dropping or renaming one of these.
    assert set(ALLOWED_ONSITE_COUNTRIES) == {
        "Pakistan", "Malaysia", "Maldives", "Bahrain", "Qatar", "Kuwait",
        "Oman", "Saudi Arabia", "Azerbaijan", "Armenia", "Lithuania",
        "Latvia", "Malta", "Singapore", "Luxembourg",
    }

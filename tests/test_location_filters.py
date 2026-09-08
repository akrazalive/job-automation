from src.common.location_filters import is_globally_remote


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

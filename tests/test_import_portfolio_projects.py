from scripts.import_portfolio_projects import (
    parse_portfolio_projects,
    to_project_entry,
)

# A small fixture mirroring the real portfolio page's exact format
# (single-quoted JS string literals, unquoted keys, escaped apostrophes,
# a private "#" url, and a trailing boolean key on the last entry) -
# without hitting the network. The real page broke an earlier version of
# this parser on that last-entry edge case (see the script's docstring).
FIXTURE_HTML = """
<script>
const PROJECTS = [
  {title:'UCBM Academy',url:'https://ucbmacademy.unicampus.it/',desc:'Training platform for a university.',tags:['WordPress','PHP','Italy'],filters:['wordpress','php'],color:'#1e40af'},
  {title:'Native Nic',url:'https://nativenic.com/',desc:'Canada\\'s leading online store.',tags:['WordPress','WooCommerce'],filters:['wordpress'],color:'#b45309'},
  {title:'Enterprise CRM',url:'#',desc:'Private client project, no public link.',tags:['WordPress','CRM'],filters:['wordpress'],color:'#334155'},
  {title:'My Hair Care App',url:'#',desc:'Mobile app for a brand.',tags:['React Native','iOS'],filters:['mobile','react'],color:'#9333ea',mobile:true},
];
</script>
"""


def test_parses_all_entries_including_trailing_boolean_key():
    records = parse_portfolio_projects(FIXTURE_HTML)
    assert len(records) == 4
    assert records[-1]["title"] == "My Hair Care App"
    assert records[-1]["mobile"] is True


def test_handles_escaped_apostrophe_in_description():
    records = parse_portfolio_projects(FIXTURE_HTML)
    native_nic = next(r for r in records if r["title"] == "Native Nic")
    assert native_nic["desc"] == "Canada's leading online store."


def test_to_project_entry_converts_hash_url_to_none():
    records = parse_portfolio_projects(FIXTURE_HTML)
    crm = next(r for r in records if r["title"] == "Enterprise CRM")
    entry = to_project_entry(crm)
    assert entry.url is None
    assert entry.name == "Enterprise CRM"


def test_to_project_entry_keeps_real_url():
    records = parse_portfolio_projects(FIXTURE_HTML)
    ucbm = next(r for r in records if r["title"] == "UCBM Academy")
    entry = to_project_entry(ucbm)
    assert entry.url == "https://ucbmacademy.unicampus.it/"
    assert entry.tech == ["WordPress", "PHP", "Italy"]


def test_parse_raises_clear_error_when_marker_missing():
    import pytest

    with pytest.raises(ValueError):
        parse_portfolio_projects("<html>no projects array here</html>")

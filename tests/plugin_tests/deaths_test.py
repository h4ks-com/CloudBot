from unittest.mock import MagicMock, patch

import freezegun
import requests

from plugins import obituary

SAMPLE_WIKITEXT = """===3===
*[[Paras Chandra Jain]], 76, Indian politician, [[Madhya Pradesh Legislative Assembly|Madhya Pradesh MLA]] (2003-2023).<ref>[https://theprint.in/india/former-madhya-pradesh-bjp-minister-paras-jain-dies/3003831/ Former Madhya Pradesh BJP minister Paras Jain dies]</ref>
*[[Hatsuo Royama]], 78, Japanese karate practitioner.<ref>[https://kyokushinkarate.news/en/news/kaicho-hatsuo-royama-passed-away Kaicho Hatsuo Royama passed away]</ref>
*[[Iván Szelényi]], 88, Hungarian-American sociologist.<ref>[https://telex.hu/belfold/2026/08/03/szelenyi-istvan-tarki-meghalt Meghalt Szelényi Iván szociológus] {{in lang|hu}}</ref>
*[[Ruslan Taramov]], 61, Russian Olympic boxer ([[Boxing at the 1988 Summer Olympics – Middleweight|1988]]).<ref>[https://chechnyatoday.com/news/398349 Из жизни ушел чеченский боксер Руслан Тарамов] {{in lang|ru}}</ref>
"""

SAMPLE_SECTIONS = [
    {"index": "1", "line": "August", "anchor": "August"},
    {"index": "2", "line": "4", "anchor": "4"},
    {"index": "3", "line": "3", "anchor": "3"},
    {"index": "4", "line": "2", "anchor": "2"},
    {"index": "5", "line": "1", "anchor": "1"},
    {"index": "6", "line": "July", "anchor": "July"},
]


def _mock_response(payload: dict) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def _api_responses(sections, wikitext_by_index):
    """Build a side_effect that returns sections or wikitext based on request params."""

    def _side_effect(url, params=None, **kwargs):
        action = (params or {}).get("action")
        if action == "parse":
            prop = params.get("prop")
            if prop == "sections":
                return _mock_response({"parse": {"sections": sections}})
            if prop == "wikitext":
                idx = params.get("section")
                return _mock_response(
                    {
                        "parse": {
                            "wikitext": {"*": wikitext_by_index.get(idx, "")}
                        }
                    }
                )
        return _mock_response({"parse": {}})

    return _side_effect


def _run(text, sections=None, wikitext_by_index=None):
    if sections is None:
        sections = SAMPLE_SECTIONS
    if wikitext_by_index is None:
        wikitext_by_index = {"3": SAMPLE_WIKITEXT}
    with patch("plugins.obituary.get_session") as mock_session:
        mock_session.return_value.get.side_effect = _api_responses(
            sections, wikitext_by_index
        )
        return obituary.deaths(text)


def test_default_returns_list():
    result = _run("")
    assert isinstance(result, list)
    assert len(result) == 4
    assert "Paras Chandra Jain" in result[0]
    assert "https://en.wikipedia.org/wiki/Paras_Chandra_Jain" in result[0]


def test_explicit_count_limits_results():
    result = _run("2")
    assert isinstance(result, list)
    assert len(result) == 2


def test_count_clamped_to_max():
    result = _run("999")
    assert isinstance(result, list)
    assert len(result) == 4


def test_invalid_count_returns_error():
    result = _run("abc")
    assert isinstance(result, str)
    assert "abc" in result


def test_negative_count_clamped_to_one():
    result = _run("-5")
    assert isinstance(result, list)
    assert len(result) == 1


def test_future_day_section_is_skipped():
    # today = 2026-08-03; section "4" (Aug 4) is in the future and must be skipped
    wikitext_by_index = {
        "2": "===4===\n*[[Future Person]], 50, test.\n",
        "3": SAMPLE_WIKITEXT,
    }
    with (
        patch("plugins.obituary.get_session") as mock_session,
        freezegun.freeze_time("2026-08-03"),
    ):
        mock_session.return_value.get.side_effect = _api_responses(
            SAMPLE_SECTIONS, wikitext_by_index
        )
        result = obituary.deaths("")
    assert isinstance(result, list)
    assert all("Future Person" not in line for line in result)
    assert "Paras Chandra Jain" in result[0]


def test_empty_results():
    sections = [{"index": "1", "line": "August", "anchor": "August"}]
    result = _run("", sections=sections, wikitext_by_index={})
    assert isinstance(result, str)
    assert "No recent deaths" in result


def test_network_error_returns_message():
    with patch("plugins.obituary.get_session") as mock_session:
        mock_session.return_value.get.side_effect = (
            requests.exceptions.ConnectionError("boom")
        )
        result = obituary.deaths("")
    assert isinstance(result, str)
    assert "Failed" in result


def test_wikitext_cleaning_strips_refs_and_templates():
    entries = obituary._parse_entries(SAMPLE_WIKITEXT, 2026, 8, 3, 10)
    assert len(entries) == 4
    sel = next(e for e in entries if e.name == "Iván Szelényi")
    assert "in lang" not in sel.details
    assert "{{" not in sel.details
    assert "<ref>" not in sel.details


def test_wikitext_cleaning_unwraps_piped_links():
    entries = obituary._parse_entries(SAMPLE_WIKITEXT, 2026, 8, 3, 10)
    jain = next(e for e in entries if e.name == "Paras Chandra Jain")
    assert "Madhya Pradesh Legislative Assembly|" not in jain.details
    assert "Madhya Pradesh MLA" in jain.details


def test_parse_count_default():
    assert obituary._parse_count("") == obituary.DEFAULT_LIMIT
    assert obituary._parse_count("   ") == obituary.DEFAULT_LIMIT


def test_parse_count_invalid():
    result = obituary._parse_count("xyz")
    assert isinstance(result, str)
    assert "xyz" in result


def test_parse_count_bounds():
    assert obituary._parse_count("0") == 1
    assert obituary._parse_count("100") == obituary.MAX_LIMIT
    assert obituary._parse_count("3") == 3


def test_format_bold_name_grey_date_cyan_link():
    entries = [
        obituary.Death(
            name="Paras Chandra Jain",
            death_date="2026-08-03",
            link="https://en.wikipedia.org/wiki/Paras_Chandra_Jain",
            details="76, Indian politician",
        )
    ]
    line = obituary.format_deaths(entries)[0]
    assert line.startswith(
        obituary._BOLD + "Paras Chandra Jain" + obituary._CLEAR
    )
    assert obituary._GREY + "2026-08-03" + obituary._CLEAR in line
    assert (
        obituary._CYAN
        + "https://en.wikipedia.org/wiki/Paras_Chandra_Jain"
        + obituary._CLEAR
        in line
    )


def test_format_strips_trailing_period_from_details():
    entries = [
        obituary.Death(
            name="Test Person",
            death_date="2026-01-01",
            link="https://en.wikipedia.org/wiki/Test_Person",
            details="42, fictional character.",
        )
    ]
    line = obituary.format_deaths(entries)[0]
    assert "character." not in line
    assert "character" in line


def test_format_omits_details_separator_when_empty():
    entries = [
        obituary.Death(
            name="No Details",
            death_date="2026-01-01",
            link="https://en.wikipedia.org/wiki/No_Details",
            details="",
        )
    ]
    line = obituary.format_deaths(entries)[0]
    assert " - " not in line


def test_clean_wikitext_strips_italic_markup():
    cleaned = obituary._clean_wikitext(
        "93, Hong Kong actress (''Soldier of Fortune'', ''The Wild, Wild Rose'')."
    )
    assert "''" not in cleaned
    assert "Soldier of Fortune" in cleaned
    assert "The Wild, Wild Rose" in cleaned


def test_clean_wikitext_strips_bold_markup():
    cleaned = obituary._clean_wikitext(
        "'''Bold name''', 50, regular description."
    )
    assert "'''" not in cleaned
    assert cleaned.startswith("Bold name,")

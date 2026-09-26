from unittest.mock import patch

import pytest
import responses

from plugins import pkg


class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data

    def json(self):
        return self._json_data


@pytest.fixture(autouse=True)
def _clean_pkg_state():
    pkg.results_queue.clear()
    yield
    pkg.results_queue.clear()


# --- _tag_text / _tag_attr ---


def test_tag_text_none_returns_empty():
    assert pkg._tag_text(None) == ""


def test_tag_text_strips_whitespace():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup("<a>  hello  </a>", "html.parser")
    assert pkg._tag_text(soup.select_one("a")) == "hello"


def test_tag_attr_none_returns_empty():
    assert pkg._tag_attr(None, "href") == ""


def test_tag_attr_missing_returns_empty():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup("<a>hi</a>", "html.parser")
    assert pkg._tag_attr(soup.select_one("a"), "href") == ""


def test_tag_attr_string_value():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup('<a href=" /foo ">hi</a>', "html.parser")
    assert pkg._tag_attr(soup.select_one("a"), "href") == "/foo"


def test_tag_attr_list_value():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup('<a class="a b">hi</a>', "html.parser")
    assert pkg._tag_attr(soup.select_one("a"), "class") == "a"


def test_tag_attr_empty_list_value():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup('<a class="">hi</a>', "html.parser")
    assert pkg._tag_attr(soup.select_one("a"), "class") == ""


# --- Package dataclass ---


def test_package_default_link_and_str():
    package = pkg.Package("foo", "1.0", "", "A description")
    assert package.link == "https://pypi.org/project/foo"
    assert "foo" in str(package)
    assert "1.0" in str(package)
    assert "A description" in str(package)


def test_package_custom_link():
    package = pkg.Package("foo", "1.0", "", "desc", "http://example.com/foo")
    assert package.link == "http://example.com/foo"


def test_package_parses_iso_datetime():
    package = pkg.Package("foo", "1.0", "2021-01-02T03:04:05+0000", "desc")
    assert package.released_date_str() == "02-1-2021"


def test_package_parses_iso_datetime_with_microseconds():
    package = pkg.Package(
        "foo", "1.0", "2021-01-02T03:04:05.123456+0000", "desc"
    )
    assert package.released_date_str() == "02-1-2021"


def test_package_unparsable_updated_kept_as_string():
    package = pkg.Package("foo", "1.0", "not-a-date", "desc")
    assert package.released_date == "not-a-date"
    assert package.released_date_str() == "not-a-date"


def test_package_no_updated_defaults_empty_str():
    package = pkg.Package("foo", "1.0", "", "desc")
    assert package.released_date is None
    assert package.released_date_str() == ""
    assert str(package).endswith("desc - ")


def test_package_released_date_str_custom_format():
    package = pkg.Package("foo", "1.0", "2021-01-02T03:04:05+0000", "desc")
    assert package.released_date_str("%Y") == "2021"


# --- pypi_search ---


def test_pypi_search_returns_package(mock_requests):
    mock_requests.add(
        responses.GET,
        "https://pypi.org/simple/",
        json={"projects": [{"name": "requests"}, {"name": "unrelated"}]},
    )
    mock_requests.add(
        responses.GET,
        "https://pypi.org/pypi/requests/json",
        json={
            "info": {
                "name": "requests",
                "version": "2.0.0",
                "package_url": "https://pypi.org/project/requests",
                "summary": "HTTP for humans",
            },
            "releases": {
                "2.0.0": [{"upload_time": "2021-01-02T03:04:05"}],
            },
        },
    )
    results = list(pkg.pypi_search("requests"))
    assert len(results) == 1
    assert results[0].name == "requests"
    assert results[0].version == "2.0.0"
    assert results[0].description == "HTTP for humans"


def test_pypi_search_no_matches(mock_requests):
    mock_requests.add(
        responses.GET,
        "https://pypi.org/simple/",
        json={"projects": [{"name": "unrelated"}]},
    )
    assert list(pkg.pypi_search("nomatch")) == []


def test_pypi_search_info_fetch_fails(mock_requests):
    mock_requests.add(
        responses.GET,
        "https://pypi.org/simple/",
        json={"projects": [{"name": "requests"}]},
    )
    mock_requests.add(
        responses.GET,
        "https://pypi.org/pypi/requests/json",
        status=404,
    )
    results = list(pkg.pypi_search("requests"))
    assert results[0].version == "-!Failed to get info!-"


def test_pypi_search_version_fallback_to_other_release(mock_requests):
    mock_requests.add(
        responses.GET,
        "https://pypi.org/simple/",
        json={"projects": [{"name": "requests"}]},
    )
    mock_requests.add(
        responses.GET,
        "https://pypi.org/pypi/requests/json",
        json={
            "info": {
                "name": "requests",
                "version": "3.0.0",
                "package_url": "https://pypi.org/project/requests",
                "summary": "HTTP for humans",
            },
            "releases": {
                "2.0.0": [{"upload_time": "2021-01-02T03:04:05"}],
            },
        },
    )
    results = list(pkg.pypi_search("requests"))
    assert results[0].version == "2.0.0"


def test_pypi_search_empty_releases_dict(mock_requests):
    mock_requests.add(
        responses.GET,
        "https://pypi.org/simple/",
        json={"projects": [{"name": "requests"}]},
    )
    mock_requests.add(
        responses.GET,
        "https://pypi.org/pypi/requests/json",
        json={
            "info": {
                "name": "requests",
                "version": "3.0.0",
                "package_url": "https://pypi.org/project/requests",
                "summary": "HTTP for humans",
            },
            "releases": {},
        },
    )
    results = list(pkg.pypi_search("requests"))
    assert results[0].released_date_str() == ""


# --- aur_search ---


AUR_HTML = """
<table><tbody>
<tr>
<td><a href="/packages/foo-bin">foo-bin</a></td>
<td>1.2.3-1</td>
<td>x</td>
<td>x</td>
<td>A foo package</td>
</tr>
</tbody></table>
"""


def test_aur_search_success():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(text=AUR_HTML)
        results = list(pkg.aur_search("foo"))
    assert len(results) == 1
    assert results[0].name == "foo-bin"
    assert results[0].link == "https://aur.archlinux.org/packages/foo-bin"
    assert results[0].version == "1.2.3-1"
    assert results[0].description == "A foo package"


def test_aur_search_non_200_returns_nothing():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(status_code=500)
        assert list(pkg.aur_search("foo")) == []


def test_aur_search_index_error_returns_nothing():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(
            text="<table><tbody><tr><td>only one column</td></tr></tbody></table>"
        )
        assert list(pkg.aur_search("foo")) == []


# --- arch_search ---

ARCH_HTML = """
<table><tbody>
<tr>
<td>c0</td><td>c1</td>
<td><a href="/packages/x86_64/core/foo/">foo</a></td>
<td>1.0-1</td>
<td>A foo package</td>
</tr>
</tbody></table>
"""


def test_arch_search_success():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(text=ARCH_HTML)
        results = list(pkg.arch_search("foo"))
    assert len(results) == 1
    assert results[0].name == "foo"
    assert results[0].link == "https://archlinux.org/packages/x86_64/core/foo/"
    assert results[0].version == "1.0-1"


def test_arch_search_non_200_returns_nothing():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(status_code=404)
        assert list(pkg.arch_search("foo")) == []


def test_arch_search_index_error_returns_nothing():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(
            text="<table><tbody><tr><td>x</td></tr></tbody></table>"
        )
        assert list(pkg.arch_search("foo")) == []


# --- crates_search ---


def test_crates_search_success():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(
            json_data={
                "crates": [
                    {
                        "name": "serde",
                        "newest_version": "1.0.0",
                        "updated_at": "2021-01-02",
                        "description": "Serialization",
                    }
                ]
            }
        )
        results = list(pkg.crates_search("serde"))
    assert results[0].name == "serde"
    assert results[0].link == "https://crates.io/crates/serde"
    assert results[0].version == "1.0.0"
    assert results[0].description == "Serialization"


def test_crates_search_missing_fields_default_empty():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(
            json_data={"crates": [{"name": "serde"}]}
        )
        results = list(pkg.crates_search("serde"))
    assert results[0].version == ""
    assert results[0].description == ""


def test_crates_search_non_200_returns_nothing():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(status_code=500)
        assert list(pkg.crates_search("serde")) == []


# --- pubdev_search ---

PUBDEV_HTML = """
<div class="packages-item">
  <h3><a href="/packages/foo">foo</a></h3>
  <span class="packages-metadata-block">1.2.3 (Jan 1, 2021)</span>
  <a class="-x-ago">2 days ago</a>
  <div class="packages-description">A foo package</div>
  <div class="-pub-tag-badge"><a class="tag-badge-sub">Flutter</a><a class="tag-badge-sub">web</a></div>
</div>
"""

PUBDEV_HTML_MINIMAL = """
<div class="packages-item">
  <h3><a href="/foo">foo</a></h3>
  <span class="packages-metadata-block">1.2.3</span>
</div>
"""


def test_pubdev_search_success_with_platforms():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(text=PUBDEV_HTML)
        results = list(pkg.pubdev_search("foo"))
    assert results[0].name == "foo"
    assert results[0].link == "https://pub.dev/packages/foo"
    assert results[0].version == "1.2.3"
    assert results[0].updated == "2 days ago"
    assert "Platforms: ['Flutter web']" in results[0].description


def test_pubdev_search_minimal_fields():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(text=PUBDEV_HTML_MINIMAL)
        results = list(pkg.pubdev_search("foo"))
    assert results[0].link == "https://pub.dev/foo"
    assert results[0].updated == ""
    assert results[0].description == ""


def test_pubdev_search_non_200_returns_nothing():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(status_code=500)
        assert list(pkg.pubdev_search("foo")) == []


# --- ubuntu_search ---

UBUNTU_HTML = """
<h3>Package foo</h3>
<ul>
<li>
<a class="resultlink" href="/foo">1.2.3-1</a>
amd64
Some description here
1.2.3-1
</li>
</ul>
"""


def test_ubuntu_search_success():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(text=UBUNTU_HTML)
        results = list(pkg.ubuntu_search("foo"))
    assert len(results) == 1
    assert results[0].name == "foo"
    assert results[0].link == "https://packages.ubuntu.com/foo"
    assert results[0].version == "1.2.3-1"


def test_ubuntu_search_non_200_returns_nothing():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(status_code=500)
        assert list(pkg.ubuntu_search("foo")) == []


# --- search_npmjs ---


def test_search_npmjs_success():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(
            json_data={
                "objects": [
                    {
                        "package": {
                            "name": "left-pad",
                            "version": "1.0.0",
                            "description": "pad a string",
                            "links": {"npm": "https://npmjs.com/left-pad"},
                            "date": {"rel": "2 years ago"},
                        }
                    }
                ]
            }
        )
        results = list(pkg.search_npmjs("left-pad"))
    assert results[0].name == "left-pad"
    assert results[0].link == "https://npmjs.com/left-pad"
    assert results[0].updated == "2 years ago"


def test_search_npmjs_non_200_returns_nothing():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(status_code=500)
        assert list(pkg.search_npmjs("left-pad")) == []


def test_search_npmjs_missing_link_points_at_npmjs():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(
            json_data={
                "objects": [
                    {
                        "package": {
                            "name": "left-pad",
                            "version": "1.0.0",
                            "description": "pad a string",
                            "links": {},
                        }
                    }
                ]
            }
        )
        results = list(pkg.search_npmjs("left-pad"))
    assert results[0].link == "https://www.npmjs.com/package/left-pad"


# --- search_nuget ---

NUGET_HTML = """
<div id="results-column">
<div>
<ul>
<li class="package">
<h2 class="package-title"><a href="/packages/Foo/1.0.0">Foo</a></h2>
<ul class="package-list">
<li>ignore0</li>
<li>ignore1</li>
<li>Last updated 1/1/2021</li>
<li>Latest version 1.0.0</li>
</ul>
<div class="package-details">A foo package</div>
</li>
</ul>
</div>
</div>
"""


def test_search_nuget_success():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(text=NUGET_HTML)
        results = list(pkg.search_nuget("foo"))
    assert results[0].name == "Foo"
    assert results[0].link == "https://www.nuget.org/packages/Foo/1.0.0"
    assert results[0].version == "1.0.0"
    assert results[0].updated == "Last updated 1/1/2021"


def test_search_nuget_non_200_returns_nothing():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(status_code=500)
        assert list(pkg.search_nuget("foo")) == []


def test_search_nuget_no_results_column_returns_nothing():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(text="<div>nothing here</div>")
        assert list(pkg.search_nuget("foo")) == []


def test_search_nuget_no_title_is_skipped():
    html = """
    <div id="results-column"><div><ul>
    <li class="package"><ul class="package-list"></ul></li>
    </ul></div></div>
    """
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(text=html)
        assert list(pkg.search_nuget("foo")) == []


def test_search_nuget_no_package_list_is_skipped():
    html = """
    <div id="results-column"><div><ul>
    <li class="package"><h2 class="package-title"><a href="/x">X</a></h2></li>
    </ul></div></div>
    """
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(text=html)
        assert list(pkg.search_nuget("foo")) == []


# --- search_dockerhub ---


def test_search_dockerhub_success_full_description():
    data = {
        "results": [
            {
                "name": "library/foo",
                "id": "abc123",
                "short_description": "A foo image",
                "updated_at": "2021-01-02",
                "rate_plans": [
                    {
                        "operating_systems": [{"os": "linux"}],
                        "architectures": [{"arch": "amd64"}],
                        "repositories": [{"pull_count": 1000}],
                    }
                ],
            }
        ]
    }
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(json_data=data)
        results = list(pkg.search_dockerhub("foo"))
    assert results[0].name == "library/foo"
    assert results[0].link == "https://hub.docker.com/r/abc123"
    assert "OS:" in results[0].description
    assert "Arch:" in results[0].description
    assert "1000" in results[0].description


def test_search_dockerhub_no_rate_plans_empty_description():
    data = {
        "results": [
            {
                "name": "library/foo",
                "id": "abc123",
                "short_description": "A foo image",
                "updated_at": "2021-01-02",
                "rate_plans": [],
            }
        ]
    }
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(json_data=data)
        results = list(pkg.search_dockerhub("foo"))
    assert results[0].description == ""


def test_search_dockerhub_non_200_returns_nothing():
    with patch("plugins.pkg.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(status_code=500)
        assert list(pkg.search_dockerhub("foo")) == []


# --- pkglist / pop3 / pkgn / pkg commands ---


def test_pkglist_lists_all_repos():
    result = pkg.pkglist()
    assert "pypi/pip/python" in result
    assert "aur/yay/picom" in result


def test_pop3_returns_up_to_three_and_stops_on_stop_iteration():
    replies = []

    def _gen():
        yield "a"
        yield "b"

    result = pkg.pop3(iter(_gen()), lambda *args: replies.extend(args))
    assert result is None
    assert replies == ["a", "b"]


def test_pop3_returns_three_lines():
    replies = []

    def _gen():
        yield "a"
        yield "b"
        yield "c"
        yield "d"

    result = pkg.pop3(iter(_gen()), lambda *args: replies.extend(args))
    assert result is None
    assert replies == ["a", "b", "c"]


def test_pop3_no_results():
    result = pkg.pop3(iter([]), lambda *args: None)
    assert result == "No [more] results found."


def test_pkgn_no_queue_for_nick():
    result = pkg.pkgn("", None, "#chan", "alice", lambda *a: None)
    assert result == "Nick 'alice' has no queue."


def test_pkgn_no_queue_for_named_user():
    pkg.results_queue[("#chan", "alice")] = iter(["x"])
    result = pkg.pkgn("bob", None, "#chan", "alice", lambda *a: None)
    assert result == "Nick 'bob' has no queue."


def test_pkgn_returns_next_for_self():
    pkg.results_queue[("#chan", "alice")] = iter(["a", "b"])
    replies = []
    result = pkg.pkgn("", None, "#chan", "alice", lambda *a: replies.extend(a))
    assert result is None
    assert replies == ["a", "b"]


def test_pkgn_returns_next_for_named_user():
    pkg.results_queue[("#chan", "alice")] = iter(["a"])
    pkg.results_queue[("#chan", "bob")] = iter(["z"])
    replies = []
    result = pkg.pkgn(
        "bob", None, "#chan", "alice", lambda *a: replies.extend(a)
    )
    assert result is None
    assert replies == ["z"]


def test_pkg_empty_text():
    result = pkg.pkg("", None, "#chan", "alice", lambda *a: None)
    assert result == "Please specify a repo and query."


def test_pkg_unknown_repo():
    result = pkg.pkg("notarepo foo", None, "#chan", "alice", lambda *a: None)
    assert "not found" in result


def test_pkg_success_stores_queue_and_replies():
    def fake_search(query):
        yield pkg.Package("found", "1.0", "", f"query was {query}")

    with patch.dict(pkg.REPOS, {"testrepo": fake_search}):
        replies = []
        result = pkg.pkg(
            "testrepo hello world",
            None,
            "#chan",
            "alice",
            lambda *a: replies.extend(a),
        )
    assert result is None
    assert len(replies) == 1
    assert "found" in replies[0]
    assert "query was hello world" in replies[0]
    assert ("#chan", "alice") in pkg.results_queue


def test_pkg_no_results():
    def fake_search(query):
        return iter([])

    with patch.dict(pkg.REPOS, {"testrepo": fake_search}):
        result = pkg.pkg(
            "testrepo foo", None, "#chan", "alice", lambda *a: None
        )
    assert result == "No [more] results found."

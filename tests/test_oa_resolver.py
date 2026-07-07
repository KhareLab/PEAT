import requests
import responses

from graph.analysis.oa_resolver import resolve_oa_pdf

DOI = "10.1016/S0969-2126(97)00177-9"
EMAIL = "test@example.edu"
GOOD_TEXT = "x" * 250  # > MIN_TEXT_CHARS


@responses.activate
def test_unpaywall_success(monkeypatch):
    responses.add(
        responses.GET,
        f"https://api.unpaywall.org/v2/{DOI}",
        json={"best_oa_location": {"url_for_pdf": "https://example.org/paper.pdf"}},
        status=200,
    )
    monkeypatch.setattr(
        "graph.analysis.oa_resolver.fetch_pdf_text", lambda url, **kw: GOOD_TEXT
    )

    result = resolve_oa_pdf(DOI, email=EMAIL)

    assert result.status == "found"
    assert result.source == "unpaywall"
    assert result.text == GOOD_TEXT


@responses.activate
def test_unpaywall_fails_openalex_succeeds(monkeypatch):
    responses.add(responses.GET, f"https://api.unpaywall.org/v2/{DOI}", status=404)
    responses.add(
        responses.GET,
        f"https://api.openalex.org/works/doi:{DOI}",
        json={"open_access": {"oa_url": "https://example.org/openalex.pdf"}},
        status=200,
    )

    monkeypatch.setattr(
        "graph.analysis.oa_resolver.fetch_pdf_text", lambda url, **kw: GOOD_TEXT
    )

    result = resolve_oa_pdf(DOI, email=EMAIL)

    assert result.status == "found"
    assert result.source == "openalex"
    # Unpaywall was attempted (its mock was registered and consumed) before OpenAlex succeeded.
    assert len(responses.calls) == 2
    assert "unpaywall" in responses.calls[0].request.url
    assert "openalex" in responses.calls[1].request.url


@responses.activate
def test_unpaywall_and_openalex_fail_crossref_succeeds(monkeypatch):
    responses.add(responses.GET, f"https://api.unpaywall.org/v2/{DOI}", status=404)
    responses.add(responses.GET, f"https://api.openalex.org/works/doi:{DOI}", status=404)
    responses.add(
        responses.GET,
        f"https://api.crossref.org/works/{DOI}",
        json={"message": {"link": [{"content-type": "application/pdf", "URL": "https://example.org/x.pdf"}]}},
        status=200,
    )
    monkeypatch.setattr(
        "graph.analysis.oa_resolver.fetch_pdf_text", lambda url, **kw: GOOD_TEXT
    )

    result = resolve_oa_pdf(DOI, email=EMAIL)

    assert result.status == "found"
    assert result.source == "crossref"


@responses.activate
def test_all_sources_fail(monkeypatch):
    responses.add(responses.GET, f"https://api.unpaywall.org/v2/{DOI}", status=404)
    responses.add(responses.GET, f"https://api.openalex.org/works/doi:{DOI}", status=404)
    responses.add(responses.GET, f"https://api.crossref.org/works/{DOI}", status=404)

    result = resolve_oa_pdf(DOI, email=EMAIL)

    assert result.status == "not_found"
    assert result.text is None
    assert result.source is None


@responses.activate
def test_no_doi_short_circuits_with_zero_network_calls():
    for doi_value in ("", "N/A", None):
        result = resolve_oa_pdf(doi_value, email=EMAIL)
        assert result.status == "no_doi"
        assert result.text is None
        assert result.source is None

    assert len(responses.calls) == 0


@responses.activate
def test_unpaywall_skipped_when_no_email():
    responses.add(
        responses.GET,
        f"https://api.openalex.org/works/doi:{DOI}",
        status=404,
    )
    responses.add(responses.GET, f"https://api.crossref.org/works/{DOI}", status=404)

    result = resolve_oa_pdf(DOI, email="")

    assert result.status == "not_found"
    # No unpaywall.org call should appear at all.
    assert all("unpaywall" not in call.request.url for call in responses.calls)


@responses.activate
def test_each_source_error_does_not_crash(monkeypatch):
    responses.add(
        responses.GET,
        f"https://api.unpaywall.org/v2/{DOI}",
        body=requests.exceptions.ConnectionError("boom"),
    )
    responses.add(
        responses.GET,
        f"https://api.openalex.org/works/doi:{DOI}",
        body=requests.exceptions.Timeout("boom"),
    )
    responses.add(responses.GET, f"https://api.crossref.org/works/{DOI}", status=500)

    result = resolve_oa_pdf(DOI, email=EMAIL)

    assert result.status == "not_found"
    assert result.text is None

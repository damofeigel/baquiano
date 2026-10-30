"""Tests for src/core/matching.py: name cleaning, countries and sister sites."""

from __future__ import annotations

import pytest

from src.core.matching import (
    company_domain, is_shared_site, website_domain, looks_like_category, name_from_website, normalize_country,
    registrable_domain, sister_of, surely_category, web_url, website_confirms_name,
)


@pytest.mark.parametrize("name, sector", [
    ("AI & SaaS", None), ("FoodTech & AI", None), ("Exit FinTech", None), ("Web3", None),
    ("Exited", None), ("Acquired", None), ("AgTech", None), ("@Tech", None), ("Mobility", None),
    ("Health", "Health"), ("Deep Tech", "deep tech"),       # a tag card read as name and sector
    ("Kilimo", "Kilimo"),                                    # or a real name copied: the website decides
])
def test_tags_and_badges_may_not_be_names(name, sector):
    assert looks_like_category(name, sector)


@pytest.mark.parametrize("name, sector", [
    ("Puna Bio", "Agtech"), ("Kilimo", None), ("Agrotoken", "FinTech"), ("Energy Vault", None),
    ("AI Bio Labs", None), ("123Seguro", "Insurtech"), ("S4", None), ("Because Energy Matters", None), ("", None),
    ("Health", None), ("Kilimo", "Kilim"),
])
def test_company_names_are_kept(name, sector):
    assert not looks_like_category(name, sector)


@pytest.mark.parametrize("name, sure", [
    ("AI & SaaS", True), ("Exit FinTech", True), ("Crypto Startups", True), ("FinTech", True),
    ("Mobility", False), ("Crypto Energy", False), ("Energy", False),     # ordinary words may be a name
    ("All", False), ("Other", False),
    ("Health", False), ("Kilimo", False), ("", False),
])
def test_only_tag_words_make_a_category_certain(name, sure):
    assert surely_category(name) is sure


@pytest.mark.parametrize("name, website_name, confirms", [
    ("Kilimo", "Kilimo", True), ("Logistics", "Logistics", True),
    ("Kilimo", "Kilimoapp", True), ("Puna", "Punabio", True),           # the domain contains the name
    ("Health", "Acme", False), ("Energy", "Lioraenergia", False),       # a different name
    ("Bio", "Punabio", False),                                          # too short to be contained
    ("FinTech", "Fintechlatam", False), ("FinTech", "Fintech", False),  # a tag word never confirms
    ("Kilimo", None, False),
])
def test_a_website_confirms_a_name_it_spells(name, website_name, confirms):
    assert website_confirms_name(name, website_name) is confirms


@pytest.mark.parametrize("url, full", [
    ("www.kigui.io", "https://www.kigui.io"), (" goflux.com.br/es ", "https://goflux.com.br/es"),
    ("https://puna.bio", "https://puna.bio"), ("HTTP://Puna.bio", "HTTP://Puna.bio"),
    ("empresa.com.ar/?next=https://x.com", "https://empresa.com.ar/?next=https://x.com"),
    ("//acme.com/about", "https://acme.com/about"),                 # protocol-relative
    ("http:puna.bio", "http://puna.bio"), ("https:/glaia.com", "https://glaia.com"),   # typos
    ("acme.com:8080/x", "https://acme.com:8080/x"),                 # a port, not a scheme
    ("https://acme.com\\@foo.ar", "https://acme.com/@foo.ar"),      # a backslash is a "/"
    ("neworgans.com.ar /es", "https://neworgans.com.ar/es"),        # a space in the host
    ("https://neworgans.com.ar :8443/", "https://neworgans.com.ar:8443/"),
])
def test_web_url(url, full):
    assert web_url(url) == full


@pytest.mark.parametrize("url", [
    "tel:+5491111111111", "mailto:hola@acme.com", "whatsapp:+5491111", "javascript:void(0)",
    "ftp://acme.com", "hola@gmail.com", "contacto@acme.com.ar/x",    # other schemes, emails
    "/portfolio", "not a url", "acme .com", "", "   ", None,
])
def test_values_that_are_no_website(url):
    assert web_url(url) is None and website_domain(url) is None


@pytest.mark.parametrize("url, domain", [
    ("https://www.Puna.bio/es", "puna.bio"), ("puna.bio", "puna.bio"), ("www.puna.com.ar/", "puna.com.ar"),
    ("empresa.com.ar/?next=https://x.com", "empresa.com.ar"),       # '://' later in the URL
    ("empresa.com.ar/go?u=http://x.com/a", "empresa.com.ar"),
    ("https://empresa.com.ar/?next=https://x.com", "empresa.com.ar"),
    ("  neworgans.com.ar  ", "neworgans.com.ar"),
    ("neworgans.com.ar.", "neworgans.com.ar"), ("https://www.neworgans.com.ar./es", "neworgans.com.ar"),
    ("https://acme.com:443/x", "acme.com"), ("acme.com:8080", "acme.com"),          # a port
    ("https://foo.ar:pw@evil.com", "evil.com"), ("https://user@Acme.com", "acme.com"),  # user:password
    ("https://[broken", None), ("https://[website]", None),
    ("//acme.com", "acme.com"), ("http:puna.bio", "puna.bio"),
    ("https://acme.com\\@foo.ar", "acme.com"), ("https://foo.ar\\@evil.com", "foo.ar"),   # as a browser reads it
    ("neworgans.com.ar /es", "neworgans.com.ar"), ("https://neworgans.com.ar :8443/", "neworgans.com.ar"),
    ("neworgans.com.ar. /x", "neworgans.com.ar"),
    (None, None), ("", None), ("   ", None),
])
def test_website_domain(url, domain):
    assert website_domain(url) == domain


@pytest.mark.parametrize("url, name", [
    ("http://www.agribodytech.com", "Agribodytech"),
    ("https://www.lioraenergia.com.br/", "Lioraenergia"),
    ("https://www.linkedin.com/company/acme", None),        # a platform, not the company's site
    ("https://acme.medium.com", None),
    ("https://ab.io", None),                                # too short to be a name
    ("https://app.foo.com/login", "Foo"),                   # a subdomain is not the name
    ("https://en.agribodytech.com", "Agribodytech"),
    ("https://www.puna.com.ar", "Puna"),                    # two-part suffix
    ("https://shop.acme.co.uk", "Acme"),
    (None, None), ("", None),
])
def test_name_from_website(url, name):
    assert name_from_website(url) == name


@pytest.mark.parametrize("raw, country", [
    ("USA", "United States"), ("U.S.A.", "United States"), ("US", "United States"), ("EE.UU.", "United States"),
    ("Estados Unidos", "United States"), ("United States", "United States"), ("UK", "United Kingdom"),
    ("Brasil", "Brazil"), ("México", "Mexico"), ("  Argentina ", "Argentina"), ("República Argentina", "Argentina"),
    ("Ireland", "Ireland"), ("", None), ("   ", None), (None, None),
    ("ARGENTINA", "Argentina"), ("argentina", "Argentina"), ("ArGeNtInA", "Argentina"),   # case
    ("EL SALVADOR", "El Salvador"), ("south  korea", "South Korea"),
    ("BOSNIA AND HERZEGOVINA", "Bosnia and Herzegovina"),       # small words stay small
    ("MEXICO", "Mexico"), ("PERU", "Peru"), ("Peru", "Peru"), ("ESPAÑA", "Spain"),
    ("república argentina", "Argentina"), ("REPUBLICA ARGENTINA", "Argentina"),     # with or without accent
    ("TÜRKIYE", "Türkiye"),                                    # accents kept outside the list
])
def test_normalize_country(raw, country):
    assert normalize_country(raw) == country


@pytest.mark.parametrize("site, others, parent", [
    ("theyieldlablatam.com", ["theyieldlab.com", "gridexponential.com"], "theyieldlab.com"),
    ("theyieldlab.asia", ["theyieldlab.com"], None),               # same label: not a sister, the same brand
    ("theyieldlab.com", ["theyieldlablatam.com"], None),            # the main site has no parent
    ("gridx.co", ["gridexponential.com"], None),
    ("500global.com", ["500.co"], None),                            # "500" is too short to claim others
    ("alaya-capital.com", ["alayacapital.com"], None),
    ("theyieldlab-asia.com", ["theyieldlab.com"], "theyieldlab.com"),        # region after a hyphen
    ("argentinatech.com", ["argentina.gob.ar"], None),                      # a prefix is not a region
    ("argentinaventures.vc", ["argentina.gob.ar"], None),
    ("drapercygnus.com", ["draper.vc"], None),
    ("kaszeklatam.com", ["kaszek.com", "gridexponential.com"], "kaszek.com"),
])
def test_sister_sites(site, others, parent):
    assert sister_of(site, others) == parent


@pytest.mark.parametrize("url, domain", [
    ("https://www.beeflow.com/", "beeflow.com"),
    ("puna.bio", "puna.bio"),                                   # no scheme
    ("https://www.linkedin.com/company/acme", None),
    ("https://ar.linkedin.com/in/someone", None),               # a platform's subdomain
    ("https://acme.medium.com/post", None),
    ("https://sites.google.com/view/acme", None),
    ("https://acme.wixsite.com/home", None),
    ("https://linkedinsights.io", "linkedinsights.io"),         # only a name that looks like one
    (None, None), ("", None),
])
def test_company_domain(url, domain):
    assert company_domain(url) == domain


def test_is_shared_site():
    assert is_shared_site("linkedin.com") and is_shared_site("es.linkedin.com")
    assert not is_shared_site("notlinkedin.com") and not is_shared_site(None)


@pytest.mark.parametrize("domain, registrable", [
    ("foo.com", "foo.com"), ("app.foo.com", "foo.com"), ("www.foo.com", "foo.com"),
    ("puna.com.ar", "puna.com.ar"), ("en.puna.com.ar", "puna.com.ar"), ("argentina.gob.ar", "argentina.gob.ar"),
    ("acme.co.uk", "acme.co.uk"), ("puna.bio", "puna.bio"), ("17sigma.vc", "17sigma.vc"),
    ("lioraenergia.com.br", "lioraenergia.com.br"), (None, None), ("", None),
])
def test_registrable_domain(domain, registrable):
    assert registrable_domain(domain) == registrable


def test_no_name_from_the_funds_own_site():
    assert name_from_website("https://17sigma.vc/portfolio/ai-saas", {"17sigma.vc"}) is None
    assert name_from_website("https://blog.17sigma.vc/x", {"17sigma.vc"}) is None
    assert name_from_website("https://lioraenergia.com.br", {"17sigma.vc"}) == "Lioraenergia"


def test_values_that_are_no_website_never_share_a_domain():
    """tel: numbers, emails and broken links used to collapse into one "domain" ('tel', 'gmail.com')."""
    assert {website_domain(u) for u in ["tel:+5491111", "tel:+5492222", "http:puna.bio", "http:glaia.com"]} == {
        None, "puna.bio", "glaia.com"}
    assert company_domain("hola@gmail.com") is None and company_domain("https://gmail.com") is None
    assert name_from_website("https://www.gmail.com") is None

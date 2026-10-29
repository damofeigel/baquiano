"""Tests for src/core/matching.py: name cleaning, countries and sister sites."""

from __future__ import annotations

import pytest

from src.core.matching import (
    company_domain, is_shared_site, looks_like_category, name_from_website, normalize_country,
    registrable_domain, sister_of,
)


@pytest.mark.parametrize("name, sector", [
    ("AI & SaaS", None), ("FoodTech & AI", None), ("Exit FinTech", None), ("Web3", None),
    ("Exited", None), ("Acquired", None), ("AgTech", None), ("@Tech", None),
    ("Liora", "Liora"),                                     # identical to its own sector tag
])
def test_tags_and_badges_are_not_names(name, sector):
    assert looks_like_category(name, sector)


@pytest.mark.parametrize("name, sector", [
    ("Puna Bio", "Agtech"), ("Kilimo", None), ("Agrotoken", "FinTech"), ("Energy Vault", None),
    ("AI Bio Labs", None), ("123Seguro", "Insurtech"), ("S4", None), ("Because Energy Matters", None), ("", None),
])
def test_company_names_are_kept(name, sector):
    assert not looks_like_category(name, sector)


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

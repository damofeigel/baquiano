"""Tests for src/core/matching.py: name cleaning, countries and sister sites."""

from __future__ import annotations

import pytest

from src.core.matching import looks_like_category, name_from_website, normalize_country, sister_of


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
])
def test_sister_sites(site, others, parent):
    assert sister_of(site, others) == parent

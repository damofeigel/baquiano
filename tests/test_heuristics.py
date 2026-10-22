"""Tests for the deterministic portfolio heuristics (pure HTML, no browser)."""

from __future__ import annotations

import asyncio

import pytest

from src.custom_agents.portfolio_agent import agent as agent_module
from src.custom_agents.portfolio_agent.agent import build_worker_prompt
from src.custom_agents.portfolio_agent.heuristics import (
    country_from_tags,
    find_sister_sites,
    split_country,
    fund_name_from_title,
    extract_companies,
    find_next_page_link,
    find_portfolio_links,
    guess_fund_name,
)
from src.custom_agents.portfolio_agent.models import StartupInfo
from src.custom_agents.portfolio_agent.prepass import PrepassResult

BASE = "https://fund.example/portfolio"

NAV = """
<nav><a href="/">Home</a><a href="/team">Equipo</a><a href="/portfolio">Portfolio</a>
<a href="/news">News</a><a href="/contact">Contacto</a></nav>
"""

WEBFLOW_GRID = """
<div class="w-dyn-items grid">
  <div class="w-dyn-item card">
    <img src="a.png" alt="Logo Andes Bio">
    <div class="card-name">Andes Bio</div>
    <p>Desarrolla bioinsumos para el agro a partir de microorganismos nativos.</p>
    <a href="https://andesbio.com.ar">Website</a>
    <a href="https://linkedin.com/company/andesbio">LinkedIn</a>
  </div>
  <div class="w-dyn-item card">
    <img src="b.png" alt="Patagonia Quantum logo">
    <div class="card-name">Patagonia Quantum</div>
    <p>Quantum sensors for mining exploration in the Andes region.</p>
    <a href="https://pquantum.io">Website</a>
  </div>
  <div class="w-dyn-item card">
    <img src="c.png" alt="">
    <div class="card-name">Litio Tech</div>
    <p>Direct lithium extraction technology for brine deposits.</p>
    <a href="https://litio.tech/">Website</a>
  </div>
  <div class="w-dyn-item card">
    <img src="d.png" alt="Robotica Sur">
    <div class="card-name">Robótica Sur</div>
    <a href="/portfolio/robotica-sur">Ver más</a>
  </div>
</div>
"""

TEAM_GRID = """
<div class="team-grid">
  <div class="member"><img alt="Ana Pérez"><h4>Ana Pérez</h4><span>Managing Partner</span>
    <a href="https://linkedin.com/in/ana">in</a></div>
  <div class="member"><img alt="Juan Gómez"><h4>Juan Gómez</h4><span>Partner</span>
    <a href="https://linkedin.com/in/juan">in</a></div>
  <div class="member"><img alt="Sofía Ruiz"><h4>Sofía Ruiz</h4><span>Associate</span>
    <a href="https://linkedin.com/in/sofia">in</a></div>
  <div class="member"><img alt="Leo Díaz"><h4>Leo Díaz</h4><span>Analyst</span>
    <a href="https://linkedin.com/in/leo">in</a></div>
</div>
"""


def _page(body: str, title: str = "Portfolio | Fondo Andino") -> str:
    return f"<html><head><title>{title}</title></head><body>{NAV}{body}</body></html>"


def test_extracts_card_grid_and_ignores_team_and_nav():
    companies = extract_companies(_page(TEAM_GRID + WEBFLOW_GRID), BASE)
    by_name = {c.name: c for c in companies}

    assert set(by_name) == {"Andes Bio", "Patagonia Quantum", "Litio Tech", "Robótica Sur"}
    assert by_name["Andes Bio"].website == "https://andesbio.com.ar"  # social link skipped
    assert by_name["Robótica Sur"].website is None                    # fund profile isn't a website
    assert "bioinsumos" in by_name["Andes Bio"].description
    assert all(c.source_url == BASE for c in companies)


def test_extracts_logo_wall_from_alt_text():
    logos = "".join(
        f'<a href="https://{n.lower()}.com"><img src="{n}.png" alt="{n} logo"></a>'
        for n in ["Acme", "Globex", "Initech", "Umbrella", "Hooli"]
    )
    companies = extract_companies(_page(f'<div class="logos">{logos}</div>'), BASE)

    assert [c.name for c in companies] == ["Acme", "Globex", "Initech", "Umbrella", "Hooli"]
    assert companies[0].website == "https://acme.com"


def test_page_sections_are_not_mistaken_for_cards():
    sections = "".join(
        f'<section class="section"><h2>{h}</h2><p>{"Texto largo sobre el fondo. " * 40}</p></section>'
        for h in ["Quiénes somos", "Tesis", "Portfolio", "Contacto"]
    )
    assert extract_companies(_page(sections), BASE) == []


def test_team_only_page_returns_nothing():
    assert extract_companies(_page(TEAM_GRID), BASE) == []


def test_find_portfolio_links_prefers_listing_page():
    html = _page('<a href="/portfolio/andes-bio">Andes Bio</a><a href="/nuestro-portafolio">Nuestro portafolio</a>')
    links = find_portfolio_links(html, "https://fund.example/")

    assert links[0] in {"https://fund.example/portfolio", "https://fund.example/nuestro-portafolio"}
    assert "https://fund.example/portfolio/andes-bio" not in links[:2]


def test_guess_fund_name_from_title_and_og_site_name():
    assert guess_fund_name(_page("")) == "Fondo Andino"
    og = '<html><head><meta property="og:site_name" content="Kamay Ventures"><title>Home</title></head></html>'
    assert guess_fund_name(og) == "Kamay Ventures"


def test_find_next_page_link():
    html = _page('<a href="/portfolio?page=2" class="pagination-next">Siguiente</a>')
    assert find_next_page_link(html, BASE, set()) == "https://fund.example/portfolio?page=2"
    assert find_next_page_link(html, BASE, {"https://fund.example/portfolio?page=2"}) is None


# --- Agent integration -----------------------------------------------------------

def test_worker_prompt_lists_prepass_companies():
    prepass = PrepassResult(
        portfolio_url=BASE,
        companies=[StartupInfo(name="Andes Bio"), StartupInfo(name="Litio Tech")],
    )
    prompt = build_worker_prompt("https://fund.example", prepass)

    assert "recorded 2 companies: Andes Bio, Litio Tech" in prompt
    assert "remove_companies" in prompt


def test_deterministic_mode_skips_the_llm(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module.config, "OUTPUT_DIR", tmp_path)

    async def fake_prepass(browser_tools, url):
        return PrepassResult(
            fund_name_hint="Fondo Andino",
            portfolio_url=BASE,
            companies=[StartupInfo(name="Andes Bio", source_url=BASE)],
        )

    async def no_llm(*args, **kwargs):
        raise AssertionError("Runner.run must not be called in deterministic mode")

    monkeypatch.setattr(agent_module, "run_prepass", fake_prepass)
    monkeypatch.setattr(agent_module.Runner, "run", no_llm)
    portfolio_agent = agent_module.PortfolioAgent()
    try:
        result = asyncio.run(portfolio_agent.run("https://fund.example", mode="deterministic"))
    finally:
        portfolio_agent.close()

    assert result.fund_name == "Fondo Andino"
    assert result.source_url == BASE
    assert [s.name for s in result.startups] == ["Andes Bio"]
    assert result.errors == []


# Shaped like newtopia.vc (WordPress/Elementor): every card shares core classes,
# most add a per-card filter class, some have none; descriptions live only in the
# logo alt text; exits are noted in the name.
def _elementor_card(name, desc, filter_id=None, post_id=1):
    extra = f" elementor-filter-{filter_id}" if filter_id else ""
    return (
        f'<article class="elementor-portfolio-item elementor-post post-{post_id} portfolio '
        f'type-portfolio status-publish has-post-thumbnail{extra}">'
        f'<a class="elementor-post__thumbnail__link" href="https://fund.example/portfolio/{post_id}/">'
        f'<img alt="{name} – {desc} | Fund portfolio">'
        f'<div class="elementor-portfolio-item__overlay"><h3>{name}</h3></div></a></article>'
    )


ELEMENTOR_HTML = _page(
    '<div class="elementor-portfolio elementor-grid">'
    + _elementor_card("Aviva", "Phygital neobank for emerging communities", 27, 1)
    + _elementor_card("Humand", "Super app for deskless workers", None, 2)
    + _elementor_card("Arkimedes", "Agentic AI for private equity", 34, 3)
    + _elementor_card("IORQ", "Private credit orchestrator", 27, 4)
    + _elementor_card("Roxom", "Bitcoin-native capital markets", 31, 5)
    + _elementor_card("Lara (exit to Visma)", "People experience platform", 27, 6)
    + "</div>"
)


def test_cards_with_optional_classes_stay_one_grid():
    companies = extract_companies(ELEMENTOR_HTML, "https://fund.example/portfolio/")
    assert [c.name for c in companies] == ["Aviva", "Humand", "Arkimedes", "IORQ", "Roxom", "Lara"]


def test_descriptions_from_logo_alt_text_and_exit_notes():
    by_name = {c.name: c for c in extract_companies(ELEMENTOR_HTML, "https://fund.example/portfolio/")}
    assert by_name["Aviva"].description == "Phygital neobank for emerging communities"
    assert by_name["Lara"].description == "People experience platform. Exit to Visma."
    assert by_name["Aviva"].website is None   # the card links to the fund's own profile page


@pytest.mark.parametrize("title, name", [
    ("Newtopia Portfolio | 90 Latin American Startups", "Newtopia"),
    ("GRIDX | Portfolio", "GRIDX"),
    ("Portafolio | Fondo Andino", "Fondo Andino"),
    ("Home - Kamay Ventures", "Kamay Ventures"),        # "Ventures" is part of the name
    ("Our Portfolio – Acme Investments", "Acme Investments"),
    ("Portfolio", None),
])
def test_fund_name_from_title(title, name):
    assert fund_name_from_title(title) == name


# --- Regional sister sites (theyieldlab.com -> theyieldlablatam.com, .eu, .asia) ----

# No portfolio link of its own (unlike _page(), which adds one in the nav)
MAIN_SITE = (
    "<html><head><title>The Yield Lab</title></head><body>"
    "<h2>The Yield Lab's Regional Sites</h2>"
    '<a href="https://theyieldlablatam.com/">LATAM</a>'
    '<a href="https://theyieldlab.eu/">Europe</a>'
    '<a href="https://www.linkedin.com/company/theyieldlab">LinkedIn</a>'
    '<a href="https://partner-fund.example/">Our partner</a>'
    "</body></html>"
)


def test_find_sister_sites():
    assert find_sister_sites(MAIN_SITE, "https://theyieldlab.com/") == [
        "https://theyieldlablatam.com/", "https://theyieldlab.eu/",
    ]
    # Short brands would match unrelated domains, so none are looked for
    assert find_sister_sites('<a href="https://abcxyz.com/">x</a>', "https://abc.com/") == []


@pytest.mark.parametrize("name, expected", [
    ("BemAgro | Brazil", ("BemAgro", "Brazil")),
    ("Agricapital - Colombia", ("Agricapital", "Colombia")),
    ("Puna Bio", ("Puna Bio", None)),
    ("Lara - People", ("Lara - People", None)),     # not a country: left alone
])
def test_split_country(name, expected):
    assert split_country(name) == expected


def _regional_card(name, country):
    return (f'<div class="card"><img alt="{name}"><h3>{name} | {country}</h3>'
            f'<a href="https://{name.lower()}.example">Website</a></div>')


class FakeBrowser:
    """Serves canned pages by URL, like BrowserManager, without a network."""

    def __init__(self, pages):
        self.pages, self.page = pages, type("Page", (), {"url": ""})()

    def go_to(self, url):
        self.page.url = url if url in self.pages else url.rstrip("/") + "/"
        if self.page.url not in self.pages:
            raise RuntimeError(f"net::ERR_NAME_NOT_RESOLVED at {url}")

    def get_page_content(self):
        return self.pages[self.page.url]

    def expand_listing(self):
        return {"scrolls": 0, "clicks": 0}


def test_prepass_collects_portfolios_from_sister_sites():
    from src.custom_agents.portfolio_agent.prepass import _prepass_sync

    latam = _page('<a href="/portfolio/">Portfolio</a>', title="The Yield Lab Latam")
    latam_portfolio = _page('<div class="grid">' + "".join(
        _regional_card(n, c) for n, c in
        [("Auravant", "Argentina"), ("Kilimo", "Argentina"), ("BemAgro", "Brazil"), ("Verqor", "Mexico")]
    ) + "</div>")
    eu = "<html><body><h2>Events and news</h2></body></html>"
    browser = FakeBrowser({
        "https://theyieldlab.com/": MAIN_SITE,
        "https://theyieldlablatam.com/": latam,
        "https://theyieldlablatam.com/portfolio/": latam_portfolio,
        "https://theyieldlab.eu/": eu,
    })
    result = _prepass_sync(browser, "https://theyieldlab.com/")

    assert result.portfolio_urls == ["https://theyieldlablatam.com/portfolio/"]
    assert {c.name: c.country for c in result.companies} == {
        "Auravant": "Argentina", "Kilimo": "Argentina", "BemAgro": "Brazil", "Verqor": "Mexico",
    }
    assert all(c.source_url == "https://theyieldlablatam.com/portfolio/" for c in result.companies)


def test_former_names_and_arrow_links_are_cleaned():
    cards = "".join([
        '<div class="c"><img alt="x"><h3>Dexer (formely AgVoice)</h3><p>Voice AI for agronomists in the field</p>'
        '<a href="https://dexer.example">↗ Visit website</a></div>',
        '<div class="c"><img alt="x"><h3>Talam Biotech (formerly MicroGen Biotech)</h3><p>Soil remediation with microbes today</p>'
        '<a href="https://talam.example">↗ Visit website</a></div>',
        '<div class="c"><img alt="x"><h3>Spotta</h3><p>Smart pest monitoring traps for farms</p>'
        '<a href="https://spotta.example">↗ Visit website</a></div>',
        '<div class="c"><img alt="x"><h3>Sundew</h3><p>Sustainable crop protection coatings</p>'
        '<a href="https://sundew.example">↗ Visit website</a></div>',
    ])
    by_name = {c.name: c for c in extract_companies(_page(f'<div class="grid">{cards}</div>'), BASE)}
    assert set(by_name) == {"Dexer", "Talam Biotech", "Spotta", "Sundew"}
    for junk in ["Visit website", "Revisit consent button", "Cookie settings"]:
        assert extract_companies(_page(f'<div class="g">{cards}<div class="c"><h3>{junk}</h3></div></div>'), BASE) \
            and junk not in {c.name for c in extract_companies(_page(f'<div class="g">{cards}</div>'), BASE)}
    assert by_name["Dexer"].description.endswith("Formerly AgVoice.")
    assert by_name["Talam Biotech"].description.endswith("Formerly MicroGen Biotech.")


# --- theyieldlab.eu: cookie dialog, cards without name text, status badges ----------

CONSENT_DIALOG = (
    '<div class="cky-consent-container"><div class="cky-preference-body">'
    + "".join(
        f'<div class="cky-accordion"><h3>{c}</h3><p>{c} cookies are used to understand how visitors interact.</p></div>'
        for c in ["Necessary", "Functional", "Analytics", "Performance", "Advertisement"]
    )
    + "</div></div>"
)


def _eu_card(slug, alt):
    return (f'<li class="post-card"><a aria-label="Read more about this company" '
            f'href="https://theyieldlab.eu/portfolio-items/{slug}/"></a><img alt="{alt}"></li>')


EU_HTML = _page(CONSENT_DIALOG + '<ul class="grid">' + "".join([
    _eu_card("apisprotect", "ApisProtect uses in-hive sensors to monitor honey bee colonies remotely."),
    _eu_card("pure-ocean-algae", "A raw material harvested sustainably from Irish seaweed beds and coasts."),
    _eu_card("kaffe-bueno", "Kaffe Bueno utilises coffee by-products – such as spent coffee grounds – as ingredients."),
    _eu_card("collie", "Collie develops wearable technologies for livestock to monitor their health."),
]) + "</ul>")


def test_cookie_dialogs_are_not_read_as_companies_or_page_text():
    from src.core.browser import extract_page_content

    names = {c.name for c in extract_companies(EU_HTML, "https://theyieldlab.eu/company-portfolio/")}
    assert not names & {"Necessary", "Functional", "Analytics", "Performance", "Advertisement"}
    assert "cookies" not in extract_page_content(EU_HTML).plain_text.lower()


def test_names_from_detail_link_slugs():
    by_name = {c.name: c for c in extract_companies(EU_HTML, "https://theyieldlab.eu/company-portfolio/")}
    assert set(by_name) == {"ApisProtect", "Pure Ocean Algae", "Kaffe Bueno", "Collie"}
    assert by_name["ApisProtect"].description.startswith("ApisProtect uses in-hive sensors")


def test_status_badge_is_not_a_name():
    cards = "".join(
        f'<div class="c"><span>Exited</span><img alt="{n}"><a href="https://{n.lower()}.example">site</a></div>'
        for n in ["Agribody", "Holganix", "Hydrosat", "Lepidext"]
    )
    names = [c.name for c in extract_companies(_page(f'<div class="g">{cards}</div>'), BASE)]
    assert names == ["Agribody", "Holganix", "Hydrosat", "Lepidext"]


@pytest.mark.parametrize("tags, country", [
    ("Crop Science Singapore Food Security", "Singapore"),
    ("Aquaculture Supply Chain Canada GHG Emissions", "Canada"),
    ("TYL Europe TYL Latam Precision Ag", None),           # a region, not a country
    ("Kilimo is a Big Data solution for water management.", None),  # prose
])
def test_country_from_tags(tags, country):
    assert country_from_tags(tags) == country

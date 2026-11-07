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
        return type("Content", (), {"links": []})()

    def click_next_page(self):
        return False

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


def _company_cards(names):
    return "".join(f'<div class="c"><img alt="{n}"><a href="https://{n.lower()}.example">{n}</a></div>' for n in names)


FOUR = ["Agribody", "Holganix", "Hydrosat", "Lepidext"]


def test_a_filter_panel_is_not_a_grid():
    # 500.co: 297 options like "Lebanon 12" beat 10 real cards per page
    filters = "".join(
        f'<div class="row"><div><button role="checkbox"></button><label>{f}</label></div><div>{n}</div></div>'
        for n, f in enumerate(["Lebanon", "Robotics", "Series A", "GA 25", "Kuwait", "Legal", "Mobile", "Seed"] * 3)
    )
    page = _page(f'<div class="panel">{filters}</div><div class="g">{_company_cards(FOUR)}</div>')
    assert [c.name for c in extract_companies(page, BASE)] == FOUR


def test_facet_rows_go_but_a_grid_inside_a_facet_template_stays():
    # sosv.com: "facetwp-checkbox" options, and the real grid is "facetwp-template"
    facets = "".join(f'<div class="facetwp-checkbox"><span>HAX {n:02}</span><span>{n}</span></div>' for n in range(20))
    grid = "".join(
        f'<div class="item"><h3><a href="https://fund.example/company/{n.lower()}/">{n}</a></h3>'
        f'<p>Long enough text about what {n} does for its customers.</p></div>' for n in FOUR)
    page = _page(f'<div>{facets}</div><div class="facetwp-template">{grid}</div>')
    assert [c.name for c in extract_companies(page, BASE)] == FOUR


def test_filter_chips_are_not_a_grid():
    # cites-gss.com: <a href="#" data-term-c1="pharma-en">Pharma</a>
    chips = "".join(f'<a href="#" data-term-c1="{t}">{t}</a>' for t in ["Pharma", "Agtech", "AI", "Food", "Med", "All"])
    page = _page(f'<div>{chips}</div><div class="g">{_company_cards(FOUR)}</div>')
    assert [c.name for c in extract_companies(page, BASE)] == FOUR


def test_a_list_of_counts_is_not_a_grid():
    rows = "".join(f'<div class="r"><span>{n}</span></div>' for n in range(1, 30))
    assert extract_companies(_page(f'<div class="g">{rows}</div>'), BASE) == []


def test_a_search_form_around_the_grid_keeps_the_grid():
    # antler.co wraps its directory in one form; a contact form still goes
    page = _page(f'<form><input type="search"><div class="g">{_company_cards(FOUR)}</div></form>')
    assert [c.name for c in extract_companies(page, BASE)] == FOUR
    assert extract_companies(_page('<form><input><div class="g"><div>a</div><div>b</div></div></form>'), BASE) == []


def test_tag_cards_are_named_from_their_links_before_looking_for_duplicates():
    # 17sigma.com: the label is all a card says, and two companies can share it
    cards = "".join(
        f'<div class="c"><a href="https://{d}/"><img alt=""><p>{label}</p></a></div>'
        for d, label in [("mentatech.io", "SaaS, Argentina"), ("buk.cl", "SaaS, Chile"), ("bhub.ai", "SaaS, Brazil"),
                         ("hedix.com.br", "SaaS, Brazil"), ("credito.aro.ia.br", "FinTech & AI, Brazil")])
    by_name = {c.name: c for c in extract_companies(_page(f'<div class="g">{cards}</div>'), BASE)}
    assert set(by_name) == {"Mentatech", "Buk", "Bhub", "Hedix", "Aro"}
    assert (by_name["Mentatech"].country, by_name["Mentatech"].sector) == ("Argentina", "SaaS")


def test_parts_of_a_card_do_not_count_as_cards():
    # 500.co: each company is a logo row and a text block, with a legal name in the alt
    cards = "".join(
        f'<div class="card"><div class="top"><img alt="{n} Holdings, Inc."><a href="https://{n.lower()}.example">{n}</a></div>'
        f'<div class="text"><span>{n.upper()}</span><span>{"Long text about the company. " * 4}</span></div></div>'
        for n in FOUR)
    assert [c.name for c in extract_companies(_page(f'<div class="g">{cards}</div>'), BASE)] == FOUR


def test_long_cards_with_their_own_links_are_still_cards():
    cards = "".join(f'<div class="c"><h3>{n}</h3><a href="https://{n.lower()}.example">site</a><p>{"Text. " * 200}</p></div>'
                    for n in FOUR)
    assert [c.name for c in extract_companies(_page(f'<div class="g">{cards}</div>'), BASE)] == FOUR


def test_card_links_to_the_funds_own_company_page():
    cards = "".join(f'<div class="c"><img alt="{n}"><a href="{BASE}company/{n.lower()}/">More</a></div>' for n in FOUR)
    companies = extract_companies(_page(f'<div class="g">{cards}</div>'), BASE)
    assert companies[0].website is None and companies[0].profile_url == f"{BASE}company/agribody/"


def test_a_cards_location_gives_its_country():
    cards = "".join(
        f'<div class="c"><h3>{n}</h3><img alt=""><div>Locations: {loc}</div></div>'
        for n, loc in zip(FOUR, ["United States", "Canada", "Boston, MA", "Argentina"]))
    by_name = {c.name: c.country for c in extract_companies(_page(f'<div class="g">{cards}</div>'), BASE)}
    assert by_name == {"Agribody": "United States", "Holganix": "Canada", "Hydrosat": None, "Lepidext": "Argentina"}


@pytest.mark.parametrize("caption", ["peec ai team photo", "Two founders posing for photo in Europe", "Reebelo founders"])
def test_photo_captions_are_not_names(caption):
    cards = _company_cards(FOUR) + f'<div class="c"><img alt="{caption}"></div>'
    assert [c.name for c in extract_companies(_page(f'<div class="g">{cards}</div>'), BASE)] == FOUR


def test_a_heading_that_describes_the_company_yields_to_the_link():
    cards = "".join(
        f'<div class="c"><a href="{BASE}portfolio/{slug}/"><img alt=""><h3>{text}</h3></a></div>'
        for slug, text in [("ab-astra-2", "Muography for mapping"), ("clover-en", "New generation of mechanical valves"),
                           ("radbio-2", "Radiation for cancer treatment now"), ("ardan-en", "New immunotherapy for cancer care")])
    assert {c.name for c in extract_companies(_page(f'<div class="g">{cards}</div>'), BASE)} == {
        "Muography for mapping", "Clover", "Radbio", "Ardan"}    # 3 words: may be a real name


def test_prepass_stops_when_pages_repeat_and_clicks_a_next_control():
    from src.custom_agents.portfolio_agent.prepass import _read_listing

    class Pager(FakeBrowser):
        clicks = 0

        def click_next_page(self):
            self.clicks += 1
            self.page.url = {1: "https://f.example/2", 2: "https://f.example/3"}.get(self.clicks, self.page.url)
            return True

    first, second = _company_cards(FOUR), _company_cards(["Zeta", "Yotta", "Xenon", "Wave"])
    browser = Pager({
        "https://f.example/1": _page(f'<div class="g">{first}</div>'),
        "https://f.example/2": _page(f'<div class="g">{second}</div>'),
        "https://f.example/3": _page(f'<div class="g">{second}</div>'),   # the same again: stop
    })
    browser.go_to("https://f.example/1")
    companies = _read_listing(browser, "https://f.example/1", browser.get_page_content)
    assert [c.name for c in companies] == FOUR + ["Zeta", "Yotta", "Xenon", "Wave"]
    assert browser.clicks == 2


def test_websites_from_the_funds_company_pages():
    from src.custom_agents.portfolio_agent.models import StartupInfo
    from src.custom_agents.portfolio_agent.prepass import fill_websites_from_profiles, website_from_profile

    profile = "https://sosv.com/company/puna-bio/"
    links = [{"href": "https://sosvdeeptech.com/"}, {"href": "https://www.linkedin.com/company/puna"},
             {"href": "https://agfundernews.com/puna-bio-news"}, {"href": "https://puna.bio"}]
    assert website_from_profile(links, "Puna Bio", profile) == "https://puna.bio"
    assert website_from_profile(links[:3], "Puna Bio", profile) is None

    class Browser:
        def go_to(self, url):
            if "broken" in url:
                raise RuntimeError("timeout")
            return type("Content", (), {"links": links})()

    companies = [StartupInfo(name="Puna Bio", profile_url=profile),
                 StartupInfo(name="Broken", profile_url="https://sosv.com/company/broken/"),
                 StartupInfo(name="Has Site", website="https://hassite.example", profile_url=profile),
                 StartupInfo(name="No Page")]
    assert fill_websites_from_profiles(Browser(), companies, []) == 1
    assert [c.website for c in companies] == ["https://puna.bio", None, "https://hassite.example", None]


def test_visiting_company_pages_stops_when_the_site_keeps_failing():
    from src.custom_agents.portfolio_agent.models import StartupInfo
    from src.custom_agents.portfolio_agent import prepass

    class Blocked:
        visits = 0

        def go_to(self, url):
            self.visits += 1
            raise RuntimeError("blocked")

    browser, notes = Blocked(), []
    companies = [StartupInfo(name=f"C{n}", profile_url=f"https://f.example/c{n}") for n in range(40)]
    assert prepass.fill_websites_from_profiles(browser, companies, notes) == 0
    assert browser.visits == prepass.MAX_PROFILE_FAILURES and "failures in a row" in notes[0]


def test_a_tag_card_with_a_short_domain_is_named_from_the_whole_domain():
    cards = "".join(f'<div class="c"><a href="https://{d}/"><img alt=""><p>FinTech, Brazil</p></a></div>'
                    for d in ["ng.cash", "hedix.com.br", "neofin.com.br", "aqmen.ai"])
    assert "Ng.cash" in {c.name for c in extract_companies(_page(f'<div class="g">{cards}</div>'), BASE)}


def test_a_logo_card_is_named_from_its_own_address_not_its_first_text():
    # drapercygnus.vc: a logo, a tag, a description and "discover more"
    cards = "".join(
        f'<div class="c"><img alt=""><div>Bio</div><div>What the company does, in a few words.</div>'
        f'<a href="/startups/{slug}">discover more</a></div>' for slug in ["ayuvant", "ab-astra", "lemon", "splight"])
    assert [c.name for c in extract_companies(_page(f'<div class="g">{cards}</div>'), BASE)] == [
        "Ayuvant", "Ab Astra", "Lemon", "Splight"]


def test_a_card_with_only_a_website_is_named_from_it_and_never_from_a_social_account():
    # nxtp.vc: a close button, a logo and links, with no name in the text
    cards = "".join(
        f'<div class="c"><a href="#">X - CLOSE</a><a href="https://{d}/es"><img alt=""></a>'
        f'<a href="https://www.linkedin.com/company/{d.split(".")[0]}-account/"><img alt=""></a><p>Vertical SaaS</p></div>'
        for d in ["axenya.com", "aloi.tech", "arcusfi.com", "ripio.com"])
    companies = extract_companies(_page(f'<div class="g">{cards}</div>'), BASE)
    assert [c.name for c in companies] == ["Axenya", "Aloi", "Arcusfi", "Ripio"]
    assert companies[0].website == "https://axenya.com/es"


def test_a_link_whose_text_is_its_own_address_beats_other_links():
    # aceleradoralitoral.com.ar: the post's author comes first on each card
    cards = "".join(
        f'<article class="c"><a href="{BASE}/author/alejandro/">Alejandro</a><img alt="">'
        f'<a href="{BASE}/portfolio-items/{name.lower().replace(" ", "-")}/">{name}</a></article>'
        for name in ["ReForest Latam", "UnTech", "Infira", "Inbioar"])
    assert [c.name for c in extract_companies(_page(f'<div class="g">{cards}</div>'), BASE)] == [
        "ReForest Latam", "UnTech", "Infira", "Inbioar"]


def test_when_the_portfolio_page_shows_nothing_the_page_linking_to_it_is_read():
    # kamayventures.com/portfolio asks for an access key; its homepage lists the companies
    from src.custom_agents.portfolio_agent.prepass import _prepass_sync

    home = _page(f'<a href="/portfolio">Portfolio</a><div class="g">{_company_cards(FOUR)}</div>')
    browser = FakeBrowser({"https://kamay.example/": home,
                           "https://kamay.example/portfolio": _page("<p>Enter the access key</p>")})
    result = _prepass_sync(browser, "https://kamay.example/")
    assert result.portfolio_url == "https://kamay.example/"
    assert [c.name for c in result.companies] == FOUR

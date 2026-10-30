# Baquiano

Self-hosted agents that browse the web and extract structured data about **deep tech startups in Argentina**, to feed a database mapping the country's innovation ecosystem.

Each URL you give it names the agent that should handle it. Today there is one agent, `portfolio`, which reads an investment fund's website and lists the startups in its portfolio, with their websites and descriptions. Everything runs locally: a headless Chromium browser (Playwright) and a self-hosted LLM served by vLLM.

---

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Setup](#setup)
- [Usage](#usage)
- [The portfolio agent](#the-portfolio-agent)
- [Output](#output)
- [Merging and enriching startups](#merging-and-enriching-startups)
- [Database and entity resolution](#database-and-entity-resolution)
- [Configuration](#configuration)
- [Adding a new agent](#adding-a-new-agent)
- [Project structure](#project-structure)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)

---

## How it works

```
targets.txt / CLI            pipeline.py                     custom_agents/registry.py
─────────────────     ─────────────────────────     ──────────────────────────────────
portfolio:https://…  →  parse "AGENT:URL", validate  →  AGENTS["portfolio"].run(url, …)
                         every entry, run each one              │
                         and print a summary                    ▼
                                                    portfolio agent
                                                    ├─ pre-pass (no LLM): find the portfolio
                                                    │  page, expand it, parse the company grid
                                                    ├─ LLM agent (vLLM): browse with tools,
                                                    │  fill gaps, fix mistakes
                                                    └─ write output/<fund>_<timestamp>.json
```

- **`pipeline.py`** reads targets, checks that every entry is valid before running anything, runs the named agent for each URL, and reports which runs returned results, came back empty or failed.
- **Agents** live in `src/custom_agents/`. Each one is registered under a name (`portfolio`) and follows a small contract (`src/custom_agents/base.py`), so the pipeline can run any of them the same way.

---

## Requirements

- **Python 3.13** (other 3.11+ versions will probably work but are untested)
- **Chromium for Playwright** (installed in the setup steps)
- **For the LLM modes:** a GPU machine running **vLLM** with a tool-calling model. This project is developed against `meta-llama/Llama-3.1-8B-Instruct`. The `deterministic` mode needs no LLM at all.

---

## Setup

### 1. Python environment

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configuration

```bash
cp .env.example .env
```

Then edit `.env`. At minimum, set `VLLM_MODEL` to the model your server runs (see [Configuration](#configuration)).

### 3. vLLM server (skip this if you only use `deterministic` mode)

The agent uses tool calling, so vLLM must run with a tool parser and with the **chat template included in this repo**, `tool_chat_template_llama3.1_json.jinja`. That template fixes a bug in which recent vLLM versions fail with `can only concatenate str (not "dict") to str` on the second request of every agent run.

Example with Docker (Llama models need a Hugging Face token with access to the model):

```bash
docker run --gpus all -p 8000:8000 \
  -e HUGGING_FACE_HUB_TOKEN=<your-token> \
  -v "$PWD/tool_chat_template_llama3.1_json.jinja:/templates/llama31.jinja" \
  vllm/vllm-openai \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --enable-auto-tool-choice \
  --tool-call-parser llama3_json \
  --chat-template /templates/llama31.jinja \
  --max-model-len 32768
```

Check that it is up:

```bash
curl http://localhost:8000/v1/models
```

vLLM reads the template once at startup: **restart the server after changing it.**

---

## Usage

### Targets: `AGENT:URL`

Every URL is prefixed with the agent that should extract it:

```
portfolio:https://www.gridexponential.com
```

- The agent name is required. List the available agents with `python pipeline.py --list-agents`.
- `https://` is added if the URL has none.

### From the command line

```bash
# One or more targets, separated by spaces or commas
python pipeline.py portfolio:https://www.gridexponential.com
python pipeline.py portfolio:https://fund-a.com portfolio:https://fund-b.com
python pipeline.py "portfolio:https://fund-a.com,portfolio:https://fund-b.com"

# No targets given: read them from targets.txt
python pipeline.py

# Options
python pipeline.py --mode deterministic portfolio:https://www.gridexponential.com
python pipeline.py --max-turns 40 portfolio:https://www.gridexponential.com
python pipeline.py --list-agents
python pipeline.py --help
```

| Option | Applies to | Description |
|---|---|---|
| `--mode {hybrid,deterministic,agent}` | `portfolio` | Extraction mode (see below). Default: `PORTFOLIO_MODE` from `.env`, else `hybrid`. |
| `--max-turns N` | LLM agents | Maximum number of model calls per URL (default 25). |
| `--list-agents` | | List the registered agents and exit. |

### From `targets.txt`

One target per line. Blank lines and comments are ignored:

```
# Argentine deep tech funds
portfolio:https://www.gridexponential.com     # biotech
portfolio:https://www.example-fund.com/portafolio
```

`#` starts a comment only at the start of a line or after a space, so URLs that contain `#` (like `https://fund.com/#/portfolio`) work. Targets given on the command line take priority over the file.

### Validation and the summary

Every entry is checked **before** any run starts. A typo on line 40 is reported immediately, together with every other invalid line:

```
pipeline.py: error: invalid entries:
  targets.txt:2: missing agent in 'https://a.com'; write AGENT:URL, e.g. portfolio:https://a.com (available: portfolio)
  targets.txt:3: unknown agent 'portfolo' in 'portfolo:https://b.com' (available: portfolio)
```

If one URL crashes, the batch continues. At the end you get a summary:

```
Done: 8 with results, 1 empty, 1 failed (of 10).
  EMPTY:  portfolio:https://www.kamayventures.com (Could not parse a company grid on …)
  FAILED: portfolio:https://… (TimeoutError: …)
```

- **Empty** means the agent ran but found nothing. Causes include an unreachable site, a private portfolio, or a page it couldn't parse. The reasons are shown in brackets.
- **Failed** means it crashed. The traceback is in the log.

---

## The portfolio agent

Given a fund's website (the homepage or the portfolio page itself), it finds the fund's portfolio and records every company with its name, website, description and country, filling in only what the site states. `sector` is the fund's own category label for the company (a tag or filter on its page), copied as written and dropped if no page shows it; the classification into technology areas comes later, from `enrich_startups.py`.

### Modes

| Mode | LLM | What it does | When to use |
|---|---|---|---|
| `hybrid` (default) | Yes | The pre-pass parses the portfolio without the LLM, then the agent receives the list, checks for companies it missed (tabs, categories, pagination) and removes wrong entries. | Real runs. Fast, accurate, and robust to odd layouts. |
| `deterministic` | No | Pre-pass only. | Many funds quickly and cheaply, or when no GPU is available. |
| `agent` | Yes | The LLM agent does everything with browser tools. | Portfolios the pre-pass can't parse; debugging the agent. |

### Pre-pass (no LLM)

1. Opens the URL and follows the most likely portfolio link ("portfolio", "portafolio", "empresas", "inversiones", "startups"…). It prefers a dedicated portfolio page over a "featured companies" section on the homepage.
2. Scrolls until the page stops growing and clicks "load more" / "ver más" buttons. It only clicks real buttons, not card links, and goes back if a click navigates away.
3. Finds the company grid: groups of 4 or more sibling elements with the same structure. Each card gives a name (heading, logo alt text or link text), a website (the first link to another site, skipping social networks) and a description. Team grids and page sections are filtered out.
4. Follows "next" / "siguiente" pagination for up to 5 pages.

On GridX it extracts all 86 portfolio companies in a few seconds.

### LLM agent

The agent browses with these tools:

- **Browser:** `navigate_to_url`, `get_current_page_content`, `get_page_content_from_url`, `get_current_page_html`, `search_current_page`, `click_element`, `scroll_page`, `load_more`.
- **Record:** `record_fund`, `record_companies` (at most 15 per call), `remove_companies`, `report_error`, `finish`.

Companies are saved in Python the moment the agent records them, not reconstructed from its final message. So a run that hits the turn limit or a model error still returns everything recorded up to that point.

### Safeguards for small models

Small local models such as Llama 3.1 8B fail in characteristic ways. Each of these has a guard:

| Failure | Guard |
|---|---|
| Sends its whole plan (navigate, record, finish) as many tool calls in one reply, which would run before any page is read | Only the first tool call of each reply is kept (`OneToolCallPerTurn`); the rest are dropped and logged. |
| Invents companies ("Company 1", `www.company1.com`) | An evidence log of all text the browser returned. Names must appear in it, unseen websites are removed, and the fund name is checked too. |
| Calls `finish` before reading anything | `finish` is refused until a page has been read. |
| Records the portfolio URL but never opens it, then finishes with only the homepage's featured companies | `record_fund` replies with the next step ("Open …/portfolio"), and `finish` is refused while that page is unopened. After 2 refusals it's accepted with an error, so a stuck model can't use up every turn. A link that fails to load counts as tried. |
| Uses a LinkedIn or other social profile as the company website | Social-network links are removed in code, whichever path the company came from. |
| Attaches a neighbouring company's website or description (one skipped card shifts every pair after it) | Page text follows the page's layout, so each card's name, description and links sit together instead of in separate lists. Websites whose domain doesn't plausibly match the company name are removed. On GridX this removed 57 of 59 shifted websites and none of the 78 correct ones. Links to the fund's own site are removed too. |
| Saves page navigation as companies ("JOIN US", "Visit our Linkedin profile") | Navigation text (English and Spanish) is rejected as a company name. |
| A long `record_companies` call is cut off at `max_tokens`, so vLLM returns it as plain text and the run would end quietly | Complete companies are recovered from the cut-off text, and the model is told to continue in smaller batches (up to 3 times). |
| Context overflow on long runs | Long pages are split into parts the model reads one at a time (`get_current_page_content(part=N)`), and only the 2 most recent page reads stay in the history. Older ones are replaced with a short note. |
| Writes "not found" or "N/A" instead of null | Converted to null. Implausible years and formatted amounts are cleaned up. |

The evidence check confirms a name **appeared** on a page, not that it is a portfolio company. Deciding that is still the agent's job (or the pre-pass's filters).

---

## Output

Each run writes its own file, so runs never overwrite each other:

```
output/<fund-domain>_<YYYYmmdd-HHMMSS>.json
e.g. output/gridexponential.com_20261010-013619.json
```

If two runs of the same fund finish in the same second, the second gets `-2`, `-3`… The log prints the exact path: `Output written to …`.

Portfolio agent format:

```json
{
  "source_url": "https://www.gridexponential.com/portfolio",
  "fund_name": "GRIDX",
  "startups": [
    {
      "name": "Puna Bio",
      "description": "Puna Bio harnesses extremophile bacteria that have lived on Earth for 3.5 billion years to create the world’s first extremophile bioinoculant.",
      "website": "https://puna.bio",
      "email": null,
      "phone": null,
      "year_created": null,
      "sector": "Agri·food·land use",
      "country": null,
      "funding_round": null,
      "funding_amount_usd": null,
      "source_url": "https://www.gridexponential.com/portfolio",
      "fund_name": "GRIDX"
    }
  ],
  "errors": []
}
```

- `errors` lists problems that may make the result incomplete (turn limit reached, page failed to load, fund name not found). Missing optional fields are normal and are not errors.
- Optional fields are only filled when the source states them. `country` is never guessed from the fund's country, the language or the domain.
- `output/` is in `.gitignore`.

---

## Merging and enriching startups

Two scripts turn the per-fund outputs into one startup list for the ecosystem map.

### 1. Merge the funds: `scripts/merge_portfolios.py`

```bash
python scripts/merge_portfolios.py            # latest output of each fund in output/
python scripts/merge_portfolios.py --all      # every portfolio file, not only the latest
python scripts/merge_portfolios.py output/a.json output/b.json
```

- **The same startup in several funds becomes one entry.** It's matched by website domain, then by name (case, accents and punctuation ignored, plus variants like "Heartbest" / "Heartbest Foods"). Beeflow, in both GRIDX and The Yield Lab, becomes one startup with `funds: ["GRIDX", "The Yield Lab"]`.
- **Every description is kept** in `descriptions`, one per fund, and the longest becomes `description`.
- **Fields that disagree between funds aren't overwritten.** They're listed under `conflicts`.
- **A regional sister site is the same fund.** `theyieldlablatam.com` counts as The Yield Lab, whose own run already follows its regional sites; otherwise every Latam startup would look like it's in two funds.
- **Tags and badges read as names are cleaned up.** Some portfolio pages show logos with a tag, so a crawl can record "AI & SaaS" or "Exited" as a name. Such a record is renamed from its website ("Exited" with agribodytech.com becomes "Agribodytech") or dropped when it has none. A name made only of ordinary words ("Mobility", "Crypto Energy") or equal to its own sector may be a real company: it is kept when its website contains the name ("Kilimo" at kilimoapp.com), and kept with a note when there is no website to check it. Only a sure tag (one with a word like FinTech or SaaS) is dropped. Each change is listed under `cleaning` in the output and printed.
- **Countries get one spelling:** "USA", "U.S.A." and "EE.UU." all become "United States".
- **Output:** `output/startups_merged_<time>.json` with a `summary` (startups, in several funds, Argentine, country unknown, with conflicts, cleaned), plus a `.csv` to open in a spreadsheet.

### 2. Enrich and classify: `scripts/enrich_startups.py`

```bash
python scripts/enrich_startups.py                         # latest merged file; Argentine or unknown country
python scripts/enrich_startups.py --limit 20              # in batches; a rerun continues where it stopped
python scripts/enrich_startups.py --names "Puna Bio" Beeflow
python scripts/enrich_startups.py --all                   # also startups known to be outside Argentina
python scripts/enrich_startups.py output/startups_merged_<new>.json --previous output/startups_enriched_<old>.json
```

For each startup it:
1. reads the startup's homepage and one about/contact page;
2. looks for Argentina signals: a `.ar` domain, a `+54` phone number, Argentine cities. These are only hints: a partner's logo or "Gobierno de Mendoza" also match;
3. makes **one structured LLM call** (JSON schema mode) for `country`, `year_founded`, `summary`, `deep_tech` with a `deep_tech_reason`, `tech_area` and `confidence`;
4. **checks the answer against the pages.** The model must quote the text behind `country` and `year_founded`; if the quote isn't on the pages, the field is set to null and the reason recorded in `checks`. It also flags a `deep_tech` answer that contradicts the chosen area.

Notes:
- **The categories are in `scripts/taxonomy.json`:** the deep-tech definition given to the model, and each area with whether it counts as deep tech. Edit it to change the classification; `tech_area` can only be one of its keys.
- **The funds' `sector` labels** are passed to the model as a hint only. `enrichment.tech_area` is the classification to use; `sector` is just what each fund called it.
- **`country` is only what a page states;** `country_likely` adds "Argentina" when nothing is stated but the site has a `.ar` domain or a +54 phone number. Mentioning Argentina or an Argentine city isn't enough for it, since partners and clients do that too. The summary counts these separately (`argentina_likely`).
- **After a new merge, reuse earlier results** with `--previous`: startups are matched by name or website, so only new ones are sent to the model. `--limit 0` just refreshes an output (country spelling, `country_likely`, the summary) without a model.
- **Startups with no website** are classified from the funds' descriptions alone, which usually means `confidence: low`.
- **Output:** `output/startups_enriched_<time>.json`, named after the merged file, with each startup's `enrichment` and a `summary` (Argentine, Argentine deep tech, by area).
- **Progress is saved after every startup,** and a rerun skips the ones already done, because with gpt-oss a full run takes hours.

## Database and entity resolution

The JSON outputs can be loaded into PostgreSQL, where startups from different funds become one **actor** each, with the relationships between them.

### 1. Start the database

```bash
docker compose up -d        # PostgreSQL 16 on 127.0.0.1:${POSTGRES_PORT}; migrations run on first start
```

Set `POSTGRES_PASSWORD` and `DATABASE_URL` in `.env` (see `.env.example`). The seed in `src/db/seeds/` contains example actors, so it is only loaded with `DB_LOAD_SEED=true`.

### 2. Load the outputs: `scripts/load_db.py`

```bash
python scripts/load_db.py                  # latest output of each fund in output/
python scripts/load_db.py output/a.json    # specific files; --all for every file, --dry-run to change nothing
```

Every record becomes a **mention**, stored as found. A mention is linked to an actor only when that's certain (the same website domain). If only the name is the same, the mention waits for resolution. If nothing similar exists, a new actor is created. Each startup gets an `Investment` relationship from its fund. Loading the same file twice adds nothing.

### 3. Resolve entities: `scripts/resolve_entities.py`

```bash
python scripts/resolve_entities.py                    # score candidate pairs; merge/link the sure ones (--dry-run)
python scripts/resolve_entities.py judge --limit 20   # ask the LLM about pairs in review (--dry-run)
python scripts/resolve_entities.py export             # pairs to review -> output/review.csv
python scripts/resolve_entities.py apply output/review.csv
```

- **Candidates:** pairs with similar names or aliases (pg_trgm), both actor pairs the loader created separately ("Heartbest" / "Heartbest Foods") and the mentions it left waiting.
- **Scoring:** each pair is scored on name (exact, a name plus words like "Foods" or "Labs", contained, similar), description overlap, country, and websites. It also counts against a pair when the same page lists both or their types differ.
- **Decisions:**
  - **match:** the mention is linked, or the duplicate actor is merged. Merged actors keep their row (`status = 'merged'`, `merged_into_id`), and their mentions, aliases, identifiers and relationships move to the surviving actor.
  - **no_match:** recorded, so the pair isn't proposed again.
  - **unsure:** goes to the `match_review_queue` view.
- **The same name alone never merges.** A match needs a strong name, some corroboration (overlapping descriptions or the same country) and nothing against it. Newtopia's "Kigui" (retail AI) and The Yield Lab's "Kigüi" (food logistics, Mexico) are two different companies.
- **Review:** `export` writes each pair with both sides' websites, countries, funds and descriptions. Write `match` or `no_match` in `your_decision` (and optionally `notes`), then `apply`. Your decisions are recorded as `human` and never overridden by a later run.
- **LLM judge** (`judge`, in `src/db/judge.py`): the model sees both records (names, websites, countries, funds, descriptions) and answers match, no_match or unsure, with a confidence and quotes from the records. Its answer is checked before anything happens:
  - a **match** needs high confidence, a quote from each record's description or website that really is in that record, and no website, country or same-page conflict;
  - a **no_match** needs medium or high confidence and one such quote;
  - quoting names, countries, types or funds doesn't count, since different companies share them, and neither does the prompt's own text ("none known");
  - anything else stays in review, now with the model's reasoning in the CSV.

  Each pair is saved as soon as it's judged, so a rerun continues. Later score runs never override the judge, and your decisions override it. It uses `VLLM_BASE_URL` and `VLLM_MODEL` from `.env`.
- **Thresholds and word lists** are at the top of `src/db/resolution.py`.

## Configuration

All settings are read from `.env` (see `.env.example`).

| Variable | Default | Description |
|---|---|---|
| `VLLM_BASE_URL` | `http://localhost:8000/v1` | OpenAI-compatible endpoint (vLLM). |
| `VLLM_MODEL` | `mistralai/Mistral-7B-Instruct-v0.3` | Model name as served by vLLM, e.g. `meta-llama/Llama-3.1-8B-Instruct`. |
| `API_KEY` | `not-needed` | API key for the endpoint (`not-needed` for local vLLM). |
| `LLM_TOOL_CHOICE` | *(empty)* | Leave empty with Llama 3.1 8B. `required` makes vLLM force every reply into the tool schemas, but with this model it skipped opening the site and emitted 50+ tool calls per reply until `max_tokens`. It may suit other models; when set, `parallel_tool_calls=false` is sent too. |
| `LLM_TEMPERATURE` | `0.0` | Sampling temperature. |
| `LLM_MAX_TOKENS` | `4096` | Max output tokens per model call. Keep it well below `--max-model-len`: vLLM rejects requests where prompt + `max_tokens` exceeds it. |
| `PORTFOLIO_MODE` | `hybrid` | Default mode for the portfolio agent: `hybrid`, `deterministic` or `agent`. |
| `HEADLESS` | `true` | Run Chromium without a window. Set `false` to watch it browse. |
| `TIMEOUT` | `20000` | Page load timeout (ms). |
| `WAIT_AFTER_LOAD` | `1000` | Extra wait after load (ms) for dynamic content. |
| `MAX_TEXT_CHARS` | `18000` | Max characters of page text per part. Longer pages are split into parts; nothing is cut off. |
| `MAX_LINKS` | `120` | Max links in `PageContent.links`. The model sees links inline in the page text, which this doesn't limit. |
| `MAX_HTML_CHARS` | `12000` | Max raw HTML returned by `get_current_page_html`. |
| `OUTPUT_DIR` | `output` | Results folder, relative to the project root. If only the older `OUTPUT_FILE` is set, its folder is used. |
| `LOG_LEVEL` | `INFO` | `DEBUG` also logs page contents and the model's final messages. |

`PLAYWRIGHT_BROWSER_TYPE` and `PAGES_FILE` are read but not used yet (Chromium is always used).

---

## Adding a new agent

1. **Create a package** in `src/custom_agents/`, e.g. `src/custom_agents/news_agent/`.
2. **Write its entry point:** an async function that extracts one URL, writes its own output, and returns a `RunReport`. It must accept `**options` and ignore the ones it doesn't use (the pipeline passes `max_turns` and `mode` to every agent).

   ```python
   from src.custom_agents.base import RunReport

   async def run_news(url: str, *, max_turns: int = 25, **_) -> RunReport:
       items = ...  # extract and save
       return RunReport(summary=f"{len(items)} news item(s)", items=len(items), errors=[])
   ```

3. **Expose a spec** in the package's `__init__.py`:

   ```python
   from src.custom_agents.base import AgentSpec
   from .agent import run_news

   SPEC = AgentSpec(name="news", description="Extracts startup news from press pages", run=run_news)
   ```

4. **Register it** in `src/custom_agents/registry.py`:

   ```python
   from .news_agent import SPEC as NEWS
   AGENTS = {spec.name: spec for spec in (PORTFOLIO, NEWS)}
   ```

It's now available as `news:https://…`. A test (`tests/test_pipeline.py`) checks that every registered agent follows the contract.

Reusable pieces from `src/core/`: `BrowserManager` (Playwright, page-to-text extraction), `get_model()` / `OneToolCallPerTurn` (vLLM model for the Agents SDK), and `config`.

---

## Project structure

```
pipeline.py                     CLI: parse AGENT:URL targets, run agents, print summary
targets.txt                     Default list of targets (one AGENT:URL per line)
tool_chat_template_llama3.1_json.jinja   Chat template for vLLM (Llama 3.1, JSON tool calls)
.env.example                    Configuration template

src/core/
  config.py                     Settings from .env
  browser.py                    BrowserManager (Playwright) and HTML → text extraction
  llm.py                        vLLM client/model and the OneToolCallPerTurn wrapper

src/custom_agents/
  base.py                       Agent contract: AgentSpec, RunReport
  registry.py                   AGENTS: name → AgentSpec
  portfolio_agent/
    __init__.py                 SPEC for the "portfolio" agent
    agent.py                    PortfolioAgent: modes, worker loop, recovery, run_portfolio()
    instructions.py             System prompt
    tools.py                    Browser tools for the agent and the evidence log
    recorder.py                 Record tools, evidence checks, salvage of cut-off calls
    prepass.py                  Deterministic pre-pass (navigation, expansion, pagination)
    heuristics.py               Portfolio link detection and card-grid extraction
    context.py                  Trims old page outputs from the model's context
    models.py                   StartupInfo / FinderResult (Pydantic)
    output.py                   Per-run output files

scripts/
  merge_portfolios.py           Merge per-fund outputs into one startup list
  enrich_startups.py            Visit startup websites, classify with the LLM (country, deep tech, area)
  taxonomy.json                 Deep-tech definition and technology areas used by enrich_startups.py
  load_db.py                    Load portfolio outputs into PostgreSQL
  resolve_entities.py           Entity resolution: merge duplicates, review queue export/apply

src/db/
  migrations/                   Schema (run in order on first start of the container)
  seeds/                        Example data (optional)
  loader.py                     JSON outputs -> mentions, actors, relationships
  resolution.py                 Candidate pairs, scoring, merges, human review

Dockerfile, docker-compose.yml  PostgreSQL 16 with the migrations

src/scrappy_project/            Scrapy skeleton (not used yet)
tests/                          Unit, agent-loop and browser tests (fixtures/ has a local test site)
```

---

## Tests

```bash
pytest -q -m "not live"                 # everything except tests against real websites
pytest -q -m "not live and not browser" # also skip the tests that launch Chromium
```

- **No GPU, vLLM server or internet needed.** The agent-loop tests drive the real Agents SDK with a scripted fake model, covering batching, `finish`, cut-off tool calls, invented companies and parallel tool calls.
- **Browser tests** (`-m browser`) start Chromium against a local test site in `tests/fixtures/portfolio_site/`: a homepage, a portfolio with a "Ver más" button, and a second page. They're skipped automatically if Chromium isn't installed.
- **Database tests** (`-m db`) need `TEST_DATABASE_URL`, an admin connection to a throwaway PostgreSQL (they create and drop their own databases); see `tests/conftest.py`. They're skipped when it isn't set.
- **Markers:** `browser` (local Chromium), `db` (PostgreSQL) and `live` (real websites).

---

## Troubleshooting

**`400 … can only concatenate str (not "dict") to str` from vLLM**
vLLM is using an old chat template. Start it with `--chat-template` pointing to this repo's `tool_chat_template_llama3.1_json.jinja`, and restart it after any change to the template.

**vLLM log: `Error in extracting tool call from response … Unterminated string`**
The model's tool call was cut off at `LLM_MAX_TOKENS`, usually a `record_companies` call with too many companies. The agent recovers automatically (`Unreadable tool call … salvaged N companies` in the log). If it happens often, lower the batch size in the prompt or raise `LLM_MAX_TOKENS`, as long as it stays below `--max-model-len`.

**vLLM log: `Expecting ',' delimiter` / model sends `"companies": "[{\"name\"…"`**
The model sent the company list as a JSON string instead of an array, and an unescaped quote broke it. The agent recovers the complete companies from it (`Unreadable tool call … salvaged N companies`) and continues. `LLM_TOOL_CHOICE=required` would prevent it in principle, but with Llama 3.1 8B it causes worse problems (see below).

**Log: `Dropped 50 extra tool call(s)` and replies taking minutes, with `LLM_TOOL_CHOICE=required`**
With a forced tool choice, vLLM accepts any number of tool calls per reply, and Llama 3.1 8B keeps adding calls until `max_tokens`. In testing it also skipped opening the site. Set `LLM_TOOL_CHOICE=` (empty).

**`ModelBehaviorError: … finish_reason='length' but produced no assistant text, tool call, or refusal`**
A reply hit `LLM_MAX_TOKENS` before any tool call was complete. The agent now recovers: it tells the model to send exactly one tool call, along with the companies already saved, and continues (up to 3 times, then it ends with an error instead of crashing).

**Log: `0 new, N merged` repeated, then the run finishes early**
The model lost track of where it was in the list. The reply to a batch with nothing new, and the note sent after a cut-off call, now list the companies already saved, so it can continue from there.

**Log: `Dropped N extra tool call(s) from one response`**
Expected with small models: the model tried to plan several steps at once, and only the first is executed. No action needed.

**Log: `… does not appear on any page you have read`**
The model tried to record a company that isn't in any page it read: invented, or with its name changed (accent dropped, words added). It's told to retry with the exact name.

**Log: `finish refused: portfolio page … not opened`**
The model recorded the portfolio URL but tried to finish without opening it. It's told to open the page first. If it still refuses after 2 attempts, the run ends with the error `The portfolio page … was never opened; the list may be incomplete`. In that case use `hybrid` mode, or pass the portfolio page URL directly (`portfolio:https://fund.com/portfolio`).

**Empty result for a fund**
Check the reason in the summary. Common causes: the portfolio is private (behind a login or access key), the site is down, or the layout isn't a card grid. Try `--mode agent` for unusual layouts.

**Nothing is logged**
Set `LOG_LEVEL=INFO` (or `DEBUG`) in `.env`.

---

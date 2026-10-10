import os
from pathlib import Path
from dotenv import load_dotenv

# Resolve paths
SRC_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SRC_DIR.parent
ROOT = SRC_DIR

load_dotenv(PROJECT_ROOT / ".env")

# vLLM / LLM
VLLM_BASE_URL: str = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
VLLM_MODEL: str = os.getenv("VLLM_MODEL", "mistralai/Mistral-7B-Instruct-v0.3")
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.0"))
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))
API_KEY: str = os.getenv("API_KEY", "not-needed")

# Portfolio extraction mode:
#   hybrid        deterministic pre-pass, then the agent fills gaps (default)
#   deterministic pre-pass only, no LLM calls
#   agent         agent only, no pre-pass
PORTFOLIO_MODE: str = os.getenv("PORTFOLIO_MODE", "hybrid").strip().lower()

# I/O
PAGES_FILE: Path = ROOT / os.getenv("PAGES_FILE", "pages.json")
OUTPUT_FILE: Path = ROOT / os.getenv("OUTPUT_FILE", "output/startups.json")
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

# Playwright
PLAYWRIGHT_BROWSER_TYPE: str = os.getenv("PLAYWRIGHT_BROWSER_TYPE", "chromium")
TIMEOUT: int = int(os.getenv("TIMEOUT", os.getenv("PAGE_LOAD_TIMEOUT", "20000")))  # ms
PAGE_LOAD_TIMEOUT: int = TIMEOUT
HEADLESS: bool = os.getenv("HEADLESS", "true").strip().lower() in ("true", "1", "yes")
WAIT_AFTER_LOAD: int = int(os.getenv("WAIT_AFTER_LOAD", "1000"))
MAX_TEXT_CHARS: int = int(os.getenv("MAX_TEXT_CHARS", "18000"))
MAX_LINKS: int = int(os.getenv("MAX_LINKS", "120"))
MAX_HTML_CHARS: int = int(os.getenv("MAX_HTML_CHARS", "12000"))

# Debugging / Logging
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


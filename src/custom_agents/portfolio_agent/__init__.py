"""Portfolio crawler agent package."""
from src.custom_agents.base import AgentSpec

from .agent import PortfolioAgent, run_portfolio
from .models import FinderResult, StartupInfo
from .recorder import PortfolioRecorder
from .tools import BrowserTools

SPEC = AgentSpec(
    name="portfolio",
    description="Lists the startups in an investment fund's portfolio "
                "(options: --mode hybrid|deterministic|agent, --max-turns)",
    run=run_portfolio,
)

__all__ = [
    "PortfolioAgent", "FinderResult", "StartupInfo", "BrowserTools",
    "PortfolioRecorder", "SPEC", "run_portfolio",
]

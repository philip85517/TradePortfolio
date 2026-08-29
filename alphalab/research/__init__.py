"""历史截面因子研究。"""

from .engine import (
    DuckDBMarketDataAdapter,
    HorizonPerformance,
    HistoricalResearchLab,
    InMemoryMarketDataAdapter,
    PortfolioSpec,
    ResearchReport,
    ResearchStudyReport,
    ResearchSpec,
)
from .data_binding import (
    DataBindingError,
    ResearchDataBinding,
    auto_bind_research_db,
    default_research_db_candidates,
    ensure_research_data,
)
from .review import ReviewRun, ReviewState, create_review_server, load_review_run, serve_review
from .plugins import (
    FixedV0Plugin,
    ResearchFactorPlugin,
    default_plugins,
    factor_definition,
    resolve_plugin,
    validate_plugin_output,
)
from .runs import ResearchRunStore
from .universe_history import (
    HISTORY_COLUMNS,
    UniverseHistoryError,
    build_baostock_universe_history,
    fetch_baostock_universe_history,
    load_universe_as_of,
    normalize_universe_history,
    upsert_universe_history,
    validate_universe_history,
)

__all__ = [
    "DuckDBMarketDataAdapter",
    "DataBindingError",
    "ResearchDataBinding",
    "auto_bind_research_db",
    "default_research_db_candidates",
    "ensure_research_data",
    "HISTORY_COLUMNS",
    "UniverseHistoryError",
    "build_baostock_universe_history",
    "fetch_baostock_universe_history",
    "load_universe_as_of",
    "normalize_universe_history",
    "upsert_universe_history",
    "validate_universe_history",
    "HorizonPerformance",
    "HistoricalResearchLab",
    "InMemoryMarketDataAdapter",
    "PortfolioSpec",
    "ResearchReport",
    "ResearchStudyReport",
    "ResearchSpec",
    "FixedV0Plugin",
    "ResearchFactorPlugin",
    "default_plugins",
    "resolve_plugin",
    "factor_definition",
    "validate_plugin_output",
    "ResearchRunStore",
    "ReviewRun",
    "ReviewState",
    "create_review_server",
    "load_review_run",
    "serve_review",
]

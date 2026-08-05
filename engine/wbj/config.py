"""Configuration loader for wbj compute engine."""

from dataclasses import dataclass, field
from pathlib import Path
from dotenv import dotenv_values


def _find_repo_root() -> Path:
    """Derive repo_root: two parents up from wbj/ directory."""
    # wbj package is at engine/wbj/
    wbj_dir = Path(__file__).parent  # engine/wbj/
    engine_dir = wbj_dir.parent  # engine/
    repo_root = engine_dir.parent  # repo_root
    return repo_root


@dataclass(repr=False)
class Settings:
    """Warren Buffett Jr settings, never repr keys."""

    fmp_api_key: str | None = None
    finnhub_api_key: str | None = None
    fred_api_key: str | None = None
    anthropic_api_key: str | None = None
    # Investigación del TAM (`overlay/tam_research.py`). Va por Gemini primero
    # porque trae búsqueda de Google integrada, y OpenAI de suplente. NO usa
    # Anthropic a propósito: es la misma cuenta que el judge, y dejar el TAM
    # colgando de un saldo agotado significaría que Market se queda sin sus
    # tres dimensiones más pesadas cada vez que el judge no puede correr.
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    # Identidad ante la SEC. Su política de fair-access exige un User-Agent con
    # un contacto real; si se dispara un límite, la SEC bloquea POR user-agent,
    # así que compartirlo con otro proyecto propaga el bloqueo. Se configura con
    # EDGAR_USER_AGENT (API/.env o entorno) — ver providers/edgar.py.
    edgar_user_agent: str | None = None
    # Modelo del agente de juicio cualitativo. Opus 5 cuesta lo mismo que 4.8
    # ($5/$25 por MTok) y es mejor en lo que hace el judge: clasificar moat,
    # catalizadores y thesis-killers. Bájalo a "claude-haiku-4-5" para abaratar.
    judge_model: str = "claude-opus-5"
    repo_root: Path = field(default_factory=_find_repo_root)
    cache_dir: Path = field(default_factory=lambda: _find_repo_root() / "engine" / "cache")
    reports_dir: Path = field(default_factory=lambda: _find_repo_root() / "Reportes")

    def __repr__(self) -> str:
        """Custom repr that never includes secret keys."""
        return (
            f"Settings(fmp_api_key={'*' * 8 if self.fmp_api_key else None}, "
            f"finnhub_api_key={'*' * 8 if self.finnhub_api_key else None}, "
            f"fred_api_key={'*' * 8 if self.fred_api_key else None}, "
            f"repo_root={self.repo_root}, "
            f"cache_dir={self.cache_dir}, "
            f"reports_dir={self.reports_dir})"
        )


def load_settings(env_file: Path | None = None) -> Settings:
    """Load settings from env file, with defaults.

    Args:
        env_file: Path to .env file. Defaults to <repo_root>/API/.env.

    Returns:
        Settings instance with keys from env file (or None if missing/empty).
    """
    repo_root = _find_repo_root()

    if env_file is None:
        env_file = repo_root / "API" / ".env"

    # Load env file if it exists; otherwise return defaults
    env_vars = {}
    if env_file.exists():
        env_vars = dotenv_values(env_file)

    import os

    def _key(name: str) -> str | None:
        """Valor de una clave: primero `API/.env`, luego el entorno real.

        El entorno importa en DOS casos que antes quedaban fuera:
          1. **Render** — no existe `API/.env`; las claves llegan por el dashboard.
          2. **`vertex.env`** — la app web lo carga a `os.environ` al arrancar.

        Antes solo `ANTHROPIC_API_KEY` miraba el entorno; `FMP`, `FinnHub` y
        `FRED` leían únicamente el archivo. Con las claves en `vertex.env` eso
        dejaba `fmp_api_key = None` y `FMPProvider.available = False`, así que
        `wbj analyze` desde el CLI corría sin FMP — y las tres categorías que
        dependen de él (Market, Technical, Valuation) salían NOT_SCORABLE con
        la clave puesta y funcionando. Silencioso, porque el provider devuelve
        `None` en vez de fallar.
        """
        return (env_vars.get(name) or os.environ.get(name) or "").strip() or None

    fmp_api_key = _key("FMP_API_KEY")
    finnhub_api_key = _key("FINNHUB_API_KEY")
    fred_api_key = _key("FRED_API_KEY")
    anthropic_api_key = _key("ANTHROPIC_API_KEY")
    gemini_api_key = _key("GEMINI_API_KEY")
    openai_api_key = _key("OPENAI_API_KEY")
    edgar_user_agent = _key("EDGAR_USER_AGENT")
    judge_model = _key("JUDGE_MODEL") or "claude-opus-5"

    return Settings(
        fmp_api_key=fmp_api_key,
        finnhub_api_key=finnhub_api_key,
        fred_api_key=fred_api_key,
        anthropic_api_key=anthropic_api_key,
        gemini_api_key=gemini_api_key,
        openai_api_key=openai_api_key,
        edgar_user_agent=edgar_user_agent,
        judge_model=judge_model,
        repo_root=repo_root,
        cache_dir=repo_root / "engine" / "cache",
        reports_dir=repo_root / "Reportes",
    )

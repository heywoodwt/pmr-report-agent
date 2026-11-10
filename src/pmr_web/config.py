"""Settings for the web application, read from the environment once at startup.

The same code runs on a laptop and in Azure. Only these settings change:

  local (default)   files under PMR_DATA_DIR, SQLite, the worker runs as a thread inside the web app
  azure             files in Blob Storage, PostgreSQL, the worker runs as its own Container App
                    (or as a thread in the web app, in the slim deployment)

Secrets never appear in code or in this module's defaults. In Azure, App Service and Container Apps
resolve Key Vault references into these environment variables, so the code only reads the environment.
"""

import os
from dataclasses import dataclass
from pathlib import Path

# Repo root: src/pmr_web/config.py -> src/pmr_web -> src -> repo.
ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path  # local storage root and the SQLite file
    database_url: str
    storage: str  # "local" or "azure"
    blob_account_url: str  # https://<account>.blob.core.windows.net, for storage="azure"
    blob_container: str
    embedded_worker: bool  # run the worker as a thread in the web process
    secret_key: str
    sample_inputs: Path | None  # a drop folder a new report can start from, for demos
    seed_cache: Path | None  # model answers copied into storage at startup
    trust_easy_auth: bool  # read the signed-in user from App Service authentication headers
    llm_enabled: bool  # False: cached model answers only, never a live call
    access_code: str  # when set, the name sign-in also asks for this shared code

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        env = os.environ if env is None else env
        data_dir = Path(env.get("PMR_DATA_DIR", ROOT / "webdata")).resolve()
        sample = Path(env.get("PMR_SAMPLE_INPUTS", ROOT / "inputs"))
        cache = Path(env.get("PMR_SEED_CACHE", ROOT / "llm_cache"))
        return cls(
            data_dir=data_dir,
            database_url=env.get("DATABASE_URL", f"sqlite:///{data_dir / 'pmr_web.db'}"),
            storage=env.get("PMR_STORAGE", "local"),
            blob_account_url=env.get("AZURE_STORAGE_ACCOUNT_URL", ""),
            blob_container=env.get("AZURE_STORAGE_CONTAINER", "pmr"),
            embedded_worker=env.get("PMR_EMBEDDED_WORKER", "1") == "1",
            secret_key=env.get("FLASK_SECRET_KEY", "dev-only-not-secret"),
            sample_inputs=sample if sample.is_dir() else None,
            seed_cache=cache if cache.is_dir() else None,
            # App Service strips client-sent X-MS-* headers only when its authentication is on, so
            # the headers are trusted only when it is. WEBSITE_AUTH_ENABLED is App Service's own flag.
            trust_easy_auth=env.get("PMR_TRUST_EASY_AUTH", env.get("WEBSITE_AUTH_ENABLED", "")).lower() in ("1", "true"),
            llm_enabled=env.get("PMR_NO_LLM", "0") != "1",
            access_code=env.get("PMR_ACCESS_CODE", ""),
        )

from pathlib import Path

import pytest

from pmr.pipeline import build_facts

INPUTS = Path(__file__).resolve().parent.parent / "inputs"

@pytest.fixture(scope="session")
def inputs() -> Path:
    return INPUTS


@pytest.fixture(scope="session")
def facts() -> dict:
    return build_facts(INPUTS, "CPERS", "4Q25")


@pytest.fixture(scope="session")
def rendered(tmp_path_factory) -> tuple[dict, Path]:
    """One full run (facts, PDF, manifest) into a temporary output folder."""
    from pmr.llm import Claude
    from pmr.pipeline import run

    # Cache only: tests never call the API, they replay the committed answers.
    return run(INPUTS, "CPERS", "4Q25", tmp_path_factory.mktemp("out"), Claude(enabled=False))

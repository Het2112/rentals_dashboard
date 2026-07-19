from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def sample_pdf() -> Path:
    path = Path(__file__).parents[1] / "monthly_statements" / "Owner packet (19).pdf"
    if not path.exists():
        pytest.skip("Private local AppFolio regression statement is not available")
    return path

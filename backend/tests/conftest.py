import os
import tempfile

if "AQUASENTINEL_DB_PATH" not in os.environ:
    _temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    _temp_db.close()
    os.environ["AQUASENTINEL_DB_PATH"] = _temp_db.name

os.environ["AQUASENTINEL_ALLOW_STUB_ALERTS"] = "1"

import pytest
from backend.main import ml_bridge, pipeline, replay_engine
import backend.main as main_mod


@pytest.fixture(autouse=True)
def configure_test_environment():
    """Ensures all standard tests run in deterministic stub mode without blocking on warmup."""
    main_mod.is_warming_up = False
    ml_bridge.force_stub_mode(reason="test_harness_stub")
    pipeline.reset_state(clear_buffers=True)
    yield
    pipeline.reset_state(clear_buffers=True)

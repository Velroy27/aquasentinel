# Pytest fixtures and test environment setup
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

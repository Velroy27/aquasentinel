# AquaSentinel Backend Configuration & Constants
from pathlib import Path
import os

# ==============================================================================
# COST MODEL ASSUMPTIONS
# ASSUMPTION - illustrative, not measured:
# Commercial water tariff: ~Rs 25/kL. An unmonitored leak averages ~5 L/s for 72 hrs (~1,296,000 L).
# Rapid detection in Net3 within 2 minutes saves ~71.8 hours = ~1,292,400 L (~Rs 32,310).
# We assume ~1 leak prevented per year for Net3 scaled by network sensor coverage factor.
# ==============================================================================
COST_PER_NODE_RS: int = 1500           # ESP32 450 + transducer 650 + enclosure/power/connectors 400
EST_LOSS_SAVED_PER_LEAK_RS: int = 32000 # Benchmark financial savings per mitigated leak event
COVERAGE_BASE_RATIO: float = 0.40      # Minimum leak detection probability with 1 sensor
COVERAGE_PER_NODE_RATIO: float = 0.075 # Incremental coverage gain per additional sensor
COVERAGE_MAX_RATIO: float = 1.0        # Upper bound coverage cap

# ==============================================================================
# PIPELINE & DETECTION CONSTANTS
# ==============================================================================
# Window size in steps (45 steps = 450s window, matching ml/smoke_test.py)
WINDOW_SIZE: int = 45
SEVERITY_CRITICAL_THRESHOLD: float = 0.85
SEVERITY_HIGH_THRESHOLD: float = 0.70
SEVERITY_MEDIUM_THRESHOLD: float = 0.50

# Number of consecutive non-anomaly windows required to reset/resolve an active leak alert
RESOLVE_CLEAR_WINDOWS: int = 12

# Minimum number of steps in sliding window before calling detector (detector requires >= 2 steps)
MIN_STEPS_FOR_DETECT: int = 2

# ML execution timeout in seconds
ML_TIMEOUT_S: float = 10.0
WARMUP_TIMEOUT_S: float = 60.0

# ==============================================================================
# SERVER & PATHS
# ==============================================================================
API_HOST: str = '0.0.0.0'
API_PORT: int = 8000

BACKEND_DIR: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = BACKEND_DIR.parent
DATA_DIR: Path = REPO_ROOT / 'data'
DATA_REPLAY_DIR: Path = DATA_DIR / 'replay'
MOCK_DIR: Path = BACKEND_DIR / 'mock'
MOCK_REPLAY_DIR: Path = MOCK_DIR / 'replay'
LOGS_DIR: Path = BACKEND_DIR / 'logs'
LOG_FILE: Path = LOGS_DIR / 'backend.log'

DB_DIR: Path = BACKEND_DIR / 'db'
DB_PATH: Path = DB_DIR / 'aquasentinel.db'
ENABLE_DB: bool = os.getenv('ENABLE_DB', 'true').lower() in ('true', '1', 'yes')
READINGS_RETENTION_DAYS: int = 7

# Allowed replay playback speed multipliers
ALLOWED_REPLAY_SPEEDS: list[int] = [1, 5, 20]

# WebSocket settings
WS_PING_INTERVAL_S: float = 20.0
WS_QUEUE_MAXSIZE: int = 250
INGEST_MAX_BODY_BYTES: int = 65536

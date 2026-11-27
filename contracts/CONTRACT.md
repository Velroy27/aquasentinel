PROJECT: AquaSentinel - leak detection, localization and sensor placement on EPANET Net3 (92 junctions) using WNTR. Sensors are simulated and streamed as real nodes would.

REPO: /ml (Role 1), /backend (Role 2), /frontend (Role 3), /data (shared), /contracts.

FORMATS (JSON, do not change without telling the team):
- Sensor reading: {"t": 10, "node_id": "10", "pressure_m": 51.2, "flow_lps": null}
- GET /api/network -> {"nodes":[{"id","x","y","elevation","is_sensor"}],"pipes":[{"id","from","to","length","diameter"}],"sensors":["id",...]}
- GET /api/scenarios -> [{"id","leak_node","leak_size_lps","start_t","description"}]
- POST /api/replay/start {"scenario_id","speed"} ; POST /api/replay/stop
- WebSocket /ws/stream sends messages with a "type" field:
  * "readings": {"t", "readings":[reading,...]}
  * "alert": {"id","t","severity","score","detector"}
  * "localization": {"alert_id","top3":[{"node_id","prob"}],"probs":{"node_id":prob}}
  * "loss": {"alert_id","est_leak_lps","litres_lost_cum"}
- GET /api/results -> {"detection_rate","false_alarm_per_day","top1","top3","neighbour_acc","chance_baseline","placement":[{"n_sensors","optimized","random_mean","random_std"}],"measured_at"}
- GET /api/placement?n=8 -> {"sensors":[ids],"coverage":float}
- GET /api/cost?nodes=8 -> {"hardware_rs","per_node_rs":1500,"est_loss_avoided_rs"}
ML PYTHON API (/ml/api.py): detect(window)->{"anomaly":bool,"score":float}; localize(window)->{"probs":{node:prob}}; estimate_size(window)->float lps.
RULES: replay from precomputed files (no live WNTR on stage); mock data in /contracts/mock/ until real data exists; feature freeze at hour 20; report honest numbers with measured date and chance baseline.

import os


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

LISTINGS_ENRICHED_PATH = os.path.join(DATA_DIR, "listings_enriched.csv")
ZILLOW_HISTORY_PATH = os.path.join(DATA_DIR, "zillow_history.csv")
REALTYAPI_ENRICHED_PATH = LISTINGS_ENRICHED_PATH
REALTYAPI_RESPONSES_PATH = os.path.join(DATA_DIR, "realtyapi_byaddress_responses.jsonl")
REALTYAPI_GRAPH_RAW_PATH = os.path.join(DATA_DIR, "realtyapi_graph_charts.jsonl")
REALTYAPI_GRAPH_POINTS_PATH = os.path.join(DATA_DIR, "realtyapi_graph_charts_points.csv")
REALTYAPI_GRAPH_SUMMARY_PATH = os.path.join(DATA_DIR, "realtyapi_graph_charts_summary.csv")

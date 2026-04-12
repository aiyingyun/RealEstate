# RealEstate

This project has three separate stages:

1. Scrape listings from Withroam
2. Enrich listings with Zillow rent/history data
3. Explore the results in the dashboard

Recommended commands:

```bash
python scripts/script_scrape_withroam.py --zip 30324
python scripts/script_add_zips.py 30075
export RAPIDAPI_KEY=your_key
python scripts/script_enrich_zillow.py --input data/listings_enriched.csv
export REALTYAPI_KEY=your_key
python scripts/script_enrich_realtyapi.py --input data/listings_enriched.csv
python scripts/script_enrich_realtyapi_graphs.py --input data/listings_enriched.csv
python app.py
```

Notes:

- Scraped listings are saved to [data/listings_enriched.csv](/Users/yingyunai/Desktop/Code/RealEstate/data/listings_enriched.csv)
- `script_add_zips.py` appends only new listings and incrementally fetches only missing RealtyAPI data for those new rows
- Zillow history is saved to [data/zillow_history.csv](/Users/yingyunai/Desktop/Code/RealEstate/data/zillow_history.csv)
- RealtyAPI raw responses are saved to [data/realtyapi_byaddress_responses.jsonl](/Users/yingyunai/Desktop/Code/RealEstate/data/realtyapi_byaddress_responses.jsonl)
- RealtyAPI enriched listing fields are written back into [data/listings_enriched.csv](/Users/yingyunai/Desktop/Code/RealEstate/data/listings_enriched.csv)
- RealtyAPI graph raw responses are saved to [data/realtyapi_graph_charts.jsonl](/Users/yingyunai/Desktop/Code/RealEstate/data/realtyapi_graph_charts.jsonl)
- RealtyAPI graph points are saved to [data/realtyapi_graph_charts_points.csv](/Users/yingyunai/Desktop/Code/RealEstate/data/realtyapi_graph_charts_points.csv)
- RealtyAPI graph summary metadata is saved to [data/realtyapi_graph_charts_summary.csv](/Users/yingyunai/Desktop/Code/RealEstate/data/realtyapi_graph_charts_summary.csv)
- The dashboard reads `listings_enriched.csv` plus the RealtyAPI graph files directly
- `run.py` still exists as a convenience wrapper, but the split scripts are the clearer workflow

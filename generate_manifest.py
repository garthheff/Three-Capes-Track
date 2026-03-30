from pathlib import Path
import json

base = Path(__file__).parent
data_dir = base / "data"
files = sorted(p.name for p in data_dir.glob("*.gpx") if p.is_file())

with open(data_dir / "files.json", "w", encoding="utf-8") as f:
    json.dump(files, f, indent=2)

print(f"Wrote {len(files)} GPX file names to {data_dir / 'files.json'}")

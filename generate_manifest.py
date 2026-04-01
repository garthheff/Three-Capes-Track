from pathlib import Path
import json
import math
from xml.etree import ElementTree as ET

from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS


BASE = Path(__file__).parent
DATA_DIR = BASE / "data"
PHOTOS_SOURCE_DIR = BASE / "photos_source"
PHOTOS_PUBLIC_DIR = BASE / "photos"

TRACK_THRESHOLD_METERS = 5000
SNAP_TO_TRACK_THRESHOLD_METERS = 20


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def clean_name(filename):
    name = Path(filename).stem
    name = name.replace("_", " ").replace("-", " ")
    return " ".join(name.split())


def parse_gpx_points(gpx_path):
    tree = ET.parse(gpx_path)
    root = tree.getroot()

    points = []

    for pt in root.iter():
        tag = pt.tag.lower()
        if tag.endswith("trkpt") or tag.endswith("rtept"):
            lat = pt.attrib.get("lat")
            lon = pt.attrib.get("lon")
            if lat is None or lon is None:
                continue
            try:
                points.append({
                    "lat": float(lat),
                    "lon": float(lon),
                })
            except ValueError:
                continue

    return points


def extract_photo_gps(photo_path):
    try:
        with Image.open(photo_path) as img:
            exif = img._getexif()
            if not exif:
                return None

            gps_info = {}

            for tag, value in exif.items():
                tag_name = TAGS.get(tag)
                if tag_name == "GPSInfo":
                    for key in value:
                        decoded = GPSTAGS.get(key)
                        gps_info[decoded] = value[key]

            if not gps_info:
                return None

            def convert_to_degrees(value):
                d = float(value[0])
                m = float(value[1])
                s = float(value[2])
                return d + (m / 60.0) + (s / 3600.0)

            lat = gps_info.get("GPSLatitude")
            lat_ref = gps_info.get("GPSLatitudeRef")
            lon = gps_info.get("GPSLongitude")
            lon_ref = gps_info.get("GPSLongitudeRef")

            if not lat or not lon or not lat_ref or not lon_ref:
                return None

            lat = convert_to_degrees(lat)
            if lat_ref != "N":
                lat = -lat

            lon = convert_to_degrees(lon)
            if lon_ref != "E":
                lon = -lon

            return {"lat": lat, "lon": lon}

    except Exception as e:
        print(f"Error reading EXIF for {photo_path.name}: {e}")
        return None


def strip_metadata_copy(src, dst):
    with Image.open(src) as img:
        rgb = img.convert("RGB")
        cleaned = Image.new("RGB", rgb.size)
        cleaned.putdata(list(rgb.getdata()))
        cleaned.save(dst, format="JPEG", quality=95)


def nearest_track(photo_lat, photo_lon, tracks):
    best = None

    for track in tracks:
        min_dist = None

        for pt in track["points"]:
            d = haversine_m(photo_lat, photo_lon, pt["lat"], pt["lon"])
            if min_dist is None or d < min_dist:
                min_dist = d

        if min_dist is None:
            continue

        if best is None or min_dist < best["distance_m"]:
            best = {
                "gpx_file": track["file"],
                "distance_m": round(min_dist, 1),
            }

    return best


def nearest_track_point(photo_lat, photo_lon, track_points):
    best_point = None
    best_dist = None

    for pt in track_points:
        d = haversine_m(photo_lat, photo_lon, pt["lat"], pt["lon"])
        if best_dist is None or d < best_dist:
            best_dist = d
            best_point = pt

    return best_point, best_dist


def build_files_manifest():
    gpx_files = sorted(p.name for p in DATA_DIR.glob("*.gpx") if p.is_file())

    with open(DATA_DIR / "files.json", "w", encoding="utf-8") as f:
        json.dump(gpx_files, f, indent=2)

    print(f"Wrote {len(gpx_files)} GPX names to data/files.json")
    return gpx_files


def build_tracks(gpx_files):
    tracks = []

    for filename in gpx_files:
        gpx_path = DATA_DIR / filename
        points = parse_gpx_points(gpx_path)
        if not points:
            print(f"Skipping GPX with no usable points: {filename}")
            continue

        tracks.append({
            "file": filename,
            "title": clean_name(filename),
            "points": points,
        })

    return tracks


def build_photos_manifest(tracks):
    PHOTOS_PUBLIC_DIR.mkdir(exist_ok=True)

    photo_entries = []

    photo_files = []
    for ext in ("*.jpg", "*.jpeg", "*.JPG", "*.JPEG", "*.png", "*.PNG"):
        photo_files.extend(PHOTOS_SOURCE_DIR.glob(ext))

    photo_files = sorted(set(photo_files))

    for photo_path in photo_files:
        gps = extract_photo_gps(photo_path)
        if not gps:
            print(f"Skipping photo with no GPS: {photo_path.name}")
            continue

        nearest = nearest_track(gps["lat"], gps["lon"], tracks)
        if not nearest:
            print(f"Skipping photo with no nearby track: {photo_path.name}")
            continue

        if nearest["distance_m"] > TRACK_THRESHOLD_METERS:
            print(
                f"Skipping photo too far from track: {photo_path.name} "
                f"{nearest['distance_m']}m from {nearest['gpx_file']}"
            )
            continue

        assigned_track = next(
            (t for t in tracks if t["file"] == nearest["gpx_file"]),
            None
        )

        snapped_lat = gps["lat"]
        snapped_lon = gps["lon"]

        if assigned_track:
            closest_point, closest_dist = nearest_track_point(
                gps["lat"],
                gps["lon"],
                assigned_track["points"]
            )

            if closest_point and closest_dist is not None and closest_dist > SNAP_TO_TRACK_THRESHOLD_METERS:
                snapped_lat = closest_point["lat"]
                snapped_lon = closest_point["lon"]
                print(
                    f"Snapped {photo_path.name} to track point on {nearest['gpx_file']} "
                    f"from {closest_dist:.1f}m away"
                )

        dst = PHOTOS_PUBLIC_DIR / photo_path.name
        strip_metadata_copy(photo_path, dst)

        photo_entries.append({
            "file": photo_path.name,
            "caption": clean_name(photo_path.name),
            "lat": snapped_lat,
            "lon": snapped_lon,
            "original_lat": gps["lat"],
            "original_lon": gps["lon"],
            "assigned_gpx": nearest["gpx_file"],
            "distance_to_track_m": nearest["distance_m"],
            "url": f"photos/{photo_path.name}",
        })

        print(
            f"Assigned {photo_path.name} to {nearest['gpx_file']} "
            f"at {nearest['distance_m']}m"
        )

    with open(DATA_DIR / "photos.json", "w", encoding="utf-8") as f:
        json.dump(photo_entries, f, indent=2)

    print(f"Wrote {len(photo_entries)} photo entries to data/photos.json")


def main():
    DATA_DIR.mkdir(exist_ok=True)
    PHOTOS_SOURCE_DIR.mkdir(exist_ok=True)
    PHOTOS_PUBLIC_DIR.mkdir(exist_ok=True)

    gpx_files = build_files_manifest()
    tracks = build_tracks(gpx_files)
    build_photos_manifest(tracks)


if __name__ == "__main__":
    main()

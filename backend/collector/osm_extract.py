"""OpenStreetMap from a downloaded Egypt extract instead of the Overpass servers.

The public Overpass servers are often at capacity. Geofabrik publishes the
whole of Egypt as one file, refreshed daily; we download it, keep only
healthcare places, clinics, hospitals, care facilities and schools with
`osmium`, and place each one in its governorate using the governorate
boundaries from the same file.

Needs the `osmium` command (Debian/Ubuntu package osmium-tool) and the
`shapely` Python package; the GitHub workflow installs both.
"""
import json
import logging
import os
import subprocess
from typing import Any, Dict, Iterator, List, Optional, Tuple

import httpx

from collector import osm

log = logging.getLogger(__name__)

EXTRACT_URL = "https://download.geofabrik.de/africa/egypt-latest.osm.pbf"
_TYPES = {"n": "node", "w": "way", "r": "relation"}


def download(url: str, path: str, http: Optional[httpx.Client] = None) -> str:
    http = http or httpx.Client(timeout=600, follow_redirects=True, headers={"User-Agent": osm.USER_AGENT})
    with http.stream("GET", url) as resp:
        resp.raise_for_status()
        with open(path, "wb") as out:
            for chunk in resp.iter_bytes(1 << 20):
                out.write(chunk)
    log.info("downloaded %s (%d MB)", url, os.path.getsize(path) >> 20)
    return path


def _osmium(*args: str) -> None:
    subprocess.run(["osmium", *args, "--overwrite"], check=True, capture_output=True, text=True)


def _features(path: str) -> Iterator[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip().lstrip("\x1e")
            if line:
                yield json.loads(line)


def _boundaries(pbf: str, workdir: str) -> Dict[str, Any]:
    """ISO 3166-2 code -> prepared shapely polygon, for Egypt's governorates."""
    from shapely.geometry import shape
    from shapely.prepared import prep

    filtered, exported = os.path.join(workdir, "boundaries.osm.pbf"), os.path.join(workdir, "boundaries.geojsonseq")
    _osmium("tags-filter", pbf, "r/admin_level=4", "-o", filtered)
    _osmium("export", filtered, "-f", "geojsonseq", "--geometry-types=polygon", "-o", exported)
    wanted = set(osm.GOVERNORATE_ISO.values())
    return {f["properties"]["ISO3166-2"]: prep(shape(f["geometry"])) for f in _features(exported)
            if (f.get("properties") or {}).get("ISO3166-2") in wanted}


def _elements(pbf: str, workdir: str) -> Iterator[Dict[str, Any]]:
    """Candidate places, shaped like Overpass `out center tags` elements."""
    from shapely.geometry import shape

    filtered, exported = os.path.join(workdir, "places.osm.pbf"), os.path.join(workdir, "places.geojsonseq")
    _osmium("tags-filter", pbf, "nwr/healthcare", "nwr/amenity=" + ",".join(osm.AMENITIES), "-o", filtered)
    _osmium("export", filtered, "-f", "geojsonseq", "--add-unique-id=type_id", "-o", exported)
    for f in _features(exported):
        tags = dict(f.get("properties") or {})
        uid = str(f.get("id") or tags.pop("@id", ""))
        if uid[:1] not in _TYPES or not uid[1:].isdigit():
            continue
        geom = shape(f["geometry"])
        point = geom if geom.geom_type == "Point" else geom.representative_point()
        yield {"type": _TYPES[uid[0]], "id": int(uid[1:]), "lat": point.y, "lon": point.x, "tags": tags}


def search(governorates: List[str], workdir: str, pbf: Optional[str] = None,
           url: str = EXTRACT_URL) -> Iterator[Tuple[str, Optional[Exception], List]]:
    """Same shape as osm.search: (governorate, error or None, elements)."""
    from shapely.geometry import Point

    try:
        pbf = pbf or download(url, os.path.join(workdir, "egypt.osm.pbf"))
        boundaries = _boundaries(pbf, workdir)
        by_code: Dict[str, List] = {code: [] for code in governorates}
        isos = [(code, boundaries[osm.GOVERNORATE_ISO[code]]) for code in governorates
                if osm.GOVERNORATE_ISO[code] in boundaries]
        seen = set()
        for element in _elements(pbf, workdir):
            key = (element["type"], element["id"])
            if key in seen or not osm.is_relevant(element):
                continue
            seen.add(key)
            point = Point(element["lon"], element["lat"])
            for code, area in isos:
                if area.contains(point):
                    by_code[code].append(element)
                    break
    except Exception as exc:  # download or osmium failed: every governorate is retried next run
        log.error("openstreetmap extract failed: %s", exc)
        for code in governorates:
            yield code, exc, []
        return
    for code in governorates:
        if osm.GOVERNORATE_ISO[code] not in boundaries:
            log.error("boundary %s not found in the extract", osm.GOVERNORATE_ISO[code])
            yield code, LookupError(osm.GOVERNORATE_ISO[code]), []
        else:
            yield code, None, by_code[code]

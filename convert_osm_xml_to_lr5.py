# -*- coding: utf-8 -*-
"""Підготовка реального локального OSM-знімка для ЛР5 без запитів Overpass.

Приклад Windows (з теки лабораторної):
    python convert_osm_xml_to_lr5.py "Khmelnytskyi.osm.gz"

Файл OSM XML (або .osm.gz/.osm.zip) завантажується з BBBike Extract Service.
Для .osm.pbf оберіть формат OSM XML на BBBike: OSMnx features_from_xml
працює саме з XML, а не з PBF.

Авторство вихідних даних: © OpenStreetMap contributors, ODbL.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree

CITY = "Хмельницький, Україна"
DEFAULT_DIR = "osm_хмельницький"
TOURISM = {"museum", "attraction", "gallery", "artwork", "viewpoint"}
STATION_RAILWAY = {"station", "halt"}
STATION_BUILDING = {"train_station"}
STATION_AMENITY = {"bus_station"}
AMENITY = {"cafe", "restaurant", "drinking_water", "toilets", "pharmacy"}
EXCLUDED_HIGHWAYS = {
    "abandoned", "bus_guideway", "construction", "cycleway", "motor", "no",
    "planned", "platform", "proposed", "raceway", "razed", "rest_area", "services",
    "motorway", "motorway_link",  # захист від суто автомобільних шляхів
}


def unpack_xml(src: Path, tempdir: Path) -> Path:
    """Повертає шлях до OSM XML; стискаючий контейнер розпаковує локально."""
    name = src.name.lower()
    if not src.is_file():
        raise FileNotFoundError(f"Не знайдено OSM-файл: {src}")
    if name.endswith(".pbf"):
        raise ValueError("Це PBF, а потрібно OSM XML. На BBBike виберіть формат 'OSM XML (gzip)'.")
    if name.endswith((".osm", ".xml")):
        return src
    target = tempdir / "city.osm"
    if name.endswith((".osm.gz", ".xml.gz")):
        with gzip.open(src, "rb") as incoming, target.open("wb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        return target
    if name.endswith((".osm.zip", ".xml.zip", ".zip")):
        with zipfile.ZipFile(src) as z:
            matches = [item for item in z.infolist()
                       if not item.is_dir() and item.filename.lower().endswith((".osm", ".xml"))]
            if len(matches) != 1:
                raise ValueError(f"У ZIP має бути один файл OSM XML; знайдено: {len(matches)}")
            with z.open(matches[0]) as incoming, target.open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
        return target
    raise ValueError("Підтримувані формати: .osm, .xml, .osm.gz, .xml.gz, .zip")


def xml_bounds(filepath: Path):
    """Зчитує точну рамку файлу BBBike, не завантажуючи повністю XML."""
    try:
        for _, elem in ElementTree.iterparse(str(filepath), events=("start",)):
            if elem.tag == "bounds":
                a = elem.attrib
                return (float(a["minlon"]), float(a["minlat"]),
                        float(a["maxlon"]), float(a["maxlat"]))
            if elem.tag in {"node", "way", "relation"}:
                break
    except (ValueError, KeyError, ElementTree.ParseError):
        pass
    return None


def nonempty(col):
    return col.notna() & col.astype(str).str.strip().str.lower().ne("none") & col.astype(str).str.strip().ne("")


def values(col, allowed):
    return col.astype(str).str.strip().str.lower().isin(allowed)


def clean(gdf):
    """Готує OSM теги до GeoParquet, як в оригінальному fetch_city_osm.py."""
    import pandas as pd
    if gdf.empty:
        return gdf.reset_index()
    out = gdf.reset_index()
    for c in out.columns:
        if c == "geometry":
            continue
        s = out[c]
        if s.dtype == object or pd.api.types.is_string_dtype(s):
            def string_or_none(v):
                if v is None:
                    return None
                if isinstance(v, (list, tuple, dict, set)):
                    return json.dumps(list(v) if isinstance(v, set) else v, ensure_ascii=False)
                try:
                    if pd.isna(v):
                        return None
                except (TypeError, ValueError):
                    pass
                return str(v)
            out[c] = s.map(string_or_none).astype("string")
    return out


def is_walkable(attrs):
    def txt(key):
        item = attrs.get(key, "")
        if isinstance(item, list):
            item = item[0] if item else ""
        return str(item or "").lower().strip()
    highway = txt("highway")
    if not highway or highway in EXCLUDED_HIGHWAYS:
        return False
    if txt("area") == "yes" or txt("foot") == "no":
        return False
    if txt("access") in {"private", "no"} or txt("service") == "private":
        return False
    if any(txt(k) == "separate" for k in (
        "sidewalk", "sidewalk:both", "sidewalk:left", "sidewalk:right"
    )):
        return False
    return True


def find_osm_source():
    """Шукає OSM XML знімок у теці проєкту та Downloads (без аргументу)."""
    from pathlib import Path
    roots = [Path.cwd(), Path(__file__).resolve().parent, Path.home() / 'Downloads']
    suffixes = ('.osm', '.osm.gz', '.xml', '.xml.gz', '.osm.zip', '.xml.zip')
    candidates = {}
    for folder in roots:
        if not folder.is_dir():
            continue
        for item in folder.iterdir():
            if not item.is_file() or not item.name.lower().endswith(suffixes):
                continue
            # Не беремо випадковий файл іншого міста.
            candidates[str(item.resolve())] = item
    if not candidates:
        return None
    items = list(candidates.values())
    marked = [i for i in items if any(x in i.name.casefold()
              for x in ('khmel', 'хмель', 'khmeln', 'chmel'))]
    if marked:
        items = marked
    elif len(items) > 1:
        print('Знайдено кілька OSM-файлів без назви міста. Укажіть шлях явно:')
        for x in items:
            print('  ', x)
        return None
    return sorted(items, key=lambda f: f.stat().st_mtime, reverse=True)[0]


def main():
    ap = argparse.ArgumentParser(description="Перетворити локальний OSM XML у шари ЛР5 для Хмельницького")
    ap.add_argument("source", nargs="?", default=None,
                    help="файл OSM XML; якщо не вказано, шукаємо в цій теці та Downloads")
    ap.add_argument("--out", default=DEFAULT_DIR, help="папка результатів (типово osm_хмельницький)")
    ap.add_argument("--overwrite", action="store_true", help="перезаписати наявні файли")
    args = ap.parse_args()

    source_arg = args.source or find_osm_source()
    if source_arg is None:
        print("\nНЕ ЗНАЙДЕНО OSM XML Хмельницького.")
        print("Завантаж 'OSM XML (gzip)' на https://extract.bbbike.org/")
        print("Поклади *.osm.gz у теку з програмою або в Downloads, тоді запускай ще раз.")
        return 2
    source = Path(source_arg).expanduser().resolve()

    import geopandas as gpd
    import networkx as nx
    import osmnx as ox
    from shapely.geometry import box
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    needed = ("boundary", "heritage", "stations", "amenities", "road_nodes", "road_edges")

    existing = {f.stem for f in out.glob("*.parquet") if f.stat().st_size > 0}
    todo = set(needed) if args.overwrite else set(needed) - existing
    if not todo:
        print(f"Усі 6 файлів уже є в папці: {out.resolve()}")
        return 0

    print("Місто:", CITY)
    print("Джерело OSM:", source)
    print("Дані завантажуйте саме для Хмельницького, НЕ для Львова!")
    print("Використання мережі: НУЛЬ запитів до Overpass (робота з локальним XML).")

    manifest = {"_місце": CITY, "_джерело": f"локальний OSM XML: {source.name}",
                "_права": "© OpenStreetMap contributors, ODbL",
                "_спосіб": "BBBike / локальний імпорт OSM XML, без Overpass",
                "_мережа": "walk (фільтрація локальних highway)",
                "_примітка": "Якщо немає адміністративного полігону, boundary є bbox витягу, не точною межею міста."}
    manifest_path = out / "manifest.json"
    if manifest_path.exists() and not args.overwrite:
        try:
            manifest.update(json.loads(manifest_path.read_text(encoding="utf-8")))
        except Exception:
            pass

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(out / "cache")
    # Для фільтрації та перевірки пішохідності зберігаємо теги доріг.
    extra_tags = ["access", "foot", "service", "area", "sidewalk", "sidewalk:both",
                  "sidewalk:left", "sidewalk:right"]
    ox.settings.useful_tags_way = list(dict.fromkeys(list(ox.settings.useful_tags_way) + extra_tags))

    with tempfile.TemporaryDirectory(prefix="lr5_osm_") as temp:
        xml = unpack_xml(source, Path(temp))
        source_bbox = xml_bounds(xml)
        # Відмовляємося підміняти справжні дані Хмельницького львівськими.
        # Координати центру підтверджуються OpenStreetMap Wiki.
        if source_bbox is not None:
            west, south, east, north = source_bbox
            if not (west <= 26.98 <= east and south <= 49.42 <= north):
                raise ValueError(
                    f"OSM XML не охоплює центр Хмельницького (49.42, 26.98). "
                    f"Його bbox: {source_bbox}. Скачай витяг саме для Хмельницького."
                )
        t0 = time.perf_counter()

        if todo.intersection({"heritage", "stations", "amenities", "boundary"}):
            print("Читаю історичні пам'ятки, вокзали та заклади з XML...")
            tags = {
                "historic": True,
                "tourism": sorted(TOURISM),
                "railway": sorted(STATION_RAILWAY),
                "building": sorted(STATION_BUILDING),
                "amenity": sorted(AMENITY | STATION_AMENITY),
                "boundary": "administrative",
            }
            all_features = ox.features_from_xml(xml, tags=tags)
            if all_features.empty:
                raise RuntimeError("У файлі не знайдено OSM-об'єктів потрібних типів. Перевір, чи це файл Хмельницького.")
            # Перед фільтрацією додаємо відсутні в деяких OSM об'єктах колонки.
            for c in tags:
                if c not in all_features:
                    all_features[c] = None

            heritage = all_features[nonempty(all_features["historic"]) |
                                    values(all_features["tourism"], TOURISM)].copy()
            stations = all_features[
                values(all_features["railway"], STATION_RAILWAY) |
                values(all_features["building"], STATION_BUILDING) |
                values(all_features["amenity"], STATION_AMENITY)
            ].copy()
            amenities = all_features[values(all_features["amenity"], AMENITY)].copy()
            for nm, frame in (("heritage", heritage), ("stations", stations), ("amenities", amenities)):
                if nm in todo:
                    if frame.empty:
                        raise RuntimeError(f"Порожній шар {nm}: перевір область витягу й набір тегів.")
                    clean(frame).to_parquet(out / (nm + ".parquet"), index=False)
                    print(f"{nm}: {len(frame)} об'єктів")

            if "boundary" in todo:
                city_re = re.compile(r"хмельницьк|khmelnytsk|khmelnitsk|chmelnyzky", re.I)
                b_candidates = all_features[
                    values(all_features["boundary"], {"administrative"}) &
                    all_features["name"].fillna("").astype(str).str.contains(city_re) &
                    all_features.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
                ] if "name" in all_features else all_features.iloc[0:0]
                if len(b_candidates):
                    # Якщо у витяг потрапила також область, беремо найменший
                    # полігон з назвою міста, а не обласну адміністративну межу.
                    names = b_candidates["name"].fillna("").astype(str).str.strip().str.lower()
                    exact = b_candidates[names.isin({"хмельницький", "khmelnytskyi", "khmelnitsky"})]
                    if len(exact):
                        b_candidates = exact
                    if "admin_level" in b_candidates:
                        c8 = b_candidates[b_candidates["admin_level"].astype(str).eq("8")]
                        if len(c8):
                            b_candidates = c8
                    b = b_candidates.loc[[b_candidates.geometry.area.idxmin()]].copy()
                    b = b.set_crs(4326, allow_override=True) if b.crs is None else b
                    method = "адміністративна межа OSM"
                else:
                    # У BBBike extract полілінія адмінмежі може бути неповною.
                    # Тоді bbox лише технічний замінник, не точна міська межа.
                    bbox = xml_bounds(xml)
                    if bbox is None:
                        bbox = all_features.total_bounds
                    xmin, ymin, xmax, ymax = bbox
                    b = gpd.GeoDataFrame({"name": ["Хмельницький (bbox витягу)"],
                                          "geometry": [box(xmin, ymin, xmax, ymax)]}, crs="EPSG:4326")
                    method = "прямокутник даних, не адміністративна межа"
                clean(b).to_parquet(out / "boundary.parquet", index=False)
                manifest["_межа"] = method
                print(f"boundary: {method}")

        if todo.intersection({"road_nodes", "road_edges"}):
            print("Будую пішохідний граф із XML локально (без Overpass)...")
            graph = ox.graph_from_xml(xml, bidirectional=True, simplify=False, retain_all=True)
            bad_edges = [(u, v, key) for u, v, key, data in graph.edges(keys=True, data=True)
                         if not is_walkable(data)]
            graph.remove_edges_from(bad_edges)
            graph.remove_nodes_from(list(nx.isolates(graph)))
            if graph.number_of_nodes() < 2 or graph.number_of_edges() < 1:
                raise RuntimeError("Після відбору пішохідних доріг граф порожній. Перевір формат OSM XML.")
            components = list(nx.weakly_connected_components(graph))
            graph = graph.subgraph(max(components, key=len)).copy()
            try:
                graph = ox.simplification.simplify_graph(graph)
            except Exception as e:
                print("Попередження: не вдалося спростити граф; залишено без спрощення:", e)
            nodes, edges = ox.convert.graph_to_gdfs(graph)
            if "road_nodes" in todo:
                clean(nodes).to_parquet(out / "road_nodes.parquet", index=False)
            if "road_edges" in todo:
                clean(edges).to_parquet(out / "road_edges.parquet", index=False)
            manifest["_граф"] = {"вузлів": len(nodes), "ребер": len(edges),
                                  "виключено_ребер": len(bad_edges), "тип": "walk"}
            print(f"road_nodes: {len(nodes)}; road_edges: {len(edges)}")

        missing = [name for name in needed if not (out / (name + ".parquet")).exists()]
        if missing:
            raise RuntimeError("Не створено файли: " + ", ".join(missing))
        manifest["_секунд_локального_імпорту"] = round(time.perf_counter() - t0, 2)
        manifest["_передано_МБ_під_час_імпорту"] = 0  # завантаження OSM XML окреме, не виміряне
        for name in needed:
            p = out / (name + ".parquet")
            with_size = p.stat().st_size
            layer = gpd.read_parquet(p)
            if layer.empty:
                raise RuntimeError(f"Порожній шар: {name}")
            manifest[name] = {"записів": len(layer), "колонок": len(layer.columns),
                              "CRS": str(layer.crs), "файл_МБ": round(with_size / 1e6, 3),
                              "типи_геометрії": layer.geom_type.value_counts().to_dict()}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nГОТОВО: створено всі шари ЛР5 у", out.resolve())
    for name in needed:
        print(" ", name + ".parquet")
    print("Тепер відкрий ноутбук ЛР5 і запусти Restart & Run All.")
    print("Обов'язково перевір вокзал, кількість точок, вузли snap та маршрут на карті.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\nПОМИЛКА: {exc}", file=sys.stderr)
        sys.exit(1)

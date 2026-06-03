"""
Fallback SIRENE + OSM pour pharmacies et matériel médical.
SIRENE est utilisé en premier (fiable, rapide).
OSM/Overpass est utilisé en complément uniquement si disponible ET si SIRENE < OSM.
"""

import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import REQUEST_DELAY

_SIRENE_SEARCH = "https://recherche-entreprises.api.gouv.fr/search"
_OVERPASS_URL  = "https://overpass-api.de/api/interpreter"
_GEO_URL       = "https://geo.api.gouv.fr/communes/{insee}?fields=contour"
_HEADERS       = {"User-Agent": "DIP-Tire-Lait-Express/1.0 (educational project)"}
_DELAY         = 0.2

_poly_cache: dict[str, str | None] = {}
_overpass_ok: bool | None = None
_overpass_lock = threading.Lock()

# ── SIRENE ────────────────────────────────────────────────────────────────────

def _is_holding(nom: str) -> bool:
    n = nom.upper()
    return (n.startswith("SPFPL")
            or "PARTICIPATIONS FINANCIERES" in n
            or (n.startswith("HOLDING") and "PHARMACIE" not in n))


def _sirene_query(insee: str, naf: str) -> list[dict]:
    if not insee:
        return []
    seen_adr: set[str] = set()
    etabs: list[dict] = []
    params = {"activite_principale": naf, "code_commune": insee,
              "page": 1, "per_page": 25}
    while True:
        try:
            time.sleep(_DELAY)
            r = requests.get(_SIRENE_SEARCH, params=params,
                             headers=_HEADERS, timeout=12)
            if not r.ok:
                break
            data = r.json()
            for ent in data.get("results", []):
                nom_ent = ent.get("nom_complet", "") or "?"
                if _is_holding(nom_ent):
                    continue
                for e in ent.get("matching_etablissements", []):
                    if e.get("etat_administratif", "A") != "A":
                        continue
                    adr = (e.get("adresse") or "").upper().strip()
                    if adr in seen_adr:
                        continue
                    if adr:
                        seen_adr.add(adr)
                    cp_e  = e.get("code_postal", "")
                    city_e = e.get("libelle_commune", "")
                    adresse = ", ".join(filter(None, [
                        e.get("adresse", "").strip(),
                        f"{cp_e} {city_e}".strip(),
                    ]))
                    etabs.append({"nom": nom_ent, "adresse": adresse})
            if params["page"] >= data.get("total_pages", 1):
                break
            params["page"] += 1
        except Exception:
            break
    return etabs


# ── OSM / Overpass (optionnel) ────────────────────────────────────────────────

_TAGS_PH_OSM = [("amenity", "pharmacy")]
_TAGS_MM_OSM = [
    ("shop", "medical_supply"), ("shop", "orthopedics"),
    ("shop", "hearing_aids"),   ("shop", "optician"),
    ("healthcare", "audiologist"),
]


def _check_overpass() -> bool:
    global _overpass_ok
    if _overpass_ok is not None:
        return _overpass_ok
    with _overpass_lock:
        if _overpass_ok is not None:
            return _overpass_ok
        try:
            r = requests.post(_OVERPASS_URL,
                              data={"data": "[out:json];out;"},
                              headers=_HEADERS, timeout=4)
            _overpass_ok = r.ok
        except Exception:
            _overpass_ok = False
        print(f"[OSM] Overpass {'disponible' if _overpass_ok else 'indisponible'}")
        return _overpass_ok


def _get_poly(insee: str) -> str | None:
    if insee in _poly_cache:
        return _poly_cache[insee]
    try:
        r = requests.get(_GEO_URL.format(insee=insee),
                         headers=_HEADERS, timeout=6)
        if not r.ok:
            _poly_cache[insee] = None
            return None
        contour = r.json().get("contour")
        if not contour:
            _poly_cache[insee] = None
            return None
        if contour["type"] == "Polygon":
            coords = contour["coordinates"][0]
        elif contour["type"] == "MultiPolygon":
            coords = max([p[0] for p in contour["coordinates"]], key=len)
        else:
            _poly_cache[insee] = None
            return None
        step = max(1, len(coords) // 60)
        coords = coords[::step]
        poly_str = " ".join(f"{c[1]} {c[0]}" for c in coords)
        _poly_cache[insee] = poly_str
        return poly_str
    except Exception:
        _poly_cache[insee] = None
        return None


def _osm_query(insee: str, cp: str) -> tuple[list[dict], list[dict]]:
    poly = _get_poly(insee)
    if not poly:
        return [], []
    ph_parts = [f'  node["amenity"="pharmacy"](poly:"{poly}");',
                f'  way["amenity"="pharmacy"](poly:"{poly}");']
    mm_parts = []
    for k, v in _TAGS_MM_OSM:
        mm_parts += [f'  node["{k}"="{v}"](poly:"{poly}");',
                     f'  way["{k}"="{v}"](poly:"{poly}");']
    query = ("[out:json][timeout:15];\n(\n"
             + "\n".join(ph_parts + mm_parts)
             + "\n);\nout center tags;")
    try:
        time.sleep(_DELAY)
        r = requests.post(_OVERPASS_URL, data={"data": query},
                          headers=_HEADERS, timeout=20)
        if not r.ok:
            return [], []
        ph_vals = {"pharmacy"}
        mm_vals = {v for _, v in _TAGS_MM_OSM}
        ph_etabs, mm_etabs = [], []
        for el in r.json().get("elements", []):
            tags = el.get("tags", {})
            nom  = tags.get("name", "")
            rue  = f"{tags.get('addr:housenumber','')} {tags.get('addr:street','')}".strip()
            city = f"{tags.get('addr:postcode', cp)} {tags.get('addr:city','')}".strip()
            adr  = ", ".join(filter(None, [rue, city]))
            e    = {"nom": nom, "adresse": adr}
            tvals = set(tags.values())
            if tvals & ph_vals:
                ph_etabs.append(e)
            elif tvals & mm_vals:
                mm_etabs.append(e)
        return ph_etabs, mm_etabs
    except Exception:
        return [], []


# ── Traitement d'une commune ──────────────────────────────────────────────────

def _process_one(c: dict, source_date: str) -> dict:
    nom   = c.get("nom", "")
    cp    = c.get("cp", "")
    insee = c.get("code_insee") or c.get("code", "")

    # SIRENE (toujours)
    ph_sir = _sirene_query(insee, "47.73Z")

    # Mat. médical : 47.74Z (vente) + 77.29Z (location/PSAD), dédup par adresse
    mm_74 = _sirene_query(insee, "47.74Z")
    mm_77 = _sirene_query(insee, "77.29Z")
    adr_vus: set[str] = {e["adresse"].upper().strip() for e in mm_74 if e["adresse"]}
    for e in mm_77:
        a = e["adresse"].upper().strip()
        if a and a not in adr_vus:
            adr_vus.add(a)
            mm_74.append(e)
    mm_sir = mm_74

    # OSM optionnel (si disponible)
    ph_osm, mm_osm = [], []
    if _check_overpass():
        ph_osm, mm_osm = _osm_query(insee, cp)

    ph = ph_osm if len(ph_osm) > len(ph_sir) else ph_sir
    mm = mm_osm if len(mm_osm) > len(mm_sir) else mm_sir

    print(f"[PJ] {nom}: ph={len(ph)}(osm={len(ph_osm)},sir={len(ph_sir)})"
          f" mag={len(mm)}(osm={len(mm_osm)},sir={len(mm_sir)})")
    return {
        "commune":                   nom,
        "cp":                        cp,
        "nb_pharmacies":             len(ph),
        "noms_pharmacies":           [e["nom"]     for e in ph],
        "adresses_pharmacies":       [e["adresse"] for e in ph],
        "nb_materiel_medical":       len(mm),
        "noms_materiel_medical":     [e["nom"]     for e in mm],
        "adresses_materiel_medical": [e["adresse"] for e in mm],
        "_source": f"SIRENE+OSM — {source_date}",
    }


# ── Point d'entrée ────────────────────────────────────────────────────────────

def get_pharmacies_and_medical(communes: list[dict]) -> list[dict]:
    source_date = time.strftime("%d/%m/%Y")

    # Dédupliquer par code_insee ET par nom
    seen: set[str] = set()
    unique: list[dict] = []
    for c in communes:
        insee = c.get("code_insee") or c.get("code", "")
        nom   = (c.get("nom", "") or "").lower().strip()
        key   = insee if insee else nom
        if not key:
            continue
        if key in seen or (nom and nom in seen):
            continue
        seen.add(key)
        if nom:
            seen.add(nom)
        unique.append(c)

    print(f"[PJ] {len(unique)} communes uniques à traiter")

    # Pré-charger les polygones si Overpass est disponible
    if _check_overpass():
        insees = [c.get("code_insee") or c.get("code", "") for c in unique]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_get_poly, insees))

    # Traiter en parallèle (4 threads)
    results: list[dict | None] = [None] * len(unique)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_process_one, c, source_date): i
                   for i, c in enumerate(unique)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:
                c = unique[idx]
                results[idx] = {
                    "commune": c.get("nom", ""), "cp": c.get("cp", ""),
                    "nb_pharmacies": 0, "noms_pharmacies": [],
                    "adresses_pharmacies": [],
                    "nb_materiel_medical": 0, "noms_materiel_medical": [],
                    "adresses_materiel_medical": [],
                    "_source": f"SIRENE+OSM — {source_date}",
                }

    return [r for r in results if r is not None]

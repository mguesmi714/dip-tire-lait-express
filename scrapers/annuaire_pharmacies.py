"""
Scraper annuaire.des-pharmacies.fr — pharmacies par code postal.
Avantages vs Pages Jaunes : pas de Playwright, pas de protection anti-bot, rapide.
Pagination : URL /departement/list/{dept}/page-{n}?q={cp} (20 par page).

Matériel médical : récupéré via SIRENE (NAF 47.74Z / 77.29Z).
"""

import re
import time
import unicodedata
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

_ANNUAIRE_URL      = "https://annuaire.des-pharmacies.fr/departement/list/{dept_slug}?q={cp}"
_ANNUAIRE_URL_PAGE = "https://annuaire.des-pharmacies.fr/departement/list/{dept_slug}/page-{page}?q={cp}"
_SIRENE_URL   = "https://recherche-entreprises.api.gouv.fr/search"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
}
_DELAY = 0.4


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"[^a-z0-9\-]", "", text)
    return re.sub(r"-+", "-", text).strip("-")


def _dept_slug(commune: dict) -> str:
    # communes_pj utilise dept_nom (str) ; communes_flat utilise departement (dict)
    nom = commune.get("dept_nom", "").strip()
    if not nom:
        dept = commune.get("departement") or {}
        nom = (dept.get("nom", "") if isinstance(dept, dict) else str(dept)).strip()
    return _slugify(nom) if nom else ""


def _scrape_pharmacies(cp: str, slug: str) -> tuple[list[str], list[str]]:
    """Retourne (noms, adresses) des pharmacies pour ce CP via l'annuaire."""
    if not cp or not slug:
        return [], []

    noms: list[str] = []
    adresses: list[str] = []
    page = 1

    while True:
        url = (
            _ANNUAIRE_URL.format(dept_slug=slug, cp=cp)
            if page == 1
            else _ANNUAIRE_URL_PAGE.format(dept_slug=slug, page=page, cp=cp)
        )
        try:
            time.sleep(_DELAY)
            r = requests.get(url, headers=_HEADERS, timeout=15)
            if not r.ok:
                break

            soup = BeautifulSoup(r.text, "html.parser")
            items = soup.find_all("div", class_="item-society")
            if not items:
                break

            for item in items:
                strong = item.find("strong")
                nom = strong.get_text(strip=True) if strong else ""
                if not nom:
                    continue

                # Adresse : texte de l'<a> dans card-body, avant l'icône téléphone <i>
                adresse = ""
                body = item.find("div", class_="card-body")
                if body:
                    a = body.find("a")
                    if a:
                        parts = []
                        for node in a.children:
                            if getattr(node, "name", None) == "i":
                                break
                            if getattr(node, "name", None) is None:
                                txt = str(node).strip()
                                if txt:
                                    parts.append(txt)
                        adresse = ", ".join(parts)

                noms.append(nom)
                adresses.append(adresse)

            # Vérifier s'il y a une page suivante
            pag = soup.find("ul", class_="pagination")
            next_link = pag.find("a", class_="page-next") if pag else None
            next_href = (next_link.get("href", "#") if next_link else "#").strip()
            if not next_href or next_href == "#":
                break
            page += 1

        except Exception as e:
            print(f"[ANNUAIRE-PH] {cp} p{page}: {e}")
            break

    return noms, adresses


def _is_holding(nom: str) -> bool:
    n = nom.upper()
    return (
        n.startswith("SPFPL")
        or "PARTICIPATIONS FINANCIERES" in n
        or (n.startswith("HOLDING") and "PHARMACIE" not in n)
    )


def _sirene_matmed(code_insee: str) -> tuple[list[str], list[str]]:
    """Matériel médical via SIRENE (NAF 47.74Z et 77.29Z)."""
    if not code_insee:
        return [], []

    noms: list[str] = []
    adresses: list[str] = []
    seen_adr: set[str] = set()

    for naf in ("47.74Z", "77.29Z"):
        params = {"activite_principale": naf, "code_commune": code_insee,
                  "page": 1, "per_page": 25}
        while True:
            try:
                time.sleep(0.2)
                r = requests.get(_SIRENE_URL, params=params, headers=_HEADERS, timeout=12)
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
                        cp_e   = e.get("code_postal", "")
                        city_e = e.get("libelle_commune", "")
                        adresse = ", ".join(filter(None, [
                            e.get("adresse", "").strip(),
                            f"{cp_e} {city_e}".strip(),
                        ]))
                        noms.append(nom_ent)
                        adresses.append(adresse)
                if params["page"] >= data.get("total_pages", 1):
                    break
                params["page"] += 1
            except Exception:
                break

    return noms, adresses


def _process_commune(commune: dict) -> dict:
    cp          = commune.get("cp", "")
    nom_commune = commune.get("nom", "")
    code_insee  = commune.get("code_insee", "")
    source_date = time.strftime("%d/%m/%Y")

    slug = _dept_slug(commune)
    ph_noms, ph_adrs = _scrape_pharmacies(cp, slug)
    mm_noms, mm_adrs = _sirene_matmed(code_insee)

    return {
        "commune":                  nom_commune,
        "cp":                       cp,
        "nb_pharmacies":            len(ph_noms),
        "noms_pharmacies":          ph_noms,
        "adresses_pharmacies":      ph_adrs,
        "nb_materiel_medical":      len(mm_noms),
        "noms_materiel_medical":    mm_noms,
        "adresses_materiel_medical": mm_adrs,
        "_source": f"Annuaire des Pharmacies + SIRENE — consulté le {source_date}",
    }


def get_pharmacies_and_medical(communes: list[dict]) -> list[dict]:
    """
    Collecte pharmacies (annuaire.des-pharmacies.fr) et matériel médical (SIRENE)
    pour chaque commune.
    Même interface que pages_jaunes.get_pharmacies_and_medical.
    """
    # Déduplication par cp : chaque code postal est une page annuaire distincte.
    # On évite code_insee comme clé car deux arrondissements (ex. 13015/13016)
    # peuvent avoir le même code_insee 13055 si la résolution est en cache.
    seen: set[str] = set()
    unique: list[dict] = []
    for c in communes:
        key = c.get("cp", "") or c.get("code_insee") or c.get("nom", "")
        if key not in seen:
            seen.add(key)
            unique.append(c)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_process_commune, c): c for c in unique}
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                c = futures[f]
                print(f"[ANNUAIRE-PH] Exception {c.get('nom', '?')}: {e}")

    return results

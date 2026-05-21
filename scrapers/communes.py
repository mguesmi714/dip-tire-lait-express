"""
Résout un code postal → liste de communes avec leur nom et code INSEE.

Stratégie :
1. Annuaire-administration.com (page département) → noms officiels attendus par l'utilisateur
2. geo.api.gouv.fr → codes INSEE pour chaque nom
3. Fusion : on garde les noms de l'annuaire + codes INSEE de l'API
"""

import re
import time
import unicodedata
import requests
from bs4 import BeautifulSoup
from config import HEADERS, REQUEST_DELAY


_DEPT_PAGE  = "https://www.annuaire-administration.com/code-postal/departement/{slug}.html"
_GEO_DEPT   = "https://geo.api.gouv.fr/departements/{code}?fields=nom"
_GEO_CP     = "https://geo.api.gouv.fr/communes?codePostal={cp}&fields=nom,code,departement,region&format=json"

# Cache des pages département pour ne pas les télécharger plusieurs fois
_dept_cache: dict[str, dict[str, list[str]]] = {}   # {dept_code: {cp: [noms]}}


def _extract_dept(cp: str) -> str:
    if cp.startswith("97") or cp.startswith("98"):
        return cp[:3]
    return cp[:2]


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"[^a-z0-9\-]", "", text)
    return re.sub(r"-+", "-", text).strip("-")


def _dept_nom(dept_code: str) -> str:
    try:
        r = requests.get(_GEO_DEPT.format(code=dept_code), timeout=8)
        return r.json().get("nom", "")
    except Exception:
        return ""


def _load_dept_page(dept_code: str) -> dict[str, list[str]]:
    """
    Charge la page département et retourne un dict {cp: [noms_communes]}.
    Le résultat est mis en cache.
    """
    if dept_code in _dept_cache:
        return _dept_cache[dept_code]

    dept_nom = _dept_nom(dept_code)
    if not dept_nom:
        _dept_cache[dept_code] = {}
        return {}

    slug = _slugify(dept_nom)
    url  = _DEPT_PAGE.format(slug=slug)

    try:
        time.sleep(REQUEST_DELAY)
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            _dept_cache[dept_code] = {}
            return {}

        soup   = BeautifulSoup(r.text, "html.parser")
        result: dict[str, list[str]] = {}

        # Chercher le tableau CP → Communes : 1re ligne = "Codes Postaux | Communes"
        for table in soup.find_all("table"):
            first_row = table.find("tr")
            if not first_row:
                continue
            first_text = first_row.get_text(" ", strip=True)
            if "Codes Postaux" not in first_text or "Communes" not in first_text:
                continue

            for row in table.find_all("tr"):
                tds = row.find_all("td")
                if len(tds) < 2:
                    continue

                # td[0] contient "Code Postal XXXXX"
                cp_match = re.search(r"\b(\d{5})\b", tds[0].get_text())
                if not cp_match:
                    continue
                cp = cp_match.group(1)

                # td[1] : les noms de communes dans les <a> ou en texte brut
                noms = [a.get_text(strip=True) for a in tds[1].find_all("a") if a.get_text(strip=True)]
                if not noms:
                    # Fallback : texte brut séparé par des espaces (moins fiable)
                    noms = [n.strip() for n in tds[1].get_text(" ", strip=True).split("  ") if n.strip()]

                if noms:
                    result[cp] = noms

            if result:
                break

        _dept_cache[dept_code] = result
        return result

    except Exception:
        _dept_cache[dept_code] = {}
        return {}


def _normalize(name: str) -> str:
    """Normalise un nom pour la comparaison (minuscule, sans accents, sans tirets/apostrophes)."""
    name = name.lower().strip()
    name = "".join(
        c for c in unicodedata.normalize("NFD", name)
        if unicodedata.category(c) != "Mn"
    )
    # Unifier tirets, apostrophes et espaces → espace simple
    name = re.sub(r"[-'\s]+", " ", name).strip()
    return name


def get_communes_for_cp(cp: str) -> list[dict]:
    """
    Retourne la liste des communes pour un code postal.
    Noms depuis annuaire-administration.com + codes INSEE depuis geo.api.gouv.fr.
    """
    dept_code = _extract_dept(cp)

    # 1. Noms depuis annuaire-administration.com
    dept_data = _load_dept_page(dept_code)
    noms_annuaire: list[str] = dept_data.get(cp, [])

    # 2. Données INSEE depuis geo.api.gouv.fr (par code postal)
    geo_data: list[dict] = []
    try:
        r = requests.get(_GEO_CP.format(cp=cp), timeout=10)
        if r.status_code == 200:
            geo_data = r.json()
    except Exception:
        pass

    # 2b. Si aucun résultat par CP, tenter comme code INSEE direct
    # (l'email peut contenir des codes COG comme 57117 au lieu du CP 57130)
    if not geo_data:
        try:
            r2 = requests.get(
                f"https://geo.api.gouv.fr/communes/{cp}?fields=nom,code,departement,region",
                timeout=10
            )
            if r2.status_code == 200:
                c = r2.json()
                if c.get("code"):
                    geo_data = [c]
                    print(f"[COMMUNES] {cp} résolu comme code INSEE → {c.get('nom')}")
        except Exception:
            pass

    # Index geo par nom normalisé → pour retrouver le code INSEE
    geo_by_norm: dict[str, dict] = {
        _normalize(c.get("nom", "")): c
        for c in geo_data
    }

    # 3. Fusion : garder noms annuaire + associer code INSEE si trouvé
    communes: list[dict] = []
    matched_geo_codes: set[str] = set()

    for nom in noms_annuaire:
        norm = _normalize(nom)
        geo  = geo_by_norm.get(norm)

        # Fallback : si pas de correspondance exacte, chercher par inclusion de mots
        if not geo:
            for geo_norm, geo_c in geo_by_norm.items():
                if norm in geo_norm or geo_norm in norm:
                    geo = geo_c
                    break

        code_insee  = geo.get("code", "") if geo else ""
        departement = geo.get("departement", {}) if geo else {"code": dept_code, "nom": ""}
        region      = geo.get("region", {})       if geo else {}
        if geo:
            matched_geo_codes.add(geo.get("code", ""))
        communes.append({
            "nom":         nom,
            "cp":          cp,
            "dept":        dept_code,
            "code_insee":  code_insee,
            "departement": departement,
            "region":      region,
        })

    # 3b. Communes de l'annuaire sans code INSEE → récupérer les codes geo restants
    unmatched_geo = [c for c in geo_data if c.get("code", "") not in matched_geo_codes]
    sans_code = [c for c in communes if not c.get("code_insee")]
    for comm, geo_c in zip(sans_code, unmatched_geo):
        comm["code_insee"]  = geo_c.get("code", "")
        comm["departement"] = geo_c.get("departement", comm["departement"])
        comm["region"]      = geo_c.get("region", comm["region"])

    # 4. Si annuaire vide → utiliser geo.api.gouv.fr seul (fallback)
    if not communes and geo_data:
        communes = [
            {
                "nom":         c.get("nom", ""),
                "cp":          cp,
                "dept":        dept_code,
                "code_insee":  c.get("code", ""),
                "departement": c.get("departement", {}),
                "region":      c.get("region", {}),
            }
            for c in geo_data
        ]
    elif not communes:
        communes = [{"nom": f"Commune_{cp}", "cp": cp, "dept": dept_code,
                     "code_insee": "", "_statut": "non résolu"}]

    return communes


def get_communes_for_all_cp(codes_postaux: list[str]) -> dict[str, list[dict]]:
    """
    Pour chaque code postal, retourne la liste des communes.
    """
    result: dict[str, list[dict]] = {}
    for cp in codes_postaux:
        result[cp] = get_communes_for_cp(cp)
    return result

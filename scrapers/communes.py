"""
Scraper annuaire-administration.com
Résout un code postal → liste de communes avec leur nom officiel.
URL pattern : https://www.annuaire-administration.com/code-postal/{cp}/
"""

import re
import time
import requests
from bs4 import BeautifulSoup
from config import HEADERS, REQUEST_DELAY


_BASE = "https://www.annuaire-administration.com/code-postal/{cp}/"
_DEPT_BASE = "https://www.annuaire-administration.com/code-postal/{dept}/"


def _extract_dept(cp: str) -> str:
    """Extrait le code département depuis un CP (ex. '56000' → '56')."""
    if cp.startswith("97") or cp.startswith("98"):
        return cp[:3]
    return cp[:2]


def get_communes_for_cp(cp: str) -> list[dict]:
    """
    Récupère la liste des communes pour un code postal donné.

    Returns:
        liste de dicts : {"nom": str, "cp": str, "dept": str}
    """
    url = _BASE.format(cp=cp)
    communes = []

    try:
        time.sleep(REQUEST_DELAY)
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Les communes sont dans des liens ou cellules de tableau
        # Chercher d'abord dans un tableau
        for tr in soup.select("table tr"):
            cells = tr.select("td, th")
            for cell in cells:
                text = cell.get_text(strip=True)
                # Ignorer les en-têtes et cellules vides
                if text and len(text) > 1 and not text.isdigit():
                    # Vérifier que ce n'est pas un CP (5 chiffres)
                    if not re.match(r"^\d{5}$", text):
                        communes.append({
                            "nom": text,
                            "cp": cp,
                            "dept": _extract_dept(cp),
                        })

        # Fallback : chercher dans des listes ou liens
        if not communes:
            for a in soup.select("a, li, .commune, [class*='commune']"):
                text = a.get_text(strip=True)
                if (text and len(text) > 2
                        and not re.match(r"^\d+$", text)
                        and len(text) < 60):
                    communes.append({
                        "nom": text,
                        "cp": cp,
                        "dept": _extract_dept(cp),
                    })

        # Dédoublonner par nom
        seen = set()
        unique = []
        for c in communes:
            key = c["nom"].upper()
            if key not in seen and not any(
                kw in c["nom"].lower()
                for kw in ["accueil", "menu", "retour", "page", "france",
                            "administration", "annuaire", "recherche"]
            ):
                seen.add(key)
                unique.append(c)

        if unique:
            return unique

    except Exception:
        pass

    # Fallback : API geo.api.gouv.fr
    return _fallback_geo_api(cp)


def _fallback_geo_api(cp: str) -> list[dict]:
    """Fallback via l'API geo.api.gouv.fr si annuaire-administration échoue."""
    try:
        url = f"https://geo.api.gouv.fr/communes?codePostal={cp}&fields=nom,code,departement&format=json"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        return [
            {
                "nom": c.get("nom", ""),
                "cp": cp,
                "dept": c.get("departement", {}).get("code", cp[:2]),
                "code_insee": c.get("code", ""),
            }
            for c in data
        ]
    except Exception:
        return [{"nom": f"Commune_{cp}", "cp": cp, "dept": cp[:2], "_statut": "non résolu"}]


def get_communes_for_all_cp(codes_postaux: list[str]) -> dict[str, list[dict]]:
    """
    Pour chaque code postal, retourne la liste des communes.

    Returns:
        dict { "56000": [{"nom": "Vannes", "cp": "56000", ...}], ... }
    """
    result: dict[str, list[dict]] = {}
    for cp in codes_postaux:
        communes = get_communes_for_cp(cp)
        result[cp] = communes
    return result

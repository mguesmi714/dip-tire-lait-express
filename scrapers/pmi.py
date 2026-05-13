"""
Scraper PMI — allopmi.fr
Recherche les centres PMI (Protection Maternelle et Infantile) par département.
"""

import time
import requests
from bs4 import BeautifulSoup
from config import HEADERS, REQUEST_DELAY, PMI_URL


_DEPT_URL = "https://allopmi.fr/departement/{dept}/"


def get_pmi(departement_code: str, departement_nom: str) -> list[dict]:
    """
    Récupère la liste des PMI pour un département.

    Args:
        departement_code: "42"
        departement_nom: "Loire"

    Returns:
        liste de dicts : nom, adresse, telephone, commune, _source
    """
    results = []
    url = _DEPT_URL.format(dept=departement_code)

    try:
        time.sleep(REQUEST_DELAY)
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Fiches PMI
        cards = soup.select(
            ".pmi-item, .listing-item, article, li.pmi, [class*='pmi']"
        )

        if not cards:
            cards = soup.select("table tr")

        for card in cards:
            text = card.get_text(" ", strip=True)
            if len(text) < 5:
                continue

            nom_el = card.select_one("h2, h3, h4, .nom, strong, a")
            nom = nom_el.get_text(strip=True) if nom_el else text[:60]
            if not nom or nom.lower() in ("nom", "adresse", "téléphone"):
                continue

            adresse_el = card.select_one("address, .adresse, [class*='adresse']")
            adresse = adresse_el.get_text(strip=True) if adresse_el else ""

            tel_el = card.select_one("a[href^='tel'], [class*='tel'], [class*='phone']")
            tel = ""
            if tel_el:
                tel = (tel_el.get_attribute("href") or "").replace("tel:", "") or tel_el.get_text(strip=True)

            commune_el = card.select_one("[class*='ville'], [class*='commune'], [class*='city']")
            commune = commune_el.get_text(strip=True) if commune_el else departement_nom

            results.append({
                "nom": nom,
                "adresse": adresse,
                "telephone": tel,
                "commune": commune,
                "_source": f"AlloPMI — consulté le {time.strftime('%d/%m/%Y')}",
            })

    except Exception as e:
        return [{
            "nom": f"Erreur de collecte : {e}",
            "adresse": "",
            "telephone": "",
            "commune": departement_nom,
            "_source": f"AlloPMI — vérification manuelle requise sur {url}",
        }]

    if not results:
        return [{
            "nom": f"Aucune PMI trouvée automatiquement pour le département {departement_code} ({departement_nom})",
            "adresse": "",
            "telephone": "",
            "commune": "",
            "_source": f"AlloPMI — vérification manuelle requise sur {url}",
        }]

    return results

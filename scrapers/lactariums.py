"""
Scraper lactariums — Association des Lactariums de France
https://association-des-lactariums-de-france.fr/liste-et-carte-des-lactariums/
"""

import time
import requests
from bs4 import BeautifulSoup
from config import HEADERS, REQUEST_DELAY, LACTARIUMS_URL


def get_lactariums(departement_code: str, region_nom: str) -> list[dict]:
    """
    Récupère la liste des lactariums et filtre par département ou région.

    Returns:
        liste de dicts : nom, ville, departement, telephone, lien, _source
        Si aucun lactarium sur la zone, retourne un message indiquant le plus proche.
    """
    all_lactariums: list[dict] = []

    try:
        time.sleep(REQUEST_DELAY)
        r = requests.get(LACTARIUMS_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Les lactariums sont typiquement listés dans des articles, divs ou tableaux
        entries = soup.select(
            "article, .lactarium-item, .entry, li, tr, [class*='lactarium']"
        )

        for entry in entries:
            text = entry.get_text(" ", strip=True)
            if len(text) < 10:
                continue

            # Chercher un nom d'hôpital / lactarium
            nom_el = entry.select_one("h2, h3, h4, strong, b, .title, a")
            nom = nom_el.get_text(strip=True) if nom_el else text[:80]

            # Chercher téléphone
            tel = ""
            for t in entry.find_all(string=True):
                if "0" in t and (len(t.strip()) >= 10):
                    cleaned = "".join(c for c in t if c.isdigit() or c in " .-")
                    if len(cleaned.replace(" ", "").replace(".", "").replace("-", "")) >= 10:
                        tel = cleaned.strip()
                        break

            # Chercher département (numéro à 2 chiffres entre parenthèses ou après une ville)
            import re
            dept_match = re.search(r"\((\d{2,3})\)", text)
            dept = dept_match.group(1) if dept_match else ""

            lien_el = entry.select_one("a[href]")
            lien = lien_el["href"] if lien_el else ""

            all_lactariums.append({
                "nom": nom,
                "departement": dept,
                "telephone": tel,
                "lien": lien,
                "_raw": text,
            })

    except Exception as e:
        return [{
            "nom": f"Erreur de collecte : {e}",
            "departement": "",
            "telephone": "",
            "lien": LACTARIUMS_URL,
            "_source": "ALF — erreur",
        }]

    # Filtrer par département
    zone_lactariums = [
        lac for lac in all_lactariums
        if lac["departement"] == departement_code
    ]

    source = f"Association des Lactariums de France — consulté le {time.strftime('%d/%m/%Y')}"

    if zone_lactariums:
        for lac in zone_lactariums:
            lac["_source"] = source
            lac.pop("_raw", None)
        return zone_lactariums

    # Chercher dans la même région (filtrage par mots-clés de la région)
    region_keywords = region_nom.lower().split("-")
    region_matches = [
        lac for lac in all_lactariums
        if any(kw in lac.get("_raw", "").lower() for kw in region_keywords)
    ]

    if region_matches:
        for lac in region_matches[:3]:
            lac["_source"] = source
            lac.pop("_raw", None)
        return region_matches[:3]

    # Aucun lactarium sur la zone
    return [{
        "nom": f"Il n'existe aucun lactarium dans le département {departement_code}. "
               f"Le lactarium référent de la région {region_nom} assure la collecte.",
        "departement": departement_code,
        "telephone": "",
        "lien": LACTARIUMS_URL,
        "_source": source,
    }]

"""
Scraper sages-femmes — Ordre national des sages-femmes
URL : https://www.ordre-sages-femmes.fr/patient-e-s/trouver-une-sage-femme/dept/{dept}/

Page HTML statique — requests + BeautifulSoup, aucun navigateur necessaire.

Logique :
  1. Charger la page pour chaque code departement demande
  2. Parser les <div> contenant un <h3> (une SF par div)
  3. Dedoublonner par nom complet : une SF dans 2 depts -> 1 entree, tous ses CP cumules
  4. Retourner la liste triee + total unique
"""

import re
import time
import requests
from bs4 import BeautifulSoup

_BASE_URL = "https://www.ordre-sages-femmes.fr/patient-e-s/trouver-une-sage-femme/dept/{dept}/"
_HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}
_CP_RE = re.compile(r'^(\d{5})\s+(.+)$')


def _parse_page(soup: BeautifulSoup, dept: str) -> list[dict]:
    """
    Extrait toutes les sages-femmes d'une page departement.

    Structure reelle (Schema.org) :
      <li class="archive-item listing-item" itemtype=".../Organization">
        <span itemprop="name">Madame ISABELLE MURAT</span>
        <div itemprop="streetAddress">16 RUE...</div>
        <span itemprop="postalCode">56000</span>
        <span itemprop="addressLocality">VANNES</span>
        <dd itemprop="telephone"><a href="tel:...">02 97...</a></dd>
        <dd itemprop="telephone"><a href="mobile:...">06...</a></dd>
        <dd itemprop="email"><a href="mailto:...">...</a></dd>
      </li>
    """
    sfs = []
    for li in soup.find_all("li", class_=lambda c: c and "archive-item" in c):
        name_span = li.find("span", itemprop="name")
        if not name_span:
            continue

        # Supprimer la civilite
        raw = re.sub(r'^(Madame|Monsieur|Mme\.?|M\.?)\s+', '',
                     name_span.get_text(strip=True), flags=re.I).strip()
        parts  = raw.split()
        nom    = parts[-1]            if parts else raw
        prenom = " ".join(parts[:-1]) if len(parts) > 1 else ""

        # Adresse via itemprop Schema.org
        street = li.find(itemprop="streetAddress")
        rue    = street.get_text(strip=True) if street else ""
        cp_tag = li.find("span", itemprop="postalCode")
        cp     = cp_tag.get_text(strip=True) if cp_tag else ""
        city_tag = li.find("span", itemprop="addressLocality")
        ville  = city_tag.get_text(strip=True) if city_tag else ""

        # Contacts
        tel    = ""
        mobile = ""
        email  = ""
        for a in li.find_all("a"):
            href = a.get("href", "")
            val  = a.get_text(strip=True)
            if href.startswith("tel:"):
                tel = val
            elif href.startswith("mobile:"):
                mobile = val
            elif href.startswith("mailto:"):
                email = href.replace("mailto:", "").strip()

        adresse = ", ".join(filter(None, [rue, f"{cp} {ville}".strip()]))

        sfs.append({
            "nom":       nom,
            "prenom":    prenom,
            "adresse":   adresse,
            "rue":       rue,
            "cp":        cp,
            "ville":     ville,
            "telephone": tel or mobile,
            "mobile":    mobile,
            "email":     email,
            "dept":      dept,
        })

    return sfs


def get_sages_femmes(dept_codes, cp_list=None) -> list[dict]:
    """
    Scrappe les sages-femmes pour les departements donnes,
    filtrees sur les codes postaux de la zone si cp_list est fourni.

    Args:
        dept_codes : str ou list[str] — ex. "56" ou ["56", "29"]
        cp_list    : list[str] optionnel — ex. ["56000", "56190"]
                     Si fourni, seules les SF dont le CP est dans cp_list
                     sont retournees.

    Returns:
        Liste de dicts dedoublonnes. Si une SF travaille dans 2 CP de la zone,
        elle apparait une seule fois avec tous ses CP cumules.
    """
    if isinstance(dept_codes, str):
        dept_codes = [dept_codes]

    # Normaliser cp_list en ensemble pour la comparaison
    cp_set = set(cp_list) if cp_list else None

    source_date = time.strftime("%d/%m/%Y")
    seen: dict[str, dict] = {}   # cle : "NOM PRENOM" majuscule

    for dept in dept_codes:
        dept = str(dept).strip()
        url  = _BASE_URL.format(dept=dept)
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            print(f"[SF] Erreur dept {dept}: {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        sfs  = _parse_page(soup, dept)
        print(f"[SF] Dept {dept}: {len(sfs)} sages-femmes trouvees au total")

        for sf in sfs:
            # Filtrer par code postal si demande
            if cp_set and sf["cp"] not in cp_set:
                continue

            key = f"{sf['nom']} {sf['prenom']}".strip().upper()
            if key in seen:
                # SF deja vue avec un autre CP de la zone -> cumuler
                cp_val = sf["cp"]
                if cp_val and cp_val not in seen[key]["code_postaux"]:
                    seen[key]["code_postaux"].append(cp_val)
                    seen[key]["adresses_secondaires"].append(sf["adresse"])
            else:
                seen[key] = {
                    **sf,
                    "code_postaux":         [sf["cp"]] if sf["cp"] else [],
                    "adresses_secondaires": [],
                    "_source": f"Ordre national des sages-femmes — consulte le {source_date}",
                }

    results = sorted(seen.values(), key=lambda r: (r["nom"], r["prenom"]))

    for r in results:
        r["code_postaux_display"] = ", ".join(r["code_postaux"])

    filtre_info = f" (filtre CP: {sorted(cp_set)})" if cp_set else ""
    print(f"[SF] {len(results)} sage(s)-femme(s) retenue(s){filtre_info}")
    return results

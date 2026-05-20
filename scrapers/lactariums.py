"""
Scraper lactariums — Association des Lactariums de France
https://association-des-lactariums-de-france.fr/liste-et-carte-des-lactariums/

Logique :
  1. Page principale -> liens des pages region
  2. Page region    -> liens des pages detail de chaque lactarium
  3. Page detail    -> toutes les infos (nom, adresse, CP, tel, email, equipe, type)
  4. Filtre par code departement (CP commence par dept_code)
  5. Si aucun dans le dept -> retourner tous ceux de la region comme "referents"
"""

import re
import time
import requests
from bs4 import BeautifulSoup

from config import HEADERS, REQUEST_DELAY, LACTARIUMS_URL

# Correspondance code departement -> slug region sur le site ALF
_DEPT_TO_REGION: dict[str, str] = {
    # Auvergne-Rhone-Alpes
    **{d: "auvergne-rhone-alpes" for d in
       ["01","03","07","15","26","38","42","43","63","69","73","74"]},
    # Bourgogne-Franche-Comte
    **{d: "bourgogne-franche-comte" for d in
       ["21","25","39","58","70","71","89","90"]},
    # Bretagne
    **{d: "bretagne" for d in ["22","29","35","56"]},
    # Centre-Val-de-Loire
    **{d: "centre-val-de-loire" for d in ["18","28","36","37","41","45"]},
    # Grand Est
    **{d: "grand-est" for d in ["08","10","51","52","54","55","57","67","68","88"]},
    # Guyane
    "973": "guyane",
    # Hauts-de-France
    **{d: "hauts-de-france" for d in ["02","59","60","62","80"]},
    # Ile-de-France
    **{d: "ile-de-france" for d in ["75","77","78","91","92","93","94","95"]},
    # Martinique
    "972": "martinique",
    # Normandie
    **{d: "normandie" for d in ["14","27","50","61","76"]},
    # Nouvelle-Aquitaine
    **{d: "nouvelle-aquitaine" for d in
       ["16","17","19","23","24","33","40","47","64","79","86","87"]},
    # Occitanie / PACA
    **{d: "occitanie-provence-alpes-cote-dazur" for d in
       ["04","05","06","09","11","12","13","30","31","32","34","46","48",
        "65","66","81","82","83","84"]},
    # Pays-de-la-Loire
    **{d: "pays-de-la-loire" for d in ["44","49","53","72","85"]},
}

_ALF_BASE = "https://association-des-lactariums-de-france.fr"


def _get(url: str) -> BeautifulSoup | None:
    """Requete HTTP avec retry."""
    for attempt in range(3):
        try:
            time.sleep(REQUEST_DELAY if attempt == 0 else 3)
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            print(f"[LAC] Tentative {attempt+1}/3 {url}: {e}")
    return None


def _region_url(dept_code: str, region_nom: str) -> str | None:
    """Retourne l'URL de la page region ALF pour un departement."""
    # 1. Correspondance directe via la table
    slug = _DEPT_TO_REGION.get(str(dept_code).zfill(2))
    if slug:
        return f"{_ALF_BASE}/region/{slug}/"

    # 2. Fallback : chercher dans la page principale par mots-cles de la region
    soup = _get(LACTARIUMS_URL)
    if not soup:
        return None
    region_kw = re.sub(r"[^a-z]", "", region_nom.lower())
    for a in soup.find_all("a", href=re.compile(r"/region/")):
        href_slug = a["href"].rstrip("/").split("/")[-1]
        if region_kw[:5] in href_slug.replace("-", ""):
            return a["href"] if a["href"].startswith("http") else _ALF_BASE + a["href"]
    return None


def _parse_detail_page(url: str) -> dict | None:
    """Scrappe la page detail d'un lactarium et retourne un dict complet."""
    soup = _get(url)
    if not soup:
        return None

    main = soup.find("main") or soup.find("article") or soup

    # Nom : h1
    h1 = main.find("h1")
    nom = h1.get_text(strip=True) if h1 else url.rstrip("/").split("/")[-1]

    # Chercher les sections h4
    def _section_text(heading: str) -> str:
        tag = main.find(lambda t: t.name in ("h4", "h3", "h2")
                        and heading.lower() in t.get_text(strip=True).lower())
        if not tag:
            return ""
        texts = []
        for sib in tag.find_next_siblings():
            if sib.name in ("h4", "h3", "h2"):
                break
            texts.append(sib.get_text(" ", strip=True))
        return " | ".join(filter(None, texts))

    coordonnees = _section_text("Coordonnées")
    infos       = _section_text("Informations")

    # CP + ville depuis coordonnees ou page entiere
    cp    = ""
    ville = ""
    rue   = ""
    m_cp  = re.search(r'\b(\d{5})\b', coordonnees or main.get_text())
    if m_cp:
        cp = m_cp.group(1)
        after = (coordonnees or "")[m_cp.end():].strip().split("|")[0].strip()
        ville = after[:50] if after else ""

    # Rue : ligne avant le CP
    if coordonnees:
        lines = [l.strip() for l in coordonnees.split("|") if l.strip()]
        for i, line in enumerate(lines):
            if re.search(r'\d{5}', line):
                if i > 0:
                    rue = lines[i - 1]
                break

    adresse = ", ".join(filter(None, [rue, f"{cp} {ville}".strip()]))

    # Tel / fax / email depuis liens
    tel   = ""
    fax   = ""
    email = ""
    for a in main.find_all("a"):
        href = a.get("href", "")
        val  = a.get_text(strip=True)
        if href.startswith("tel:"):
            tel = val
        elif href.startswith("fax:"):
            fax = val
        elif href.startswith("mailto:"):
            email = href.replace("mailto:", "").strip()

    # Tel depuis texte si pas trouve via lien
    if not tel:
        m_tel = re.search(r'0[1-9](?:[\s.]?\d{2}){4}', coordonnees or "")
        if m_tel:
            tel = m_tel.group(0)

    # Equipe : tableau h4 "Equipe"
    equipe = []
    equipe_h = main.find(lambda t: t.name in ("h4","h3","h2")
                         and "quipe" in t.get_text())
    if equipe_h:
        table = equipe_h.find_next("table")
        if table:
            for row in table.find_all("tr")[1:]:  # sauter l'en-tete
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if cells:
                    equipe.append(" — ".join(filter(None, cells)))

    # Type (interne/externe)
    type_lac = ""
    if "interne et externe" in infos.lower():
        type_lac = "Usage interne et externe"
    elif "interne" in infos.lower():
        type_lac = "Usage interne"
    elif "externe" in infos.lower():
        type_lac = "Usage externe"

    don_anonyme = "Oui" if "don de lait anonyme" in infos.lower() else ""

    return {
        "nom":         nom,
        "adresse":     adresse,
        "rue":         rue,
        "cp":          cp,
        "ville":       ville,
        "telephone":   tel,
        "fax":         fax,
        "email":       email,
        "type":        type_lac,
        "don_anonyme": don_anonyme,
        "equipe":      equipe,
        "lien":        url,
    }


def get_lactariums(departement_code: str, region_nom: str) -> list[dict]:
    """
    Retourne les lactariums de la zone (dept en priorite, sinon region entiere).
    """
    source_date = time.strftime("%d/%m/%Y")
    source      = f"Association des Lactariums de France — consulte le {source_date}"
    dept_code   = str(departement_code).lstrip("0") or departement_code

    # 1. Trouver la page region
    region_url = _region_url(departement_code, region_nom)
    if not region_url:
        print(f"[LAC] Region introuvable pour dept {departement_code}")
        return [{"nom": "Region ALF introuvable", "cp": "", "adresse": "",
                 "telephone": "", "email": "", "lien": LACTARIUMS_URL,
                 "_source": source}]

    # 2. Recuperer les liens de detail depuis la page region
    soup_region = _get(region_url)
    if not soup_region:
        return []

    detail_links = []
    for a in soup_region.find_all("a", href=re.compile(r"/lactarium/")):
        href = a["href"] if a["href"].startswith("http") else _ALF_BASE + a["href"]
        if href not in detail_links:
            detail_links.append(href)

    print(f"[LAC] {len(detail_links)} lactarium(s) trouves en region ({region_url})")

    # 3. Scrapper chaque page detail
    all_lacs = []
    for link in detail_links:
        lac = _parse_detail_page(link)
        if lac:
            lac["_source"] = source
            all_lacs.append(lac)

    # 4. Filtrer par departement (CP commence par le code dept)
    dept_lacs = [l for l in all_lacs if l.get("cp", "").startswith(departement_code)]

    if dept_lacs:
        print(f"[LAC] {len(dept_lacs)} lactarium(s) dans le dept {departement_code}")
        return dept_lacs

    # 5. Aucun dans le dept -> liste vide
    print(f"[LAC] Aucun lactarium dans le dept {departement_code}")
    return []

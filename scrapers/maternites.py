"""
Scraper maternités — journaldesfemmes.fr
URL pattern : https://www.journaldesfemmes.fr/maman/maternite/{commune_slug}/ville-{code_insee}
Ex : https://www.journaldesfemmes.fr/maman/maternite/annonay/ville-07010
"""

import re
import time
import unicodedata
import requests
from bs4 import BeautifulSoup
from config import HEADERS, REQUEST_DELAY


_BASE = "https://www.journaldesfemmes.fr/maman/maternite/{slug}/ville-{cog}"


def _slugify(nom: str) -> str:
    """'Saint-Étienne' → 'saint-etienne'"""
    nom = nom.lower().strip()
    # Supprimer les accents
    nom = "".join(
        c for c in unicodedata.normalize("NFD", nom)
        if unicodedata.category(c) != "Mn"
    )
    # Remplacer espaces et tirets multiples
    nom = re.sub(r"[\s_]+", "-", nom)
    nom = re.sub(r"[^a-z0-9\-]", "", nom)
    nom = re.sub(r"-+", "-", nom).strip("-")
    return nom


def _scrape_maternite_page(url: str) -> list[dict]:
    """
    Scrape une page maternité journaldesfemmes.fr.
    Retourne la liste des maternités trouvées (souvent 1 ou quelques-unes).
    """
    results = []
    try:
        time.sleep(REQUEST_DELAY)
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # ── Tenter les différentes structures de page ──────────────────────────

        # Structure 1 : cartes maternités avec classe spécifique
        cards = soup.select(
            "[class*='maternite'], [class*='maternity'], "
            "[class*='hospital'], article, .card, .result-item"
        )

        # Si pas de cartes distinctes, chercher le bloc principal
        if not cards:
            main = soup.select_one("main, #main, .main-content, [role='main']")
            if main:
                cards = [main]

        for card in cards:
            text = card.get_text(" ", strip=True)
            if len(text) < 10:
                continue

            # Nom de la maternité
            nom_el = card.select_one("h1, h2, h3, h4, strong, .title, [class*='name']")
            nom = nom_el.get_text(strip=True) if nom_el else ""

            # Filtrer les faux positifs (menus, footers…)
            if not nom or len(nom) < 5:
                continue
            if any(kw in nom.lower() for kw in ["accueil", "menu", "recherche", "connexion"]):
                continue

            # Statut public / privé
            statut = ""
            text_lower = text.lower()
            if "publique" in text_lower or "public" in text_lower:
                statut = "Public"
            elif "privé" in text_lower or "clinique" in text_lower or "prive" in text_lower:
                statut = "Privé"

            # Niveau (1, 2 ou 3)
            niveau = ""
            m = re.search(r"niveau\s+([1-3IVX]+)", text, re.IGNORECASE)
            if m:
                niveau = f"Niveau {m.group(1)}"

            # Ville + CP dans le texte
            ville = ""
            cp_trouve = ""
            cp_match = re.search(r"\b(\d{5})\b", text)
            if cp_match:
                cp_trouve = cp_match.group(1)
            ville_match = re.search(r"([A-ZÀ-Ÿa-zà-ÿ\s\-]+)\s*\(\d{5}\)", text)
            if ville_match:
                ville = ville_match.group(1).strip()

            # Nombre d'accouchements
            nb_acc = ""
            acc_match = re.search(r"(\d[\d\s]*)\s*accouchements?", text, re.IGNORECASE)
            if acc_match:
                nb_acc = acc_match.group(1).strip()

            results.append({
                "nom":                 nom,
                "statut":              statut,
                "type_niveau":         niveau,
                "nb_accouchements_an": nb_acc,
                "ville":               ville,
                "cp":                  cp_trouve,
                "url_source":          url,
            })

        # Dédoublonner par nom
        seen = set()
        unique = []
        for r_ in results:
            key = r_["nom"].strip().upper()
            if key not in seen:
                seen.add(key)
                unique.append(r_)

        return unique

    except Exception:
        return []


def get_maternites_par_cp(cp_communes: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """
    Pour chaque CP, scrape les maternités de chaque commune associée.

    Args:
        cp_communes: dict { "07100": [{"nom": "Annonay", "code_insee": "07010", ...}], ... }

    Returns:
        dict { "07100": [{"nom": "Maternité d'Ardèche Nord", ...}], "07200": [], ... }
    """
    result: dict[str, list[dict]] = {}
    source_date = time.strftime("%d/%m/%Y")

    for cp, communes in cp_communes.items():
        maternites_cp: list[dict] = []

        for commune in communes:
            cog = commune.get("code_insee") or commune.get("code", "")
            nom_commune = commune.get("nom", "")

            if not cog or not nom_commune:
                continue

            slug = _slugify(nom_commune)
            url  = _BASE.format(slug=slug, cog=cog)

            found = _scrape_maternite_page(url)
            for m in found:
                m["_source"]       = f"Journal des Femmes — consulté le {source_date}"
                m["_cp_recherche"] = cp
                m["_url"]          = url
                maternites_cp.append(m)

        result[cp] = maternites_cp

    return result

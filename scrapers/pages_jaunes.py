"""
Scraper Pages Jaunes — pharmacies et matériel médical
Utilise Playwright car le site est rendu côté client (JavaScript).
"""

import time
import urllib.parse
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from config import REQUEST_DELAY


_BASE = "https://www.pagesjaunes.fr/annuaire/chercherlespros"


def _build_url(quoi: str, commune: str, cp: str) -> str:
    ou = f"{commune} ({cp})"
    params = {
        "quoiqui": quoi,
        "ou": ou,
        "univers": "pagesjaunes",
    }
    return _BASE + "?" + urllib.parse.urlencode(params)


def _scrape_count_and_names(page, url: str) -> tuple[int, list[str]]:
    """
    Ouvre l'URL et extrait le nombre de résultats + les noms des 10 premiers.
    Retourne (count, [noms]).
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        # Nombre de résultats : balise contenant "résultats" ou le compteur
        count = 0
        names: list[str] = []

        # Sélecteur du compteur global
        for sel in [
            "[class*='totalResults']",
            "[class*='total-results']",
            ".nb-results",
            "#nb-results",
            "[data-nb-results]",
        ]:
            try:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    digits = "".join(c for c in text if c.isdigit())
                    if digits:
                        count = int(digits)
                        break
            except Exception:
                continue

        # Noms des établissements
        for sel in [
            "[class*='denomination-links'] a",
            ".bi-denomination a",
            "a.bi-denomination",
            "[class*='company-name']",
        ]:
            try:
                els = page.query_selector_all(sel)
                if els:
                    names = [e.inner_text().strip() for e in els[:10]]
                    break
            except Exception:
                continue

        return count, names

    except PWTimeout:
        return 0, []
    except Exception:
        return 0, []


def get_pharmacies_and_medical(communes: list[dict]) -> list[dict]:
    """
    Pour chaque commune, compte le nombre de pharmacies et de magasins de
    matériel médical.

    Args:
        communes: liste de dicts {"nom": str, "cp": str}

    Returns:
        liste de dicts avec clés : commune, cp, nb_pharmacies, noms_pharmacies,
        nb_materiel_medical, noms_materiel_medical, _source
    """
    results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        for c in communes:
            nom = c["nom"]
            cp = c["cp"]
            entry: dict = {
                "commune": nom,
                "cp": cp,
                "nb_pharmacies": 0,
                "noms_pharmacies": [],
                "nb_materiel_medical": 0,
                "noms_materiel_medical": [],
                "_source": f"Pages Jaunes — consulté le {time.strftime('%d/%m/%Y')}",
            }

            # Pharmacies
            url_ph = _build_url("pharmacie", nom, cp)
            count_ph, names_ph = _scrape_count_and_names(page, url_ph)
            entry["nb_pharmacies"] = count_ph
            entry["noms_pharmacies"] = names_ph
            time.sleep(REQUEST_DELAY)

            # Matériel médical
            url_mm = _build_url("magasin materiel medical", nom, cp)
            count_mm, names_mm = _scrape_count_and_names(page, url_mm)
            entry["nb_materiel_medical"] = count_mm
            entry["noms_materiel_medical"] = names_mm
            time.sleep(REQUEST_DELAY)

            results.append(entry)

        context.close()
        browser.close()

    return results

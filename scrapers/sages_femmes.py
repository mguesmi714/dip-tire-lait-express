"""
Scraper sages-femmes — Ordre national des sages-femmes
https://www.ordre-sages-femmes.fr/patient-e-s/trouver-une-sage-femme/
Utilise Playwright (formulaire de recherche JavaScript).
"""

import time
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from config import REQUEST_DELAY, SAGES_FEMMES_URL


def get_sages_femmes(communes: list[dict]) -> list[dict]:
    """
    Recherche les sages-femmes libérales pour chaque commune.
    Dédoublonne par Nom + Prénom (une SF à plusieurs adresses = 1 entrée).

    Args:
        communes: liste de dicts {"nom": str, "cp": str}

    Returns:
        liste de dicts dédoublonnés : nom, prenom, adresse, telephone, email, commune, _source
    """
    seen: dict[str, dict] = {}  # clé : "NOM PRENOM"
    results: list[dict] = []

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

        for commune in communes:
            nom_c = commune["nom"]
            cp = commune["cp"]

            try:
                page.goto(SAGES_FEMMES_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)

                # Remplir le champ de recherche par ville/CP
                search_selectors = [
                    "input[name='search_localisation']",
                    "input[placeholder*='ville']",
                    "input[placeholder*='commune']",
                    "input[placeholder*='code postal']",
                    "#search_localisation",
                    "input[type='text']",
                ]
                for sel in search_selectors:
                    try:
                        el = page.query_selector(sel)
                        if el:
                            el.fill(f"{nom_c} {cp}")
                            page.keyboard.press("Enter")
                            break
                    except Exception:
                        continue

                page.wait_for_timeout(3000)

                # Extraire les fiches
                card_selectors = [
                    ".fiche-praticien",
                    ".result-praticien",
                    "[class*='praticien']",
                    "[class*='sage-femme']",
                    "article",
                    ".entry",
                ]
                cards = []
                for sel in card_selectors:
                    cards = page.query_selector_all(sel)
                    if cards:
                        break

                for card in cards:
                    try:
                        card_text = card.inner_text()

                        nom_el = card.query_selector("h2, h3, .nom, strong")
                        nom_complet = nom_el.inner_text().strip() if nom_el else ""

                        # Extraire NOM et PRÉNOM (généralement MAJUSCULE Prénom)
                        parts = nom_complet.split()
                        nom_sf = " ".join(p for p in parts if p.isupper()) or nom_complet
                        prenom_sf = " ".join(p for p in parts if not p.isupper())

                        key = f"{nom_sf} {prenom_sf}".strip().upper()
                        if key in seen:
                            continue  # dédoublonnage

                        adresse_el = card.query_selector("[class*='adresse'], address, .adresse")
                        adresse = adresse_el.inner_text().strip() if adresse_el else ""

                        tel_el = card.query_selector("[class*='tel'], [class*='phone'], a[href^='tel']")
                        tel = ""
                        if tel_el:
                            href = tel_el.get_attribute("href") or ""
                            tel = href.replace("tel:", "") or tel_el.inner_text().strip()

                        mail_el = card.query_selector("a[href^='mailto']")
                        mail = ""
                        if mail_el:
                            mail = (mail_el.get_attribute("href") or "").replace("mailto:", "")

                        entry = {
                            "nom": nom_sf,
                            "prenom": prenom_sf,
                            "adresse": adresse,
                            "telephone": tel,
                            "email": mail,
                            "commune": nom_c,
                            "_source": f"Ordre national des sages-femmes — consulté le {time.strftime('%d/%m/%Y')}",
                        }
                        seen[key] = entry
                        results.append(entry)

                    except Exception:
                        continue

            except PWTimeout:
                results.append({
                    "nom": f"Timeout sur {nom_c}",
                    "prenom": "", "adresse": "", "telephone": "", "email": "",
                    "commune": nom_c,
                    "_source": "Ordre SF — timeout",
                })
            except Exception as e:
                results.append({
                    "nom": f"Erreur : {e}",
                    "prenom": "", "adresse": "", "telephone": "", "email": "",
                    "commune": nom_c,
                    "_source": "Ordre SF — erreur",
                })

            time.sleep(REQUEST_DELAY)

        context.close()
        browser.close()

    return results

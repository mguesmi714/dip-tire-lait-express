"""
Scraper maternités — journaldesfemmes.fr

Stratégie :
1. Page département  → liste des URLs de fiches maternité
2. Chaque fiche      → nom exact (h1 avant " à "), CP exact (1er CP du texte),
                       niveau, statut public/privé
3. Filtre strict     → ne garder que les maternités dont le CP correspond
                       exactement au code postal recherché
"""

import re
import time
import unicodedata
import requests
from bs4 import BeautifulSoup
from config import HEADERS, REQUEST_DELAY


_DEPT_PAGE  = "https://www.journaldesfemmes.fr/maman/maternite/{dept_slug}/departement-{dept_code}"
_GEO_DEPT   = "https://geo.api.gouv.fr/departements/{code}?fields=nom"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    text = re.sub(r"[\s_']+", "-", text)
    text = re.sub(r"[^a-z0-9\-]", "", text)
    return re.sub(r"-+", "-", text).strip("-")


def _dept_nom(dept_code: str) -> str:
    """Récupère le nom officiel du département via l'API geo.gouv.fr."""
    try:
        r = requests.get(_GEO_DEPT.format(code=dept_code), timeout=8)
        return r.json().get("nom", "")
    except Exception:
        return ""


def _get_maternite_links(dept_code: str) -> list[str]:
    """
    Retourne toutes les URLs de fiches maternité du département.
    Format URL : /maman/maternite/maternite-xxx/maternite-YYYYYYY
    """
    dept_nom = _dept_nom(dept_code)
    if not dept_nom:
        return []

    dept_slug = _slugify(dept_nom)
    url = _DEPT_PAGE.format(dept_slug=dept_slug, dept_code=dept_code)

    try:
        time.sleep(REQUEST_DELAY)
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        base = "https://www.journaldesfemmes.fr"
        links = []
        for a in soup.select('a[href*="/maternite-"]'):
            href = a.get("href", "")
            # Garder uniquement les fiches individuelles (pas les pages ville)
            if re.search(r"/maternite-\d+$", href):
                full = href if href.startswith("http") else base + href
                if full not in links:
                    links.append(full)
        return links
    except Exception:
        return []


def _scrape_fiche(url: str, source_date: str) -> dict | None:
    """
    Scrape une fiche maternité individuelle.
    Retourne un dict ou None si la page est inaccessible.
    """
    try:
        time.sleep(REQUEST_DELAY)
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" ", strip=True)

        # ── Nom exact : h1 avant " à " ─────────────────────────────────────
        h1_el = soup.select_one("h1")
        if not h1_el:
            return None
        h1_text = h1_el.get_text(strip=True)
        nom = re.split(r"\s+[àa]\s+", h1_text)[0].strip()

        if not nom or len(nom) < 5:
            return None

        # ── CP exact : premier code postal (5 chiffres) dans la page ───────
        # Le premier trouvé est celui de l'adresse de la maternité
        cp_all = re.findall(r"\b(\d{5})\b", text)
        cp = cp_all[0] if cp_all else ""

        # ── Ville : mot(s) après le CP dans la page ─────────────────────────
        ville = ""
        ville_match = re.search(r"\b" + re.escape(cp) + r"\s+([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ\s\-]*)", text)
        if ville_match:
            ville = ville_match.group(1).split()[0]  # juste le premier mot

        # ── Statut public / privé ───────────────────────────────────────────
        statut = ""
        if re.search(r"maternit[ée]\s+publique", text, re.IGNORECASE):
            statut = "Public"
        elif re.search(r"maternit[ée]\s+priv[ée]|clinique priv[ée]", text, re.IGNORECASE):
            statut = "Privé"

        # ── Niveau ──────────────────────────────────────────────────────────
        niveau = ""
        m = re.search(r"niveau\s+([1-3])", text, re.IGNORECASE)
        if m:
            niveau = f"Niveau {m.group(1)}"

        # ── Nb accouchements ────────────────────────────────────────────────
        nb_acc = ""
        acc_match = re.search(r"([\d\s]+)\s*accouchements?", text, re.IGNORECASE)
        if acc_match:
            nb_acc = acc_match.group(1).strip().replace(" ", " ")

        return {
            "nom":                 nom,
            "cp":                  cp,
            "ville":               ville,
            "statut":              statut,
            "type_niveau":         niveau,
            "nb_accouchements_an": nb_acc,
            "url_source":          url,
            "_source":             f"Journal des Femmes — consulté le {source_date}",
        }

    except Exception:
        return None


# ── Point d'entrée ────────────────────────────────────────────────────────────

def get_maternites_par_cp(cp_communes: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """
    Pour chaque CP, retourne les maternités dont le CP exact correspond.

    Args:
        cp_communes: dict { "92700": [...communes...], "07110": [...], ... }

    Returns:
        dict { "92700": [{"nom": "Maternité Louis Mourier", ...}], "07110": [], ... }
        Liste vide si aucune maternité exactement dans ce CP.
    """
    source_date = time.strftime("%d/%m/%Y")
    result: dict[str, list[dict]] = {cp: [] for cp in cp_communes}

    # Grouper les CP par département pour ne charger la page dept qu'une seule fois
    dept_to_cps: dict[str, list[str]] = {}
    for cp in cp_communes:
        dept = cp[:3] if cp.startswith("97") else cp[:2]
        dept_to_cps.setdefault(dept, []).append(cp)

    for dept_code, cps in dept_to_cps.items():
        # 1. Récupérer toutes les fiches du département
        fiches_urls = _get_maternite_links(dept_code)

        if not fiches_urls:
            continue

        # 2. Scraper chaque fiche
        for fiche_url in fiches_urls:
            fiche = _scrape_fiche(fiche_url, source_date)
            if not fiche:
                continue

            cp_fiche = fiche.get("cp", "")

            # 3. Filtre strict : affecter au CP qui correspond exactement
            if cp_fiche in result:
                fiche["_cp_recherche"] = cp_fiche
                result[cp_fiche].append(fiche)

    return result


# ── Scraper Pages Jaunes ────────────────────────────────────────────────────

def verifier_pages_jaunes(url: str) -> dict:
    """
    Vérifie les résultats d'une recherche sur Pages Jaunes.
    Args:
        url: URL de la recherche Pages Jaunes.

    Returns:
        dict: Contient le nombre de résultats et si le mot "couvre" est présent.
    """
    try:
        time.sleep(REQUEST_DELAY)
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return {
                "erreur": "Statut HTTP non valide",
                "code_statut": r.status_code,
                "contenu": r.text[:500]  # Inclure un extrait du contenu pour diagnostic
            }

        soup = BeautifulSoup(r.text, "html.parser")

        # Extraire le nombre de résultats
        resultats_text = soup.select_one(".result-summary-container")
        if not resultats_text:
            return {
                "erreur": "Impossible de trouver le conteneur des résultats",
                "contenu": r.text[:500]
            }

        resultats_text = resultats_text.get_text(strip=True)
        nombre_resultats = int(re.search(r"\d+", resultats_text).group())

        # Vérifier la présence du mot "couvre"
        contient_couvre = "couvre" in soup.get_text()

        return {
            "nombre_resultats": nombre_resultats,
            "contient_couvre": contient_couvre
        }

    except Exception as e:
        return {
            "erreur": "Exception levée",
            "details": str(e)
        }


def verifier_pages_jaunes_commune(url: str, commune: str) -> dict:
    """
    Vérifie les résultats d'une recherche sur Pages Jaunes pour une commune spécifique.
    Args:
        url: URL de la recherche Pages Jaunes.
        commune: Nom exact de la commune à vérifier.

    Returns:
        dict: Contient les résultats filtrés pour la commune.
    """
    try:
        time.sleep(REQUEST_DELAY)
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return {
                "erreur": "Statut HTTP non valide",
                "code_statut": r.status_code,
                "contenu": r.text[:500]  # Inclure un extrait du contenu pour diagnostic
            }

        soup = BeautifulSoup(r.text, "html.parser")

        # Extraire les résultats
        resultats = []
        exclusions = []  # Pour loguer les résultats exclus
        for item in soup.select(".bi-bloc"):
            nom = item.select_one(".denomination-links").get_text(strip=True) if item.select_one(".denomination-links") else ""
            adresse = item.select_one(".adresse-container").get_text(strip=True) if item.select_one(".adresse-container") else ""

            # Vérification stricte ligne par ligne
            lignes_adresse = adresse.split(",")  # Découper l'adresse en lignes
            adresse_valide = False
            for ligne in lignes_adresse:
                ligne = ligne.strip().lower()
                if commune.lower() in ligne and "couvre" not in ligne:
                    # Correspondance stricte : vérifier que la commune est isolée
                    if re.fullmatch(rf".*\b{re.escape(commune.lower())}\b.*", ligne):
                        adresse_valide = True
                        break

            if adresse_valide:
                resultats.append({
                    "nom": nom,
                    "adresse": adresse
                })
            else:
                exclusions.append({"nom": nom, "adresse": adresse, "raison": "Contient 'couvre' ou ne correspond pas exactement à la commune"})

        return {
            "commune": commune,
            "resultats": resultats,
            "nombre_resultats": len(resultats),
            "exclusions": exclusions  # Inclure les exclusions pour diagnostic
        }

    except Exception as e:
        return {
            "erreur": "Exception levée",
            "details": str(e)
        }

# Exemple d'utilisation
if __name__ == "__main__":
    url_test = "https://www.pagesjaunes.fr/annuaire/chercherlespros?quoiqui=magasin+materiel+medical&ou=Vannes+%2856000%29&univers=pagesjaunes&idOu="
    commune_test = "Ambon"
    resultat = verifier_pages_jaunes_commune(url_test, commune_test)
    print(resultat)

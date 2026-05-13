"""
Scraper INSEE — comparateur de territoires
https://www.insee.fr/fr/statistiques/zones/1405599

Pour chaque commune (code COG), récupère les indicateurs socio-démographiques
via l'API du comparateur et les endpoints JSON de statistiques locales.
"""

import time
import requests
from config import HEADERS, REQUEST_DELAY, GEO_API_URL


# ── API geo.api.gouv.fr ────────────────────────────────────────────────────────

def resolve_communes(cp: str) -> list[dict]:
    """Résout un code postal en liste de communes via l'API geo.gouv.fr."""
    url = GEO_API_URL.format(cp=cp)
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


# ── API INSEE statistiques locales ────────────────────────────────────────────

INDICATEURS_MILLESIMES = {
    # (dataset, variable, millesime_label)
    "pop_2022":            ("RP", "POP", "2022"),
    "superficie_km2":      ("RP", "SUPERF", "2022"),
    "densite_2022":        ("RP", "DENSPOP", "2022"),
    "var_pop_2016_2022":   ("RP", "TXVAR_POP", "2022"),
    "solde_naturel":       ("RP", "TXVAR_NATUR", "2022"),
    "solde_migratoire":    ("RP", "TXVAR_MIGR", "2022"),
    "nb_menages_2022":     ("RP", "MENPRINC", "2022"),
    "naissances_2022":     ("ETATCIVIL", "NAISD", "2022"),
    "deces_2022":          ("ETATCIVIL", "DECESD", "2022"),
    # Logement
    "nb_logements_2022":          ("RP", "LOGEMENT", "2022"),
    "part_res_principales_2022":  ("RP", "P_RP", "2022"),
    "part_res_secondaires_2022":  ("RP", "P_RSECOCC", "2022"),
    "part_logements_vacants_2022":("RP", "P_LVAC", "2022"),
    "part_proprietaires_2022":    ("RP", "P_PROPRIO", "2022"),
    # Revenus
    "nb_menages_fiscaux_2021":    ("DGI", "NBMENFISC", "2021"),
    "part_menages_imposes_2021":  ("DGI", "PIMP", "2021"),
    "mediane_revenu_2021":        ("DGI", "MED", "2021"),
    "taux_pauvrete_2021":         ("FILOSOFI", "TP60", "2021"),
    # Emploi
    "emploi_total_2022":          ("RP", "EMPLT", "2022"),
    "part_emploi_salarie_2022":   ("RP", "P_SAL", "2022"),
    "var_emploi_2016_2022":       ("RP", "TXVAR_EMPLT", "2022"),
    "taux_activite_15_64_2022":   ("RP", "TXACT1564", "2022"),
    "taux_chomage_15_64_2022":    ("RP", "TXCHO1564", "2022"),
    # Etablissements (Flores / REE)
    "nb_etab_actifs_2023":        ("REE", "NBETAB", "2023"),
    "part_agriculture_2023":      ("REE", "P_AGR", "2023"),
    "part_industrie_2023":        ("REE", "P_IND", "2023"),
    "part_construction_2023":     ("REE", "P_CONST", "2023"),
    "part_commerce_transp_2023":  ("REE", "P_COM", "2023"),
    "part_admin_sante_2023":      ("REE", "P_ADM", "2023"),
    "part_etab_1_9_sal_2023":     ("REE", "P_E1T9", "2023"),
    "part_etab_10_sal_plus_2023": ("REE", "P_E10P", "2023"),
}

# Endpoint REST INSEE statistiques locales
_INSEE_STATS_BASE = "https://api.insee.fr/metadonnees/geo/commune/{cog}/donnees"
_INSEE_OPEN_BASE  = "https://statistiques-locales.insee.fr/COMBINAISON/STAT-DEF/COM/{cog}/SER/{var}/GEO/{cog}/MILL/{mill}/NOM/FRANCE"


def _fetch_open_stat(cog: str, var: str, mill: str) -> str | None:
    """
    Tente de récupérer un indicateur via l'interface ouverte INSEE.
    Retourne la valeur en chaîne ou None si non disponible.
    """
    url = _INSEE_OPEN_BASE.format(cog=cog, var=var, mill=mill)
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            data = r.json()
            # La valeur est souvent dans data["value"] ou data["observations"][0]["value"]
            if "value" in data:
                return str(data["value"])
            obs = data.get("observations", [])
            if obs:
                return str(obs[0].get("value", ""))
    except Exception:
        pass
    return None


# ── Fallback : scraping HTML du comparateur ───────────────────────────────────

def _scrape_comparateur_html(cog: str) -> dict:
    """
    Scrape la page HTML du comparateur INSEE pour un code commune COG.
    URL pattern : https://www.insee.fr/fr/statistiques/2011101?geo=COM-{cog}
    """
    from bs4 import BeautifulSoup
    url = f"https://www.insee.fr/fr/statistiques/2011101?geo=COM-{cog}"
    result: dict = {}
    try:
        time.sleep(REQUEST_DELAY)
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Les tableaux de statistiques ont des balises <td> avec data-value
        for td in soup.select("td[data-value]"):
            label_el = td.find_previous("th")
            if label_el:
                label = label_el.get_text(strip=True)
                value = td.get("data-value", "").strip()
                result[label] = value
    except Exception:
        pass
    return result


# ── Fallback Google ───────────────────────────────────────────────────────────

def _fallback_google(commune_nom: str, departement: str) -> dict:
    """
    Si une commune est introuvable sur INSEE, retourne un dict vide
    avec un marqueur pour indiquer à l'opérateur de chercher manuellement.
    (Les recherches Google automatisées sont bloquées sans clé API.)
    """
    return {"_statut": f"INTROUVABLE INSEE — vérification manuelle requise pour {commune_nom} ({departement})"}


# ── Fonction principale ────────────────────────────────────────────────────────

def get_commune_data(commune: dict) -> dict:
    """
    Collecte tous les indicateurs pour une commune.

    Args:
        commune: dict avec au minimum {"code": "42186", "nom": "Roanne",
                  "departement": {"code": "42", "nom": "Loire"},
                  "region": {"code": "84", "nom": "Auvergne-Rhône-Alpes"}}

    Returns:
        dict plat avec tous les indicateurs + métadonnées de source.
    """
    cog = commune["code"]
    nom = commune.get("nom", cog)
    dept = commune.get("departement", {}).get("nom", "")

    result: dict = {
        "commune": nom,
        "code_insee": cog,
        "departement": dept,
        "region": commune.get("region", {}).get("nom", ""),
        "_statut": "OK",
    }

    # Tentative via scraping HTML du comparateur (le plus fiable)
    time.sleep(REQUEST_DELAY)
    html_data = _scrape_comparateur_html(cog)

    # Correspondance labels HTML → clés normalisées
    LABEL_MAP = {
        "Population en 2022": "pop_2022",
        "Superficie (en km²)": "superficie_km2",
        "Densité de la population (nombre d'habitants au km²) en 2022": "densite_2022",
        "Variation de la population : taux annuel moyen entre 2016 et 2022 (en %)": "var_pop_2016_2022",
        "dont variation due au solde naturel (en %)": "solde_naturel",
        "dont variation due au solde apparent des entrées sorties (en %)": "solde_migratoire",
        "Nombre de ménages en 2022": "nb_menages_2022",
        "Naissances domiciliées en 2022": "naissances_2022",
        "Décès domiciliés en 2022": "deces_2022",
        "Nombre total de logements en 2022": "nb_logements_2022",
        "Part des résidences principales en 2022 (en %)": "part_res_principales_2022",
        "Part des résidences secondaires (y compris les logements occasionnels) en 2022 (en %)": "part_res_secondaires_2022",
        "Part des logements vacants en 2022 (en %)": "part_logements_vacants_2022",
        "Part des ménages propriétaires de leur résidence principale en 2022 (en %)": "part_proprietaires_2022",
        "Nombre de ménages fiscaux en 2021": "nb_menages_fiscaux_2021",
        "Part des ménages fiscaux imposés en 2021 (en %)": "part_menages_imposes_2021",
        "Médiane du revenu disponible par unité de consommation en 2021 (en euros)": "mediane_revenu_2021",
        "Taux de pauvreté en 2021 (en %)": "taux_pauvrete_2021",
        "Emploi total (salarié et non salarié) au lieu de travail en 2022": "emploi_total_2022",
        "dont part de l'emploi salarié au lieu de travail en 2022 (en %)": "part_emploi_salarie_2022",
        "Variation de l'emploi total au lieu de travail : taux annuel moyen entre 2016 et 2022 (en %)": "var_emploi_2016_2022",
        "Taux d'activité des 15 à 64 ans en 2022 (en %)": "taux_activite_15_64_2022",
        "Taux de chômage des 15 à 64 ans en 2022 (en %)": "taux_chomage_15_64_2022",
        "Nombre d'établissements actifs fin 2023": "nb_etab_actifs_2023",
        "Agriculture (en %)": "part_agriculture_2023",
        "Industrie (en %)": "part_industrie_2023",
        "Construction (en %)": "part_construction_2023",
        "Commerce, transports et services divers (en %)": "part_commerce_transp_2023",
        "Administration publique, enseignement, santé et action sociale (en %)": "part_admin_sante_2023",
        "Établissements de 1 à 9 salariés (en %)": "part_etab_1_9_sal_2023",
        "Établissements de 10 salariés ou plus (en %)": "part_etab_10_sal_plus_2023",
    }

    for label, key in LABEL_MAP.items():
        if label in html_data:
            result[key] = html_data[label]
        else:
            result[key] = None

    # Si aucune donnée récupérée, marquer comme introuvable
    non_null = sum(1 for k, v in result.items() if v is not None and not k.startswith("_") and k not in ("commune", "code_insee", "departement", "region"))
    if non_null == 0:
        result.update(_fallback_google(nom, dept))

    result["_source"] = "INSEE comparateur RP 2022 / REE 2023 / DGI 2021"
    return result


def get_all_communes_data(communes: list[dict]) -> list[dict]:
    """Lance la collecte pour une liste de communes."""
    results = []
    for c in communes:
        data = get_commune_data(c)
        results.append(data)
    return results

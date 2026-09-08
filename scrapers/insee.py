"""
Scraper INSEE — Comparateur de territoires
https://www.insee.fr/fr/statistiques/1405599

Stratégie :
1. Construire UNE SEULE URL avec tous les codes INSEE des communes
   ?geo=COM-07029+COM-07058+COM-...
2. Récupérer la page en HTTP simple (requests)
3. Extraire les 5 tableaux de comparaison
4. Mapper chaque colonne à sa commune

Pas de navigateur : la page /fr/statistiques/1405599 (sans le segment /zones/)
est rendue côté serveur et contient déjà les tableaux. C'est l'URL sur laquelle
atterrit le bouton « COMPARER LES TERRITOIRES » du comparateur interactif.
Se passer de Playwright évite de dépendre des libs système de Chromium, absentes
de l'image Streamlit Cloud.
"""

import re
import time

import requests
from bs4 import BeautifulSoup

_BASE_URL = "https://www.insee.fr/fr/statistiques/1405599"
_MAX_COMMUNES_PER_REQUEST = 20   # limite prudente pour l'URL
_TIMEOUT = 30
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}
 
 
# ── Mapping label court → clé normalisée ─────────────────────────────────────
 
#
# NB : les années présentes dans les patterns ne servent que de documentation.
# _match_label() les neutralise (voir _norm_label) car l'INSEE change de millésime
# chaque année : « Population en 2022 » est devenue « Population en 2023 ».
LABEL_MAP = {
    # Population
    "population en 2022":                                               "pop_2022",
    "densité de la population":                                         "densite_2022",
    "superficie en 2022":                                               "superficie_km2",
    "variation de la population : taux annuel":                         "var_pop_2016_2022",
    "dont variation due au solde naturel":                              "solde_naturel",
    "dont variation due au solde apparent":                             "solde_migratoire",
    "nombre de ménages en 2022":                                        "nb_menages_2022",
    "naissances domiciliées":                                           "naissances_2022",
    "décès domiciliés":                                                 "deces_2022",
    # Logement
    "nombre total de logements en 2022":                                "nb_logements_2022",
    "part des résidences principales en 2022":                          "part_res_principales_2022",
    "part des résidences secondaires":                                  "part_res_secondaires_2022",
    "part des logements vacants en 2022":                               "part_logements_vacants_2022",
    "part des ménages propriétaires":                                   "part_proprietaires_2022",
    # Revenus
    "niveau de vie médian en 2023":                                     "mediane_revenu_2021",
    "médiane du revenu disponible":                                     "mediane_revenu_2021",
    "taux de pauvreté en 2023":                                         "taux_pauvrete_2021",
    "taux de pauvreté en 2021":                                         "taux_pauvrete_2021",
    "nombre de ménages fiscaux":                                        "nb_menages_fiscaux_2021",
    "part des ménages fiscaux imposés":                                 "part_menages_imposes_2021",
    # Emploi
    "emploi total (salarié et non salarié)":                            "emploi_total_2022",
    "nombre d'emplois au lieu de travail":                              "emploi_total_2022",
    "dont part de l'emploi salarié":                                    "part_emploi_salarie_2022",
    "variation de l'emploi total au lieu de t":                         "var_emploi_2016_2022",
    "taux d'activité des 15 à 64 ans en 2022":                          "taux_activite_15_64_2022",
    "taux de chômage des 15 à 64 ans en 2022":                          "taux_chomage_15_64_2022",
    # Établissements
    "nombre d'établissements fin 2024":                                 "nb_etab_actifs_2023",
    "nombre d'établissements fin 2023":                                 "nb_etab_actifs_2023",
    "nombre d'établissements actifs fin 2023":                          "nb_etab_actifs_2023",
    "part de l'agriculture":                                            "part_agriculture_2023",
    "part de l'industrie":                                              "part_industrie_2023",
    "part de la construction":                                          "part_construction_2023",
    "part du commerce, transports et services":                         "part_commerce_transp_2023",
    "part de l'administration publique":                                "part_admin_sante_2023",
    "part des établissements de 1 à 9 salarié":                         "part_etab_1_9_sal_2023",
    "part des établissements de 10 salariés o":                         "part_etab_10_sal_plus_2023",
}
 
 
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _norm_label(s: str) -> str:
    """Minuscules + années remplacées par un joker.

    Le comparateur INSEE renomme ses libellés à chaque millésime
    (« Population en 2022 » → « Population en 2023 »). Neutraliser l'année
    évite de devoir repatcher LABEL_MAP tous les ans.

    Les espaces sont aussi normalisés : le HTML de l'INSEE coupe ses libellés
    sur plusieurs lignes avec de l'indentation.
    """
    return _YEAR_RE.sub("@", re.sub(r"\s+", " ", s.lower()).strip())


def _match_label(label: str) -> str | None:
    """Retourne la clé normalisée pour un label INSEE (correspondance partielle)."""
    label_norm = _norm_label(label)
    for pattern, key in LABEL_MAP.items():
        pattern_norm = _norm_label(pattern)
        if label_norm.startswith(pattern_norm) or pattern_norm in label_norm:
            return key
    return None
 
 
def _clean_value(val: str) -> str:
    """Nettoie une valeur INSEE : normalise les espaces, remplace – par -."""
    val = val.replace(" ", " ").replace(" ", " ").replace("–", "-")
    return re.sub(r"\s+", " ", val).strip()
 
 
def _fetch_page(url: str) -> str | None:
    """Recupere la page du comparateur, avec une seconde tentative si besoin."""
    for attempt in (1, 2):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            print(f"[INSEE] Tentative {attempt}/2 echouee : {type(e).__name__}: {e}")
            if attempt == 1:
                time.sleep(3)
    return None


def _extract_tables(soup, results: dict, unmatched: set | None = None) -> None:
    """Extrait les donnees des tableaux comparatifs et remplit results.

    unmatched : set optionnel ou sont collectes les libelles non reconnus, pour
    detecter rapidement un renommage cote INSEE.
    """
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        # Chaque colonne porte « Commune : Nom (12345) » -> on la relie a son COG
        col_to_cog: dict[int, str] = {}
        for col_idx, cell in enumerate(rows[0].find_all(["th", "td"])):
            cell_text = cell.get_text(" ", strip=True)
            m = re.search(r"\((\d{5})\)", cell_text) or re.search(r"\b(\d{5})\b", cell_text)
            if m and m.group(1) in results:
                col_to_cog[col_idx] = m.group(1)
        if not col_to_cog:
            continue

        for row in rows[1:]:
            cells = row.find_all(["th", "td"])
            if not cells:
                continue
            label = _clean_value(cells[0].get_text(" ", strip=True))
            key = _match_label(label)
            if not key:
                # Ligne de donnees non reconnue -> probable renommage INSEE.
                # On ignore les lignes de bas de tableau (sources, champ, notes).
                if (
                    unmatched is not None
                    and label
                    and len(cells) > 1
                    and not label.lower().startswith(("source", "champ", "avertissement", "note"))
                ):
                    unmatched.add(label)
                continue
            for col_idx, cog in col_to_cog.items():
                if col_idx < len(cells):
                    val = _clean_value(cells[col_idx].get_text(" ", strip=True))
                    if val:
                        results[cog][key] = val


def _scrape_batch(communes_batch: list[dict]) -> dict[str, dict]:
    """
    Recupere le comparateur INSEE pour un lot de communes.
    communes_batch : list[dict] avec 'nom' et 'code_insee'.
    Retourne dict { "57022": {indicateurs...}, ... }
    """
    cog_codes = [c["code_insee"] for c in communes_batch if c.get("code_insee")]
    if not cog_codes:
        return {}

    geo_param = "+".join(f"COM-{cog}" for cog in cog_codes)
    url = f"{_BASE_URL}?geo={geo_param}"

    results: dict[str, dict] = {cog: {} for cog in cog_codes}

    try:
        html = _fetch_page(url)
        if html is None:
            print("[INSEE] Page inaccessible — lot ignore")
            return results

        unmatched: set[str] = set()
        _extract_tables(BeautifulSoup(html, "html.parser"), results, unmatched)

        if unmatched:
            print("[INSEE] Libelles non reconnus (LABEL_MAP a mettre a jour ?) :")
            for lbl in sorted(unmatched):
                print(f"        - {lbl}")

    except Exception as e:
        import traceback
        err_msg = f"[INSEE] Erreur scraping : {type(e).__name__}: {e}"
        print(err_msg)
        print(traceback.format_exc())
        try:
            import streamlit as st
            st.error(err_msg)
            with st.expander("Details techniques INSEE"):
                st.code(traceback.format_exc())
        except Exception:
            pass
 
    return results
 
 
def get_all_communes_data(communes: list[dict]) -> list[dict]:
    """
    Collecte les indicateurs INSEE pour toutes les communes.
    Utilise le comparateur multi-territoires en une seule requête par lot.
 
    Args:
        communes: liste de dicts avec au minimum {"nom": str, "code_insee"/"code": str, ...}
 
    Returns:
        liste de dicts plats avec tous les indicateurs, 1 dict par commune.
    """
    source_date = time.strftime("%d/%m/%Y")
 
    # Séparer communes avec code COG valide et celles sans
    commune_by_cog: dict[str, dict] = {}
    communes_sans_cog: list[dict] = []
    for c in communes:
        cog = c.get("code_insee") or c.get("code", "")
        if cog and re.match(r"^\d{5}$", cog):
            commune_by_cog[cog] = c
        else:
            communes_sans_cog.append(c)
 
    # Communes sans code COG → ligne vide directement
    final_sans_cog: list[dict] = []
    all_keys = list(dict.fromkeys(LABEL_MAP.values()))
    for c in communes_sans_cog:
        row: dict = {
            "commune":     c.get("nom", "?"),
            "code_insee":  "",
            "cp":          c.get("cp", ""),
            "departement": c.get("departement", {}).get("nom", "") if isinstance(c.get("departement"), dict) else c.get("departement", ""),
            "region":      c.get("region", {}).get("nom", "") if isinstance(c.get("region"), dict) else c.get("region", ""),
            "_source":     f"INSEE Comparateur de territoires — consulté le {source_date}",
            "_statut":     "Code INSEE manquant",
        }
        for k in all_keys:
            row[k] = None
        final_sans_cog.append(row)
 
    if not commune_by_cog:
        return final_sans_cog
 
    # Scraper par lots de MAX_COMMUNES_PER_REQUEST
    all_scraped: dict[str, dict] = {}
    communes_with_cog = [
        {**c, "code_insee": cog} if not c.get("code_insee") else c
        for cog, c in commune_by_cog.items()
    ]
 
    for i in range(0, len(communes_with_cog), _MAX_COMMUNES_PER_REQUEST):
        batch = communes_with_cog[i: i + _MAX_COMMUNES_PER_REQUEST]
        batch_data = _scrape_batch(batch)
        all_scraped.update(batch_data)
 
    # Construire les dicts de résultats
    final: list[dict] = []
    for cog, commune in commune_by_cog.items():
        scraped = all_scraped.get(cog, {})
 
        row: dict = {
            "commune":    commune.get("nom", cog),
            "code_insee": cog,
            "cp":         commune.get("cp", ""),
            "departement": commune.get("departement", {}).get("nom", ""),
            "region":     commune.get("region", {}).get("nom", ""),
            "_source":    f"INSEE Comparateur de territoires — consulté le {source_date}",
            "_statut":    "OK" if scraped else "Données non trouvées",
        }
 
        # Remplir tous les indicateurs (None si absent)
        all_keys = list(LABEL_MAP.values())
        seen_keys: set[str] = set()
        for k in all_keys:
            if k not in seen_keys:
                seen_keys.add(k)
                row[k] = scraped.get(k)
 
        # Superficie : depuis le millésime RP2023 l'INSEE ne publie plus la ligne
        # « Superficie » dans le comparateur. On la reconstruit à partir de la
        # population et de la densité, toutes deux encore présentes.
        if not row.get("superficie_km2"):
            pop  = _to_float(row.get("pop_2022"))
            dens = _to_float(row.get("densite_2022"))
            if pop is not None and dens is not None and dens > 0:
                row["superficie_km2"] = str(round(pop / dens, 1)).replace(".", ",")

        # Remplacer 's' (secret statistique INSEE) par 'S'
        for k, v in row.items():
            if v == "s":
                row[k] = "S (secret statistique)"
 
        final.append(row)
 
    return final + final_sans_cog
 
 
# ── Indicateurs : type de calcul pour la ligne TOTAL ─────────────────────────
 
# Clés dont le total = SOMME
SUM_KEYS = {
    "pop_2022", "nb_menages_2022", "naissances_2022", "deces_2022",
    "nb_logements_2022", "nb_menages_fiscaux_2021", "emploi_total_2022",
    "nb_etab_actifs_2023", "superficie_km2",
}
 
# Clés dont le total = MOYENNE PONDÉRÉE par pop_2022
WAVG_KEYS = {
    "var_pop_2016_2022", "solde_naturel", "solde_migratoire",
    "part_res_principales_2022", "part_res_secondaires_2022",
    "part_logements_vacants_2022", "part_proprietaires_2022",
    "part_menages_imposes_2021", "mediane_revenu_2021", "taux_pauvrete_2021",
    "part_emploi_salarie_2022", "var_emploi_2016_2022",
    "taux_activite_15_64_2022", "taux_chomage_15_64_2022",
    "part_agriculture_2023", "part_industrie_2023", "part_construction_2023",
    "part_commerce_transp_2023", "part_admin_sante_2023",
    "part_etab_1_9_sal_2023", "part_etab_10_sal_plus_2023",
}
 
# Densité = population totale / superficie totale (pas une moyenne pondérée)
DENSITY_KEY = "densite_2022"
 
 
def _to_float(v) -> float | None:
    """Convertit une valeur INSEE (str ou None) en float."""
    if v is None:
        return None
    s = str(v).replace("\xa0", "").replace(" ", "").replace(",", ".").replace("–", "-").strip()
    if s in ("", "s", "S", "vm"):
        return None
    try:
        return float(s)
    except ValueError:
        return None
 
 
def compute_zone_totals(communes_data: list[dict]) -> dict:
    """
    Calcule les totaux et moyennes pondérées pour la zone entière.
 
    Règles :
    - Nombres absolus (population, logements, emplois…) → SOMME
    - Taux et pourcentages → MOYENNE PONDÉRÉE par population
    - Densité → population totale / superficie totale
    - Secret statistique ('S') → 'S (zone : secret partiel)'
 
    Returns:
        dict avec les mêmes clés que les dicts communes, rempli avec les agrégats.
    """
    if not communes_data:
        return {}
 
    totals: dict = {
        "commune":    "TOTAL ZONE",
        "code_insee": "",
        "cp":         "",
        "departement": communes_data[0].get("departement", ""),
        "region":     communes_data[0].get("region", ""),
        "_source":    communes_data[0].get("_source", ""),
        "_statut":    "Calculé",
    }
 
    # ── Sommes ────────────────────────────────────────────────────────────────
    for key in SUM_KEYS:
        vals = [_to_float(r.get(key)) for r in communes_data]
        nums = [v for v in vals if v is not None]
        if nums:
            total = sum(nums)
            # Conserver l'entier si valeur entière
            totals[key] = str(int(total)) if total == int(total) else str(round(total, 1))
        else:
            # Vérifier s'il y a des secrets statistiques
            raws = [r.get(key) for r in communes_data if r.get(key) is not None]
            totals[key] = "S (zone)" if any("secret" in str(v).lower() for v in raws) else None
 
    # ── Densité : pop totale / superficie totale ──────────────────────────────
    pop_total = _to_float(totals.get("pop_2022"))
    sup_total = _to_float(totals.get("superficie_km2"))
    if pop_total and sup_total and sup_total > 0:
        totals[DENSITY_KEY] = str(round(pop_total / sup_total, 1))
    else:
        totals[DENSITY_KEY] = None
 
    # ── Moyennes pondérées par population ─────────────────────────────────────
    pops = [_to_float(r.get("pop_2022")) for r in communes_data]
 
    for key in WAVG_KEYS:
        vals  = [_to_float(r.get(key))      for r in communes_data]
        secrets = [r.get(key) for r in communes_data if "secret" in str(r.get(key, "")).lower()]
 
        num_pairs = [
            (v, p) for v, p in zip(vals, pops)
            if v is not None and p is not None and p > 0
        ]
 
        if num_pairs:
            total_w = sum(p for _, p in num_pairs)
            total_v = sum(v * p for v, p in num_pairs)
            avg = round(total_v / total_w, 1) if total_w > 0 else None
            totals[key] = str(avg) if avg is not None else None
            if secrets:
                totals[key] = (totals[key] or "") + " (partiel)"
        else:
            totals[key] = "S (zone : secret partiel)" if secrets else None
 
    return totals
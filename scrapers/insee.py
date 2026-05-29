"""
Scraper INSEE — Comparateur de territoires
https://www.insee.fr/fr/statistiques/zones/1405599
 
Stratégie :
1. Construire UNE SEULE URL avec tous les codes INSEE des communes
   ?geo=COM-07029+COM-07058+COM-...
2. Playwright → clic "COMPARER LES TERRITOIRES"
3. Extraire les 5 tableaux de comparaison
4. Mapper chaque colonne à sa commune
"""
 
import re
import time
 
try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_OK = True
except ImportError:
    _PLAYWRIGHT_OK = False
 
_BASE_URL = "https://www.insee.fr/fr/statistiques/zones/1405599"
_MAX_COMMUNES_PER_REQUEST = 20   # limite prudente pour l'URL
 
 
# ── Mapping label court → clé normalisée ─────────────────────────────────────
 
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
 
 
def _match_label(label: str) -> str | None:
    """Retourne la clé normalisée pour un label INSEE (correspondance partielle)."""
    label_lower = label.lower().strip()
    for pattern, key in LABEL_MAP.items():
        if label_lower.startswith(pattern.lower()) or pattern.lower() in label_lower:
            return key
    return None
 
 
def _clean_value(val: str) -> str:
    """Nettoie une valeur INSEE : supprime espaces insécables, remplace – par -."""
    return val.replace("\xa0", " ").replace(" ", " ").replace("–", "-").strip()
 
 
def _search_add_commune(page, nom: str, cog: str) -> bool:
    """Ajoute une commune au comparateur via la barre de recherche (par nom)."""
    for inp_sel in [
        "input[placeholder*='territoire']",
        "input[placeholder*='commune']",
        "input[placeholder*='Territoire']",
        "input[aria-autocomplete='list']",
        ".comparateur input[type='text']",
        "input[type='text']",
    ]:
        try:
            inp = page.locator(inp_sel).first
            if not inp.is_visible(timeout=1500):
                continue
            inp.click()
            inp.fill("")
            inp.type(nom, delay=80)
            time.sleep(1.5)
            for res_sel in [
                "li[class*='suggestion']", "[class*='autocomplete'] li",
                "[role='option']", "ul[class*='dropdown'] li", ".tt-suggestion",
            ]:
                items = page.locator(res_sel).all()
                for item in items:
                    txt = item.inner_text()
                    if "COM" in txt and (cog in txt or nom[:6].lower() in txt.lower()):
                        item.click()
                        time.sleep(0.5)
                        return True
                for item in items:
                    if "COM" in item.inner_text():
                        item.click()
                        time.sleep(0.5)
                        return True
            break
        except Exception:
            pass
    return False
 
 
def _extract_tables(page, results: dict) -> None:
    """Extrait les données des tableaux comparatifs et remplit results."""
    tables = page.query_selector_all("table")
    for table in tables:
        rows = table.query_selector_all("tr")
        if len(rows) < 2:
            continue
        header_cells = rows[0].query_selector_all("th, td")
        col_to_cog: dict[int, str] = {}
        for col_idx, cell in enumerate(header_cells):
            cell_text = cell.inner_text().strip()
            m = re.search(r"\((\d{5})\)", cell_text) or re.search(r"\b(\d{5})\b", cell_text)
            if m and m.group(1) in results:
                col_to_cog[col_idx] = m.group(1)
        for row in rows[1:]:
            cells = row.query_selector_all("th, td")
            if not cells:
                continue
            label = cells[0].inner_text().strip()
            key = _match_label(label)
            if not key:
                continue
            for col_idx, cog in col_to_cog.items():
                if col_idx < len(cells):
                    val = _clean_value(cells[col_idx].inner_text().strip())
                    if val:
                        results[cog][key] = val
 
 
def _scrape_batch(communes_batch: list[dict]) -> dict[str, dict]:
    """
    Scrape le comparateur INSEE pour un lot de communes.
    communes_batch : list[dict] avec 'nom' et 'code_insee'.
    Retourne dict { "57022": {indicateurs...}, ... }
    """
    if not _PLAYWRIGHT_OK:
        print("[INSEE] Playwright non disponible — données INSEE non collectées")
        return {c["code_insee"]: {} for c in communes_batch if c.get("code_insee")}
 
    cog_codes = [c["code_insee"] for c in communes_batch if c.get("code_insee")]
    if not cog_codes:
        return {}
 
    geo_param = "+".join(f"COM-{cog}" for cog in cog_codes)
    url = f"{_BASE_URL}?geo={geo_param}&debut=0"
 
    results: dict[str, dict] = {cog: {} for cog in cog_codes}
    cog_to_nom = {c["code_insee"]: c.get("nom", "") for c in communes_batch if c.get("code_insee")}
 
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                locale="fr-FR",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            )
            page = context.new_page()

            # Charger la page — fallback sur domcontentloaded si networkidle timeout
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
            except Exception:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(5)
                except Exception:
                    pass

            # Attendre et cliquer sur le bouton de comparaison
            try:
                page.wait_for_selector("button:has-text('comparer')", timeout=20000)
            except Exception:
                pass

            clicked = False
            for txt in ["COMPARER LES TERRITOIRES", "Comparer les territoires", "comparer"]:
                btn = page.get_by_text(txt, exact=False)
                if btn.count() > 0:
                    try:
                        btn.first.click()
                        clicked = True
                        break
                    except Exception:
                        pass

            if clicked:
                try:
                    page.wait_for_load_state("networkidle", timeout=40000)
                except Exception:
                    time.sleep(6)
                time.sleep(3)

            # Première extraction via les tableaux déjà présents
            _extract_tables(page, results)

            # Pour les communes sans données → recherche par nom dans le comparateur
            for cog in cog_codes:
                if not results[cog]:
                    nom = cog_to_nom.get(cog, "")
                    if nom:
                        print(f"[INSEE] Recherche par nom : {nom} ({cog})")
                        added = _search_add_commune(page, nom, cog)
                        if added:
                            try:
                                page.wait_for_load_state("networkidle", timeout=15000)
                            except Exception:
                                time.sleep(3)
                            _extract_tables(page, results)

            context.close()
            browser.close()
 
    except Exception as e:
        import traceback
        err_msg = f"[INSEE] Erreur scraping : {type(e).__name__}: {e}"
        print(err_msg)
        print(traceback.format_exc())
        try:
            import streamlit as st
            st.error(err_msg)
            with st.expander("Détails techniques INSEE"):
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
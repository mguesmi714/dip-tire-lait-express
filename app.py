"""
Interface web — Mise à jour DIP Tire-Lait Express
Lancer : streamlit run app.py
"""

import io
import os
import re
import sys
import json
import hashlib
import subprocess
import concurrent.futures
from pathlib import Path
from datetime import date, datetime

import requests
import streamlit as st
import pandas as pd

try:
    import folium
    from streamlit_folium import st_folium
    _FOLIUM_OK = True
except ImportError:
    _FOLIUM_OK = False

try:
    import plotly.express as px
    import plotly.graph_objects as go
    _PLOTLY_OK = True
except ImportError:
    _PLOTLY_OK = False


sys.path.insert(0, str(Path(__file__).parent))

from auth import is_authenticated, get_current_user, logout, show_login_page
from dip_store import save_dip, list_dips, load_dip, load_dip_results


@st.cache_resource
def _install_playwright_chromium():
    """Installe Chromium pour Playwright une seule fois par session cloud."""
    import tempfile
    flag = Path(tempfile.gettempdir()) / ".playwright_installed"
    if not flag.exists():
        try:
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True, capture_output=True,
            )
            flag.write_text("ok")
        except subprocess.CalledProcessError as e:
            st.warning(f"Playwright install : {e.stderr.decode() if e.stderr else e}")
    return True

_install_playwright_chromium()

from scrapers.communes import get_communes_for_all_cp
from scrapers.insee import get_all_communes_data, compute_zone_totals
from scrapers.annuaire_pharmacies import get_pharmacies_and_medical
from scrapers.maternites import get_maternites_par_cp
from scrapers.lactariums import get_lactariums
from scrapers.sages_femmes import get_sages_femmes
from scrapers.pmi import get_pmi
from generator.excel_export import generate_excel
from generator.word_export import generate_word


# ── Config page ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Mise à jour DIP — Tire-Lait Express",
    page_icon="👶",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .stProgress > div > div > div { background-color: #1F4E79 !important; }
    .step-badge {
        display:inline-block; background:#1F4E79; color:white;
        border-radius:50%; width:28px; height:28px; text-align:center;
        line-height:28px; font-weight:bold; margin-right:8px;
    }
    .done-badge {
        display:inline-block; background:#2e7d32; color:white;
        border-radius:50%; width:28px; height:28px; text-align:center;
        line-height:28px; margin-right:8px;
    }
    /* Sidebar à droite */
    section[data-testid="stSidebar"] {
        position: fixed; right: 0; left: auto !important;
        border-left: 1px solid #e0e0e0; border-right: none;
    }
    .block-container { padding-right: 1rem; }
    /* Cartes DIP */
    .dip-card {
        background: #f8faff; border: 1px solid #d0dff5;
        border-radius: 10px; padding: 12px 14px; margin-bottom: 10px;
        cursor: pointer;
    }
    .dip-card:hover { background: #e8f0ff; }
    .dip-zone { font-weight: 700; color: #1F4E79; font-size: 0.95rem; }
    .dip-meta { color: #666; font-size: 0.78rem; margin-top: 2px; }
    .dip-legacy { color: #999; font-size: 0.73rem; }
</style>
""", unsafe_allow_html=True)


# ── Session state init ────────────────────────────────────────────────────────

def _init():
    defaults = {
        "step": 1,
        "collect_step": 1,
        "cp_uniques": [],
        "communes_par_cp": {},
        "dept_code": "",
        "dept_codes": [],
        "dept_nom": "",
        "region": "",
        "results": {},
        "communes_flat": [],
        "xlsx_bytes": None,
        "docx_bytes": None,
        # Navigation
        "page":        "dashboard",  # "dashboard" | "wizard" | "view_dip"
        "view_dip_id": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init()


# ── Garde login ───────────────────────────────────────────────────────────────

if not is_authenticated():
    show_login_page()
    st.stop()


# ── Sidebar (panneau DIPs — affiché à droite via CSS) ─────────────────────────

def _reset_wizard():
    for key in ["step","collect_step","cp_uniques","communes_par_cp",
                "dept_code","dept_codes","dept_nom","region","results",
                "communes_flat","xlsx_bytes","docx_bytes","_carte"]:
        if key in st.session_state:
            del st.session_state[key]
    _init()
    st.session_state.page = "wizard"


def _sidebar():
    with st.sidebar:
        st.markdown(f"**👤 {get_current_user()}**")
        if st.button("🚪 Déconnexion", use_container_width=True):
            logout()
            st.rerun()

        st.divider()

        if st.button("➕ Nouveau DIP", type="primary", use_container_width=True):
            _reset_wizard()
            st.rerun()

        if st.button("🏠 Tableau de bord", use_container_width=True):
            st.session_state.page = "dashboard"
            st.rerun()

        st.divider()
        st.markdown("**📁 DIPs récents**")

        dips = list_dips()
        if not dips:
            st.caption("Aucun DIP enregistré.")
        for dip in dips[:15]:
            date_str = dip.get("created_at", "")[:10]
            zone = dip.get("zone_nom") or dip.get("dept_nom", "?")
            nb   = dip.get("nb_communes", "–")
            is_legacy = dip.get("legacy", False)

            label = f"{'📄' if is_legacy else '📊'} {zone[:22]}\n{date_str}"
            if st.button(label, key=f"dip_{dip['id']}", use_container_width=True):
                if is_legacy:
                    st.session_state.page      = "view_dip"
                    st.session_state.view_dip_id = dip["id"]
                else:
                    st.session_state.page      = "view_dip"
                    st.session_state.view_dip_id = dip["id"]
                st.rerun()

_sidebar()


# ── Cache résultats ───────────────────────────────────────────────────────────

_CACHE_DIR = Path(__file__).parent / "output" / "cache"
_CACHE_TTL_DAYS = 7


def _zone_key(cp_uniques: list[str]) -> str:
    return hashlib.md5("|".join(sorted(cp_uniques)).encode()).hexdigest()[:12]


def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{key}.json"


def _load_cache(cp_uniques: list[str]) -> tuple[dict, str] | tuple[None, None]:
    path = _cache_path(_zone_key(cp_uniques))
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        saved = datetime.fromisoformat(data["_saved_at"])
        if (datetime.now() - saved).days > _CACHE_TTL_DAYS:
            return None, None
        return data["results"], saved.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return None, None


def _save_cache(cp_uniques: list[str], results: dict) -> None:
    try:
        path = _cache_path(_zone_key(cp_uniques))
        payload = {"_saved_at": datetime.now().isoformat(), "results": results}
        path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass


# ── Carte interactive ─────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def _get_commune_centre(code_insee: str) -> tuple[float, float] | None:
    try:
        r = requests.get(
            f"https://geo.api.gouv.fr/communes/{code_insee}?fields=centre",
            timeout=5,
        )
        coords = r.json().get("centre", {}).get("coordinates")
        if coords:
            return coords[1], coords[0]  # lat, lon
    except Exception:
        pass
    return None


def _build_map(communes_flat, mat_data, sf_data, pmi_data):
    if not _FOLIUM_OK:
        return None
    lats, lons = [], []
    m = folium.Map(zoom_start=10, tiles="CartoDB positron")

    # Communes (centroïdes)
    for c in communes_flat:
        cog = c.get("code_insee") or c.get("code", "")
        if not cog:
            continue
        coords = _get_commune_centre(cog)
        if not coords:
            continue
        lat, lon = coords
        lats.append(lat); lons.append(lon)
        folium.CircleMarker(
            [lat, lon], radius=5, color="#1F4E79", fill=True, fill_opacity=0.6,
            tooltip=f"{c.get('nom','')} ({c.get('cp','')})",
        ).add_to(m)

    # Maternités
    if isinstance(mat_data, dict):
        for cp, mats in mat_data.items():
            for mat in mats:
                ville = mat.get("ville", "")
                if not ville:
                    continue
                try:
                    r = requests.get(
                        f"https://geo.api.gouv.fr/communes?nom={ville}&codePostal={cp}&fields=centre&limit=1",
                        timeout=4,
                    ).json()
                    if r:
                        coords = r[0].get("centre", {}).get("coordinates")
                        if coords:
                            lat, lon = coords[1], coords[0]
                            lats.append(lat); lons.append(lon)
                            folium.Marker(
                                [lat, lon],
                                icon=folium.Icon(color="red", icon="plus-sign"),
                                tooltip=f"🏥 {mat.get('nom','')} — {mat.get('type_niveau','')}",
                                popup=f"<b>{mat.get('nom','')}</b><br>{mat.get('statut','')} {mat.get('type_niveau','')}",
                            ).add_to(m)
                except Exception:
                    pass

    # Sages-femmes
    for sf in sf_data:
        cp_sf = sf.get("cp", "")
        ville_sf = sf.get("ville", "")
        if not cp_sf:
            continue
        try:
            r = requests.get(
                f"https://geo.api.gouv.fr/communes?codePostal={cp_sf}&fields=centre&limit=1",
                timeout=4,
            ).json()
            if r:
                coords = r[0].get("centre", {}).get("coordinates")
                if coords:
                    lat, lon = coords[1], coords[0]
                    lats.append(lat); lons.append(lon)
                    folium.CircleMarker(
                        [lat, lon], radius=6, color="#2e7d32", fill=True, fill_opacity=0.8,
                        tooltip=f"🤱 {sf.get('nom','')} {sf.get('prenom','')} — {ville_sf}",
                    ).add_to(m)
        except Exception:
            pass

    # PMI
    for pmi in pmi_data:
        cp_pmi = pmi.get("cp", "")
        if not cp_pmi:
            continue
        try:
            r = requests.get(
                f"https://geo.api.gouv.fr/communes?codePostal={cp_pmi}&fields=centre&limit=1",
                timeout=4,
            ).json()
            if r:
                coords = r[0].get("centre", {}).get("coordinates")
                if coords:
                    lat, lon = coords[1], coords[0]
                    lats.append(lat); lons.append(lon)
                    folium.Marker(
                        [lat, lon],
                        icon=folium.Icon(color="orange", icon="baby"),
                        tooltip=f"👶 {pmi.get('nom','')}",
                    ).add_to(m)
        except Exception:
            pass

    if lats and lons:
        m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
    return m





# ── Inférer dept/région depuis les CP via geo.api.gouv.fr ────────────────────

def _inferer_zone(cp_uniques: list[str]) -> tuple[str, str, str]:
    """Retourne (dept_code, dept_nom, region) depuis le premier CP."""
    if not cp_uniques:
        return "", "", ""
    cp = cp_uniques[0]
    try:
        url = f"https://geo.api.gouv.fr/communes?codePostal={cp}&fields=departement,region&format=json"
        r = requests.get(url, timeout=8)
        data = r.json()
        if data:
            dept_code = data[0].get("departement", {}).get("code", cp[:2])
            dept_nom  = data[0].get("departement", {}).get("nom", "")
            region    = data[0].get("region",       {}).get("nom", "")
            return dept_code, dept_nom, region
    except Exception:
        pass
    return cp[:2], "", ""


# ── Tableau INSEE pivoté : indicateurs en lignes, communes en colonnes ─────────

# (clé interne, libellé affiché, type de calcul pour la colonne TOTAL ZONE)
_INDICATEURS = [
    # Population
    ("pop_2022",             "Population en 2022",                          "Somme"),
    ("densite_2022",         "Densité (hab/km²)",                           "Pop / Superf."),
    ("superficie_km2",       "Superficie (km²)",                            "Somme"),
    ("var_pop_2016_2022",    "Variation pop. 2016-2022 (%)",                 "Moy. pond."),
    ("solde_naturel",        "Solde naturel (%)",                           "Moy. pond."),
    ("solde_migratoire",     "Solde migratoire (%)",                        "Moy. pond."),
    ("nb_menages_2022",      "Nb ménages 2022",                             "Somme"),
    ("naissances_2022",      "Naissances domiciliées",                      "Somme"),
    ("deces_2022",           "Décès domiciliés",                            "Somme"),
    # Logement
    ("nb_logements_2022",          "Nb logements 2022",                     "Somme"),
    ("part_res_principales_2022",  "Part résidences principales (%)",       "Moy. pond."),
    ("part_res_secondaires_2022",  "Part résidences secondaires (%)",       "Moy. pond."),
    ("part_logements_vacants_2022","Part logements vacants (%)",            "Moy. pond."),
    ("part_proprietaires_2022",    "Part ménages propriétaires (%)",        "Moy. pond."),
    # Revenus
    ("mediane_revenu_2021",        "Niveau de vie médian (€)",              "Moy. pond."),
    ("taux_pauvrete_2021",         "Taux de pauvreté (%)",                  "Moy. pond."),
    ("nb_menages_fiscaux_2021",    "Nb ménages fiscaux",                    "Somme"),
    ("part_menages_imposes_2021",  "Part ménages imposés (%)",              "Moy. pond."),
    # Emploi
    ("emploi_total_2022",          "Emploi total 2022",                     "Somme"),
    ("part_emploi_salarie_2022",   "Part emploi salarié (%)",               "Moy. pond."),
    ("var_emploi_2016_2022",       "Variation emploi 2016-2022 (%)",        "Moy. pond."),
    ("taux_activite_15_64_2022",   "Taux d'activité 15-64 ans (%)",         "Moy. pond."),
    ("taux_chomage_15_64_2022",    "Taux de chômage 15-64 ans (%)",         "Moy. pond."),
    # Établissements
    ("nb_etab_actifs_2023",        "Nb établissements actifs",              "Somme"),
    ("part_agriculture_2023",      "Part agriculture (%)",                  "Moy. pond."),
    ("part_industrie_2023",        "Part industrie (%)",                    "Moy. pond."),
    ("part_construction_2023",     "Part construction (%)",                 "Moy. pond."),
    ("part_commerce_transp_2023",  "Part commerce/transports/services (%)", "Moy. pond."),
    ("part_admin_sante_2023",      "Part admin. publique/santé (%)",        "Moy. pond."),
    ("part_etab_1_9_sal_2023",     "Part étab. 1-9 salariés (%)",           "Moy. pond."),
    ("part_etab_10_sal_plus_2023", "Part étab. 10+ salariés (%)",           "Moy. pond."),
]


def _build_insee_pivot(insee_data: list[dict], all_communes: list[dict] | None = None):
    """
    Construit un DataFrame pivoté stylé :
    - lignes = indicateurs
    - colonnes = INDICATEUR | commune1 | commune2 | … | TOTAL ZONE | Calcul
    - cellules vides (commune sans données INSEE) en jaune
    """
    def _v(val):
        return "" if val is None else val

    # Index insee_data par nom de commune
    insee_by_nom = {r.get("commune", r.get("code_insee", "?")): r for r in insee_data}

    # Communes sans données INSEE → colonne jaune
    # Critère : statut "Code INSEE manquant" / "Données non trouvées", ou aucune valeur numérique
    _statuts_vides = {"Code INSEE manquant", "Données non trouvées"}
    communes_sans_donnees: set[str] = {
        r.get("commune", r.get("code_insee", "?"))
        for r in insee_data
        if r.get("_statut") in _statuts_vides
    }

    # Liste complète des communes à afficher
    if all_communes:
        all_noms = []
        seen = set()
        for c in all_communes:
            nom = c.get("nom", c.get("code_insee", "?"))
            if nom not in seen:
                seen.add(nom)
                all_noms.append(nom)
        for nom in insee_by_nom:
            if nom not in seen:
                all_noms.append(nom)
    else:
        all_noms = list(insee_by_nom.keys())

    totaux = compute_zone_totals(insee_data)

    rows = []
    for key, label, calcul in _INDICATEURS:
        row = {"Indicateur": label}
        for nom in all_noms:
            r = insee_by_nom.get(nom, {})
            row[nom] = _v(r.get(key))
        row["TOTAL ZONE"] = _v(totaux.get(key))
        row["Calcul"] = calcul
        rows.append(row)

    cols = ["Indicateur"] + all_noms + ["TOTAL ZONE", "Calcul"]
    df = pd.DataFrame(rows, columns=cols)

    # Coloration jaune pour les colonnes sans données INSEE
    def _highlight(df):
        styles = pd.DataFrame("", index=df.index, columns=df.columns)
        for nom in communes_sans_donnees:
            if nom in styles.columns:
                styles[nom] = "background-color: #FFF59D"
        return styles

    return df.style.apply(_highlight, axis=None)


# ── Helper export Excel ───────────────────────────────────────────────────────

def _df_to_xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Données")
    return buf.getvalue()


def _dl_button(df: pd.DataFrame, label: str, filename: str) -> None:
    today = date.today().strftime("%Y-%m-%d")
    st.download_button(
        label=f"⬇️ Télécharger {label} (.xlsx)",
        data=_df_to_xlsx(df),
        file_name=f"{filename}_{today}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE : TABLEAU DE BORD
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state.page == "dashboard":
    st.title("👶 DIP Tire-Lait Express")
    st.caption(f"Bienvenue **{get_current_user()}** — Dossiers d'Information Périnatale")
    st.divider()

    dips = list_dips()
    col_new, col_stat1, col_stat2 = st.columns([2, 1, 1])
    with col_new:
        if st.button("➕ Créer un nouveau DIP", type="primary", use_container_width=True):
            _reset_wizard()
            st.rerun()
    with col_stat1:
        st.metric("DIPs créés", len(dips))
    with col_stat2:
        from datetime import timedelta
        recent = sum(
            1 for d in dips
            if d.get("created_at", "")[:10] >= (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        )
        st.metric("Cette semaine", recent)

    st.divider()
    st.subheader("📋 Tous les DIPs")

    if not dips:
        st.info("Aucun DIP enregistré. Créez votre premier DIP !")
    else:
        # Tableau
        rows = []
        for dip in dips:
            rows.append({
                "Zone":       dip.get("zone_nom") or dip.get("dept_nom", "?"),
                "Département": dip.get("dept_nom", ""),
                "Date":       dip.get("created_at", "")[:10],
                "Communes":   dip.get("nb_communes", "–"),
                "Créé par":   dip.get("created_by", ""),
                "_id":        dip.get("id"),
                "_legacy":    dip.get("legacy", False),
            })
        df_dips = pd.DataFrame(rows)

        # Affichage avec boutons Consulter
        for i, row in df_dips.iterrows():
            c1, c2, c3, c4, c5, c6 = st.columns([2.5, 2, 1.5, 1, 1.5, 1.5])
            c1.write(f"**{row['Zone']}**")
            c2.write(row["Département"])
            c3.write(row["Date"])
            c4.write(str(row["Communes"]))
            c5.write(row["Créé par"])
            btn_label = "📥 Fichiers" if row["_legacy"] else "🔍 Consulter"
            if c6.button(btn_label, key=f"view_{row['_id']}"):
                st.session_state.page        = "view_dip"
                st.session_state.view_dip_id = row["_id"]
                st.rerun()

    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE : CONSULTATION D'UN DIP PASSÉ
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state.page == "view_dip":
    dip_id = st.session_state.get("view_dip_id")
    dip    = load_dip(dip_id) if dip_id else None

    if not dip:
        st.error("DIP introuvable.")
        if st.button("← Retour"):
            st.session_state.page = "dashboard"
            st.rerun()
        st.stop()

    zone_nom  = dip.get("zone_nom") or dip.get("dept_nom", "?")
    st.title(f"📊 DIP — {zone_nom}")
    st.caption(f"Créé le {dip.get('created_at','')[:10]}  ·  {dip.get('nb_communes','?')} commune(s)")

    col_ret, col_dl1, col_dl2 = st.columns([2, 2, 2])
    with col_ret:
        if st.button("← Tableau de bord"):
            st.session_state.page = "dashboard"
            st.rerun()

    # Téléchargements
    xlsx_path = Path(__file__).parent / "output" / dip.get("xlsx_filename", "")
    docx_path = Path(__file__).parent / "output" / dip.get("docx_filename", "")
    with col_dl1:
        if xlsx_path.exists():
            st.download_button("⬇️ Excel", xlsx_path.read_bytes(),
                               file_name=xlsx_path.name,
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True)
        else:
            st.caption("Excel non disponible")
    with col_dl2:
        if docx_path.exists():
            st.download_button("⬇️ Word", docx_path.read_bytes(),
                               file_name=docx_path.name,
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                               use_container_width=True)
        else:
            st.caption("Word non disponible")

    st.divider()

    # Résultats interactifs si cache disponible
    cache_key = dip.get("cache_key")
    if cache_key:
        results = load_dip_results(cache_key)
        if results:
            communes_list = dip.get("communes_list", [])
            dept_code = dip.get("dept_code", "")
            dept_nom  = dip.get("dept_nom", "")
            region    = dip.get("region", "")
            insee_data = results.get("insee", [])
            pj_data    = results.get("pages_jaunes", results.get("annuaire_pharmacies", []))

            tab_insee, tab_ph, tab_mat = st.tabs(["📊 INSEE", "💊 Pharmacies", "🏥 Maternités"])

            with tab_insee:
                if insee_data:
                    st.dataframe(
                        _build_insee_pivot(insee_data, communes_list or None),
                        use_container_width=True, hide_index=True
                    )
                else:
                    st.info("Données INSEE non disponibles dans ce DIP.")

            with tab_ph:
                if pj_data:
                    df_ph = pd.DataFrame([{
                        "Commune": r.get("commune",""), "CP": r.get("cp",""),
                        "Pharmacies": r.get("nb_pharmacies",0),
                        "Mat. médical": r.get("nb_materiel_medical",0),
                    } for r in pj_data])
                    st.dataframe(df_ph, use_container_width=True, hide_index=True)
                else:
                    st.info("Données pharmacies non disponibles dans ce DIP.")

            with tab_mat:
                mat_data = results.get("maternites", {})
                if mat_data:
                    rows_mat = []
                    for cp, mats in (mat_data.items() if isinstance(mat_data, dict) else {}.items()):
                        for m in mats:
                            rows_mat.append({"CP": cp, "Maternité": m.get("nom",""),
                                             "Niveau": m.get("type_niveau",""),
                                             "Ville": m.get("ville","")})
                    if rows_mat:
                        st.dataframe(pd.DataFrame(rows_mat), use_container_width=True, hide_index=True)
                    else:
                        st.info("Aucune maternité.")
                else:
                    st.info("Données maternités non disponibles dans ce DIP.")
        else:
            st.warning("Cache de résultats expiré ou supprimé — seuls les fichiers sont disponibles.")
    else:
        st.info("Ce DIP est un fichier legacy — consultation des données non disponible.")

    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE : WIZARD (nouveau DIP) — redirige vers dashboard si page != "wizard"
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state.page not in ("wizard", None):
    st.session_state.page = "dashboard"
    st.rerun()


# ── Header ────────────────────────────────────────────────────────────────────

st.title("👶 Mise à jour DIP — Tire-Lait Express")
st.caption("Collecte automatique des données socio-démographiques pour l'Annexe 5")

steps_labels = ["Codes postaux", "Communes", "Collecte", "Résultats", "Téléchargement"]
cols_steps = st.columns(5)
for i, (col, label) in enumerate(zip(cols_steps, steps_labels), 1):
    with col:
        if i < st.session_state.step:
            st.markdown(f'<span class="done-badge">✓</span> **{label}**', unsafe_allow_html=True)
        elif i == st.session_state.step:
            st.markdown(f'<span class="step-badge">{i}</span> **{label}**', unsafe_allow_html=True)
        else:
            st.markdown(f'<span style="color:#aaa">{i}. {label}</span>', unsafe_allow_html=True)

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 1 — Extraction des codes postaux
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state.step == 1:
    st.subheader("📋 Étape 1 — Codes postaux")
    st.write("Collez le contenu de l'email. Les codes postaux sont extraits automatiquement.")

    email_text = st.text_area(
        "Contenu de l'email",
        height=180,
        placeholder="Collez ici le texte de l'email (noms, codes postaux, tout le contenu)…",
    )

    # Extraction
    cp_uniques: list[str] = list(st.session_state.cp_uniques)

    if email_text:
        tous_cp = re.findall(r"\b(\d{5})\b", email_text)
        cp_valides = [cp for cp in tous_cp if 1000 <= int(cp) <= 97680]
        seen: set[str] = set(cp_uniques)
        for cp in cp_valides:
            if cp not in seen:
                seen.add(cp)
                cp_uniques.append(cp)
        cp_uniques.sort()

    if cp_uniques:
        st.success(f"**{len(cp_uniques)} code(s) postal/aux**")
        df_cp = pd.DataFrame({"Code postal": cp_uniques})
        st.dataframe(df_cp, width='content', hide_index=True)

    # Modification manuelle
    with st.expander("✏️ Ajouter / Supprimer des codes postaux"):
        col1, col2 = st.columns(2)
        with col1:
            ajouter = st.text_input("Codes à ajouter (virgules ou espaces)", key="add_cp")
            if st.button("➕ Ajouter"):
                seen2: set[str] = set(cp_uniques)
                for cp in re.findall(r"\b\d{5}\b", ajouter):
                    if cp not in seen2 and 1000 <= int(cp) <= 97680:
                        cp_uniques.append(cp)
                        seen2.add(cp)
                cp_uniques.sort()
                st.session_state.cp_uniques = cp_uniques
                st.rerun()
        with col2:
            suppr = st.text_input("Codes à supprimer", key="del_cp")
            if st.button("➖ Supprimer"):
                a_suppr = set(re.findall(r"\b\d{5}\b", suppr))
                cp_uniques = [cp for cp in cp_uniques if cp not in a_suppr]
                st.session_state.cp_uniques = cp_uniques
                st.rerun()

    st.divider()
    if cp_uniques and st.button("✅ Confirmer et résoudre les communes →", type="primary"):
        st.session_state.cp_uniques = cp_uniques
        with st.spinner("Recherche des communes sur annuaire-administration.com…"):
            communes_par_cp = get_communes_for_all_cp(cp_uniques)
        st.session_state.communes_par_cp = communes_par_cp

        dept_code, dept_nom, region = _inferer_zone(cp_uniques)
        dept_codes = list(dict.fromkeys(
            cp[:3] if cp.startswith("97") else cp[:2]
            for cp in cp_uniques
        ))
        st.session_state.dept_code  = dept_code
        st.session_state.dept_codes = dept_codes
        st.session_state.dept_nom   = dept_nom
        st.session_state.region     = region

        st.session_state.step = 2
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 2 — Validation des communes
# ══════════════════════════════════════════════════════════════════════════════

elif st.session_state.step == 2:
    st.subheader("🏘️ Étape 2 — Communes")

    dept_code = st.session_state.dept_code
    dept_nom  = st.session_state.dept_nom
    region    = st.session_state.region

    # Info zone auto-inférée
    col1, col2, col3 = st.columns(3)
    col1.metric("Département", f"{dept_nom} ({dept_code})" if dept_nom else dept_code)
    col2.metric("Région", region or "—")
    col3.metric("Codes postaux", len(st.session_state.cp_uniques))

    st.divider()
    st.write("**Communes associées à chaque code postal :**")

    rows_communes = []
    for cp, communes in st.session_state.communes_par_cp.items():
        if communes:
            for c in communes:
                rows_communes.append({
                    "Code postal": cp,
                    "Commune":     c["nom"],
                    "Code INSEE":  c.get("code_insee", ""),
                    "Département": c.get("departement", {}).get("nom", "") if isinstance(c.get("departement"), dict) else "",
                    "Région":      c.get("region", {}).get("nom", "") if isinstance(c.get("region"), dict) else "",
                })
        else:
            rows_communes.append({"Code postal": cp, "Commune": "⚠️ Non résolu", "Code INSEE": "", "Département": "", "Région": ""})

    df_communes = pd.DataFrame(rows_communes)
    st.dataframe(df_communes, width='stretch', hide_index=True)
    _dl_button(df_communes, "Communes", "communes")

    st.divider()
    col_back, col_next = st.columns([1, 5])
    with col_back:
        if st.button("← Retour"):
            st.session_state.step = 1
            st.rerun()
    with col_next:
        if st.button("🚀 Lancer la collecte →", type="primary"):
            st.session_state.step = 3
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 3 — Collecte
# ══════════════════════════════════════════════════════════════════════════════

elif st.session_state.step == 3:

    communes_par_cp = st.session_state.communes_par_cp
    cp_uniques      = st.session_state.cp_uniques

    # ── Cache : proposer la réutilisation si données récentes ────────────────
    cached_results, cached_date = _load_cache(cp_uniques)
    if cached_results and not st.session_state.results:
        st.info(f"💾 Données en cache disponibles du **{cached_date}** pour cette zone.")
        col_cache_use, col_cache_skip = st.columns([1, 2])
        with col_cache_use:
            if st.button("⚡ Utiliser le cache", type="primary"):
                st.session_state.results = cached_results
                st.rerun()
        with col_cache_skip:
            st.caption("ou lancez les scrapers ci-dessous pour actualiser les données")
    dept_code       = st.session_state.dept_code
    dept_codes      = list(dict.fromkeys(
        cp[:3] if cp.startswith("97") else cp[:2]
        for cp in cp_uniques
    )) or [dept_code]
    dept_nom        = st.session_state.dept_nom
    region          = st.session_state.region

    # Aplatir les communes (calcul une seule fois)
    communes_flat: list[dict] = []
    seen_cog: set[str] = set()
    for cp, communes in communes_par_cp.items():
        for c in communes:
            cog = c.get("code_insee") or c.get("code") or c["nom"]
            if cog not in seen_cog:
                seen_cog.add(cog)
                c.setdefault("departement", {"code": dept_code, "nom": dept_nom})
                c.setdefault("region",      {"code": "",        "nom": region})
                c.setdefault("cp", cp)
                communes_flat.append(c)

    st.session_state.communes_flat = communes_flat

    communes_pj = [
        {
            "nom":        c["nom"],
            "cp":         c.get("cp", ""),
            "code_insee": c.get("code_insee") or c.get("code", ""),
            "dept_code":  (c.get("departement") or {}).get("code", "") or c.get("cp", "")[:2],
            "dept_nom":   (c.get("departement") or {}).get("nom", ""),
        }
        for c in communes_flat
    ]

    cp_communes_dict = {
        cp: [dict(c, code_insee=c.get("code_insee") or c.get("code", "")) for c in communes]
        for cp, communes in communes_par_cp.items()
    }

    # ── Définition des 6 sous-étapes ──────────────────────────────────────────
    COLLECT_STEPS = [
        {
            "key":   "maternites",
            "label": "🏥 Maternités",
            "desc":  "Journal des Femmes — maternités par code postal",
            "fn":    get_maternites_par_cp,
            "args":  (cp_communes_dict,),
        },
        {
            "key":   "insee",
            "label": "📊 INSEE",
            "desc":  "INSEE — données socio-démographiques",
            "fn":    get_all_communes_data,
            "args":  (communes_flat,),
        },
        {
            "key":   "lactariums",
            "label": "🥛 Lactariums",
            "desc":  "ALF — lactariums",
            "fn":    get_lactariums,
            "args":  (dept_code, region),
        },
        {
            "key":   "sages_femmes",
            "label": "🤱 Sages-femmes",
            "desc":  "Ordre SF — sages-femmes libérales",
            "fn":    get_sages_femmes,
            "args":  (dept_codes, cp_uniques,
                      [c["nom"] for c in communes_flat]),
        },
        {
            "key":   "pmi",
            "label": "👶 PMI",
            "desc":  "AlloPMI — PMI",
            "fn":    get_pmi,
            "args":  (dept_code, dept_nom, cp_uniques, communes_pj),
        },
        {
            "key":   "pages_jaunes",
            "label": "💊 Pharmacies",
            "desc":  "Annuaire des Pharmacies + SIRENE — pharmacies & matériel médical",
            "fn":    get_pharmacies_and_medical,
            "args":  (communes_pj,),
        },
    ]

    # ── Lancer un scraper si demandé ──────────────────────────────────────────
    launch_key = st.session_state.pop("_launch_key", None)
    if launch_key:
        step_to_run = next((s for s in COLLECT_STEPS if s["key"] == launch_key), None)
        if step_to_run:
            with st.spinner(f"Collecte : {step_to_run['desc']}…"):
                try:
                    result_data = step_to_run["fn"](*step_to_run["args"])
                    st.session_state.results[launch_key] = result_data
                except Exception as e:
                    st.session_state.results[launch_key] = {} if launch_key == "maternites" else []
                    st.error(f"Erreur {step_to_run['label']} : {e}")
        st.rerun()

    # ── Cartes indépendantes ──────────────────────────────────────────────────
    st.subheader("⚙️ Étape 3 — Collecte des données")
    st.caption("Lancez chaque source indépendamment, dans n'importe quel ordre.")

    # Bouton tout lancer
    nb_done = sum(1 for s in COLLECT_STEPS if s["key"] in st.session_state.results)
    if nb_done < len(COLLECT_STEPS):
        if st.button("▶▶ Tout lancer d'un coup", use_container_width=False):
            st.session_state["_launch_all"] = True
            st.rerun()

    # Lancer tous les scrapers d'un coup
    if st.session_state.pop("_launch_all", False):
        for step in COLLECT_STEPS:
            if step["key"] not in st.session_state.results:
                with st.spinner(f"{step['label']} en cours…"):
                    try:
                        st.session_state.results[step["key"]] = step["fn"](*step["args"])
                    except Exception as e:
                        st.session_state.results[step["key"]] = {} if step["key"] == "maternites" else []
        _save_cache(cp_uniques, st.session_state.results)
        st.rerun()

    card_cols = st.columns(3)
    for i, step in enumerate(COLLECT_STEPS):
        key = step["key"]
        data = st.session_state.results.get(key)
        with card_cols[i % 3]:
            with st.container(border=True):
                if data is None:
                    icon, caption = "⚪", "À lancer"
                else:
                    icon = "✅"
                    if isinstance(data, list):
                        caption = f"{len(data)} résultat(s)" if data else "Aucun résultat"
                    elif isinstance(data, dict):
                        n = sum(len(v) for v in data.values() if isinstance(v, list))
                        caption = f"{n} résultat(s)" if n else "Aucun résultat"
                    else:
                        caption = "OK"
                st.markdown(f"{icon} **{step['label']}**")
                st.caption(caption)
                btn_lbl = "🔄 Relancer" if data is not None else "▶ Lancer"
                if st.button(btn_lbl, key=f"btn_{key}", use_container_width=True):
                    st.session_state.results.pop(key, None)
                    st.session_state["_launch_key"] = key
                    st.rerun()

    st.divider()

    # ── Résultats par source (expandables) ───────────────────────────────────
    for step in COLLECT_STEPS:
        key  = step["key"]
        data = st.session_state.results.get(key)
        if data is None:
            continue
        with st.expander(f"{step['label']} — Résultats", expanded=False):
            if key == "maternites" and isinstance(data, dict):
                rows = []
                for cp, mats in data.items():
                    if mats:
                        for m in mats:
                            rows.append({"CP": cp, "Maternité": m.get("nom",""), "Statut": m.get("statut",""),
                                         "Niveau": m.get("type_niveau",""), "Accouchements/an": m.get("nb_accouchements_an",""), "Ville": m.get("ville","")})
                    else:
                        rows.append({"CP": cp, "Maternité": "—", "Statut": "", "Niveau": "", "Accouchements/an": "", "Ville": ""})
                df_mat = pd.DataFrame(rows)
                st.dataframe(df_mat, use_container_width=True, hide_index=True)
                if rows:
                    _dl_button(df_mat, "Maternités", "maternites")

            elif key == "insee" and isinstance(data, list):
                if data:
                    st.dataframe(_build_insee_pivot(data, st.session_state.get("communes_flat")), use_container_width=True, hide_index=True)
                    df_insee = pd.DataFrame([
                        {k: v for k, v in r.items() if not k.startswith("_")} | {"Statut": r.get("_statut", "")}
                        for r in data
                    ])
                    _dl_button(df_insee, "INSEE", "insee")
                else:
                    st.warning("Aucune donnée INSEE récupérée.")

            elif key == "pages_jaunes" and isinstance(data, list):
                if data:
                    df_pj = pd.DataFrame([{
                        "Commune": r.get("commune",""), "CP": r.get("cp",""),
                        "Nb pharmacies": r.get("nb_pharmacies",0),
                        "Noms pharmacies": " | ".join(r.get("noms_pharmacies",[])),
                        "Nb mat. médical": r.get("nb_materiel_medical",0),
                        "Noms mat. médical": " | ".join(r.get("noms_materiel_medical",[])),
                    } for r in data])
                    st.dataframe(df_pj[["Commune","CP","Nb pharmacies","Nb mat. médical"]], use_container_width=True, hide_index=True)
                    _dl_button(df_pj, "Pharmacies & Matériel médical", "pharmacies_mm")
                else:
                    st.warning("Aucune donnée Pages Jaunes récupérée.")

            elif key == "lactariums" and isinstance(data, list):
                if data:
                    df_lac = pd.DataFrame([{
                        "Nom": r.get("nom",""), "CP": r.get("cp",""), "Ville": r.get("ville",""),
                        "Adresse": r.get("adresse",""), "Tél.": r.get("telephone",""),
                        "Email": r.get("email",""), "Type": r.get("type",""),
                        "Don anonyme": r.get("don_anonyme",""),
                        "Équipe": " / ".join(r.get("equipe",[])[:3]),
                    } for r in data])
                    st.dataframe(df_lac, use_container_width=True, hide_index=True)
                    _dl_button(df_lac, "Lactariums", "lactariums")
                else:
                    st.info("Aucun lactarium dans votre zone.")

            elif key == "sages_femmes" and isinstance(data, list):
                if data:
                    st.metric("Sages-femmes (dédoublonnées)", len(data))
                    df_sf = pd.DataFrame([{
                        "Nom": f"{r.get('nom','')} {r.get('prenom','')}".strip(),
                        "Codes postaux": r.get("code_postaux_display", r.get("cp","")),
                        "Adresse": r.get("adresse",""), "Tél.": r.get("telephone",""),
                        "Email": r.get("email",""),
                    } for r in data])
                    st.dataframe(df_sf, use_container_width=True, hide_index=True)
                    _dl_button(df_sf, "Sages-femmes", "sages_femmes")
                else:
                    st.warning("Aucune sage-femme trouvée.")

            elif key == "pmi" and isinstance(data, list):
                if data:
                    st.metric("Centres PMI", len(data))
                    df_pmi = pd.DataFrame([{
                        "Nom": r.get("nom",""), "CP": r.get("cp",""), "Ville": r.get("ville",""),
                        "Adresse": r.get("adresse",""), "Téléphone": r.get("telephone",""),
                        "Email": r.get("email",""), "Horaires": r.get("horaires",""),
                    } for r in data])
                    st.dataframe(df_pmi, use_container_width=True, hide_index=True)
                    _dl_button(df_pmi, "PMI", "pmi")
                else:
                    st.info("Aucune PMI dans votre zone.")

    st.divider()

    # ── Navigation ────────────────────────────────────────────────────────────
    col_back, col_gen = st.columns([1, 3])
    with col_back:
        if st.button("← Retour étape 2"):
            st.session_state.step = 2
            st.session_state.results = {}
            st.rerun()
    with col_gen:
        if st.button("✅ Valider et générer les fichiers", type="primary"):
            results = st.session_state.results
            mat_raw = results.get("maternites", {})
            maternites_flat = [
                dict(m, _cp_recherche=cp)
                for cp, mats in (mat_raw.items() if isinstance(mat_raw, dict) else {}.items())
                for m in mats
            ]
            today     = date.today().strftime("%Y-%m-%d")
            zone_slug = f"DIP_{dept_nom or dept_code}".replace(" ", "_")[:30]
            out_dir   = Path(__file__).parent / "output"
            out_dir.mkdir(exist_ok=True)
            xlsx_path = str(out_dir / f"donnees_{zone_slug}_{today}.xlsx")
            docx_path = str(out_dir / f"synthese_{zone_slug}_{today}.docx")
            zone_nom  = f"{dept_nom} ({dept_code})" if dept_nom else dept_code

            with st.spinner("Génération du fichier Excel…"):
                generate_excel(
                    output_path=xlsx_path, zone_nom=zone_nom,
                    dept_code=dept_code, dept_nom=dept_nom, region=region,
                    communes_data=results.get("insee", []),
                    pj_data=results.get("pages_jaunes", []),
                    maternites=maternites_flat,
                    lactariums=results.get("lactariums", []),
                    sages_femmes=results.get("sages_femmes", []),
                    pmi=results.get("pmi", []),
                )
                st.session_state.xlsx_bytes = open(xlsx_path, "rb").read()

            with st.spinner("Génération du document Word…"):
                generate_word(
                    output_path=docx_path, zone_nom=zone_nom,
                    dept_code=dept_code, dept_nom=dept_nom, region=region,
                    communes=communes_flat,
                    communes_data=results.get("insee", []),
                    pj_data=results.get("pages_jaunes", []),
                    maternites=maternites_flat,
                    lactariums=results.get("lactariums", []),
                    sages_femmes=results.get("sages_femmes", []),
                    pmi=results.get("pmi", []),
                )
                st.session_state.docx_bytes = open(docx_path, "rb").read()

            _save_cache(cp_uniques, st.session_state.results)

            # Enregistrer le DIP dans l'index pour consultation future
            cache_key = _zone_key(cp_uniques)
            save_dip({
                "id":           cache_key,
                "zone_nom":     zone_nom,
                "dept_nom":     dept_nom,
                "dept_code":    dept_code,
                "region":       region,
                "cp_uniques":   cp_uniques,
                "nb_communes":  len(communes_flat),
                "created_at":   datetime.now().isoformat(),
                "created_by":   get_current_user(),
                "cache_key":    cache_key,
                "xlsx_filename": Path(xlsx_path).name,
                "docx_filename": Path(docx_path).name,
                "communes_list": [
                    {"nom": c.get("nom",""), "cp": c.get("cp",""),
                     "code_insee": c.get("code_insee","")}
                    for c in communes_flat
                ],
            })

            st.session_state.page = "wizard"
            st.session_state.step = 4
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 4 — Résultats
# ══════════════════════════════════════════════════════════════════════════════

elif st.session_state.step == 4:
    st.subheader("📊 Étape 4 — Résultats")

    results    = st.session_state.results
    cp_uniques = st.session_state.cp_uniques
    insee_data = results.get("insee", [])
    pj_data    = results.get("pages_jaunes", [])
    mat_data   = results.get("maternites", {})
    sf_data    = results.get("sages_femmes", [])
    pmi_data   = results.get("pmi", [])
    lac_data   = results.get("lactariums", [])

    zone_totals = compute_zone_totals(insee_data) if insee_data else {}

    def _to_num(v):
        if v is None:
            return None
        try:
            return float(str(v).replace("\xa0", "").replace(" ", "").replace(",", "."))
        except Exception:
            return None

    def _kpi(key):
        v = zone_totals.get(key)
        if v is None:
            return "N/D"
        try:
            n = int(float(str(v).replace(",", ".")))
            return f"{n:,}".replace(",", " ")
        except Exception:
            return str(v)

    # KPIs
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Population 2022", _kpi("pop_2022"))
    col2.metric("Naissances 2022", _kpi("naissances_2022"))
    col3.metric("Pharmacies", sum(r.get("nb_pharmacies", 0) for r in pj_data))
    col4.metric("Sages-femmes", len(sf_data))
    col5.metric("PMI", len([p for p in pmi_data if "Erreur" not in p.get("nom", "")]))

    st.divider()

    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
        "📊 Tableau de bord", "🏘️ Communes", "💊 Pharmacies", "🏥 Maternités",
        "🤱 Sages-femmes", "🥛 Lactariums", "👶 PMI", "🗺️ Carte",
    ])

    # ── Tableau de bord ───────────────────────────────────────────────────────
    with tab1:
        if not _PLOTLY_OK:
            st.warning("Installez `plotly` pour afficher les graphiques.")
        else:
            # ── Ligne 1 : Population & Naissances ──────────────────────────
            if insee_data:
                df_insee = pd.DataFrame([
                    {
                        "Commune":       r.get("commune", ""),
                        "Population":    _to_num(r.get("pop_2022")),
                        "Naissances":    _to_num(r.get("naissances_2022")),
                        "Taux pauvreté": _to_num(r.get("taux_pauvrete_2021")),
                        "Taux chômage":  _to_num(r.get("taux_chomage_15_64_2022")),
                        "Revenu médian": _to_num(r.get("mediane_revenu_2021")),
                        "Densité":       _to_num(r.get("densite_2022")),
                    }
                    for r in insee_data if r.get("commune") != "TOTAL ZONE"
                ]).dropna(subset=["Commune"])

                col_a, col_b = st.columns(2)
                with col_a:
                    df_pop = df_insee.dropna(subset=["Population"]).sort_values("Population", ascending=True)
                    if not df_pop.empty:
                        fig = px.bar(df_pop, x="Population", y="Commune", orientation="h",
                                     title="Population par commune (2022)",
                                     color="Population", color_continuous_scale="Blues",
                                     height=max(300, len(df_pop) * 28))
                        fig.update_layout(showlegend=False, coloraxis_showscale=False, margin=dict(l=0, r=0, t=40, b=0))
                        st.plotly_chart(fig, use_container_width=True)

                with col_b:
                    df_nais = df_insee.dropna(subset=["Naissances"]).sort_values("Naissances", ascending=True)
                    if not df_nais.empty:
                        fig = px.bar(df_nais, x="Naissances", y="Commune", orientation="h",
                                     title="Naissances domiciliées (2022)",
                                     color="Naissances", color_continuous_scale="Greens",
                                     height=max(300, len(df_nais) * 28))
                        fig.update_layout(showlegend=False, coloraxis_showscale=False, margin=dict(l=0, r=0, t=40, b=0))
                        st.plotly_chart(fig, use_container_width=True)

                # ── Ligne 2 : Revenus & Pauvreté ───────────────────────────
                col_c, col_d = st.columns(2)
                with col_c:
                    df_rev = df_insee.dropna(subset=["Revenu médian"]).sort_values("Revenu médian", ascending=True)
                    if not df_rev.empty:
                        fig = px.bar(df_rev, x="Revenu médian", y="Commune", orientation="h",
                                     title="Revenu disponible médian (€)",
                                     color="Revenu médian", color_continuous_scale="Oranges",
                                     height=max(300, len(df_rev) * 28))
                        fig.update_layout(showlegend=False, coloraxis_showscale=False, margin=dict(l=0, r=0, t=40, b=0))
                        st.plotly_chart(fig, use_container_width=True)

                with col_d:
                    df_pauv = df_insee.dropna(subset=["Taux pauvreté"]).sort_values("Taux pauvreté", ascending=False)
                    if not df_pauv.empty:
                        fig = px.bar(df_pauv, x="Taux pauvreté", y="Commune", orientation="h",
                                     title="Taux de pauvreté (%)",
                                     color="Taux pauvreté", color_continuous_scale="Reds",
                                     height=max(300, len(df_pauv) * 28))
                        fig.update_layout(showlegend=False, coloraxis_showscale=False, margin=dict(l=0, r=0, t=40, b=0))
                        st.plotly_chart(fig, use_container_width=True)

            st.divider()

            # ── Ligne 3 : Offre de soins ────────────────────────────────────
            col_e, col_f, col_g = st.columns(3)

            with col_e:
                # Maternités par niveau
                if isinstance(mat_data, dict):
                    niveaux_count: dict[str, int] = {}
                    for cp, mats in mat_data.items():
                        for m in mats:
                            niv = m.get("type_niveau", "Non renseigné") or "Non renseigné"
                            niveaux_count[niv] = niveaux_count.get(niv, 0) + 1
                    if niveaux_count:
                        fig = px.pie(
                            names=list(niveaux_count.keys()),
                            values=list(niveaux_count.values()),
                            title="Maternités par niveau",
                            color_discrete_sequence=px.colors.sequential.RdBu,
                        )
                        fig.update_layout(margin=dict(l=0, r=0, t=40, b=0))
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("Aucune maternité")

            with col_f:
                # Statut public/privé
                if isinstance(mat_data, dict):
                    statuts: dict[str, int] = {}
                    for cp, mats in mat_data.items():
                        for m in mats:
                            s = m.get("statut", "Non renseigné") or "Non renseigné"
                            statuts[s] = statuts.get(s, 0) + 1
                    if statuts:
                        fig = px.pie(
                            names=list(statuts.keys()),
                            values=list(statuts.values()),
                            title="Maternités public / privé",
                            color_discrete_map={"Public": "#1F4E79", "Privé": "#e57373", "Non renseigné": "#aaa"},
                        )
                        fig.update_layout(margin=dict(l=0, r=0, t=40, b=0))
                        st.plotly_chart(fig, use_container_width=True)

            with col_g:
                # Pharmacies par commune (top 10)
                if pj_data:
                    df_ph = pd.DataFrame([
                        {"Commune": r.get("commune", ""), "Pharmacies": r.get("nb_pharmacies", 0)}
                        for r in pj_data
                    ]).sort_values("Pharmacies", ascending=False).head(10)
                    if not df_ph.empty:
                        fig = px.bar(df_ph, x="Commune", y="Pharmacies",
                                     title="Pharmacies (top 10 communes)",
                                     color="Pharmacies", color_continuous_scale="Purples")
                        fig.update_layout(showlegend=False, coloraxis_showscale=False,
                                          margin=dict(l=0, r=0, t=40, b=0), xaxis_tickangle=-45)
                        st.plotly_chart(fig, use_container_width=True)

            # ── Ligne 4 : Chômage ───────────────────────────────────────────
            if insee_data:
                df_chom = df_insee.dropna(subset=["Taux chômage"]).sort_values("Taux chômage", ascending=False)
                if not df_chom.empty:
                    fig = px.bar(df_chom, x="Commune", y="Taux chômage",
                                 title="Taux de chômage 15-64 ans (%)",
                                 color="Taux chômage", color_continuous_scale="YlOrRd")
                    fig.update_layout(showlegend=False, coloraxis_showscale=False,
                                      margin=dict(l=0, r=0, t=40, b=0), xaxis_tickangle=-45)
                    st.plotly_chart(fig, use_container_width=True)

    # ── Onglet Communes ───────────────────────────────────────────────────────
    with tab2:
        if insee_data:
            st.dataframe(_build_insee_pivot(insee_data, st.session_state.get("communes_flat")), width='stretch', hide_index=True)
        else:
            st.warning("Données INSEE non récupérées — le site a peut-être bloqué la requête. Relancez la collecte.")

    with tab3:
        if pj_data:
            st.caption("✏️ Les colonnes **Pharmacies** et **Matériel médical** sont modifiables — double-cliquez sur une cellule pour corriger.")
            df_edit = pd.DataFrame([{
                "Commune":            r.get("commune", ""),
                "CP":                 r.get("cp", ""),
                "Pharmacies":         int(r.get("nb_pharmacies", 0)),
                "Matériel médical":   int(r.get("nb_materiel_medical", 0)),
                "Noms pharmacies":    " | ".join(r.get("noms_pharmacies", [])[:5]),
                "Noms mat. médical":  " | ".join(r.get("noms_materiel_medical", [])[:5]),
            } for r in pj_data])

            edited = st.data_editor(
                df_edit,
                column_config={
                    "Commune":           st.column_config.TextColumn(disabled=True),
                    "CP":                st.column_config.TextColumn(disabled=True),
                    "Pharmacies":        st.column_config.NumberColumn(min_value=0, step=1),
                    "Matériel médical":  st.column_config.NumberColumn(min_value=0, step=1),
                    "Noms pharmacies":   st.column_config.TextColumn(disabled=True),
                    "Noms mat. médical": st.column_config.TextColumn(disabled=True),
                },
                hide_index=True,
                use_container_width=True,
                key="pj_editor",
            )

            if st.button("💾 Sauvegarder les modifications"):
                pj_updated = st.session_state.results.get("pages_jaunes", [])
                for _, row in edited.iterrows():
                    commune = row["Commune"]
                    for r in pj_updated:
                        if r.get("commune") == commune:
                            r["nb_pharmacies"]    = int(row["Pharmacies"])
                            r["nb_materiel_medical"] = int(row["Matériel médical"])
                            break
                st.session_state.results["pages_jaunes"] = pj_updated
                st.success("Modifications sauvegardées.")
                st.rerun()
        else:
            st.warning("Données Pages Jaunes non récupérées.")

    with tab4:
        rows_mat = []
        if isinstance(mat_data, dict):
            for cp, mats in mat_data.items():
                if mats:
                    for m in mats:
                        rows_mat.append({
                            "CP":               cp,
                            "Maternité":        m.get("nom", ""),
                            "Statut":           m.get("statut", ""),
                            "Niveau":           m.get("type_niveau", ""),
                            "Accouchements/an": m.get("nb_accouchements_an", ""),
                            "Ville":            m.get("ville", ""),
                            "Lien":             m.get("url_source", ""),
                        })
                else:
                    rows_mat.append({"CP": cp, "Maternité": "—", "Statut": "", "Niveau": "",
                                     "Accouchements/an": "", "Ville": "", "Lien": ""})
        if rows_mat:
            df_mat = pd.DataFrame(rows_mat)
            st.dataframe(df_mat, width='stretch', hide_index=True)
        else:
            st.warning("Aucune maternité trouvée.")

    with tab5:
        if sf_data:
            df = pd.DataFrame([{
                "Nom":       f"{r.get('nom','')} {r.get('prenom','')}".strip(),
                "Adresse":   r.get("adresse", ""),
                "Téléphone": r.get("telephone", ""),
                "Email":     r.get("email", ""),
                "Commune":   r.get("commune", ""),
            } for r in sf_data])
            st.dataframe(df, width='stretch', hide_index=True)
        else:
            st.warning("Aucune sage-femme trouvée.")

    with tab6:
        if lac_data:
            df = pd.DataFrame([{
                "Nom":         r.get("nom", ""),
                "Département": r.get("departement", ""),
                "Téléphone":   r.get("telephone", ""),
            } for r in lac_data])
            st.dataframe(df, width='stretch', hide_index=True)
        else:
            st.warning("Aucun lactarium trouvé.")

    with tab7:
        if pmi_data:
            df = pd.DataFrame([{
                "Nom":       r.get("nom", ""),
                "CP":        r.get("cp", ""),
                "Ville":     r.get("ville", ""),
                "Adresse":   r.get("adresse", ""),
                "Téléphone": r.get("telephone", ""),
                "Email":     r.get("email", ""),
            } for r in pmi_data])
            st.dataframe(df, width='stretch', hide_index=True)
        else:
            st.warning("Aucune PMI trouvée.")

    # ── Carte interactive ─────────────────────────────────────────────────────
    with tab8:
        if not _FOLIUM_OK:
            st.warning("Installez `folium` et `streamlit-folium` pour afficher la carte.")
        else:
            communes_flat_map = st.session_state.get("communes_flat", [])
            if "_carte" not in st.session_state:
                with st.spinner("Chargement de la carte…"):
                    st.session_state["_carte"] = _build_map(communes_flat_map, mat_data, sf_data, pmi_data)
            fmap = st.session_state["_carte"]
            if fmap:
                st.caption("🔵 Communes · 🔴 Maternités · 🟢 Sages-femmes · 🟠 PMI")
                st_folium(fmap, use_container_width=True, height=520, returned_objects=[])
            else:
                st.info("Aucune donnée géolocalisable disponible.")


    st.divider()
    if st.button("📥 Télécharger les fichiers →", type="primary"):
        st.session_state.step = 5
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 5 — Téléchargement
# ══════════════════════════════════════════════════════════════════════════════

elif st.session_state.step == 5:
    st.subheader("📥 Étape 5 — Téléchargement")
    st.success("Vos fichiers sont prêts !")

    dept_nom  = st.session_state.dept_nom
    dept_code = st.session_state.dept_code
    today     = date.today().strftime("%Y-%m-%d")
    zone_slug = f"DIP_{dept_nom or dept_code}".replace(" ", "_")[:30]

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            label="⬇️ Télécharger le fichier Excel (.xlsx)",
            data=st.session_state.xlsx_bytes,
            file_name=f"donnees_{zone_slug}_{today}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        st.caption("6 onglets : Geo · Logement · Revenus · Emploi · Etablissements · Synthese_Zone")

    with col2:
        st.download_button(
            label="⬇️ Télécharger la synthèse Word (.docx)",
            data=st.session_state.docx_bytes,
            file_name=f"synthese_{zone_slug}_{today}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True,
        )
        st.caption("8 sections : Zone · Socio-démo · Pharmacies · Maternités · Lactariums · SF · PMI · Allaitement")

    st.divider()
    st.info("""
**Étapes manuelles restantes (5–10 min) :**
1. Vérifier les cellules **N/D** dans le Excel
2. Coller la ligne **Synthese_Zone** dans `partenaires.xlsx`
3. Lancer le **publipostage Word** (`DIP_modele.docx`)
4. Ajouter les **captures maternités** + **carte Google Maps**
5. Relire et exporter en **PDF**
    """)

    st.divider()
    col_nd, col_tb = st.columns(2)
    with col_nd:
        if st.button("➕ Nouveau DIP", type="primary", use_container_width=True):
            _reset_wizard()
            st.rerun()
    with col_tb:
        if st.button("🏠 Tableau de bord", use_container_width=True):
            st.session_state.page = "dashboard"
            st.rerun()

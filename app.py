"""
Interface web — Mise à jour DIP Tire-Lait Express
Lancer : streamlit run app.py
"""

import re
import sys
import concurrent.futures
from pathlib import Path
from datetime import date

import requests
import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from scrapers.communes import get_communes_for_all_cp
from scrapers.insee import get_all_communes_data
from scrapers.pages_jaunes import get_pharmacies_and_medical
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
    initial_sidebar_state="collapsed",
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
    [data-testid="stSidebar"] { display: none; }
</style>
""", unsafe_allow_html=True)


# ── Session state init ────────────────────────────────────────────────────────

def _init():
    defaults = {
        "step": 1,
        "cp_uniques": [],
        "communes_par_cp": {},
        "dept_code": "",
        "dept_nom": "",
        "region": "",
        "results": {},
        "xlsx_bytes": None,
        "docx_bytes": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init()


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
        st.dataframe(df_cp, use_container_width=False, hide_index=True, width=200)

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
        st.session_state.dept_code = dept_code
        st.session_state.dept_nom  = dept_nom
        st.session_state.region    = region

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

    rows = []
    for cp, communes in st.session_state.communes_par_cp.items():
        noms = ", ".join(c["nom"] for c in communes) if communes else "⚠️ Non résolu"
        rows.append({"Code postal": cp, "Commune(s)": noms})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

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
    st.subheader("⚙️ Étape 3 — Collecte en cours")

    communes_par_cp = st.session_state.communes_par_cp
    cp_uniques      = st.session_state.cp_uniques
    dept_code       = st.session_state.dept_code
    dept_nom        = st.session_state.dept_nom
    region          = st.session_state.region

    # Aplatir les communes
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

    communes_pj = [{"nom": c["nom"], "cp": c.get("cp", "")} for c in communes_flat]

    # Construire le dict cp → communes pour le scraper maternités
    cp_communes_dict = {
        cp: [
            dict(c, code_insee=c.get("code_insee") or c.get("code", ""))
            for c in communes
        ]
        for cp, communes in communes_par_cp.items()
    }

    progress_bar = st.progress(0)
    status_lines = st.empty()
    log: list[str] = []
    results: dict  = {}
    total = 6

    def run_scraper(key, fn, *args):
        try:
            return key, fn(*args), None
        except Exception as e:
            return key, ([] if key != "maternites" else {}), str(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as exe:
        futures = {
            exe.submit(run_scraper, "insee",        get_all_communes_data,      communes_flat):          "insee",
            exe.submit(run_scraper, "pages_jaunes", get_pharmacies_and_medical, communes_pj):            "pages_jaunes",
            exe.submit(run_scraper, "maternites",   get_maternites_par_cp,      cp_communes_dict):       "maternites",
            exe.submit(run_scraper, "lactariums",   get_lactariums,             dept_code, region):      "lactariums",
            exe.submit(run_scraper, "sages_femmes", get_sages_femmes,           communes_pj):            "sages_femmes",
            exe.submit(run_scraper, "pmi",          get_pmi,                    dept_code, dept_nom):    "pmi",
        }
        labels = {
            "insee":        "INSEE — données socio-démographiques",
            "pages_jaunes": "Pages Jaunes — pharmacies & matériel médical",
            "maternites":   "Journal des Femmes — maternités",
            "lactariums":   "ALF — lactariums",
            "sages_femmes": "Ordre SF — sages-femmes libérales",
            "pmi":          "AlloPMI — PMI",
        }
        done = 0
        for f in concurrent.futures.as_completed(futures):
            key, data, err = f.result()
            results[key] = data
            done += 1
            icon = "✅" if not err else "❌"
            log.append(f"{icon} {labels[key]}" + (f"  _{err}_" if err else ""))
            status_lines.markdown("\n\n".join(log))
            progress_bar.progress(done / total)

    st.session_state.results = results

    # Générer les fichiers
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

    def _sum(key):
        total = 0
        for r in insee_data:
            v = r.get(key)
            if v is not None:
                try:
                    total += float(str(v).replace(",", ".").replace(" ", "").replace(" ", ""))
                except ValueError:
                    pass
        return int(total) if total else "N/D"

    # KPIs
    col1, col2, col3, col4, col5 = st.columns(5)
    pop = _sum("pop_2022")
    col1.metric("Population 2022", f"{pop:,}".replace(",", " ") if isinstance(pop, int) else "N/D")
    col2.metric("Naissances 2022", _sum("naissances_2022"))
    col3.metric("Pharmacies", sum(r.get("nb_pharmacies", 0) for r in pj_data))
    col4.metric("Sages-femmes", len(sf_data))
    col5.metric("PMI", len([p for p in pmi_data if "Erreur" not in p.get("nom", "")]))

    st.divider()

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "🏘️ Communes", "💊 Pharmacies", "🏥 Maternités", "🤱 Sages-femmes", "🥛 Lactariums", "👶 PMI"
    ])

    with tab1:
        if insee_data:
            df = pd.DataFrame([{
                "Commune":             r.get("commune", ""),
                "Population 2022":     r.get("pop_2022", ""),
                "Naissances 2022":     r.get("naissances_2022", ""),
                "Taux chômage (%)":    r.get("taux_chomage_15_64_2022", ""),
                "Médiane revenu (€)":  r.get("mediane_revenu_2021", ""),
                "Statut":              r.get("_statut", "OK"),
            } for r in insee_data])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.warning("Données INSEE non récupérées — le site a peut-être bloqué la requête. Relancez la collecte.")

    with tab2:
        if pj_data:
            df = pd.DataFrame([{
                "Commune":          r.get("commune", ""),
                "CP":               r.get("cp", ""),
                "Pharmacies":       r.get("nb_pharmacies", 0),
                "Matériel médical": r.get("nb_materiel_medical", 0),
                "Noms pharmacies":  ", ".join(r.get("noms_pharmacies", [])[:5]),
            } for r in pj_data])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.warning("Données Pages Jaunes non récupérées.")

    with tab3:
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
            st.dataframe(df_mat, use_container_width=True, hide_index=True)
        else:
            st.warning("Aucune maternité trouvée.")

    with tab4:
        if sf_data:
            df = pd.DataFrame([{
                "Nom":       f"{r.get('nom','')} {r.get('prenom','')}".strip(),
                "Adresse":   r.get("adresse", ""),
                "Téléphone": r.get("telephone", ""),
                "Email":     r.get("email", ""),
                "Commune":   r.get("commune", ""),
            } for r in sf_data])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.warning("Aucune sage-femme trouvée.")

    with tab5:
        if lac_data:
            df = pd.DataFrame([{
                "Nom":         r.get("nom", ""),
                "Département": r.get("departement", ""),
                "Téléphone":   r.get("telephone", ""),
            } for r in lac_data])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.warning("Aucun lactarium trouvé.")

    with tab6:
        if pmi_data:
            df = pd.DataFrame([{
                "Nom":       r.get("nom", ""),
                "Adresse":   r.get("adresse", ""),
                "Téléphone": r.get("telephone", ""),
                "Commune":   r.get("commune", ""),
            } for r in pmi_data])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.warning("Aucune PMI trouvée.")

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
    if st.button("🔄 Nouveau DIP", type="secondary"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

"""
Génère le fichier Excel structuré avec 6 onglets.
Chaque onglet a une ligne par commune + une ligne TOTAL/MOYENNE pondérée.
"""

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


# ── Styles ────────────────────────────────────────────────────────────────────

_HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=10)
_TOTAL_FILL  = PatternFill("solid", fgColor="BDD7EE")
_TOTAL_FONT  = Font(bold=True, size=10)
_THIN_BORDER = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin"),
)
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
_LEFT   = Alignment(horizontal="left", vertical="center", wrap_text=True)


def _write_header(ws, columns: list[str]) -> None:
    ws.append(columns)
    for col_idx, _ in enumerate(columns, 1):
        cell = ws.cell(1, col_idx)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _CENTER
        cell.border = _THIN_BORDER
    ws.row_dimensions[1].height = 40


def _style_row(ws, row_idx: int, is_total: bool = False) -> None:
    for col_idx in range(1, ws.max_column + 1):
        cell = ws.cell(row_idx, col_idx)
        cell.border = _THIN_BORDER
        cell.alignment = _LEFT
        if is_total:
            cell.font = _TOTAL_FONT
            cell.fill = _TOTAL_FILL


def _autofit(ws) -> None:
    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=10)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 2, 30)


def _safe_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ".").replace(" ", "").replace(" ", ""))
    except ValueError:
        return None


def _sum_col(rows: list[dict], key: str) -> str:
    vals = [_safe_float(r.get(key)) for r in rows]
    vals = [v for v in vals if v is not None]
    return str(round(sum(vals))) if vals else "N/D"


def _weighted_avg(rows: list[dict], key: str, weight_key: str = "pop_2022") -> str:
    total_w = 0.0
    total_v = 0.0
    for r in rows:
        v = _safe_float(r.get(key))
        w = _safe_float(r.get(weight_key))
        if v is not None and w is not None and w > 0:
            total_v += v * w
            total_w += w
    if total_w == 0:
        return "N/D"
    return str(round(total_v / total_w, 1))


# ── Onglets ────────────────────────────────────────────────────────────────────

def _onglet_geo(wb, communes_data: list[dict]) -> None:
    ws = wb.create_sheet("Geo")
    cols = [
        "Commune", "Code INSEE", "Département", "Région",
        "Population 2022", "Superficie (km²)", "Densité (hab/km²) 2022",
        "Variation pop. 2016-2022 (%)", "Solde naturel (%)", "Solde migratoire (%)",
        "Nb ménages 2022", "Naissances 2022", "Décès 2022", "Source", "Statut",
    ]
    _write_header(ws, cols)

    for row_idx, r in enumerate(communes_data, 2):
        ws.append([
            r.get("commune", ""), r.get("code_insee", ""),
            r.get("departement", ""), r.get("region", ""),
            r.get("pop_2022", ""), r.get("superficie_km2", ""),
            r.get("densite_2022", ""), r.get("var_pop_2016_2022", ""),
            r.get("solde_naturel", ""), r.get("solde_migratoire", ""),
            r.get("nb_menages_2022", ""), r.get("naissances_2022", ""),
            r.get("deces_2022", ""), r.get("_source", ""), r.get("_statut", ""),
        ])
        _style_row(ws, row_idx)

    # Ligne TOTAL
    total_row = ["TOTAL ZONE", "", "", "",
                 _sum_col(communes_data, "pop_2022"),
                 _sum_col(communes_data, "superficie_km2"),
                 _weighted_avg(communes_data, "densite_2022"),
                 _weighted_avg(communes_data, "var_pop_2016_2022"),
                 _weighted_avg(communes_data, "solde_naturel"),
                 _weighted_avg(communes_data, "solde_migratoire"),
                 _sum_col(communes_data, "nb_menages_2022"),
                 _sum_col(communes_data, "naissances_2022"),
                 _sum_col(communes_data, "deces_2022"),
                 "Calculé", ""]
    ws.append(total_row)
    _style_row(ws, ws.max_row, is_total=True)
    _autofit(ws)


def _onglet_logement(wb, communes_data: list[dict]) -> None:
    ws = wb.create_sheet("Logement")
    cols = [
        "Commune", "Nb logements 2022",
        "Résidences principales (%)", "Résidences secondaires (%)",
        "Logements vacants (%)", "Ménages propriétaires (%)",
    ]
    _write_header(ws, cols)

    for row_idx, r in enumerate(communes_data, 2):
        ws.append([
            r.get("commune", ""),
            r.get("nb_logements_2022", ""),
            r.get("part_res_principales_2022", ""),
            r.get("part_res_secondaires_2022", ""),
            r.get("part_logements_vacants_2022", ""),
            r.get("part_proprietaires_2022", ""),
        ])
        _style_row(ws, row_idx)

    ws.append([
        "TOTAL / MOY. PONDÉRÉE",
        _sum_col(communes_data, "nb_logements_2022"),
        _weighted_avg(communes_data, "part_res_principales_2022"),
        _weighted_avg(communes_data, "part_res_secondaires_2022"),
        _weighted_avg(communes_data, "part_logements_vacants_2022"),
        _weighted_avg(communes_data, "part_proprietaires_2022"),
    ])
    _style_row(ws, ws.max_row, is_total=True)
    _autofit(ws)


def _onglet_revenus(wb, communes_data: list[dict]) -> None:
    ws = wb.create_sheet("Revenus")
    cols = [
        "Commune",
        "Nb ménages fiscaux 2021", "Ménages imposés 2021 (%)",
        "Médiane revenu disponible 2021 (€)", "Taux de pauvreté 2021 (%)",
    ]
    _write_header(ws, cols)

    for row_idx, r in enumerate(communes_data, 2):
        ws.append([
            r.get("commune", ""),
            r.get("nb_menages_fiscaux_2021", ""),
            r.get("part_menages_imposes_2021", ""),
            r.get("mediane_revenu_2021", ""),
            r.get("taux_pauvrete_2021", ""),
        ])
        _style_row(ws, row_idx)

    ws.append([
        "TOTAL / MOY. PONDÉRÉE",
        _sum_col(communes_data, "nb_menages_fiscaux_2021"),
        _weighted_avg(communes_data, "part_menages_imposes_2021"),
        _weighted_avg(communes_data, "mediane_revenu_2021"),
        _weighted_avg(communes_data, "taux_pauvrete_2021"),
    ])
    _style_row(ws, ws.max_row, is_total=True)
    _autofit(ws)


def _onglet_emploi(wb, communes_data: list[dict]) -> None:
    ws = wb.create_sheet("Emploi")
    cols = [
        "Commune",
        "Emploi total 2022", "Part emploi salarié 2022 (%)",
        "Variation emploi 2016-2022 (%)",
        "Taux d'activité 15-64 ans 2022 (%)",
        "Taux de chômage 15-64 ans 2022 (%)",
    ]
    _write_header(ws, cols)

    for row_idx, r in enumerate(communes_data, 2):
        ws.append([
            r.get("commune", ""),
            r.get("emploi_total_2022", ""),
            r.get("part_emploi_salarie_2022", ""),
            r.get("var_emploi_2016_2022", ""),
            r.get("taux_activite_15_64_2022", ""),
            r.get("taux_chomage_15_64_2022", ""),
        ])
        _style_row(ws, row_idx)

    ws.append([
        "TOTAL / MOY. PONDÉRÉE",
        _sum_col(communes_data, "emploi_total_2022"),
        _weighted_avg(communes_data, "part_emploi_salarie_2022"),
        _weighted_avg(communes_data, "var_emploi_2016_2022"),
        _weighted_avg(communes_data, "taux_activite_15_64_2022"),
        _weighted_avg(communes_data, "taux_chomage_15_64_2022"),
    ])
    _style_row(ws, ws.max_row, is_total=True)
    _autofit(ws)


def _onglet_etablissements(wb, communes_data: list[dict]) -> None:
    ws = wb.create_sheet("Etablissements")
    cols = [
        "Commune",
        "Nb établissements actifs fin 2023",
        "Agriculture (%)", "Industrie (%)", "Construction (%)",
        "Commerce/Transp./Services (%)", "Admin./Santé/Action soc. (%)",
        "1 à 9 salariés (%)", "10 salariés ou + (%)",
    ]
    _write_header(ws, cols)

    for row_idx, r in enumerate(communes_data, 2):
        ws.append([
            r.get("commune", ""),
            r.get("nb_etab_actifs_2023", ""),
            r.get("part_agriculture_2023", ""),
            r.get("part_industrie_2023", ""),
            r.get("part_construction_2023", ""),
            r.get("part_commerce_transp_2023", ""),
            r.get("part_admin_sante_2023", ""),
            r.get("part_etab_1_9_sal_2023", ""),
            r.get("part_etab_10_sal_plus_2023", ""),
        ])
        _style_row(ws, row_idx)

    ws.append([
        "TOTAL / MOY. PONDÉRÉE",
        _sum_col(communes_data, "nb_etab_actifs_2023"),
        _weighted_avg(communes_data, "part_agriculture_2023"),
        _weighted_avg(communes_data, "part_industrie_2023"),
        _weighted_avg(communes_data, "part_construction_2023"),
        _weighted_avg(communes_data, "part_commerce_transp_2023"),
        _weighted_avg(communes_data, "part_admin_sante_2023"),
        _weighted_avg(communes_data, "part_etab_1_9_sal_2023"),
        _weighted_avg(communes_data, "part_etab_10_sal_plus_2023"),
    ])
    _style_row(ws, ws.max_row, is_total=True)
    _autofit(ws)


def _onglet_synthese(wb, communes_data: list[dict], pj_data: list[dict],
                     maternites: list[dict], lactariums: list[dict],
                     sages_femmes: list[dict], pmi: list[dict],
                     zone_nom: str, dept_code: str, dept_nom: str, region: str) -> None:
    ws = wb.create_sheet("Synthese_Zone")
    rows = [
        ["VARIABLE", "VALEUR", "SOURCE / MILLÉSIME"],
        ["Zone", zone_nom, "Saisie"],
        ["Département", f"{dept_nom} ({dept_code})", "Saisie"],
        ["Région", region, "Saisie"],
        ["Nb communes", len(communes_data), "Calculé"],
        ["Population totale 2022", _sum_col(communes_data, "pop_2022"), "INSEE RP 2022"],
        ["Superficie totale (km²)", _sum_col(communes_data, "superficie_km2"), "INSEE RP 2022"],
        ["Densité moy. pondérée (hab/km²)", _weighted_avg(communes_data, "densite_2022"), "INSEE RP 2022"],
        ["Variation pop. moy. 2016-2022 (%)", _weighted_avg(communes_data, "var_pop_2016_2022"), "INSEE RP 2022"],
        ["Nb ménages 2022", _sum_col(communes_data, "nb_menages_2022"), "INSEE RP 2022"],
        ["Naissances domiciliées 2022", _sum_col(communes_data, "naissances_2022"), "INSEE État civil 2022"],
        ["Décès domiciliés 2022", _sum_col(communes_data, "deces_2022"), "INSEE État civil 2022"],
        ["Nb logements 2022", _sum_col(communes_data, "nb_logements_2022"), "INSEE RP 2022"],
        ["Part résidences principales (%)", _weighted_avg(communes_data, "part_res_principales_2022"), "INSEE RP 2022"],
        ["Part résidences secondaires (%)", _weighted_avg(communes_data, "part_res_secondaires_2022"), "INSEE RP 2022"],
        ["Part logements vacants (%)", _weighted_avg(communes_data, "part_logements_vacants_2022"), "INSEE RP 2022"],
        ["Part ménages propriétaires (%)", _weighted_avg(communes_data, "part_proprietaires_2022"), "INSEE RP 2022"],
        ["Nb ménages fiscaux 2021", _sum_col(communes_data, "nb_menages_fiscaux_2021"), "INSEE DGI 2021"],
        ["Part ménages imposés 2021 (%)", _weighted_avg(communes_data, "part_menages_imposes_2021"), "INSEE DGI 2021"],
        ["Médiane revenu disponible 2021 (€)", _weighted_avg(communes_data, "mediane_revenu_2021"), "INSEE Filosofi 2021"],
        ["Taux de pauvreté 2021 (%)", _weighted_avg(communes_data, "taux_pauvrete_2021"), "INSEE Filosofi 2021"],
        ["Emploi total 2022", _sum_col(communes_data, "emploi_total_2022"), "INSEE RP 2022"],
        ["Part emploi salarié 2022 (%)", _weighted_avg(communes_data, "part_emploi_salarie_2022"), "INSEE RP 2022"],
        ["Variation emploi 2016-2022 (%)", _weighted_avg(communes_data, "var_emploi_2016_2022"), "INSEE RP 2022"],
        ["Taux d'activité 15-64 ans 2022 (%)", _weighted_avg(communes_data, "taux_activite_15_64_2022"), "INSEE RP 2022"],
        ["Taux de chômage 15-64 ans 2022 (%)", _weighted_avg(communes_data, "taux_chomage_15_64_2022"), "INSEE RP 2022"],
        ["Nb établissements actifs 2023", _sum_col(communes_data, "nb_etab_actifs_2023"), "INSEE REE 2023"],
        ["Nb pharmacies (total zone)", sum(r.get("nb_pharmacies", 0) for r in pj_data), "Pages Jaunes"],
        ["Nb magasins matériel médical (total zone)", sum(r.get("nb_materiel_medical", 0) for r in pj_data), "Pages Jaunes"],
        ["Nb maternités", len([m for m in maternites if m.get("nom") and "Erreur" not in m["nom"]]), "Journal des Femmes"],
        ["Nb lactariums sur zone", len([l for l in lactariums if "aucun" not in l.get("nom", "").lower()]), "ALF"],
        ["Nb sages-femmes libérales (dédoublonné)", len(sages_femmes), "Ordre SF"],
        ["Nb PMI", len([p for p in pmi if p.get("nom") and "Erreur" not in p["nom"]]), "AlloPMI"],
    ]

    for row_idx, row in enumerate(rows, 1):
        ws.append(row)
        cell = ws.cell(row_idx, 1)
        cell.border = _THIN_BORDER
        ws.cell(row_idx, 2).border = _THIN_BORDER
        ws.cell(row_idx, 3).border = _THIN_BORDER
        if row_idx == 1:
            for col in range(1, 4):
                c = ws.cell(row_idx, col)
                c.font = _HEADER_FONT
                c.fill = _HEADER_FILL
                c.alignment = _CENTER

    _autofit(ws)


# ── Point d'entrée ────────────────────────────────────────────────────────────

def generate_excel(
    output_path: str,
    zone_nom: str,
    dept_code: str,
    dept_nom: str,
    region: str,
    communes_data: list[dict],
    pj_data: list[dict],
    maternites: list[dict],
    lactariums: list[dict],
    sages_femmes: list[dict],
    pmi: list[dict],
) -> None:
    """Génère le fichier Excel avec 6 onglets."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # supprimer la feuille vide par défaut

    _onglet_geo(wb, communes_data)
    _onglet_logement(wb, communes_data)
    _onglet_revenus(wb, communes_data)
    _onglet_emploi(wb, communes_data)
    _onglet_etablissements(wb, communes_data)
    _onglet_synthese(wb, communes_data, pj_data, maternites, lactariums, sages_femmes, pmi,
                     zone_nom, dept_code, dept_nom, region)

    wb.save(output_path)

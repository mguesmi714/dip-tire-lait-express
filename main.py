"""
Mise à jour DIP — Tire-Lait Express
Usage : python main.py
"""

import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import date

from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

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

console = Console()


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — Extraction et traitement des codes postaux
# ══════════════════════════════════════════════════════════════════════════════

def phase1_extraire_codes_postaux() -> tuple[list[str], dict]:
    """
    Phase 1 :
    1. L'utilisateur colle le contenu de l'email
    2. Extraction automatique des codes postaux (regex)
    3. Dédoublonnage + affichage
    4. Confirmation ou ajout manuel
    5. Scraping annuaire-administration.com → communes

    Returns:
        (codes_postaux_uniques, communes_par_cp)
    """
    console.print(Panel(
        "[bold cyan]PHASE 1 — Extraction des codes postaux[/bold cyan]\n"
        "Collez le contenu de l'email (codes postaux + noms)\n"
        "Appuyez sur [bold]Entrée deux fois[/bold] pour valider.",
        title="Mise à jour DIP"
    ))

    # ── Étape 1 : Saisie du contenu email ─────────────────────────────────────
    lines = []
    empty_count = 0
    while True:
        line = input()
        if line.strip() == "":
            empty_count += 1
            if empty_count >= 2:
                break
        else:
            empty_count = 0
            lines.append(line)

    contenu_email = "\n".join(lines)

    # ── Étape 2 : Extraction des codes postaux ────────────────────────────────
    # Regex : 5 chiffres commençant par 0-9 (codes postaux français)
    tous_cp = re.findall(r"\b([0-9]{5})\b", contenu_email)

    # Filtrer les faux positifs (ex: années comme 2024, numéros de téléphone)
    cp_valides = [
        cp for cp in tous_cp
        if cp[:2] not in ("00", "99")           # pas de CP fictifs
        and not cp.startswith("0")              # les CP français ne commencent pas par 0 (sauf 01-09)
        or cp[:2] in (                           # départements 01-09 valides
            "01", "02", "03", "04", "05", "06", "07", "08", "09"
        )
    ]
    # Re-filtrer : les CP français sont entre 01000 et 97680
    cp_valides = [cp for cp in tous_cp if 1000 <= int(cp) <= 97680]

    # Dédoublonnage en conservant l'ordre d'apparition
    seen: set[str] = set()
    cp_uniques: list[str] = []
    for cp in cp_valides:
        if cp not in seen:
            seen.add(cp)
            cp_uniques.append(cp)

    # ── Étape 3 : Affichage pour confirmation ─────────────────────────────────
    console.print()
    if not cp_uniques:
        console.print("[yellow]Aucun code postal détecté dans le texte collé.[/yellow]")
    else:
        t = Table(title=f"{len(cp_uniques)} code(s) postal/aux extrait(s)", show_lines=True)
        t.add_column("#", style="dim", width=4)
        t.add_column("Code postal", style="cyan bold")
        for i, cp in enumerate(cp_uniques, 1):
            t.add_row(str(i), cp)
        console.print(t)

    # ── Étape 4 : Ajout manuel ou confirmation ────────────────────────────────
    while True:
        choix = Prompt.ask(
            "\nQue voulez-vous faire ?",
            choices=["confirmer", "ajouter", "supprimer", "recommencer"],
            default="confirmer"
        )

        if choix == "confirmer":
            if not cp_uniques:
                console.print("[red]Aucun code postal. Ajoutez-en au moins un.[/red]")
                choix = "ajouter"
            else:
                break

        if choix == "ajouter":
            nouveaux = Prompt.ask(
                "Entrez les codes postaux à ajouter (séparés par des virgules ou espaces)"
            )
            for cp in re.findall(r"\b\d{5}\b", nouveaux):
                if cp not in seen and 1000 <= int(cp) <= 97680:
                    seen.add(cp)
                    cp_uniques.append(cp)
            console.print(f"[green]Liste mise à jour : {', '.join(cp_uniques)}[/green]")

        elif choix == "supprimer":
            a_suppr = Prompt.ask(
                "Entrez les codes postaux à supprimer (séparés par des virgules)"
            )
            for cp in re.findall(r"\b\d{5}\b", a_suppr):
                if cp in seen:
                    seen.discard(cp)
                    cp_uniques.remove(cp)
            console.print(f"[green]Liste mise à jour : {', '.join(cp_uniques)}[/green]")

        elif choix == "recommencer":
            return phase1_extraire_codes_postaux()

    # ── Étape 5 : Résolution des communes via annuaire-administration.com ──────
    console.print(
        f"\n[bold yellow]Résolution des communes pour {len(cp_uniques)} code(s) postal/aux...[/bold yellow]"
    )

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        task = progress.add_task("Scraping annuaire-administration.com...", total=None)
        communes_par_cp = get_communes_for_all_cp(cp_uniques)
        progress.update(task, completed=True, description="[green]✓ Communes résolues[/green]")

    # Afficher le résultat
    console.print()
    t2 = Table(title="Communes associées aux codes postaux", show_lines=True)
    t2.add_column("Code postal", style="cyan bold")
    t2.add_column("Commune(s)", style="white")
    for cp, communes in communes_par_cp.items():
        noms = ", ".join(c["nom"] for c in communes) if communes else "[red]Non résolu[/red]"
        t2.add_row(cp, noms)
    console.print(t2)

    if not Confirm.ask("\nCes communes sont-elles correctes ?", default=True):
        console.print("[yellow]Relancez le script et corrigez les codes postaux.[/yellow]")
        sys.exit(0)

    return cp_uniques, communes_par_cp


# ══════════════════════════════════════════════════════════════════════════════
# SAISIE INFOS DE ZONE
# ══════════════════════════════════════════════════════════════════════════════

def demander_infos_zone(cp_uniques: list[str]) -> tuple[str, str, str, str]:
    """Demande les infos de zone (inférées depuis les CP si possible)."""
    console.print()
    dept_infere = cp_uniques[0][:2] if cp_uniques else ""

    zone_nom = Prompt.ask("[bold]Nom de la zone[/bold] (ex. QUIMPER N° 29+56)")
    dept_code = Prompt.ask("[bold]Code département[/bold]", default=dept_infere)
    dept_nom = Prompt.ask("[bold]Nom du département[/bold]")
    region = Prompt.ask("[bold]Région[/bold]")
    return zone_nom, dept_code, dept_nom, region


# ══════════════════════════════════════════════════════════════════════════════
# COLLECTE PARALLÈLE
# ══════════════════════════════════════════════════════════════════════════════

def collecter_donnees(
    communes_par_cp: dict,
    cp_uniques: list[str],
    dept_code: str,
    dept_nom: str,
    region: str,
) -> dict:
    """Lance tous les scrapers. Maternités par CP (Phase 3)."""

    # Aplatir la liste des communes pour les scrapers qui en ont besoin
    communes_flat: list[dict] = []
    seen_cog: set[str] = set()
    for cp, communes in communes_par_cp.items():
        for c in communes:
            cog = c.get("code_insee", c.get("code", c["nom"]))
            if cog not in seen_cog:
                seen_cog.add(cog)
                if "departement" not in c:
                    c["departement"] = {"code": dept_code, "nom": dept_nom}
                if "region" not in c:
                    c["region"] = {"code": "", "nom": region}
                c.setdefault("cp", cp)
                communes_flat.append(c)

    communes_pj = [{"nom": c["nom"], "cp": c.get("cp", "")} for c in communes_flat]

    results: dict = {}

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        tasks_labels = {
            "insee":        "INSEE — données socio-démographiques...",
            "pages_jaunes": "Pages Jaunes — pharmacies et matériel médical...",
            "maternites":   "Journal des Femmes — maternités par code postal...",
            "lactariums":   "ALF — lactariums...",
            "sages_femmes": "Ordre SF — sages-femmes...",
            "pmi":          "AlloPMI — PMI...",
        }
        ptasks = {k: progress.add_task(v, total=None) for k, v in tasks_labels.items()}

        def run(key, fn, *args):
            try:
                data = fn(*args)
                return key, data, None
            except Exception as e:
                return key, [], str(e)

        with ThreadPoolExecutor(max_workers=3) as exe:
            futures = [
                exe.submit(run, "insee",        get_all_communes_data,       communes_flat),
                exe.submit(run, "pages_jaunes", get_pharmacies_and_medical,  communes_pj),
                exe.submit(run, "maternites",   get_maternites_par_cp,       cp_uniques),
                exe.submit(run, "lactariums",   get_lactariums,              dept_code, region),
                exe.submit(run, "sages_femmes", get_sages_femmes,            dept_code, cp_uniques),
                exe.submit(run, "pmi",          get_pmi,                     dept_code, dept_nom, cp_uniques, communes_pj),
            ]
            for f in as_completed(futures):
                key, data, err = f.result()
                results[key] = data
                label = tasks_labels[key]
                if err:
                    progress.update(ptasks[key], description=f"[red]✗ {label} — {err}[/red]")
                else:
                    progress.update(ptasks[key], description=f"[green]✓[/green] {label}")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# RÉSUMÉ TERMINAL
# ══════════════════════════════════════════════════════════════════════════════

def afficher_resume(zone_nom: str, cp_uniques: list[str], results: dict) -> None:
    console.print()
    insee_data  = results.get("insee", [])
    pj_data     = results.get("pages_jaunes", [])
    mat_data    = results.get("maternites", {})   # dict cp → list
    sf_data     = results.get("sages_femmes", [])
    pmi_data    = results.get("pmi", [])

    def _sum(key):
        total = 0
        for r in insee_data:
            v = r.get(key)
            if v is not None:
                try:
                    total += float(str(v).replace(",", ".").replace(" ", ""))
                except ValueError:
                    pass
        return str(int(total)) if total else "N/D"

    total_mat = sum(len(v) for v in mat_data.values()) if isinstance(mat_data, dict) else len(mat_data)

    t = Table(title=f"Résumé — {zone_nom}", show_lines=True)
    t.add_column("Indicateur", style="cyan")
    t.add_column("Valeur", style="white bold")
    t.add_row("Codes postaux", ", ".join(cp_uniques))
    t.add_row("Population totale 2022", _sum("pop_2022"))
    t.add_row("Naissances 2022", _sum("naissances_2022"))
    t.add_row("Pharmacies", str(sum(r.get("nb_pharmacies", 0) for r in pj_data)))
    t.add_row("Matériel médical", str(sum(r.get("nb_materiel_medical", 0) for r in pj_data)))
    t.add_row("Maternités trouvées", str(total_mat))
    t.add_row("Sages-femmes (dédoublonné)", str(len(sf_data)))
    t.add_row("PMI", str(len([p for p in pmi_data if "Erreur" not in p.get("nom", "")])))

    manquantes = [r.get("commune", "") for r in insee_data if r.get("_statut", "OK") != "OK"]
    if manquantes:
        t.add_row("[red]Communes sans données INSEE[/red]", ", ".join(manquantes))

    console.print(t)

    # Détail maternités par CP
    if isinstance(mat_data, dict):
        console.print()
        t2 = Table(title="Maternités par code postal", show_lines=True)
        t2.add_column("CP", style="cyan", width=8)
        t2.add_column("Maternité(s)", style="white")
        for cp, mats in mat_data.items():
            if mats:
                val = "\n".join(
                    f"{m['nom']}" + (f" ({m['statut']})" if m.get("statut") else "")
                    for m in mats
                )
            else:
                val = "[dim]—[/dim]"
            t2.add_row(cp, val)
        console.print(t2)


# ══════════════════════════════════════════════════════════════════════════════
# GÉNÉRATION DES FICHIERS
# ══════════════════════════════════════════════════════════════════════════════

def generer_fichiers(
    zone_nom: str, dept_code: str, dept_nom: str, region: str,
    communes_par_cp: dict, results: dict
) -> None:
    today = date.today().strftime("%Y-%m-%d")
    zone_slug = re.sub(r"[^\w]", "_", zone_nom)[:30]
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)

    xlsx_path = str(output_dir / f"donnees_DIP_{zone_slug}_{today}.xlsx")
    docx_path = str(output_dir / f"synthese_DIP_{zone_slug}_{today}.docx")

    # Aplatir les maternités (dict cp→list) en liste pour les exports
    mat_raw = results.get("maternites", {})
    maternites_flat = []
    if isinstance(mat_raw, dict):
        for cp, mats in mat_raw.items():
            for m in mats:
                m.setdefault("_cp_recherche", cp)
                maternites_flat.append(m)
    else:
        maternites_flat = mat_raw

    communes_flat = [c for communes in communes_par_cp.values() for c in communes]

    console.print("\n[bold yellow]Génération des fichiers...[/bold yellow]")

    generate_excel(
        output_path=xlsx_path,
        zone_nom=zone_nom,
        dept_code=dept_code,
        dept_nom=dept_nom,
        region=region,
        communes_data=results.get("insee", []),
        pj_data=results.get("pages_jaunes", []),
        maternites=maternites_flat,
        lactariums=results.get("lactariums", []),
        sages_femmes=results.get("sages_femmes", []),
        pmi=results.get("pmi", []),
    )
    console.print(f"[green]✓ Excel :[/green] {xlsx_path}")

    generate_word(
        output_path=docx_path,
        zone_nom=zone_nom,
        dept_code=dept_code,
        dept_nom=dept_nom,
        region=region,
        communes=communes_flat,
        communes_data=results.get("insee", []),
        pj_data=results.get("pages_jaunes", []),
        maternites=maternites_flat,
        lactariums=results.get("lactariums", []),
        sages_femmes=results.get("sages_femmes", []),
        pmi=results.get("pmi", []),
    )
    console.print(f"[green]✓ Word  :[/green] {docx_path}")

    console.print(Panel(
        f"[bold green]Terminé ![/bold green]\n\n"
        f"Fichiers dans : [cyan]{output_dir}[/cyan]\n\n"
        "Étapes manuelles restantes :\n"
        "  1. Vérifier les cellules N/D dans le .xlsx\n"
        "  2. Coller la ligne Synthese_Zone dans partenaires.xlsx\n"
        "  3. Lancer le publipostage Word (DIP_modele.docx)\n"
        "  4. Ajouter captures maternités + carte Google Maps\n"
        "  5. Relire et exporter en PDF",
        title="Résultat"
    ))


# ══════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    # Phase 1 : extraction codes postaux → communes
    cp_uniques, communes_par_cp = phase1_extraire_codes_postaux()

    # Infos de zone
    zone_nom, dept_code, dept_nom, region = demander_infos_zone(cp_uniques)

    # Collecte toutes sources en parallèle (incl. Phase 3 maternités)
    console.print("\n[bold yellow]Lancement de la collecte...[/bold yellow]\n")
    results = collecter_donnees(communes_par_cp, cp_uniques, dept_code, dept_nom, region)

    # Résumé
    afficher_resume(zone_nom, cp_uniques, results)

    # Génération fichiers
    generer_fichiers(zone_nom, dept_code, dept_nom, region, communes_par_cp, results)


if __name__ == "__main__":
    main()

"""
Fallback SIRENE pour pharmacies et materiel medical.
Source : recherche-entreprises.api.gouv.fr (donnees officielles INSEE/INPI)

NAF 47.73Z : Commerce de detail de produits pharmaceutiques (pharmacies)
NAF 47.74Z : Commerce de detail articles medicaux et orthopediques (mat. medical)

Les SPFPL (societes de participations financieres) sont exclues : ce sont des
holdings qui ne gerent pas d'officine et faussent le compte.
Dedoublonnage par adresse : evite de compter deux SIRET pour le meme local.
"""

import re
import time
import requests
from config import REQUEST_DELAY

_SIRENE_SEARCH = "https://recherche-entreprises.api.gouv.fr/search"
_HEADERS       = {"User-Agent": "DIP-Tire-Lait-Express/1.0 (educational project)"}


def _is_holding(nom: str) -> bool:
    """Exclut les SPFPL et societes de participations (pas des officines)."""
    n = nom.upper()
    return n.startswith("SPFPL") or "PARTICIPATIONS FINANCIERES" in n


def _is_audio(nom: str) -> bool:
    """
    Exclut les audioprothesistes du comptage 'materiel medical'.
    PJ les classe dans une categorie separee ('Audioprothesistes').
    """
    n = nom.upper()
    return any(kw in n for kw in [
        "AUDITION", "AUDILAB", "AUDIOPROTHESE", "ACOUSTICIA",
        "AMPLIFON", "AUDIOLYS", "AUDILAB", "AUDIKA", "ENTENDRE",
        "KRYS AUDIO", "MASSON AUDITION", "AUDIO CONSEIL",
    ])


def _nom_affiche(nom_complet: str) -> str:
    """
    Extrait le nom commercial depuis le nom SIRENE.
    'SELARL PHARMACIE ROUSSEAU (PHARMACIE DE TOHANNIC)' -> 'PHARMACIE DE TOHANNIC'
    """
    m = re.search(r'\(([^)]+)\)', nom_complet)
    if m:
        return m.group(1).strip()
    return nom_complet.strip()


def _sirene_query(insee: str, cp: str, naf: str,
                  exclude_audio: bool = False) -> tuple[int, list[dict]]:
    """
    Retourne (count, etablissements) pour un code NAF donne.
    Filtre : actifs uniquement, sans SPFPL, dedoublonnage par adresse.
    exclude_audio : exclut les audioprothesistes (pour NAF 47.74Z)
    """
    seen_adr: set[str] = set()
    etablissements: list[dict] = []
    params: dict = {"activite_principale": naf, "page": 1, "per_page": 25}
    if insee:
        params["code_commune"] = insee
    else:
        params["code_postal"] = cp

    while True:
        try:
            time.sleep(REQUEST_DELAY)
            r = requests.get(_SIRENE_SEARCH, params=params,
                             headers=_HEADERS, timeout=15)
            if not r.ok:
                break
            data = r.json()
            for ent in data.get("results", []):
                nom_ent = ent.get("nom_complet", "") or "?"
                if _is_holding(nom_ent):
                    continue
                if exclude_audio and _is_audio(nom_ent):
                    continue
                for e in ent.get("matching_etablissements", []):
                    if e.get("etat_administratif", "A") != "A":
                        continue
                    adr_raw = (e.get("adresse") or "").upper().strip()
                    if adr_raw in seen_adr:
                        continue
                    if adr_raw:
                        seen_adr.add(adr_raw)
                    cp_e    = e.get("code_postal", "")
                    city_e  = e.get("libelle_commune", "")
                    adresse = ", ".join(filter(None, [
                        e.get("adresse", "").strip(),
                        f"{cp_e} {city_e}".strip(),
                    ]))
                    etablissements.append({
                        "nom":     _nom_affiche(nom_ent),
                        "adresse": adresse,
                    })
            if params["page"] >= data.get("total_pages", 1):
                break
            params["page"] += 1
        except Exception:
            break

    return len(etablissements), etablissements[:10]


def get_pharmacies_and_medical(communes: list[dict]) -> list[dict]:
    source_date = time.strftime("%d/%m/%Y")
    results = []

    for c in communes:
        nom   = c.get("nom", "")
        cp    = c.get("cp", "")
        insee = c.get("code_insee", "")

        nb_ph,  etab_ph = _sirene_query(insee, cp, "47.73Z")
        nb_mm,  etab_mm = _sirene_query(insee, cp, "47.74Z", exclude_audio=True)

        results.append({
            "commune":                   nom,
            "cp":                        cp,
            "nb_pharmacies":             nb_ph,
            "noms_pharmacies":           [e["nom"]     for e in etab_ph],
            "adresses_pharmacies":       [e["adresse"] for e in etab_ph],
            "nb_materiel_medical":       nb_mm,
            "noms_materiel_medical":     [e["nom"]     for e in etab_mm],
            "adresses_materiel_medical": [e["adresse"] for e in etab_mm],
            "_source": f"SIRENE — NAF 47.73Z pharmacies, 47.74Z mat. medical — {source_date}",
        })
        print(f"[SIRENE] {nom}: ph={nb_ph}, mag={nb_mm}")

    return results

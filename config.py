INSEE_COMPARATEUR_URL = "https://www.insee.fr/fr/statistiques/zones/1405599"
INSEE_COMPARATEUR_API = "https://www.insee.fr/fr/statistiques/zones/1405599#graphique-figure"

PAGES_JAUNES_BASE = "https://www.pagesjaunes.fr/annuaire/chercherlespros"

MATERNITES_URL = "https://www.journaldesfemmes.fr/maman/maternite"

LACTARIUMS_URL = "https://association-des-lactariums-de-france.fr/liste-et-carte-des-lactariums/"

SAGES_FEMMES_URL = "https://www.ordre-sages-femmes.fr/patient-e-s/trouver-une-sage-femme/"

PMI_URL = "https://allopmi.fr/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_DELAY = 1.5

# Table de correspondance partielle Code Postal → Code COG INSEE (5 chiffres)
# Format : "CP" -> ["COG1", "COG2", ...]  (un CP peut couvrir plusieurs communes)
# Complétée à la volée via l'API géo INSEE si absente
CP_TO_COG: dict[str, list[str]] = {
    "42110": ["42095", "42057", "42315"],  # Feurs, Civens, Valeille
    "42300": ["42186", "42128", "42329"],  # Roanne, Mably, Villerest
    "42720": ["42015", "42026"],           # La Bénisson-Dieu, Briennon
    "42310": ["42015", "42026"],
    "42820": ["42008"],                     # Ambierle
    "42330": ["42277"],                     # Saint-Bonnet-les-Oules
    "42840": ["42277"],
}

GEO_API_URL = "https://geo.api.gouv.fr/communes?codePostal={cp}&fields=code,nom,departement,region&format=json"

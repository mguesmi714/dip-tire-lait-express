"""
Scraper pharmacies et materiel medical — Pages Jaunes
Moteur : camoufox (Firefox stealth), headless

Logique :
  1. Lire le total "X resultats" affiche par Pages Jaunes
  2. Si "couvre" absent -> retourner le total + noms/adresses page 1
  3. Sinon : paginer et exclure les cartes "couvre"

Extraction : evaluate() JS en priorite (plus fiable), inner_text en fallback
Fallback global : OSM + SIRENE si Pages Jaunes inaccessible
"""

import re
import time
import unicodedata
from urllib.parse import quote_plus
import requests

from config import REQUEST_DELAY

_PER_PAGE = 20

# Mapping type de recherche → slug categorie PJ (/annuaire/{ville}-{dept}/{slug})
_CATEGORIE_SLUGS = {
    "pharmacie":               "pharmacies",
    "magasin materiel medical": "materiel-medical",
}


def _commune_slug(commune: str) -> str:
    """'Noyal-Muzillac' -> 'noyal-muzillac'"""
    s = commune.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _annuaire_url(commune: str, cp: str, categorie: str, debut: int = 0) -> str:
    """
    URL format categories PJ : /annuaire/{commune-slug}-{dept}/{categorie}
    Ex: https://www.pagesjaunes.fr/annuaire/vannes-56/pharmacies
    """
    dept = cp[:2]
    slug = _commune_slug(commune)
    base = f"https://www.pagesjaunes.fr/annuaire/{slug}-{dept}/{categorie}"
    return f"{base}?debut={debut}" if debut else base


def _pj_url_search(quoi: str, commune: str, cp: str, debut: int = 0) -> str:
    """URL de recherche generique (fallback si slug categorie inconnu)."""
    lieu = f"{commune} ({cp})"
    return (f"https://www.pagesjaunes.fr/annuaire/chercherlespros"
            f"?quoiqui={quote_plus(quoi)}"
            f"&ou={quote_plus(lieu)}&univers=pagesjaunes&idOu="
            f"&debut={debut}")


def _is_blocked(text: str) -> bool:
    low = text.lower()
    return ("cf-challenge"         in low
            or "just a moment"     in low
            or "enable javascript" in low
            or "checking your browser" in low
            or "verify you are human"  in low
            or "attaques informatiques" in low
            or ("challenge" in low and "pagesjaunes" in low))


def _get_total(text: str) -> int:
    # Pattern visible: "39 résultats" (existing)
    m = re.search(r'(\d[\d \xa0\s]*)\s{0,5}r[eé]sultat', text, re.I)
    if m:
        digits = re.sub(r'[^\d]', '', m.group(1))
        if digits:
            return int(digits)

    # Fallbacks: data-total attributes in pagination or listing
    m2 = re.search(r'data-total=["\']?(\d+)["\']?', text)
    if m2:
        return int(m2.group(1))

    # JSON-LD or inline JS with totalResults
    m3 = re.search(r'"totalResults"\s*:\s*(\d+)', text)
    if m3:
        return int(m3.group(1))

    # aria-labels or other phrases
    m4 = re.search(r'Afficher les\s*(\d+)\s*résult', text, re.I)
    if m4:
        return int(m4.group(1))

    return 0


# ── Extraction JS structuree ───────────────────────────────────────────────────

_JS_EXTRACT = """() => {
    // Cartes PJ : essayer li.bi-item en premier (structure la plus courante)
    const CARD_SELS = [
        'li.bi-item', 'li[class*="bi-item"]', 'li[class*="pro-item"]',
        'article', '[class*="bi-profile"]', '[class*="listing-item"]'
    ];
    let cards = [];
    for (const sel of CARD_SELS) {
        const found = Array.from(document.querySelectorAll(sel));
        if (found.length > 0) { cards = found; break; }
    }

    return cards.map(card => {
        const rawText = card.innerText || '';
        if (rawText.toLowerCase().includes('couvre')) return null;

        // Nom : selecteurs PJ specifiques puis h2/h3 generique
        let nom = '';
        const NOM_SELS = [
            '.bi-denomination a', 'h2.denomination', '.denomination a',
            '.denomination', '[class*="denomination"]', 'h2 a', 'h2', 'h3'
        ];
        for (const sel of NOM_SELS) {
            const el = card.querySelector(sel);
            if (el && el.innerText.trim()) {
                nom = el.innerText.trim().split('\\n')[0].trim();
                break;
            }
        }

        // Adresse : selecteur .bi-address / address, puis fallback lignes
        let adresse = '';
        const adrEl = card.querySelector(
            '.bi-address, address.bi-address, [class*="bi-address"], address, [class*="localite"]'
        );
        if (adrEl) {
            // Reconstruire "Rue, CP Ville" depuis les spans si disponibles
            const rueEl   = adrEl.querySelector('[class*="adresse"]:not([class*="bi"])');
            const cpEl    = adrEl.querySelector('[class*="cp"]');
            const villeEl = adrEl.querySelector('[class*="localite"], [class*="ville"]');
            if (rueEl && cpEl) {
                adresse = [rueEl.innerText.trim(),
                           cpEl.innerText.trim() + (villeEl ? ' ' + villeEl.innerText.trim() : '')]
                          .filter(p => p).join(', ');
            } else {
                adresse = adrEl.innerText.trim().replace(/\\n+/g, ', ');
            }
        }

        // Fallback adresse via lignes avec CP
        if (!adresse) {
            const lines = rawText.split('\\n').map(l => l.trim()).filter(l => l);
            for (let i = 0; i < lines.length; i++) {
                if (/\\b\\d{5}\\b/.test(lines[i])) {
                    if (i > 0 && (/^\\d+/.test(lines[i-1]) ||
                        /\\b(rue|avenue|boulevard|impasse|chemin|place|all[eé]e|allee|route|voie|passage|cit[eé]|cite|square|hameau|lotissement|zone)\\b/i.test(lines[i-1]))) {
                        adresse = lines[i-1] + ', ' + lines[i];
                    } else {
                        adresse = lines[i];
                    }
                    break;
                }
            }
        }

        // Fallback nom
        if (!nom) {
            const lines = rawText.split('\\n').map(l => l.trim()).filter(l => l && l.length > 2);
            nom = lines[0] || '';
        }

        return { nom, adresse, text: rawText };
    }).filter(c => c !== null);
}"""


def _extract_cards_js(page) -> list[dict] | None:
    """
    Tente l'extraction via evaluate(). Retourne None si echec.
    Chaque element : {"nom": str, "adresse": str, "text": str}
    """
    try:
        return page.evaluate(_JS_EXTRACT)
    except Exception as e:
        print(f"[PJ] JS extract error: {e}")
        return None


def _card_nom_adresse(raw: str) -> tuple[str, str]:
    """Fallback text-based — extrait (nom, adresse) depuis inner_text d'une carte."""
    lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
    if not lines:
        return "", ""
    nom = lines[0]
    adresse = ""
    for i, line in enumerate(lines):
        if re.search(r'\b\d{5}\b', line):
            if i > 0:
                prev = lines[i - 1]
                is_street = (
                    re.match(r'^\d+', prev) or
                    re.search(r'\b(rue|avenue|boulevard|impasse|chemin|place|'
                              r'all[eé]e|route|voie|passage|cit[eé]|square|'
                              r'hameau|lotissement|zone)\b', prev, re.I)
                )
                adresse = f"{prev}, {line}" if is_street else line
            else:
                adresse = line
            break
    if not adresse and len(lines) > 1:
        adresse = lines[1]
    return nom, adresse


def _extract_cards(page, quoi: str, commune: str, cp: str,
                   base_url: str = "") -> tuple[int, list[dict]]:
    """
    Retourne (count, etablissements).
    etablissements = list[{"nom": str, "adresse": str}] (max 10)
    base_url : URL de base pour la pagination (sans ?debut=)
    """
    text     = page.inner_text("body")
    total_pj = _get_total(text)
    if total_pj == 0:
        return 0, []

    has_couvre  = "couvre" in text.lower()
    count       = 0
    etablissements: list[dict] = []
    zero_streak = 0

    cat_slug = _CATEGORIE_SLUGS.get(quoi)

    for debut in range(0, min(total_pj + _PER_PAGE, 500), _PER_PAGE):
        if debut > 0:
            try:
                time.sleep(REQUEST_DELAY)
                # Utiliser le meme format d'URL que la page initiale pour la pagination
                if cat_slug:
                    next_url = _annuaire_url(commune, cp, cat_slug, debut)
                else:
                    next_url = _pj_url_search(quoi, commune, cp, debut)
                page.goto(next_url, wait_until="load", timeout=25000)
                time.sleep(1)
            except Exception:
                break

        # ── Extraction JS en priorite ──────────────────────────────────────────
        js_cards = _extract_cards_js(page)

        if js_cards:                          # [] ou None → fallback texte
            page_count = len(js_cards)
            for card in js_cards:
                count += 1
                if len(etablissements) < 10:
                    etablissements.append({
                        "nom":     card.get("nom", ""),
                        "adresse": card.get("adresse", ""),
                    })
            print(f"[PJ] JS  {commune} p{debut//20+1}: {page_count} cartes")

        else:
            # ── Fallback : selectors CSS + inner_text ──────────────────────────
            cards = []
            for sel in ["article", "li.bi-item", "li[class*='bi-item']",
                        "[class*='bi-profile']", "[class*='listing-item']"]:
                cards = page.query_selector_all(sel)
                if cards:
                    break

            if not cards:
                break

            page_count = 0
            for card in cards:
                try:
                    raw = card.inner_text()
                    if "couvre" in raw.lower():
                        continue
                    count += 1
                    page_count += 1
                    if len(etablissements) < 10:
                        nom, adresse = _card_nom_adresse(raw)
                        etablissements.append({"nom": nom, "adresse": adresse})
                except Exception:
                    pass
            print(f"[PJ] TXT {commune} p{debut//20+1}: {page_count} cartes")

        # Pas de "couvre" : total PJ est fiable, pas besoin de paginer
        if not has_couvre:
            return total_pj, etablissements

        if page_count == 0:
            zero_streak += 1
            if zero_streak >= 2:
                break
        else:
            zero_streak = 0

    return count, etablissements


def _wait_for_unblock(page, max_attempts: int = 5, delay: float = 3.0) -> bool:
    """
    Attend que le challenge Cloudflare se resolve automatiquement.
    Un vrai Firefox resout le challenge JS en quelques secondes.
    Retourne True si la page est accessible, False si toujours bloquee.
    """
    for attempt in range(max_attempts):
        text = page.inner_text("body")
        if not _is_blocked(text):
            return True
        if attempt == 0:
            print(f"[PJ] Challenge CF — attente auto-resolution ({max_attempts * delay:.0f}s max)...")
        time.sleep(delay)
    return False


def _search_count(page, quoi: str, commune: str, cp: str) -> tuple[int, list[dict]]:
    # Format /annuaire/{ville-slug}-{dept}/{categorie} en priorite (plus precis)
    cat_slug = _CATEGORIE_SLUGS.get(quoi)
    url_cat = _annuaire_url(commune, cp, cat_slug) if cat_slug else None
    url_search = _pj_url_search(quoi, commune, cp)

    # Try category page first (if any)
    chosen_url = url_search
    if url_cat:
        chosen_url = url_cat

    print(f"[PJ] URL: {chosen_url}")
    try:
        page.goto(chosen_url, wait_until="load", timeout=35000)
    except Exception as e:
        print(f"[PJ] goto {commune}/{quoi}: {str(e)[:120]}")

    # Laisser le challenge Cloudflare se resoudre (jusqu'a 15s)
    if not _wait_for_unblock(page, max_attempts=5, delay=3.0):
        raise RuntimeError("Challenge securite PJ — IP bannie ou CAPTCHA requis")

    try:
        page.wait_for_function(
            r"() => /\d[\d\s\xa0]*\s{0,5}r[eé]sultat/i.test(document.body.innerText)",
            timeout=20000,
        )
    except Exception:
        time.sleep(3)

    text = page.inner_text("body")
    if _is_blocked(text):
        raise RuntimeError("Challenge securite PJ persiste")

    total_chosen = _get_total(text)

    # If we used the category slug and there's also a generic search page, compare totals
    if url_cat:
        try:
            time.sleep(REQUEST_DELAY)
            page.goto(url_search, wait_until="load", timeout=30000)
            time.sleep(1)
            text_search = page.inner_text("body")
            total_search = _get_total(text_search)
            # Prefer the larger total (search is often broader than category slug)
            if total_search > total_chosen:
                print(f"[PJ] Using search URL (total {total_search}) instead of category (total {total_chosen})")
                chosen_url = url_search
                text = text_search
                total_chosen = total_search
        except Exception:
            pass

    # Use chosen_url as base for pagination (strip query)
    base_url = chosen_url.split('?')[0]

    # Place the page on the chosen URL (ensure consistency)
    try:
        page.goto(chosen_url, wait_until="load", timeout=30000)
    except Exception:
        pass

    return _extract_cards(page, quoi, commune, cp, base_url=base_url)


def _scrape_pj(communes: list[dict]) -> list[dict]:
    from camoufox.sync_api import Camoufox

    source_date = time.strftime("%d/%m/%Y")
    results = []

    with Camoufox(headless=True, os="windows") as browser:
        page = browser.new_page()

        page.goto("https://www.pagesjaunes.fr/", wait_until="load", timeout=30000)
        time.sleep(3)

        for sel in [
            "#tarteaucitronAllAllowed",
            "button[id*='accept']",
            "button[class*='acceptAll']",
            "button:has-text('Tout accepter')",
            "button:has-text('Accepter et fermer')",
            "button:has-text('Accepter')",
        ]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1500):
                    btn.click()
                    time.sleep(1.5)
                    break
            except Exception:
                pass

        if _is_blocked(page.inner_text("body")):
            raise RuntimeError("Pages Jaunes bloque par Cloudflare")

        pj_blocked = False

        for c in communes:
            nom = c.get("nom", "")
            cp  = c.get("cp", "")

            entry: dict = {
                "commune":                   nom,
                "cp":                        cp,
                "nb_pharmacies":             0,
                "noms_pharmacies":           [],
                "adresses_pharmacies":       [],
                "nb_materiel_medical":       0,
                "noms_materiel_medical":     [],
                "adresses_materiel_medical": [],
                "_source": f"Pages Jaunes — consulte le {source_date}",
            }

            if pj_blocked:
                results.append(entry)
                continue

            try:
                time.sleep(REQUEST_DELAY)
                nb, etablissements = _search_count(page, "pharmacie", nom, cp)
                entry["nb_pharmacies"]       = nb
                entry["noms_pharmacies"]     = [e["nom"]     for e in etablissements]
                entry["adresses_pharmacies"] = [e["adresse"] for e in etablissements]
            except RuntimeError as e:
                print(f"[PJ] Bloque sur {nom}: {e} — basculement total")
                raise
            except Exception as e:
                print(f"[PJ] pharmacie {nom}: {e}")

            if not pj_blocked:
                try:
                    time.sleep(REQUEST_DELAY)
                    nb, etablissements = _search_count(
                        page, "magasin materiel medical", nom, cp)
                    entry["nb_materiel_medical"]         = nb
                    entry["noms_materiel_medical"]       = [e["nom"]     for e in etablissements]
                    entry["adresses_materiel_medical"]   = [e["adresse"] for e in etablissements]
                except RuntimeError as e:
                    print(f"[PJ] Bloque sur {nom}: {e} — basculement total")
                    raise
                except Exception as e:
                    print(f"[PJ] medical {nom}: {e}")

            results.append(entry)
            print(f"[PJ] {nom}: ph={entry['nb_pharmacies']}, mag={entry['nb_materiel_medical']}")

    return results


def _scrape_osm_fallback(communes: list[dict]) -> list[dict]:
    from scrapers.pages_jaunes_osm import get_pharmacies_and_medical as _osm
    return _osm(communes)


def get_pharmacies_and_medical(communes: list[dict]) -> list[dict]:
    def _pj_total_via_http(quoi: str, commune: str, cp: str) -> int:
        """Try to get the total count from PagesJaunes via plain HTTP (category + search)."""
        totals = []
        try:
            cat_slug = _CATEGORIE_SLUGS.get(quoi)
            urls = []
            if cat_slug:
                urls.append(_annuaire_url(commune, cp, cat_slug))
            urls.append(_pj_url_search(quoi, commune, cp))
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
                "Accept-Language": "fr-FR,fr;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            }
            sess = requests.Session()
            # preload homepage to get cookies
            try:
                sess.get("https://www.pagesjaunes.fr/", headers=headers, timeout=6)
            except Exception:
                pass
            for u in urls:
                try:
                    r = sess.get(u, headers=headers, timeout=8)
                    if r.ok and r.text:
                        t = _get_total(r.text)
                        totals.append(t)
                except Exception:
                    pass
        except Exception:
            pass
        return max(totals) if totals else 0

    try:
        import camoufox  # noqa: F401
        results = _scrape_pj(communes)

        zero_idx = [
            i for i, r in enumerate(results)
            if r.get("nb_pharmacies", 0) == 0 and r.get("nb_materiel_medical", 0) == 0
        ]
        if zero_idx:
            zero_communes = [communes[i] for i in zero_idx]
            print(f"[PJ] {len(zero_communes)} commune(s) a 0 — complement OSM+SIRENE")
            osm = {r["commune"]: r for r in _scrape_osm_fallback(zero_communes)}
            for i in zero_idx:
                n = communes[i].get("nom", "")
                if n in osm:
                    results[i] = osm[n]

        return results

    except Exception as e:
        print(f"[PJ] {e} — basculement total sur OSM+SIRENE")
        # Try to retrieve totals from Pages Jaunes via HTTP to keep reported counts
        pj_totals = {}
        for c in communes:
            nom = c.get("nom", "")
            cp = c.get("cp", "")
            ph_total = _pj_total_via_http("pharmacie", nom, cp)
            mm_total = _pj_total_via_http("magasin materiel medical", nom, cp)
            pj_totals[nom] = {"pharmacies": ph_total, "materiel_medical": mm_total}

        osm_results = _scrape_osm_fallback(communes)
        # Override counts with PJ totals when available, keep names/adresses from OSM/SIRENE
        for r in osm_results:
            nom = r.get("commune", "")
            pj_t = pj_totals.get(nom, {})
            if pj_t:
                if pj_t.get("pharmacies"):
                    r["nb_pharmacies"] = pj_t["pharmacies"]
                if pj_t.get("materiel_medical"):
                    r["nb_materiel_medical"] = pj_t["materiel_medical"]
                # Annotate source to indicate counts taken from PJ while details from SIRENE
                r["_source"] = r.get("_source", "") + f" (comptes PJ: ph={pj_t.get('pharmacies',0)}, mag={pj_t.get('materiel_medical',0)})"

        return osm_results

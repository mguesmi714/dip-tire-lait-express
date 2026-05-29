"""
Scraper PMI — allopmi.fr
https://allopmi.fr/pmi/{dept-slug}/

Logique :
  1. Page departement -> liens des villes avec leur CP
  2. Filtrer les villes dont le CP est dans cp_list
  3. Page ville -> liens des pages detail PMI
  4. Page detail -> nom, adresse, CP, email, horaires
     Telephone : bouton "Afficher" -> POST /pmi/.../tel -> {"number": "XX XX XX XX XX"}
"""

import re
import time
import unicodedata
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import HEADERS, REQUEST_DELAY

_PMI_BASE = "https://allopmi.fr/pmi/"
_session  = requests.Session()


def _get(url: str, retry: bool = True) -> BeautifulSoup | None:
    attempts = 3 if retry else 1
    for attempt in range(attempts):
        try:
            time.sleep(REQUEST_DELAY if attempt == 0 else 3)
            r = _session.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 404:
                return None  # page n'existe pas, pas la peine de réessayer
            r.raise_for_status()
            return BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            if attempt < attempts - 1:
                print(f"[PMI] Tentative {attempt+1}/{attempts} {url}: {e}")
            else:
                print(f"[PMI] Échec {url}: {e}")
    return None


def _get_phone(page_url: str, c2call_path: str) -> str:
    """POST vers l'endpoint /tel pour recuperer le numero de telephone."""
    tel_url = (
        "https://allopmi.fr" + c2call_path
        if c2call_path.startswith("/")
        else c2call_path
    )
    try:
        r = _session.post(
            tel_url,
            headers={
                **HEADERS,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type":     "application/x-www-form-urlencoded",
                "Referer":          page_url,
                "Origin":           "https://allopmi.fr",
                "Accept":           "application/json, text/javascript, */*; q=0.01",
            },
            data="",
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("number", "").strip()
    except Exception as e:
        print(f"[PMI] Telephone {c2call_path}: {e}")
    return ""


def _dept_slug(dept_code: str, dept_nom: str) -> str:
    """'Morbihan' + '56' -> 'morbihan-56'"""
    nom = dept_nom.lower().strip()
    nom = "".join(c for c in unicodedata.normalize("NFD", nom)
                  if unicodedata.category(c) != "Mn")
    nom = re.sub(r"[^a-z0-9]+", "-", nom).strip("-")
    return f"{nom}-{dept_code}"


def _find_dept_url(dept_code: str, dept_nom: str) -> str | None:
    """Trouve l'URL de la page departement, avec fallback sur la page /pmi/."""
    slug = _dept_slug(dept_code, dept_nom)
    url  = f"{_PMI_BASE}{slug}/"
    try:
        r = _session.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if r.status_code == 200:
            return url
    except Exception:
        pass

    soup = _get("https://allopmi.fr/pmi/")
    if not soup:
        return None
    for a in soup.find_all("a", href=re.compile(rf"-{dept_code}/")):
        href = a["href"]
        return href if href.startswith("http") else "https://allopmi.fr" + href
    return None


def _parse_detail(url: str) -> dict | None:
    """Scrappe une page detail de PMI."""
    soup = _get(url)
    if not soup:
        return None

    h1  = soup.find("h1")
    nom = h1.get_text(strip=True) if h1 else url.rstrip("/").split("/")[-1]

    rue       = ""
    cp        = ""
    ville     = ""
    telephone = ""
    email     = ""
    horaires  = ""

    # Section "Par courrier" -> adresse
    h3_courrier = soup.find(
        lambda t: t.name in ("h2", "h3", "h4")
        and "courrier" in t.get_text(strip=True).lower()
    )
    if h3_courrier:
        p = h3_courrier.find_next("p")
        if p:
            lines = [ln.strip()
                     for ln in p.get_text("\n").split("\n") if ln.strip()]
            for line in lines:
                m = re.match(r"^(\d{5})\s+(.+)$", line)
                if m:
                    cp    = m.group(1)
                    ville = m.group(2).strip()
                elif line and not rue and re.search(r"\d", line):
                    rue = line

    adresse = ", ".join(filter(None, [rue, f"{cp} {ville}".strip()]))

    # Telephone : bouton js-c2call -> POST /tel
    a_phone = soup.find("a", attrs={"data-c2call": True})
    if a_phone:
        telephone = _get_phone(url, a_phone["data-c2call"])

    # Section "Par e-mail"
    h3_email = soup.find(
        lambda t: t.name in ("h2", "h3", "h4")
        and "e-mail" in t.get_text(strip=True).lower()
    )
    if h3_email:
        a_mail = h3_email.find_next("a", href=re.compile(r"^mailto:"))
        if a_mail:
            email = a_mail["href"].replace("mailto:", "").strip()

    # Section "Horaires"
    h3_hor = soup.find(
        lambda t: t.name in ("h2", "h3", "h4")
        and "horaire" in t.get_text(strip=True).lower()
    )
    if h3_hor:
        p_hor = h3_hor.find_next("p")
        if p_hor:
            horaires = p_hor.get_text(" | ", strip=True)[:200]

    return {
        "nom":       nom,
        "adresse":   adresse,
        "rue":       rue,
        "cp":        cp,
        "ville":     ville,
        "telephone": telephone,
        "email":     email,
        "horaires":  horaires,
        "lien":      url,
    }


def _city_slug(nom: str) -> str:
    """'Noyal-Muzillac' -> 'noyal-muzillac'"""
    s = nom.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def get_pmi(departement_code: str, departement_nom: str,
            cp_list: list[str] | None = None,
            communes: list[dict] | None = None) -> list[dict]:
    """
    Recupere les PMI pour les codes postaux / communes donnes.

    Strategie double :
      1. Index dept allopmi (incomplet) -> villes matchant cp_list
      2. URLs directes depuis les noms de communes (index souvent absent)

    Args:
        departement_code : ex. "56"
        departement_nom  : ex. "Morbihan"
        cp_list          : ex. ["56000", "56190"]
        communes         : ex. [{"nom": "Vannes", "cp": "56000"}, ...]
    """
    source_date = time.strftime("%d/%m/%Y")
    source      = f"AlloPMI — consulte le {source_date}"
    cp_set      = set(cp_list) if cp_list else None

    # Dériver les depts depuis les préfixes CP (fiable même si dept_nom manque)
    def _dept_nom_from_api(code: str) -> str:
        try:
            r = _session.get(
                f"https://geo.api.gouv.fr/departements/{code}?fields=nom",
                headers=HEADERS, timeout=6,
            )
            return r.json().get("nom", "")
        except Exception:
            return ""

    dept_code_to_nom: dict[str, str] = {departement_code: departement_nom}
    if communes:
        for c in communes:
            cp_c = c.get("cp", "")
            if cp_c:
                dc = cp_c[:3] if cp_c.startswith("97") else cp_c[:2]
                if dc not in dept_code_to_nom:
                    dept_code_to_nom[dc] = (
                        c.get("dept_nom", "")
                        or _dept_nom_from_api(dc)
                    )

    depts_to_search: list[tuple[str, str]] = list(dept_code_to_nom.items())

    city_urls: list[str] = []

    # 1. Index dept -> villes trouvees dans l'index (pour chaque dept)
    for dc, dn in depts_to_search:
        dept_url = _find_dept_url(dc, dn)
        if dept_url:
            soup_dept = _get(dept_url)
            if soup_dept:
                for a in soup_dept.find_all("a", href=re.compile(r"/pmi/.*\.html$")):
                    text     = a.get_text(strip=True)
                    m        = re.search(r"(\d{5})", text)
                    cp_ville = m.group(1) if m else ""
                    if cp_set and cp_ville not in cp_set:
                        continue
                    href = a["href"]
                    if not href.startswith("http"):
                        href = "https://allopmi.fr" + href
                    if href not in city_urls:
                        city_urls.append(href)

    # 2. URLs directes depuis les noms de communes — dept dérivé du CP (fiable)
    if communes:
        for c in communes:
            cp_c  = c.get("cp", "")
            nom_c = c.get("nom", "")
            if cp_set and cp_c not in cp_set:
                continue
            if not nom_c or not cp_c:
                continue
            dc    = cp_c[:3] if cp_c.startswith("97") else cp_c[:2]
            dn    = dept_code_to_nom.get(dc, "")
            if not dn:
                continue
            dslug = _dept_slug(dc, dn)
            direct = f"https://allopmi.fr/pmi/{dslug}/{_city_slug(nom_c)}.html"
            if direct not in city_urls:
                city_urls.append(direct)

    print(f"[PMI] {len(city_urls)} URL(s) de ville a verifier")

    # 3. Pour chaque ville, recuperer les liens des pages detail (parallélisé)
    def _fetch_city_links(city_url: str) -> list[str]:
        # strategy 2 URLs are guesses — no retry on failure
        soup_city = _get(city_url, retry=False)
        if not soup_city:
            return []
        if "introuvable" in soup_city.get_text()[:200].lower():
            return []
        links = []
        for a in soup_city.find_all("a", href=re.compile(r"/pmi/.*-\d+\.html$")):
            href = a["href"].split("#")[0]
            if not href.startswith("http"):
                href = "https://allopmi.fr" + href
            if href not in links:
                links.append(href)
        return links

    detail_links: list[str] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_fetch_city_links, u): u for u in city_urls}
        for fut in as_completed(futures):
            for href in fut.result():
                if href not in detail_links:
                    detail_links.append(href)

    print(f"[PMI] {len(detail_links)} centre(s) PMI unique(s) a scrapper")

    # 4. Scrapper chaque page detail + filtre CP + dedoublonnage
    results    = []
    seen_keys: set[str] = set()

    for link in detail_links:
        pmi = _parse_detail(link)
        if not pmi:
            continue
        # Filtrer par CP reel extrait de la page
        if cp_set and pmi.get("cp") and pmi["cp"] not in cp_set:
            print(f"[PMI] Ignore (CP {pmi['cp']} hors zone) : {pmi['nom']}")
            continue
        # Dedoublonnage : meme adresse physique = meme rue + CP
        dedup_key = (pmi.get("rue", "").lower().strip(), pmi.get("cp", ""))
        if dedup_key[0] and dedup_key in seen_keys:
            print(f"[PMI] Doublon ignore : {pmi['nom']} ({pmi['cp']})")
            continue
        if dedup_key[0]:
            seen_keys.add(dedup_key)
        pmi["_source"] = source
        results.append(pmi)

    print(f"[PMI] {len(results)} PMI recuperee(s)")
    return results

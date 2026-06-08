"""
Persistance des DIPs : index JSON + scan du répertoire output/.
Chaque DIP sauvegardé est associé à la clé de cache pour la consultation.
"""

import json
import re
from datetime import datetime
from pathlib import Path

_OUTPUT_DIR = Path(__file__).parent / "output"
_INDEX_PATH = _OUTPUT_DIR / "dips_index.json"


def _load_index() -> list[dict]:
    if not _INDEX_PATH.exists():
        return []
    try:
        return json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_dip(metadata: dict) -> None:
    """Sauvegarde ou met à jour un DIP dans l'index."""
    _OUTPUT_DIR.mkdir(exist_ok=True)
    index = _load_index()
    index = [d for d in index if d.get("id") != metadata.get("id")]
    index.insert(0, metadata)
    _INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def list_dips() -> list[dict]:
    """
    Retourne tous les DIPs : d'abord ceux de l'index (consultables),
    puis les anciens fichiers Excel détectés dans output/ (téléchargement seulement).
    """
    indexed = _load_index()
    indexed_ids = {d.get("id") for d in indexed}

    # Scan fichiers Excel legacy (non indexés)
    legacy: list[dict] = []
    pattern = re.compile(r"donnees_DIP_(.+?)_(\d{4}-\d{2}-\d{2})\.xlsx$")
    for f in sorted(_OUTPUT_DIR.glob("donnees_DIP_*.xlsx"), reverse=True):
        m = pattern.match(f.name)
        if not m:
            continue
        zone = m.group(1).replace("_", " ")
        date_str = m.group(2)
        # Construire un ID stable basé sur le nom de fichier
        fid = f"legacy_{f.stem}"
        if fid in indexed_ids:
            continue
        legacy.append({
            "id":           fid,
            "zone_nom":     zone,
            "dept_nom":     zone,
            "created_at":   date_str,
            "created_by":   "",
            "cache_key":    None,
            "xlsx_filename": f.name,
            "docx_filename": f.name.replace("donnees_", "synthese_").replace(".xlsx", ".docx"),
            "nb_communes":  "–",
            "legacy":       True,
        })

    return indexed + legacy


def load_dip(dip_id: str) -> dict | None:
    for d in list_dips():
        if d.get("id") == dip_id:
            return d
    return None


def load_dip_results(cache_key: str) -> dict | None:
    """Charge les résultats depuis le cache JSON."""
    if not cache_key:
        return None
    path = _OUTPUT_DIR / "cache" / f"{cache_key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("results")
    except Exception:
        return None

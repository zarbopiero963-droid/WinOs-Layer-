r"""Gli adapter sopravvivono al riavvio del runtime. Il legame con la finestra no.

Cosa c'era prima
----------------
`_adapters` e' un dizionario di processo. Un adapter costruito ispezionando
un'applicazione — le sue azioni, i suoi selettori — spariva alla chiusura del
runtime, e andava ricostruito ispezionando di nuovo. «Persistente» significa che
quel lavoro si fa una volta.

La distinzione che decide tutto il resto
-----------------------------------------
Di un adapter ci sono due cose, e hanno durata diversa:

* la **descrizione** — quali azioni esistono, su quali `automation_id`, con che
  rischio. Vale finche' l'applicazione non cambia interfaccia: si salva.
* il **legame** — l'`hwnd` della finestra su cui agire. E' un numero che il
  sistema operativo riassegna: dopo un riavvio lo stesso `hwnd` puo' appartenere
  a **un'altra applicazione**.

Salvare l'`hwnd` e ricaricarlo come se fosse ancora valido significa costruire
un adapter che clicca su una finestra che non e' quella che credeva. Per questo
un adapter ricaricato torna **non agganciato**: e' noto, non utilizzabile, e
`invoke_action` lo rifiuta finche' qualcuno non lo riaggancia a una finestra
viva. Un adapter che agisce sulla finestra sbagliata sarebbe peggio di un
adapter che non c'e'.

Cosa NON viene salvato
----------------------
L'`openapi`: e' derivato dalle azioni, e un documento generato salvato accanto
alla sua sorgente e' un modo per farli divergere. Si rigenera al caricamento.

Fail-closed su versione e corruzione
-------------------------------------
Un manifest con una `manifest_version` che non conosciamo **non** viene
interpretato a naso: interpretare un formato futuro secondo le regole di quello
vecchio e' esattamente il modo di ottenere un adapter con azioni sbagliate. Lo
stesso per un file illeggibile. In entrambi i casi il manifest viene saltato e
il motivo viene **riportato**, non inghiottito: `load_all` restituisce anche gli
scarti, cosi' chi chiama puo' dirlo invece di far sparire un adapter in
silenzio.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENV_VAR = "WINOS_ADAPTER_STORE"

# La versione del formato. Si alza quando cambia il significato dei campi, non
# quando se ne aggiunge uno compatibile.
MANIFEST_VERSION = 1

MANIFEST_UNREADABLE = "MANIFEST_UNREADABLE"
MANIFEST_VERSION_UNKNOWN = "MANIFEST_VERSION_UNKNOWN"
MANIFEST_MALFORMED = "MANIFEST_MALFORMED"


@dataclass(frozen=True)
class SkippedManifest:
    """Un manifest che non e' stato caricato, e perche'."""

    path: str
    code: str
    reason: str


def _user_config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "winos-api"


def store_dir() -> Path:
    """Dove vivono i manifest. `WINOS_ADAPTER_STORE` la sposta.

    Letta a ogni chiamata e non memorizzata: un test che la sposta con
    `monkeypatch.setenv` deve vederla spostata, non trovare il valore che c'era
    quando il modulo e' stato importato.
    """
    override = os.environ.get(ENV_VAR, "").strip()
    if override:
        return Path(override)
    return _user_config_dir() / "adapters"


def _manifest_path(app_id: str) -> Path:
    # `app_id` e' gia' validato da `validate_app_id` prima di arrivare qui, ma il
    # nome del file non si costruisce con la fiducia: solo caratteri sicuri,
    # cosi' nessun `app_id` puo' scrivere fuori dalla cartella dello store.
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in app_id)
    return store_dir() / f"{safe}.json"


def to_manifest(adapter: Any) -> dict[str, Any]:
    """La descrizione dell'adapter, senza il legame con la finestra."""
    return {
        "manifest_version": MANIFEST_VERSION,
        "app_id": adapter.app_id,
        "app_name": adapter.app_name,
        "trust_level": adapter.trust_level,
        "saved_at": time.time(),
        "actions": [
            {
                "name": action.name,
                "description": action.description,
                "automation_id": action.automation_id,
                "control_type": action.control_type,
                "params": list(action.params),
                "risk": action.risk,
                # Il verdetto di verifica vive con l'adapter: al prossimo avvio
                # si deve poter sapere cosa era gia' stato dimostrato, senza
                # rifare tutte le prove. Campo AGGIUNTIVO — un manifest che non
                # ce l'ha resta leggibile, e per questo la versione non sale:
                # sale quando cambia il significato dei campi, non quando se ne
                # aggiunge uno compatibile.
                "verification": action.verification,
            }
            for action in adapter.actions
        ],
    }


def save(adapter: Any) -> Path:
    """Scrive il manifest. Restituisce il percorso, cosi' il chiamante sa dove."""
    path = _manifest_path(adapter.app_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Scrittura in due tempi: un runtime che muore a meta' `write_text` lascia un
    # manifest troncato, e un manifest troncato e' un adapter che al prossimo
    # avvio sparisce. Il rename e' atomico sullo stesso filesystem.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(to_manifest(adapter), indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def delete(app_id: str) -> bool:
    """Toglie il manifest. `False` se non c'era."""
    path = _manifest_path(app_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def _read_manifest(path: Path) -> tuple[dict[str, Any] | None, SkippedManifest | None]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, SkippedManifest(str(path), MANIFEST_UNREADABLE, str(exc))

    if not isinstance(raw, dict):
        return None, SkippedManifest(
            str(path), MANIFEST_MALFORMED, "il manifest non e' un oggetto JSON"
        )

    version = raw.get("manifest_version")
    if version != MANIFEST_VERSION:
        return None, SkippedManifest(
            str(path),
            MANIFEST_VERSION_UNKNOWN,
            f"manifest_version {version!r}, questo runtime legge {MANIFEST_VERSION}",
        )

    if not isinstance(raw.get("app_id"), str) or not raw["app_id"].strip():
        return None, SkippedManifest(
            str(path), MANIFEST_MALFORMED, "app_id mancante o vuoto"
        )
    if not isinstance(raw.get("actions"), list):
        return None, SkippedManifest(
            str(path), MANIFEST_MALFORMED, "actions mancante o non e' una lista"
        )
    return raw, None


def load_all() -> tuple[list[dict[str, Any]], list[SkippedManifest]]:
    """I manifest leggibili, e quelli scartati con il motivo.

    Gli scarti sono restituiti e non registrati soltanto: un adapter che sparisce
    in silenzio e' un adapter che il chiamante crede di avere.
    """
    directory = store_dir()
    if not directory.is_dir():
        return [], []

    loaded: list[dict[str, Any]] = []
    skipped: list[SkippedManifest] = []
    for path in sorted(directory.glob("*.json")):
        manifest, problem = _read_manifest(path)
        if problem is not None:
            skipped.append(problem)
        elif manifest is not None:
            loaded.append(manifest)
    return loaded, skipped

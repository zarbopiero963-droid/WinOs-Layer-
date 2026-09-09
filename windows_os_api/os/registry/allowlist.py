"""Dove puo' scrivere questa API nel registro? Solo sotto i prefissi elencati.

Decisione owner D2-B, issue #6 (2026-09-08):

    default:  HKCU\\Software\\
    estende:  WINOS_REGISTRY_ALLOWLIST=HKCU\\Software\\MyApp\\,HKCU\\Software\\Company\\
    mai:      HKLM\\SYSTEM\\   HKLM\\SECURITY\\   HKLM\\SAM\\

e, testualmente: «l'architettura deve permettere di aggiungere successivamente
prefissi espliciti senza riscrivere il security gate. Nessun write arbitrario
sul registry tramite semplice ADMIN/flag.»

Cosa c'era prima
----------------
Nessun controllo. `FakeBackend` e `LinuxBackend` scrivevano su qualunque
percorso; `WindowsBackend` non scriveva affatto (uno stub che rispondeva sempre
«requires elevation»), quindi la mancanza di un gate non si era mai vista. Con
la scrittura reale implementata in questa stessa PR, l'assenza del gate
diventerebbe la possibilita' di scrivere ovunque il processo abbia i permessi.

Le tre aree vietate non si riaprono per configurazione
-------------------------------------------------------
`HKLM\\SYSTEM\\`, `HKLM\\SECURITY\\` e `HKLM\\SAM\\` sono rifiutate **anche se
scritte nella variabile d'ambiente**. L'owner ha chiesto che richiedano «una
policy specifica e separata»: se bastasse aggiungerle all'allowlist, quella
policy separata non esisterebbe. `HKLM\\SYSTEM\\CurrentControlSet\\` e' a un
errore di battitura da una macchina che non riavvia.

I due modi in cui un'allowlist di prefissi si buca
---------------------------------------------------
Entrambi hanno un test dedicato, perche' entrambi sono errori che si fanno una
volta sola e si scoprono tardi:

1. **prefisso senza separatore finale** — `HKCU\\Software` (senza `\\`)
   autorizzerebbe anche `HKCU\\SoftwareAltro`, che e' una chiave diversa. Ogni
   prefisso e' normalizzato con il separatore finale.

2. **alias della hive** — `HKEY_LOCAL_MACHINE\\SYSTEM\\...` e
   `HKLM\\SYSTEM\\...` sono lo stesso posto scritto in due modi. Senza
   normalizzare la hive, la denylist si aggira scrivendo il nome per esteso.

E il traversal: `HKCU\\Software\\..\\..\\SYSTEM` sembra stare sotto il prefisso
e non ci sta. I segmenti `..` sono rifiutati invece di essere risolti — risolverli
richiederebbe di indovinare cosa intendeva il chiamante, e un gate non indovina.
"""
from __future__ import annotations

import os
from typing import Any

ENV_VAR = "WINOS_REGISTRY_ALLOWLIST"

REGISTRY_PATH_NOT_ALLOWED = "REGISTRY_PATH_NOT_ALLOWED"
REGISTRY_PATH_FORBIDDEN = "REGISTRY_PATH_FORBIDDEN"
REGISTRY_PATH_INVALID = "REGISTRY_PATH_INVALID"
REGISTRY_READ_FORBIDDEN = "REGISTRY_READ_FORBIDDEN"
REGISTRY_VALUE_FORBIDDEN = "REGISTRY_VALUE_FORBIDDEN"

# Il default sicuro: le impostazioni di un'applicazione per l'utente corrente.
# Nessuna scrittura qui puo' rompere la macchina o toccare un altro utente.
DEFAULT_PREFIXES = ("HKCU\\SOFTWARE\\",)

# Non negoziabili: nessuna variabile d'ambiente le riapre.
FORBIDDEN_PREFIXES = (
    "HKLM\\SYSTEM\\",
    "HKLM\\SECURITY\\",
    "HKLM\\SAM\\",
)

# `HKEY_LOCAL_MACHINE\SYSTEM` e `HKLM\SYSTEM` sono lo stesso posto: senza questa
# mappa la denylist si aggira scrivendo il nome per esteso.
_HIVE_ALIASES = {
    "HKEY_LOCAL_MACHINE": "HKLM",
    "HKEY_CURRENT_USER": "HKCU",
    "HKEY_CLASSES_ROOT": "HKCR",
    "HKEY_USERS": "HKU",
    "HKEY_CURRENT_CONFIG": "HKCC",
}


class RegistryPathRejected(Exception):
    """Il percorso non e' scrivibile. Porta con se' il motivo e il codice."""

    def __init__(self, message: str, code: str = REGISTRY_PATH_NOT_ALLOWED) -> None:
        super().__init__(message)
        self.code = code


def _segments(path: object) -> list[str]:
    """Spezza il percorso, o solleva se non e' interpretabile."""
    if not isinstance(path, str) or not path.strip():
        raise RegistryPathRejected(
            "percorso di registro mancante o vuoto", code=REGISTRY_PATH_INVALID
        )

    segments = [s for s in path.strip().replace("/", "\\").split("\\") if s]
    if not segments:
        raise RegistryPathRejected(
            f"percorso non interpretabile: {path!r}", code=REGISTRY_PATH_INVALID
        )
    if any(s in ("..", ".") for s in segments):
        # Non risolti: risolverli vorrebbe dire indovinare cosa intendeva il
        # chiamante, e un gate non indovina. `HKCU\Software\..\..\SYSTEM`
        # sembra stare sotto il prefisso autorizzato e non ci sta.
        raise RegistryPathRejected(
            f"il percorso contiene segmenti relativi: {path!r}",
            code=REGISTRY_PATH_INVALID,
        )
    return segments


def normalize(path: object) -> str:
    """Il percorso da USARE: separatori uniformi, resto com'e' stato scritto.

    Distinto da `comparison_key` di proposito. La chiave di confronto e'
    maiuscola e con la hive canonicalizzata, perche' il registro non distingue
    le maiuscole; ma restituire QUELLA al chiamante sposterebbe la scrittura su
    una chiave diversa da quella chiesta — gli store di `FakeBackend` e
    `LinuxBackend` sono dizionari, e per un dizionario `Software` e `SOFTWARE`
    sono due chiavi. Il gate decide, non riscrive la richiesta.
    """
    return "\\".join(_segments(path))


def comparison_key(path: object) -> str:
    """La forma su cui si confrontano prefissi e denylist.

    Maiuscola (il registro e' case-insensitive) e con la hive canonicalizzata:
    senza quest'ultima, `HKEY_LOCAL_MACHINE\\SYSTEM\\...` aggirerebbe la
    denylist scritta come `HKLM\\SYSTEM\\`.
    """
    segments = _segments(path)
    hive = segments[0].upper()
    hive = _HIVE_ALIASES.get(hive, hive)
    return "\\".join([hive, *(s.upper() for s in segments[1:])])


def _as_prefix(value: str) -> str:
    """Un prefisso finisce sempre col separatore.

    Senza, `HKCU\\Software` autorizzerebbe `HKCU\\SoftwareAltro` — una chiave
    diversa che comincia con le stesse lettere.
    """
    key = comparison_key(value)
    return key if key.endswith("\\") else key + "\\"


def _is_forbidden(key: str) -> bool:
    target = key if key.endswith("\\") else key + "\\"
    return any(target.startswith(bad) for bad in FORBIDDEN_PREFIXES)


def allowed_prefixes() -> tuple[str, ...]:
    """I prefissi scrivibili: il default piu' quelli configurati.

    Le voci che cadono in un'area vietata sono **scartate**, non onorate: la
    variabile d'ambiente estende la policy, non la sovrascrive.
    """
    extra: list[str] = []
    for part in os.environ.get(ENV_VAR, "").split(","):
        if not part.strip():
            continue
        try:
            prefix = _as_prefix(part)
        except RegistryPathRejected:
            continue  # una voce malformata non allarga niente
        if _is_forbidden(prefix):
            continue
        extra.append(prefix)
    return tuple(DEFAULT_PREFIXES) + tuple(extra)


def check(path: object) -> str:
    """Autorizza una scrittura, o solleva `RegistryPathRejected`.

    Restituisce il percorso normalizzato: chi chiama non deve ri-normalizzare.
    """
    normalized = normalize(path)
    key = comparison_key(path)

    if _is_forbidden(key):
        forbidden = ", ".join(p.rstrip("\\") for p in FORBIDDEN_PREFIXES)
        raise RegistryPathRejected(
            f"{normalized!r} e' in un'area critica del registro ({forbidden}): "
            f"richiede una policy specifica e separata, non l'allowlist",
            code=REGISTRY_PATH_FORBIDDEN,
        )

    prefixes = allowed_prefixes()
    target = key if key.endswith("\\") else key + "\\"
    if not any(target.startswith(p) for p in prefixes):
        raise RegistryPathRejected(
            f"{normalized!r} non e' sotto nessun prefisso autorizzato. "
            f"Consentiti: {', '.join(prefixes)}. "
            f"Estendibili con {ENV_VAR}."
        )

    return normalized


def rejection(exc: RegistryPathRejected, path: str, name: str) -> dict[str, Any]:
    """La forma del rifiuto, uguale a quella usata dal resto del progetto."""
    return {
        "ok": False,
        "denied": True,
        "code": exc.code,
        "error": str(exc),
        "path": path,
        "name": name,
    }


# ---------------------------------------------------------------------------
# Lettura — decisione owner D6, issue #6 (2026-09-09): DENYLIST
# ---------------------------------------------------------------------------
# Quello che c'era prima: niente. `registry_read` leggeva qualunque hive, e la
# permission `registry.read` e' assegnata anche a `VIEWER`, il ruolo piu' basso
# (`core/permissions/model.py`). Il ruolo NON e' stato alzato: e' una scelta
# esplicita dell'owner, che ha preferito filtrare i percorsi invece dei ruoli.
#
# **Una denylist e' fail-open per costruzione.** Protegge solo cio' che qualcuno
# ha pensato di elencare: una chiave sensibile non prevista resta leggibile.
# L'alternativa era l'allowlist simmetrica alla scrittura (D2-B), che nega tutto
# per default; l'owner ha scelto la denylist per non rompere nessuna lettura
# esistente, sapendo il compromesso. Sta scritto qui perche' chi legge questo
# file dopo sappia che il buco e' noto e accettato, non dimenticato.

# Le tre aree gia' vietate in scrittura, piu' due che riguardano solo la
# lettura. Riusare `FORBIDDEN_PREFIXES` rende esplicito il rapporto: cio' che non
# si puo' scrivere non si puo' nemmeno leggere.
#
# `HKLM\SYSTEM\` intero, non il solo `...\Control\Lsa\`: `CurrentControlSet` e'
# un collegamento a `ControlSet001`, quindi una regola sul solo nome corrente si
# aggira scrivendo `ControlSet001`. Vietare il sottoalbero toglie il gioco degli
# alias; la configurazione dei servizi ha comunque il suo endpoint dedicato.
FORBIDDEN_READ_PREFIXES = FORBIDDEN_PREFIXES + (
    # `DefaultPassword` in chiaro quando l'autologon e' attivo.
    "HKLM\\SOFTWARE\\MICROSOFT\\WINDOWS NT\\CURRENTVERSION\\WINLOGON\\",
    # `HKU\<SID>` e' l'`HKCU` di un ALTRO utente. Il proprio resta raggiungibile
    # come `HKCU\`, quindi vietare l'hive non toglie nulla a chi chiede il suo.
    "HKU\\",
)

# Termini cercati come sottostringa nel NOME del valore, ovunque compaia: un
# programma qualunque puo' tenere una password sotto `HKCU\Software\<suo nome>\`,
# che nessun elenco di percorsi prevedera' mai.
#
# Il costo e' dichiarato: e' una sottostringa, quindi rifiuta anche nomi innocui
# che la contengono — `PasswordExpiryDays`, `TokenLifetime`. Un rifiuto e'
# rumoroso e si corregge; una credenziale che esce e' silenziosa. Il rifiuto dice
# quale termine ha fatto scattare il blocco, cosi' l'errore si vede subito.
#
# Non c'e' dentro tutto: `TOKEN` si', `KEY` da solo no (rifiuterebbe meta' del
# registro). Il confine e' arbitrario, ed e' esattamente il limite di una
# denylist.
SECRET_VALUE_TERMS = (
    "PASSWORD",
    "PASSWD",
    "SECRET",
    "CREDENTIAL",
    "PRIVATEKEY",
    "APIKEY",
    "TOKEN",
    "DIGITALPRODUCTID",
)


def _is_read_forbidden(key: str) -> bool:
    target = key if key.endswith("\\") else key + "\\"
    return any(target.startswith(bad) for bad in FORBIDDEN_READ_PREFIXES)


def secret_term_in(name: object) -> str | None:
    """Il termine che rende segreto questo nome di valore, o `None`.

    Restituisce il termine invece di un booleano perche' il rifiuto deve poter
    dire PERCHE': «`ProxyPassword` contiene PASSWORD» si corregge, «negato» no.
    """
    if not isinstance(name, str):
        return None
    upper = name.upper()
    for term in SECRET_VALUE_TERMS:
        if term in upper:
            return term
    return None


def check_read(path: object, name: object = None) -> str:
    """Autorizza una lettura, o solleva `RegistryPathRejected`.

    Due controlli distinti, perche' sono due modi diversi di chiedere la stessa
    cosa: l'area (il percorso) e il nome del valore.
    """
    normalized = normalize(path)
    key = comparison_key(path)

    if _is_read_forbidden(key):
        forbidden = ", ".join(p.rstrip("\\") for p in FORBIDDEN_READ_PREFIXES)
        raise RegistryPathRejected(
            f"{normalized!r} e' in un'area del registro non leggibile da questa API "
            f"({forbidden})",
            code=REGISTRY_READ_FORBIDDEN,
        )

    term = secret_term_in(name)
    if term is not None:
        raise RegistryPathRejected(
            f"il valore {name!r} non e' leggibile: il nome contiene {term!r}",
            code=REGISTRY_VALUE_FORBIDDEN,
        )

    return normalized


def filter_values(values: Any) -> tuple[dict[str, Any], list[str]]:
    """Toglie dai valori enumerati quelli il cui NOME e' una credenziale.

    Senza questo il controllo sul nome sarebbe aggirabile in un passaggio: si
    chiede la chiave senza `name`, il backend restituisce TUTTI i valori, e la
    password esce insieme agli altri.

    I nomi tolti vengono restituiti al chiamante, non nascosti: una risposta a
    cui manca silenziosamente un pezzo e' peggio di un rifiuto, perche' chi legge
    conclude che il valore non esiste.
    """
    if not isinstance(values, dict):
        return {}, []
    kept: dict[str, Any] = {}
    withheld: list[str] = []
    for value_name, value in values.items():
        if secret_term_in(value_name) is not None:
            withheld.append(value_name)
        else:
            kept[value_name] = value
    return kept, withheld

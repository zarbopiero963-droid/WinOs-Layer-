"""`GET /v1/registry` leggeva ovunque. Decisione owner D6: denylist.

Quello che c'era prima
----------------------
`os/registry/service.py` passava il percorso al backend senza guardarlo::

    def read(path, name=None):
        return get_backend().registry_read(path, name)

E `registry.read` non e' una permission da amministratore: `ROLE_PERMISSIONS`
la assegna anche a **`VIEWER`**, il ruolo piu' basso. Quindi il chiamante meno
privilegiato poteva chiedere qualunque chiave il token del processo riuscisse ad
aprire — fra cui `HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon`,
dove sta `DefaultPassword` in chiaro se l'autologon e' attivo, e `HKU\\<SID>`,
che e' l'`HKCU` di un altro utente.

Cosa dimostrano questi test
---------------------------
Le aree vietate sono rifiutate **prima** che qualcosa raggiunga il backend, e i
tre modi in cui una denylist di percorsi si aggira hanno un test ciascuno:

1. **alias della hive** — `HKEY_LOCAL_MACHINE\\SECURITY` e `HKLM\\SECURITY` sono
   lo stesso posto scritto in due modi;
2. **alias del control set** — `CurrentControlSet` e' un collegamento a
   `ControlSet001`: una regola sul solo nome corrente si aggira scrivendo
   l'altro. Per questo e' vietato `HKLM\\SYSTEM\\` intero;
3. **enumerazione** — si chiede la chiave SENZA `name` e il backend restituisce
   tutti i valori insieme, password compresa. E' il buco che rende inutile un
   controllo fatto solo sul nome richiesto.

Il limite, dichiarato
---------------------
Una denylist e' fail-open: protegge solo cio' che qualcuno ha elencato. E' la
scelta dell'owner, presa sapendolo, contro l'allowlist simmetrica alla scrittura
(D2-B) che nega tutto per default. Questi test verificano che l'elenco funzioni,
non che l'elenco sia completo — nessun test puo' dimostrare quest'ultima cosa.
"""
from __future__ import annotations

import pytest

from windows_os_api.os.registry import service as registry_service
from windows_os_api.os.registry.allowlist import (
    REGISTRY_PATH_INVALID,
    REGISTRY_READ_FORBIDDEN,
    REGISTRY_VALUE_FORBIDDEN,
    SECRET_VALUE_TERMS,
)

pytestmark = pytest.mark.security


class SpyBackend:
    """Registra ogni lettura che lo raggiunge. Se ne riceve una, il gate non ha retto."""

    def __init__(self, values: dict | None = None):
        self.calls: list[tuple] = []
        self._values = values or {"Theme": "dark"}

    def registry_read(self, path, name=None):
        self.calls.append((path, name))
        if name is None:
            return {"ok": True, "path": path, "values": dict(self._values)}
        if name not in self._values:
            return {"ok": False, "error": "value not found", "path": path, "name": name}
        return {"ok": True, "path": path, "name": name, "value": self._values[name]}


@pytest.fixture
def spy(monkeypatch):
    backend = SpyBackend()
    monkeypatch.setattr(registry_service, "get_backend", lambda: backend)
    return backend


def test_the_spy_is_actually_wired(spy):
    """La premessa dei test qui sotto.

    Senza questo, un «il backend non e' stato raggiunto» passerebbe anche se il
    backend non fosse raggiungibile per un motivo qualunque — e non
    dimostrerebbe piu' niente sul gate.
    """
    result = registry_service.read(r"HKCU\Software\ContosoCRM", "Theme")
    assert result["ok"] is True, result
    assert spy.calls == [(r"HKCU\Software\ContosoCRM", "Theme")]


# ---------------------------------------------------------------------------
# Le aree vietate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [
        r"HKLM\SAM\SAM\Domains\Account\Users",
        r"HKLM\SECURITY\Policy\Secrets\DefaultPassword",
        r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa",
        # L'alias: `CurrentControlSet` punta qui. Una regola scritta sul solo
        # nome corrente lascerebbe passare questo.
        r"HKLM\SYSTEM\ControlSet001\Control\Lsa",
        r"HKLM\SYSTEM\ControlSet002\Control\Lsa",
        r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
        r"HKU\S-1-5-21-1111111111-2222222222-3333333333-1001\Software\Contoso",
        # La chiave stessa, senza niente sotto.
        r"HKLM\SAM",
        r"HKU",
    ],
)
def test_a_forbidden_area_is_refused_and_never_reaches_the_backend(spy, path):
    result = registry_service.read(path)
    assert result["ok"] is False, result
    assert result["denied"] is True, result
    assert result["code"] == REGISTRY_READ_FORBIDDEN, result
    assert spy.calls == [], f"il percorso vietato ha raggiunto il backend: {spy.calls}"


@pytest.mark.parametrize(
    "path",
    [
        r"HKEY_LOCAL_MACHINE\SECURITY\Policy",
        r"HKEY_USERS\S-1-5-21-1-2-3-1001\Software",
        "hklm/security/policy",
        r"HKLM\\SECURITY\\Policy",
        "  HKLM\\SECURITY\\Policy  ",
        r"hkLm\SaM\Domains",
    ],
)
def test_the_denylist_is_not_dodged_by_how_the_path_is_written(spy, path):
    """Alias della hive, maiuscole, separatori, spazi: stesso posto, stesso rifiuto."""
    result = registry_service.read(path)
    assert result["ok"] is False, result
    assert result["code"] == REGISTRY_READ_FORBIDDEN, result
    assert spy.calls == []


def test_a_relative_segment_is_refused_not_resolved(spy):
    """`HKCU\\Software\\..\\..\\SECURITY` sembra stare in un'area innocua.

    Risolverlo vorrebbe dire indovinare cosa intendeva il chiamante, e un gate
    non indovina: si rifiuta.
    """
    result = registry_service.read(r"HKCU\Software\..\..\SECURITY\Policy")
    assert result["ok"] is False, result
    assert result["code"] == REGISTRY_PATH_INVALID, result
    assert spy.calls == []


# ---------------------------------------------------------------------------
# I nomi di valore che sono credenziali
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    [
        "DefaultPassword",
        "AltDefaultPassword",
        "ProxyPassword",
        "passwd",
        "ClientSecret",
        "StoredCredential",
        "PrivateKeyBlob",
        "ApiKey",
        "AccessToken",
        "DigitalProductId",
    ],
)
def test_a_credential_value_is_refused_wherever_it_lives(spy, name):
    """Il nome conta ovunque compaia: nessun elenco di percorsi prevede tutti i
    programmi che tengono una password sotto la propria chiave in `HKCU`."""
    result = registry_service.read(r"HKCU\Software\Contoso", name)
    assert result["ok"] is False, result
    assert result["denied"] is True, result
    assert result["code"] == REGISTRY_VALUE_FORBIDDEN, result
    assert spy.calls == []


def test_the_refusal_says_which_term_matched(spy):
    """«negato» non si corregge; «contiene PASSWORD» si'."""
    result = registry_service.read(r"HKCU\Software\Contoso", "ProxyPassword")
    assert "PASSWORD" in result["error"], result


@pytest.mark.parametrize("term", SECRET_VALUE_TERMS)
def test_every_declared_term_actually_blocks(spy, term):
    """L'elenco non deve contenere voci decorative: ognuna deve rifiutare."""
    result = registry_service.read(r"HKCU\Software\Contoso", f"My{term.title()}Value")
    assert result["ok"] is False, (term, result)
    assert result["code"] == REGISTRY_VALUE_FORBIDDEN, (term, result)


def test_the_short_name_of_a_family_is_covered_too(spy):
    """Il buco che ha trovato CI su Windows vero.

    Il termine era `DIGITALPRODUCTID`, e
    `HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion` contiene un valore che
    si chiama **`ProductId`** — piu' corto, quindi non conteneva il termine, e
    usciva insieme agli altri. Un termine piu' specifico del nome che vuole
    intercettare non intercetta niente: si mette lo STEM della famiglia.
    """
    for value_name in ("ProductId", "DigitalProductId", "DigitalProductId4"):
        result = registry_service.read(r"HKCU\Software\Contoso", value_name)
        assert result["ok"] is False, (value_name, result)
        assert result["code"] == REGISTRY_VALUE_FORBIDDEN, (value_name, result)


@pytest.mark.parametrize(
    "value_name",
    [
        "API_KEY",
        "Proxy-Password",
        "default.password",
        "client secret",
        "PRIVATE_KEY",
        "priv_key",
        "Digital Product Id",
    ],
)
def test_a_separator_does_not_get_a_secret_past_the_filter(spy, value_name):
    """Chi sceglie il nome del valore e' il programma che ci ha messo la password.

    Se il confronto guardasse il nome cosi' com'e' scritto, un trattino o un
    underscore basterebbe: `API_KEY` non contiene `APIKEY`. Il nome viene
    appiattito a sole lettere e cifre prima del confronto.
    """
    result = registry_service.read(r"HKCU\Software\Contoso", value_name)
    assert result["ok"] is False, (value_name, result)
    assert result["code"] == REGISTRY_VALUE_FORBIDDEN, (value_name, result)


def test_the_separator_trick_does_not_work_through_enumeration_either(monkeypatch):
    """Le due difese devono valere insieme, non una per volta."""
    backend = SpyBackend({"Theme": "dark", "API_KEY": "sk-live-1", "Proxy-Password": "hunter2"})
    monkeypatch.setattr(registry_service, "get_backend", lambda: backend)

    result = registry_service.read(r"HKCU\Software\Contoso")
    assert result["withheld"] == ["API_KEY", "Proxy-Password"], result
    assert "sk-live-1" not in repr(result), "il segreto e' uscito lo stesso"
    assert "hunter2" not in repr(result), "il segreto e' uscito lo stesso"


# ---------------------------------------------------------------------------
# L'aggiramento in una mossa: enumerare invece di chiedere
# ---------------------------------------------------------------------------
def test_enumeration_does_not_hand_over_what_a_named_read_refuses(monkeypatch):
    """Il buco che rende inutile un controllo fatto solo sul nome richiesto.

    Si chiede la chiave SENZA `name`: il backend restituisce tutti i valori
    insieme. Se il filtro non si applicasse anche qui, la password uscirebbe
    accanto al tema dell'interfaccia.
    """
    backend = SpyBackend({
        "Theme": "dark",
        "LastUser": "alice",
        "ProxyPassword": "hunter2",
        "ApiKey": "sk-live-abcdef",
    })
    monkeypatch.setattr(registry_service, "get_backend", lambda: backend)

    result = registry_service.read(r"HKCU\Software\Contoso")
    assert result["ok"] is True, result
    assert "ProxyPassword" not in result["values"], result
    assert "ApiKey" not in result["values"], result
    assert "hunter2" not in repr(result), "il segreto e' uscito lo stesso"
    assert "sk-live-abcdef" not in repr(result), "il segreto e' uscito lo stesso"
    # Cio' che non e' un segreto continua ad arrivare.
    assert result["values"]["Theme"] == "dark", result
    assert result["values"]["LastUser"] == "alice", result


def test_what_was_withheld_is_declared_not_hidden(monkeypatch):
    """Una risposta a cui manca un pezzo in silenzio e' peggio di un rifiuto.

    Chi legge concluderebbe che il valore non esiste, e agirebbe su quella
    conclusione.
    """
    backend = SpyBackend({"Theme": "dark", "ProxyPassword": "hunter2"})
    monkeypatch.setattr(registry_service, "get_backend", lambda: backend)

    result = registry_service.read(r"HKCU\Software\Contoso")
    assert result["withheld"] == ["ProxyPassword"], result


def test_a_key_with_nothing_secret_carries_no_withheld_field(spy):
    """Il campo compare solo quando qualcosa e' stato davvero tolto."""
    result = registry_service.read(r"HKCU\Software\Contoso")
    assert result["ok"] is True, result
    assert "withheld" not in result, result


# ---------------------------------------------------------------------------
# Quello che DEVE continuare a funzionare
# ---------------------------------------------------------------------------
def test_ordinary_reads_are_untouched(spy):
    """La denylist e' stata scelta per non rompere le letture esistenti."""
    result = registry_service.read(r"HKCU\Software\ContosoCRM", "Theme")
    assert result["ok"] is True, result
    assert result["value"] == "dark", result


@pytest.mark.parametrize(
    "path",
    [
        r"HKLM\SOFTWARE\WinOsApi",
        r"HKCU\Software\ContosoCRM",
        r"HKCR\.txt",
        # Vicino a un'area vietata ma fuori: `SAMPLE` non e' `SAM`.
        r"HKLM\SAMPLE\Things",
        r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion",
    ],
)
def test_paths_outside_the_denied_areas_reach_the_backend(spy, path):
    registry_service.read(path)
    assert spy.calls, f"{path} e' stato rifiutato: la denylist e' troppo larga"


def test_a_prefix_match_does_not_swallow_a_longer_name(spy):
    """`HKLM\\SAMPLE` comincia per `HKLM\\SAM` e non e' `HKLM\\SAM`.

    E' l'errore che si fa una volta sola confrontando prefissi senza il
    separatore finale, e si scopre tardi.
    """
    registry_service.read(r"HKLM\SAMPLE\Things")
    assert spy.calls == [(r"HKLM\SAMPLE\Things", None)]


# ---------------------------------------------------------------------------
# Il controllo sta nel SERVICE, non nella route (#13)
# ---------------------------------------------------------------------------
def test_the_gate_holds_for_a_caller_that_never_touches_the_api(spy):
    """Questi test chiamano `registry_service.read` direttamente, non l'HTTP.

    E' il punto: un controllo che vive nella route protegge solo chi passa dalla
    route. Un controllo che il chiamante puo' dimenticare non e' un controllo.
    """
    result = registry_service.read(r"HKLM\SECURITY\Policy")
    assert result["denied"] is True, result
    assert spy.calls == []


# ---------------------------------------------------------------------------
# La superficie HTTP
# ---------------------------------------------------------------------------
def test_the_api_answers_403_not_200(client, auth_headers):
    """Un rifiuto che risponde 200 e' un successo per chiunque legga lo status."""
    response = client.get(
        "/v1/registry", headers=auth_headers,
        params={"path": r"HKLM\SECURITY\Policy\Secrets"},
    )
    assert response.status_code == 403, response.text


def test_the_api_still_serves_an_allowed_read(client, auth_headers):
    response = client.get(
        "/v1/registry", headers=auth_headers,
        params={"path": r"HKCU\Software\ContosoCRM", "name": "Theme"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["value"] == "dark", response.text


def test_the_api_refuses_a_credential_value_name(client, auth_headers):
    response = client.get(
        "/v1/registry", headers=auth_headers,
        params={"path": r"HKCU\Software\ContosoCRM", "name": "DefaultPassword"},
    )
    assert response.status_code == 403, response.text

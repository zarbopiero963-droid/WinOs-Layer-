"""L'allowlist di prefissi del registro: default `HKCU\\Software\\`, aree critiche mai.

Decisione owner D2-B, issue #6 (2026-09-08), testualmente: «default sicuro su
"HKCU\\Software\\". L'architettura deve permettere di aggiungere successivamente
prefissi espliciti senza riscrivere il security gate. Nessun write arbitrario
sul registry tramite semplice ADMIN/flag», e fuori dal percorso standard
`HKLM\\SYSTEM\\`, `HKLM\\SECURITY\\`, `HKLM\\SAM\\`.

Cosa c'era prima
----------------
Nessun controllo. `FakeBackend` e `LinuxBackend` scrivevano su qualunque
percorso; `WindowsBackend` non scriveva affatto — uno stub che rispondeva sempre
«requires elevation» — quindi la mancanza del gate non si era mai vista. Con la
scrittura reale implementata nella stessa PR, quella mancanza sarebbe diventata
la possibilita' di scrivere ovunque il processo abbia i permessi.

I tre modi in cui un gate di prefissi si buca
----------------------------------------------
Hanno un test ciascuno, perche' sono errori che si fanno una volta sola e si
scoprono tardi:

* **prefisso senza separatore** — `HKCU\\Software` autorizzerebbe
  `HKCU\\SoftwareAltro`, che e' una chiave diversa;
* **alias della hive** — `HKEY_LOCAL_MACHINE\\SYSTEM\\` e `HKLM\\SYSTEM\\` sono
  lo stesso posto scritto in due modi, e senza canonicalizzazione la denylist si
  aggira scrivendo il nome per esteso;
* **traversal** — `HKCU\\Software\\..\\..\\SYSTEM` sembra stare sotto il
  prefisso e non ci sta.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from windows_os_api.os.registry import service as reg
from windows_os_api.os.registry.allowlist import (
    DEFAULT_PREFIXES,
    ENV_VAR,
    FORBIDDEN_PREFIXES,
    REGISTRY_PATH_FORBIDDEN,
    REGISTRY_PATH_INVALID,
    REGISTRY_PATH_NOT_ALLOWED,
    RegistryPathRejected,
    allowed_prefixes,
    check,
    comparison_key,
    normalize,
)


@pytest.fixture(autouse=True)
def clean_allowlist(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)


# ---------------------------------------------------------------------------
# Il default
# ---------------------------------------------------------------------------
def test_the_default_is_hkcu_software_and_nothing_else():
    assert allowed_prefixes() == ("HKCU\\SOFTWARE\\",)
    assert DEFAULT_PREFIXES == ("HKCU\\SOFTWARE\\",)


def test_a_path_under_the_default_prefix_is_authorised():
    assert check(r"HKCU\Software\MyApp") == r"HKCU\Software\MyApp"
    assert check(r"HKCU\Software\Company\Product\Settings")


def test_the_authorised_path_keeps_the_case_the_caller_wrote():
    """Il gate decide, non riscrive la richiesta.

    Restituire la chiave di confronto (maiuscola) sposterebbe la scrittura su
    una chiave diversa da quella chiesta: gli store di FakeBackend e
    LinuxBackend sono dizionari, e per un dizionario `Software` e `SOFTWARE`
    sono due chiavi.
    """
    assert check(r"HKCU\Software\MyApp") == r"HKCU\Software\MyApp"
    assert check(r"HKCU/Software/MyApp") == r"HKCU\Software\MyApp"  # solo i separatori


def test_paths_outside_the_default_are_refused():
    for path in (
        r"HKLM\SOFTWARE\Microsoft",
        r"HKCU\Environment",
        r"HKCR\.txt",
        r"HKU\S-1-5-21\Software",
    ):
        with pytest.raises(RegistryPathRejected) as exc:
            check(path)
        assert exc.value.code == REGISTRY_PATH_NOT_ALLOWED, path


# ---------------------------------------------------------------------------
# Il prefisso deve finire col separatore
# ---------------------------------------------------------------------------
def test_a_prefix_does_not_match_a_longer_key_name():
    """`HKCU\\Software\\` non autorizza `HKCU\\SoftwareAltro\\`.

    Il bug classico: confrontare `startswith("HKCU\\Software")` senza il
    separatore finale autorizza qualunque chiave che cominci con quelle lettere.
    """
    with pytest.raises(RegistryPathRejected) as exc:
        check(r"HKCU\SoftwareAltro\X")
    assert exc.value.code == REGISTRY_PATH_NOT_ALLOWED

    with pytest.raises(RegistryPathRejected):
        check(r"HKCU\SoftwareEvil")


def test_a_configured_prefix_without_a_separator_still_gets_one(monkeypatch):
    """Anche i prefissi scritti dall'operatore sono normalizzati."""
    monkeypatch.setenv(ENV_VAR, r"HKCU\Tools")
    assert any(p == "HKCU\\TOOLS\\" for p in allowed_prefixes()), allowed_prefixes()
    assert check(r"HKCU\Tools\App")
    with pytest.raises(RegistryPathRejected):
        check(r"HKCU\ToolsAltro\App")


# ---------------------------------------------------------------------------
# Le aree critiche — non riaprbili per configurazione
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", [
    r"HKLM\SYSTEM\CurrentControlSet\Services",
    r"HKLM\SECURITY\Policy",
    r"HKLM\SAM\SAM",
    r"HKLM\System\CurrentControlSet",          # minuscole
    r"HKEY_LOCAL_MACHINE\SYSTEM\Foo",          # alias della hive
    r"hkey_local_machine\system\foo",          # alias + minuscole
    r"HKLM/SYSTEM/Foo",                        # separatori a slash
])
def test_the_critical_areas_are_refused_however_they_are_written(path):
    """Le quattro scritture dello stesso posto devono avere lo stesso esito.

    L'alias della hive e' il bypass che conta: senza canonicalizzazione,
    `HKEY_LOCAL_MACHINE\\SYSTEM\\` non corrisponderebbe a `HKLM\\SYSTEM\\` e la
    denylist si aggirerebbe scrivendo il nome per esteso.
    """
    with pytest.raises(RegistryPathRejected) as exc:
        check(path)
    assert exc.value.code == REGISTRY_PATH_FORBIDDEN, f"{path} -> {exc.value.code}"


def test_the_environment_cannot_re_open_a_critical_area(monkeypatch):
    """La variabile ESTENDE la policy, non la sovrascrive.

    L'owner ha chiesto che quelle aree richiedano «una policy specifica e
    separata»: se bastasse aggiungerle all'allowlist, quella policy separata non
    esisterebbe.
    """
    monkeypatch.setenv(ENV_VAR, "HKLM\\SYSTEM\\,HKLM\\SECURITY\\,HKLM\\SAM\\")
    assert allowed_prefixes() == DEFAULT_PREFIXES, allowed_prefixes()
    with pytest.raises(RegistryPathRejected) as exc:
        check(r"HKLM\SYSTEM\CurrentControlSet")
    assert exc.value.code == REGISTRY_PATH_FORBIDDEN


def test_the_environment_cannot_re_open_them_through_an_alias(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "HKEY_LOCAL_MACHINE\\SYSTEM\\Services\\")
    assert allowed_prefixes() == DEFAULT_PREFIXES, allowed_prefixes()


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", [
    r"HKCU\Software\..\..\SYSTEM",
    r"HKCU\Software\..\Environment",
    r"HKCU\Software\.\..\..\HKLM",
    r"HKCU/Software/../../SYSTEM",
])
def test_relative_segments_are_refused_not_resolved(path):
    """Rifiutati, non risolti.

    Risolverli vorrebbe dire indovinare cosa intendeva il chiamante, e un gate
    non indovina. Il percorso sembra stare sotto il prefisso autorizzato e non
    ci sta.
    """
    with pytest.raises(RegistryPathRejected) as exc:
        check(path)
    assert exc.value.code == REGISTRY_PATH_INVALID, path


@pytest.mark.parametrize("bad", ["", "   ", None, 42, [], "HKCU", "\\", "///"])
def test_a_path_that_is_not_a_path_is_refused(bad):
    with pytest.raises(RegistryPathRejected):
        check(bad)


# ---------------------------------------------------------------------------
# Estensione della policy
# ---------------------------------------------------------------------------
def test_the_allowlist_extends_without_touching_the_gate(monkeypatch):
    """Il requisito esplicito dell'owner: aggiungere prefissi senza riscrivere il gate."""
    with pytest.raises(RegistryPathRejected):
        check(r"HKCU\Tools\App")

    monkeypatch.setenv(ENV_VAR, "HKCU\\Tools\\,HKCU\\Company\\")
    assert check(r"HKCU\Tools\App")
    assert check(r"HKCU\Company\Product")
    assert check(r"HKCU\Software\Still")  # il default resta


def test_a_malformed_entry_does_not_widen_anything(monkeypatch):
    """Fail-closed su una variabile scritta male: si scarta la voce, non si apre."""
    for junk in (",", ",,", "   ", "..\\..", "\\"):
        monkeypatch.setenv(ENV_VAR, junk)
        assert allowed_prefixes() == DEFAULT_PREFIXES, junk


def test_comparison_is_case_insensitive_but_the_path_is_not_rewritten():
    assert comparison_key(r"hkcu\software\app") == r"HKCU\SOFTWARE\APP"
    assert normalize(r"hkcu\software\app") == r"hkcu\software\app"
    assert check(r"hkcu\software\app") == r"hkcu\software\app"


# ---------------------------------------------------------------------------
# SECURITY BLOCK — il rifiuto arriva prima del registro
# ---------------------------------------------------------------------------
def test_a_refused_write_never_reaches_the_backend(monkeypatch):
    """La proprieta' che conta: non «fallisce dopo», non arriva.

    Il backend viene sostituito con uno che solleva se toccato: se il gate
    fosse controllato dopo, o non fosse controllato, il test fallirebbe.
    """
    touched = []

    class _Explodes:
        name = "explodes"

        def registry_write(self, path, name, value):
            touched.append((path, name, value))
            raise AssertionError(f"il registro e' stato toccato: {path}")

    monkeypatch.setattr(
        "windows_os_api.os.registry.service.get_backend", lambda: _Explodes()
    )

    for path in (r"HKLM\SYSTEM\Foo", r"HKCU\Environment", r"HKCU\Software\..\..\X"):
        out = reg.write(path, "k", "v")
        assert out["ok"] is False, out
        assert out["denied"] is True, out
    assert touched == [], touched


def test_an_authorised_write_does_reach_the_backend(monkeypatch):
    """Il gate autorizza, non blocca soltanto.

    Senza questo, un `return denied` incondizionato passerebbe tutti gli altri.
    """
    seen = []

    class _Records:
        name = "records"

        def registry_write(self, path, name, value):
            seen.append((path, name, value))
            return {"ok": True, "path": path, "name": name, "value": value}

    monkeypatch.setattr(
        "windows_os_api.os.registry.service.get_backend", lambda: _Records()
    )
    out = reg.write(r"HKCU\Software\MyApp", "Setting", "on")
    assert out["ok"] is True, out
    assert seen == [(r"HKCU\Software\MyApp", "Setting", "on")], seen


def test_admin_is_not_a_shortcut_around_the_registry_allowlist(monkeypatch):
    """Decisione owner: nessun write arbitrario con solo ADMIN/flag."""
    monkeypatch.setenv("WINOS_ALLOW_PRIVILEGED", "true")
    monkeypatch.setenv("WINOS_ADMIN", "true")

    with pytest.raises(RegistryPathRejected):
        check(r"HKLM\SOFTWARE\Microsoft")

    # Sul CODICE, non sul testo: la docstring di `write()` spiega proprio che
    # ADMIN non e' una scorciatoia.
    tree = ast.parse(inspect.getsource(reg.write).lstrip())
    names = (
        {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        | {n.value for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    )
    for shortcut in ("admin", "ADMIN", "privileged", "WINOS_ALLOW_PRIVILEGED"):
        assert shortcut not in names, f"write() usa {shortcut!r} nel codice"


def test_the_forbidden_list_is_what_the_owner_named():
    """Pinned: cambiarla e' una decisione di prodotto, non un refactor."""
    assert set(FORBIDDEN_PREFIXES) == {
        "HKLM\\SYSTEM\\", "HKLM\\SECURITY\\", "HKLM\\SAM\\",
    }


# ---------------------------------------------------------------------------
# La scrittura Windows non e' piu' uno stub
# ---------------------------------------------------------------------------
def test_the_windows_write_is_implemented_not_a_stub():
    """Era `return {"ok": False, "error": "registry write requires elevation"}`.

    Restituito INCONDIZIONATAMENTE: non tentava mai, nemmeno sotto `HKCU` dove
    non serve alcuna elevazione. Un errore sempre uguale non dice niente sul
    perche', e un gate davanti a una porta che non si apre sarebbe teatro.
    """
    from windows_os_api.backends import windows as win

    source = inspect.getsource(win.WindowsBackend.registry_write)
    assert "SetValueEx" in source, "la scrittura non tocca piu' il registro"
    assert "CreateKeyEx" in source
    assert "QueryValueEx" in source, (
        "senza rilettura, `ok: true` significherebbe solo «SetValueEx non ha sollevato»"
    )
    assert "registry write requires elevation" not in source, "lo stub e' tornato"

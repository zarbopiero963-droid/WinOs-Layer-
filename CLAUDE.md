# CLAUDE.md

## REGOLA PRINCIPALE

Prima di lavorare su questo repository, leggi e segui `AGENTS.md`.

`AGENTS.md` contiene le policy complete del progetto, incluse:
- invarianti di sicurezza;
- sequenza operativa;
- regole di scope;
- Phase 0;
- micro-audit;
- test;
- hard verify;
- gestione delle PR;
- gestione dei review finding;
- criteri di merge;
- template e formati di risposta.

Questo file aggiunge le regole operative universali dell'agente e i puntatori alle specifiche del repository. Non deve duplicare inutilmente `AGENTS.md`.

Tratta come codice di produzione qualsiasi componente che possa:
- modificare dati persistenti;
- eseguire operazioni esterne;
- modificare autenticazione/autorizzazione;
- gestire denaro, ordini, transazioni o risorse;
- modificare stato runtime;
- elaborare input non fidato;
- gestire segreti o credenziali;
- controllare processi, worker, thread o servizi;
- modificare CI/CD, deployment o packaging;
- influenzare direttamente la sicurezza, l'affidabilità o l'integrità del sistema.

Una modifica sbagliata può causare perdita di dati, esecuzioni errate, duplicazioni, race condition, bypass di sicurezza, deploy indesiderati o corruzione dello stato.

Il merge è gated secondo la sezione `AUTO-MERGE`. Fuori dalle condizioni gated il merge resta manuale dell'owner.

---

# QUANDO USARE QUESTE REGOLE

Usa il flusso completo definito da `AGENTS.md` e dall'`AUTO PR FLOW` del repository per qualsiasi task che:

- modifica codice destinato a una PR;
- modifica runtime, lifecycle o teardown;
- modifica parser, listener, webhook, API o input handler;
- modifica autenticazione/autorizzazione;
- modifica validazione o sanitizzazione;
- modifica database, schema o persistenza;
- modifica configurazione o settings;
- modifica API client/server;
- modifica business logic con effetti esterni;
- modifica sistemi di esecuzione, job, worker, queue o scheduler;
- modifica error handling, retry, idempotenza o circuit breaker;
- modifica sistemi di sicurezza o guardrail;
- modifica AI/LLM agent, tool calling o decision engine;
- modifica build, packaging, deployment o CI/CD;
- modifica dipendenze;
- modifica workflow GitHub Actions o altri workflow CI;
- richiede commit, push, PR, resolve thread o valutazione merge;
- corregge review comments, check rossi o finding di strumenti automatici.

Per domande, spiegazioni e analisi puramente read-only non serve il flusso completo.

---

# LAVORO IN BACKGROUND — CICLO AUTONOMO E VERDETTO OBBLIGATORIO

L'agente porta avanti l'intero ciclo PR in autonomia, senza fermarsi ad aspettare istruzioni intermedie per i passi già coperti dalle policy.

Sequenza generale:

`analisi → Phase 0 → implementazione → micro-audit → test → commit → push → check settled → review triage → patch → verifica → gate finale → verdict → merge/manual`

L'agente non deve chiedere all'owner conferma per passi già autorizzati dalle policy.

Si ferma esclusivamente per:
- decisioni che spettano all'owner;
- ambiguità reali;
- rischio non risolvibile automaticamente;
- estensione dello scope;
- autorizzazioni esplicite richieste dalla policy;
- condizioni gated non soddisfatte.

Ogni ciclo termina SEMPRE con un verdetto esplicito.

Possibili stati:

1. `READY_TO_MERGE`
2. `NEEDS_MANUAL`
3. `CHECKS_PENDING`
4. `FAILED`
5. `PATCH_REQUIRED_LOOP_STOPPED`

Mai terminare lasciando l'owner senza sapere se il lavoro è pronto.

Se tutte le condizioni di AUTO-MERGE sono soddisfatte e il progetto non classifica il cambiamento come safety-critical, l'agente può eseguire il merge secondo la policy.

Altrimenti deve dichiarare esplicitamente:

`PRONTA PER MERGE`

oppure lo stato reale:

`NEEDS_MANUAL`
`CHECKS_PENDING`
`FAILED`
`PATCH_REQUIRED_LOOP_STOPPED`

con il motivo preciso.

---

# AUTO PR FLOW — OBBLIGATORIO

Segui la macchina a stati definita dalla specifica del repository, normalmente:

`docs/auto_pr_flow_spec.md`

PRIMA di iniziare un task che modifica codice destinato a una PR:

1. leggi `AGENTS.md`;
2. leggi `docs/auto_pr_flow_spec.md` se presente;
3. individua eventuali task spec;
4. individua `files_allowed` / `files_forbidden`;
5. determina se il cambiamento è safety-critical;
6. esegui Phase 0 quando richiesta.

Non procedere a memoria quando esiste una specifica formale.

Se il task utilizza un marker:

`[TASK: <key>]`

la chiave deve essere formalmente valida e registrata secondo le regole del repository.

Marker mancante o task spec malformato:

`FAIL CLOSED`

---

# REGOLE NON NEGOZIABILI

## Fail-closed

Evidence:
- mancante;
- ambigua;
- contraddittoria;
- non verificabile;

=> `NEEDS_MANUAL`.

Mai inventare evidence.

Mai considerare una condizione soddisfatta senza prova.

Mai aggirare un gate per "fare prima".

---

## UNA SOLA ATTIVITÀ PR ALLA VOLTA

È consentita una sola attività attiva e una sola PR aperta per il task workflow, salvo diversa autorizzazione esplicita prevista dal repository.

Se esiste già una PR aperta:

- task non correlato => `BLOCKED`;
- fix della PR esistente => lavorare sullo stesso branch;
- mai aprire una seconda PR per aggirare una PR esistente;
- mai lavorare direttamente su `main`.

Una nuova PR si apre solo dopo merge/close della precedente, salvo policy esplicita diversa.

---

## CURRENT HEAD

Lavora esclusivamente sul current head della PR.

Head mismatch:

`NEEDS_MANUAL`

Non assumere che il codice visto in precedenza sia ancora quello corrente.

---

## PHASE 0

Prima di ogni patch safety-critical:

- esegui Phase 0;
- verifica il contratto;
- identifica invarianti;
- identifica file autoritativi;
- identifica rischi;
- identifica test richiesti;
- controlla eventuali regressioni nelle ultime PR.

Phase 0 assente o `NEEDS_MANUAL`:

non patchare.

---

## PATCH STRETTE

Modifica solo:

- bug reali;
- regressioni;
- blocker reali;
- requisiti espliciti del task;
- modifiche necessarie per mantenere invarianti.

Non trasformare un fix in una riscrittura.

Non fare "fix everything".

Non introdurre refactoring non necessario durante un fix safety-critical.

---

## SCOPE

Rispetta sempre:

- `files_allowed`;
- `files_forbidden`;
- task spec;
- policy del repository.

I file critici del progetto devono essere considerati protetti per default.

Esempi di aree potenzialmente protette:

- runtime;
- core;
- security;
- authentication;
- authorization;
- database;
- persistence;
- deployment;
- CI/CD;
- secrets;
- API;
- external integrations;
- payment/transaction logic;
- worker/scheduler;
- infrastructure.

La lista effettiva deve essere determinata da `AGENTS.md` e dalla configurazione del repository.

Violazione dello scope:

`FAILED`.

---

# MICRO-AUDIT

Dopo ogni patch e prima di test/commit/push:

1. controlla il diff;
2. verifica che il cambiamento rispetti il task;
3. controlla i file modificati;
4. controlla eventuali file fuori scope;
5. verifica regressioni evidenti;
6. verifica invarianti safety;
7. verifica documentazione;
8. verifica test;
9. verifica che non siano stati introdotti bypass;
10. verifica che non siano comparsi segreti.

Il micro-audit è obbligatorio.

---

# PUSH / RESOLVE / RERUN

Nessun:

- push;
- resolve thread;
- rerun;
- merge;

deve essere eseguito automaticamente senza la relativa autorizzazione prevista dalla policy.

Flag tipiche:

- `AUTO_PUSH_ENABLED`
- `AUTO_RESOLVE_ENABLED`
- `AUTO_RERUN_ENABLED`

L'autorizzazione esplicita dell'owner può valere per la PR corrente secondo le regole di `AGENTS.md`.

Mai interpretare una semplice intenzione come autorizzazione esterna.

---

# DOCUMENTAZIONE

Ogni modifica a:

- funzione;
- classe;
- modulo;
- comportamento;
- API;
- configurazione;
- gate;
- contratto;
- workflow;
- processo;
- UI;
- roadmap;

deve aggiornare la documentazione corrispondente nello stesso PR quando necessario.

Le docs devono descrivere il comportamento reale.

Mai documentare un comportamento che il codice non implementa.

Se la documentazione necessaria è fuori `files_allowed`:

- non violare lo scope;
- non modificare il file arbitrariamente;
- dichiarare `NEEDS_MANUAL` oppure richiedere estensione esplicita dell'allowlist.

Se non è necessario modificare la documentazione:

dichiarare esplicitamente `N/A` con motivazione.

---

# HARD VERIFY — OBBLIGATORIO

Prima di dichiarare un task o PR:

- implementato;
- completo;
- pronto;
- pronto al merge;

esegui la verifica definita da:

`docs/hard_verify_spec.md`

quando presente.

La verifica deve includere, dove applicabile:

- contratto del task;
- current-head;
- static audit;
- file autoritativi;
- test;
- `py_compile` se Python;
- test mirati;
- wiring;
- integrazione;
- scope;
- fail-closed;
- documentazione;
- invarianti;
- build;
- packaging;
- deployment checks.

Il report deve contenere una delle seguenti etichette:

- `MISSING`
- `PARTIAL`
- `IMPLEMENTED_WITH_NOTE`
- `FULLY_IMPLEMENTED`
- `MERGED_BUT_NOT_FULLY_AUTOMATED`

`PR merged` non significa automaticamente `FULLY_IMPLEMENTED`.

---

# AI PR REVIEW — REVIEWER + GATE

Se il repository dispone di reviewer AI automatici, applica le relative regole definite nei documenti del progetto.

L'agente deve distinguere:

### Reviewer obbligatori

Reviewer che costituiscono un vero gate CI/review.

### Reviewer advisory

Reviewer utili ma non bloccanti in caso di:

- rate-limit;
- usage-quota;
- provider unavailable;

secondo `AGENTS.md`.

### Reviewer finali

Reviewer forti eventualmente richiesti come gate finale pre-merge.

Non inventare reviewer, modelli o workflow che il repository non possiede.

---

# COSTO DEI REVIEWER

Quando vengono usati modelli AI a consumo:

`max_tokens` / `MAX_OUTPUT_TOKENS` è un limite massimo, non automaticamente il numero di token addebitati.

Non utilizzare un tetto basso come strategia primaria di risparmio.

Se una review viene troncata:

- aumentare il limite se necessario;
- non accorciare arbitrariamente il contenuto utile;
- evitare chiamate duplicate.

Le principali strategie di risparmio sono:

1. evitare review duplicate;
2. ridurre il numero di push;
3. raggruppare i fix;
4. evitare rerun inutili;
5. usare reviewer costosi solo quando richiesto dalla policy;
6. usare reasoning appropriato al compito.

Mai sacrificare la qualità di una review obbligatoria per risparmiare token.

---

# REVIEW FINDINGS

A ogni check-in leggere:

- review bodies;
- inline comments;
- conversation comments;
- review threads;
- annotazioni CI;
- finding automatici.

Non limitarsi al nome/stato del check.

Ogni finding deve essere classificato:

### `PATCH_REQUIRED`

Bug reale current-head.

### `EVIDENCE_RESOLVE`

Finding già coperto, outdated o dimostrabilmente non applicabile.

### `SKIP`

Falso positivo, duplicato, cosmetico o fuori scope.

### `NEEDS_MANUAL`

Ambiguo, rischioso o richiede decisione dell'owner.

I blocker non possono essere lasciati aperti.

---

# PATCH DA REVIEW

Per ogni finding reale:

1. riprodurre il problema;
2. creare/aggiornare il test;
3. verificare che il test fallisca sul comportamento precedente;
4. applicare la patch minima;
5. verificare PASS;
6. eseguire test mirati;
7. eseguire hard verify;
8. rispondere al finding con evidence.

Mai dichiarare risolto un finding senza prova.

---

# TEST HARD — OBBLIGATORI

Ogni modifica di codice deve avere test adeguati.

Ogni nuova funzione, ramo o comportamento modificato deve essere coperto da un test che:

- utilizza il codice reale del progetto;
- è deterministico;
- è riproducibile;
- fallisce se la regressione torna;
- non usa `assert True`;
- non è un mock-only test;
- non usa `|| true`;
- non dipende da credenziali reali.

Per bug/finding:

`test regression → FAIL sul vecchio comportamento → patch → PASS`

Quando possibile usare:

- stub;
- fake;
- simulation;
- temporary directories;
- database temporanei;
- fixture;
- mock delle sole integrazioni esterne.

Mai usare sistemi live per test unitari.

---

# MATRICE DI RESILIENZA UNIVERSALE

Per sistemi con stato o side effects, verificare i casi pertinenti.

## Idempotenza

- stesso input non produce duplicazioni;
- retry sicuri;
- operazioni ripetute non corrompono lo stato;
- stato persistente sopravvive al restart.

## Errori

- timeout => fail-closed quando necessario;
- risposta ambigua => nessuna azione pericolosa;
- errore parziale => rollback o stato esplicito;
- retry solo quando semanticamente sicuro.

## Persistenza

- write failure non lascia stato mezzo-applicato;
- crash recovery;
- rollback;
- backup;
- migrazioni compatibili.

## Concorrenza

- race condition;
- doppia esecuzione;
- worker duplicati;
- lifecycle incoerente;
- shutdown incompleto.

## Configurazione

- default sicuri;
- backward compatibility;
- config corrotta gestita;
- save failure sicuro;
- nessun segreto hardcoded.

## Input

- input vuoto;
- input malformato;
- dati mancanti;
- valori fuori range;
- NaN/Inf quando applicabile;
- encoding inatteso;
- payload non fidato.

## Sicurezza

- autenticazione;
- autorizzazione;
- secret handling;
- sanitizzazione;
- injection;
- path traversal;
- privilege boundaries;
- logging sicuro.

## AI / Agent

Quando presente:

- tool access gated;
- input non fidato trattato come non affidabile;
- nessun bypass dei guardrail;
- errori/dati insufficienti => comportamento conservativo;
- nessuna azione esterna senza validazione;
- nessun secret esposto al modello;
- nessun tool privilegiato senza autorizzazione.

---

# TEST MANUALI

Ciò che richiede un ambiente reale deve essere dichiarato:

`MANUAL_ONLY`

Esempi:

- hardware reale;
- GUI reale;
- browser reale;
- device;
- produzione;
- API live;
- deployment reale;
- certificati;
- autenticazione reale;
- packaging specifico della piattaforma.

Mai dichiarare automaticamente testato ciò che non è stato realmente eseguito.

---

# QUANDO TOCCHI DATABASE / PERSISTENZA

Verifica:

- compatibilità;
- migrazione;
- rollback;
- atomicità;
- concorrenza;
- crash recovery;
- backup;
- consistenza.

Modifiche allo schema sono considerate breaking change salvo prova contraria.

Servono:

- approvazione prevista dal task;
- nota di migrazione;
- test di compatibilità;
- rollback test quando applicabile.

Mai:

- drop silenzioso;
- riuso ambiguo di colonne;
- modifica incompatibile non documentata.

---

# QUANDO TOCCHI CONFIG / SETTINGS

Verifica:

- configurazione esistente caricata;
- nuove chiavi con default sicuri;
- backward compatibility;
- save failure;
- config corrotta;
- backup;
- secret handling;
- path portabili.

Mai introdurre un default che abiliti automaticamente una funzionalità pericolosa.

---

# QUANDO TOCCHI SECURITY

Tratta qualsiasi modifica a:

- auth;
- authorization;
- permissions;
- secrets;
- encryption;
- token;
- session;
- sandbox;
- command execution;
- external integrations;

come potenzialmente safety-critical.

Verifica:

- fail-closed;
- privilege boundaries;
- input validation;
- secret redaction;
- audit logging;
- least privilege;
- nessun bypass accidentale.

---

# QUANDO TOCCHI RUNTIME / LIFECYCLE

Verifica:

- START;
- STOP;
- restart;
- shutdown;
- crash;
- timeout;
- worker lifecycle;
- thread lifecycle;
- process lifecycle;
- cleanup.

Nessun vecchio worker, listener, poller o sessione deve sopravvivere accidentalmente a un nuovo ciclo.

START fallito:

nessuna sessione parzialmente attiva.

STOP:

teardown completo e verificabile.

---

# QUANDO TOCCHI API / INTEGRAZIONI ESTERNE

Verifica:

- timeout;
- retry;
- idempotenza;
- error handling;
- risposta parziale;
- risposta ambigua;
- authentication;
- rate limits;
- circuit breaker;
- fallback.

Mai effettuare retry ciechi di operazioni non idempotenti.

Mai considerare una risposta ambigua come successo.

---

# QUANDO TOCCHI BUILD / PACKAGING / DEPLOYMENT

Verifica:

- workflow valido;
- dipendenze coerenti;
- lockfile;
- artifact;
- packaging;
- path;
- configurazione;
- secret handling;
- reproducibility.

Se la build reale non è stata eseguita:

`Build not run in this environment`

Mai dichiarare artifact generati se non lo sono.

La build non deve effettuare push/merge/deploy automatici salvo policy esplicita.

---

# GATE UI / DESIGN HANDOFF

Se il progetto possiede una specifica di design/handoff, questa è la fonte di verità per la UI.

Quando una modifica riguarda:

- finestre;
- tab;
- pulsanti;
- controlli;
- campi;
- stati;
- indicatori;
- colori;
- copy;
- UX;
- information architecture;
- flussi di conferma;

aggiorna il relativo design handoff nello stesso PR.

Se non esiste una superficie UI o la modifica è puramente interna:

`N/A`

con motivazione.

Mai lasciare documentazione UI in contraddizione con il codice.

---

# GATE DI CONSEGNA

Una PR è consegnata solo quando l'owner possiede tutte le informazioni necessarie per decidere.

## 1. REVIEW FINALE

Se la policy del repository richiede reviewer finali sul range completo:

eseguirli prima del verdetto.

Se esiste un singolo push e la review automatica copre già l'intero range:

non duplicare inutilmente una review costosa.

---

## 2. VERDETTO ESPLICITO

Il verdetto deve essere esplicito:

`READY_TO_MERGE`

oppure:

`NEEDS_MANUAL`
`CHECKS_PENDING`
`FAILED`
`PATCH_REQUIRED_LOOP_STOPPED`

---

## 3. COSA È STATO FATTO

Spiegare in termini funzionali:

- cosa cambia;
- quale problema risolve;
- quali rischi sono stati verificati;
- cosa è stato testato;
- cosa NON è stato testato;
- eventuali regressioni note.

Non limitarsi al changelog del diff.

---

## 4. PROVA VISIVA

Se il progetto possiede una superficie visibile e la modifica la tocca:

fornire una prova visiva reale quando l'ambiente lo consente.

Mai sostituire uno screenshot reale con un mockup dichiarandolo prova.

Se non applicabile:

`N/A`

---

## 5. COSTO REVIEW

Se il repository dispone di strumenti di contabilizzazione delle review:

calcolare il costo totale della PR.

Segnalare anche le review prive di dati di costo.

Non nascondere review non contabilizzate.

---

# AUTO-MERGE — GATED

L'auto-merge è consentito esclusivamente quando TUTTE le condizioni previste dalla policy del repository sono soddisfatte.

Condizioni minime:

1. tutti i check obbligatori current-head sono `SETTLED`;
2. tutti i check obbligatori sono verdi;
3. nessun blocker reale;
4. nessun `manual-review-required`;
5. nessun `NEEDS_MANUAL` aperto;
6. nessun thread bloccante irrisolto;
7. PR able-to-merge;
8. branch protection soddisfatta;
9. PR non draft;
10. hard verify PASS;
11. test hard PASS;
12. scope pulito;
13. documentazione aggiornata;
14. eventuali reviewer finali obbligatori completati.

Se una sola condizione manca:

NON auto-mergiare.

---

# SAFETY-CRITICAL

Una modifica è safety-critical se `AGENTS.md` o il progetto la classifica come tale.

In assenza di classificazione esplicita, considera safety-critical almeno:

- security;
- authentication;
- authorization;
- secrets;
- financial/transaction logic;
- dati persistenti critici;
- deployment;
- CI/CD;
- command execution;
- privilege boundaries;
- core runtime;
- external side effects;
- sistemi che possono causare danni se eseguiti erroneamente.

Per default:

`SAFETY-CRITICAL => MERGE MANUALE OWNER`

salvo override esplicito previsto dalla policy.

---

# OVERRIDE SAFETY-CRITICAL

Un override può essere valido solo se:

- proviene esplicitamente dall'owner;
- è riferito al task/issue corretto;
- è inequivocabile;
- è tracciato;
- non rimuove gli altri gate.

L'override può rimuovere solo il divieto di auto-merge safety-critical.

Non può bypassare:

- test;
- check;
- reviewer obbligatori;
- hard verify;
- scope;
- blocker;
- manual-review;
- branch protection;
- fail-closed.

---

# NEED-MANUAL

Quando una decisione spetta all'owner:

1. fermati;
2. non aggirare il problema;
3. formula una domanda precisa;
4. presenta le opzioni;
5. indica il rischio;
6. annota la decisione nella issue/task;
7. attendi la decisione;
8. riprendi dal punto corretto.

Mai interpretare il silenzio dell'owner come approvazione.

---

# REVIEWER IN USAGE-QUOTA / RATE-LIMIT

Per reviewer advisory:

- usage-quota => `ASSENTE`;
- rate-limit => `ASSENTE`;
- non aspettare indefinitamente;
- annotare che non ha revisionato.

Per reviewer classificati dalla policy come gate obbligatori:

seguire esattamente la regola di `AGENTS.md`.

Un gate obbligatorio non può essere semplicemente ignorato solo perché il provider è indisponibile, salvo esplicito carve-out previsto dalla policy.

---

# POST-MERGE TRACKING

Review tardive possono arrivare dopo il merge.

Quando un finding tardivo è reale:

1. identificare PR;
2. identificare head SHA;
3. identificare file/riga;
4. identificare reviewer;
5. classificare severità;
6. verificare Issue esistente;
7. creare Issue se necessaria;
8. creare fix PR dal `main` aggiornato;
9. eseguire nuovamente Phase 0;
10. micro-audit;
11. test;
12. hard verify.

Non riaprire arbitrariamente una PR già mergiata per applicare fix non correlati.

---

# REGOLA CONTRO IL RUMORE

Non creare commit per:

- falsi positivi;
- commenti cosmetici;
- preferenze di stile;
- naming soggettivo;
- formatting già coperto;
- finding diff-only dimostrabilmente errati.

Rispondere con evidence.

Patchare solo quando esiste un problema reale.

---

# PARSIMONIA DEI PUSH

Un push può attivare:

- CI;
- reviewer AI;
- build;
- deployment checks;
- costi API.

Quindi:

- raggruppare i fix;
- evitare push cosmetici;
- evitare push per inseguire falsi positivi;
- non fare commit inutili;
- completare il lavoro prima del push quando possibile.

Un singolo push completo è preferibile a molti push incrementali.

---

# REGOLA D'ORO

Non cercare di "fare tutto".

Meglio:

`patch piccola + comportamento prevedibile + test reale + evidence`

che:

`grande riscrittura + molti cambiamenti + test insufficienti`.

La catena fondamentale di qualsiasi progetto deve rimanere:

`input → parsing/interpretazione → validazione → business logic → safety gates → side effect → riconciliazione/stato → persistenza`

Quando il progetto non ha uno di questi passaggi, non inventarlo: usa l'architettura reale del repository.

Qualsiasi modifica che possa rompere questa catena deve essere:

- bloccata;
- corretta con una patch stretta;
- oppure approvata esplicitamente dall'owner.

Mai sacrificare sicurezza, integrità, idempotenza, consistenza o verificabilità per velocità.
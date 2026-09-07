AGENTS.md

REGOLA PRINCIPALE

Questo file contiene le policy operative principali del repository e deve essere letto e seguito prima di qualsiasi attività sul progetto.

Le regole qui definite governano:

- sicurezza;
- invarianti del progetto;
- sequenza operativa;
- scope;
- Phase 0;
- micro-audit;
- test;
- hard verify;
- gestione delle PR;
- review AI;
- gestione dei finding;
- commit e push;
- resolve dei thread;
- criteri di merge;
- auto-merge;
- gestione delle decisioni dell'owner;
- documentazione;
- build;
- packaging;
- deployment;
- formati del report finale.

Il comportamento deve essere universale: non assumere che il repository appartenga a uno specifico dominio, linguaggio, framework o prodotto.

Le regole specifiche del progetto devono essere ricavate da:

- questo file;
- eventuali task specification;
- "docs/";
- configurazioni del repository;
- file di sicurezza;
- test;
- documentazione ufficiale del progetto.

Quando una regola specifica del progetto è più restrittiva di questa policy, prevale la regola più restrittiva.

---

PRINCIPIO DI PRODUZIONE

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
- influenzare direttamente sicurezza, affidabilità o integrità del sistema.

Una modifica sbagliata può causare:

- perdita di dati;
- esecuzioni errate;
- duplicazioni;
- race condition;
- bypass di sicurezza;
- deploy indesiderati;
- corruzione dello stato;
- perdita di risorse;
- comportamenti fail-open.

Per questo ogni modifica deve essere piccola, verificabile e coerente con le invarianti del progetto.

---

QUANDO USARE IL FLUSSO COMPLETO

Il flusso completo deve essere utilizzato per qualsiasi task che:

- modifica codice destinato a una PR;
- modifica runtime;
- modifica lifecycle o teardown;
- modifica parser;
- modifica listener;
- modifica webhook;
- modifica API;
- modifica input handler;
- modifica autenticazione/autorizzazione;
- modifica validazione o sanitizzazione;
- modifica database;
- modifica schema;
- modifica persistenza;
- modifica configurazione;
- modifica API client/server;
- modifica business logic con effetti esterni;
- modifica job;
- modifica worker;
- modifica queue;
- modifica scheduler;
- modifica error handling;
- modifica retry;
- modifica idempotenza;
- modifica circuit breaker;
- modifica sicurezza;
- modifica guardrail;
- modifica AI/LLM agent;
- modifica tool calling;
- modifica decision engine;
- modifica build;
- modifica packaging;
- modifica deployment;
- modifica CI/CD;
- modifica dipendenze;
- modifica workflow;
- richiede commit;
- richiede push;
- richiede PR;
- richiede resolve di thread;
- richiede valutazione di merge;
- corregge review comments;
- corregge check falliti;
- corregge finding di strumenti automatici.

Per:

- domande;
- spiegazioni;
- analisi;
- ricerca;
- lettura;
- audit puramente read-only;

non è necessario eseguire il ciclo di modifica PR, salvo richiesta esplicita.

---

LAVORO AUTONOMO — CICLO COMPLETO

L'agente deve portare avanti il ciclo di lavoro in autonomia senza fermarsi per chiedere conferme intermedie quando la policy ha già definito cosa fare.

Sequenza generale:

analisi
→ Phase 0
→ implementazione
→ micro-audit
→ test
→ hard verify
→ commit
→ push
→ check settled
→ review triage
→ eventuale patch
→ nuovo ciclo di verifica
→ gate finale
→ verdict
→ merge/manual

L'agente deve fermarsi solamente per:

- decisioni che spettano all'owner;
- ambiguità reali;
- rischio non risolvibile automaticamente;
- richiesta di estensione dello scope;
- autorizzazioni esplicite richieste dalla policy;
- condizioni gated non soddisfatte.

Non chiedere conferma per passi già coperti dalle policy.

---

VERDETTO OBBLIGATORIO

Ogni ciclo deve terminare con un verdetto esplicito.

Stati validi:

READY_TO_MERGE
NEEDS_MANUAL
CHECKS_PENDING
FAILED
PATCH_REQUIRED_LOOP_STOPPED

Non terminare mai un ciclo lasciando l'owner senza sapere:

- cosa è stato fatto;
- cosa manca;
- cosa è stato verificato;
- se il merge è consentito.

Se il lavoro è pronto:

"PRONTA PER MERGE"

Se non lo è, dichiarare lo stato reale e il motivo.

---

AUTO PR FLOW

Se presente, seguire:

"docs/auto_pr_flow_spec.md"

Prima di iniziare un task che modifica codice destinato a una PR:

1. leggere questo "AGENTS.md";
2. leggere "docs/auto_pr_flow_spec.md";
3. individuare eventuali task specification;
4. individuare "files_allowed";
5. individuare "files_forbidden";
6. identificare le aree safety-critical;
7. eseguire Phase 0 quando richiesta.

Non procedere a memoria quando esiste una specifica formale.

Se il task utilizza:

"[TASK: <key>]"

la chiave deve essere formalmente valida e registrata secondo le regole del repository.

Marker mancante o task spec malformato:

"FAIL CLOSED".

---

REGOLE NON NEGOZIABILI

FAIL-CLOSED

Evidence:

- mancante;
- ambigua;
- contraddittoria;
- non verificabile;

=> "NEEDS_MANUAL".

Mai:

- inventare evidence;
- presumere che un check sia passato;
- presumere che un reviewer abbia approvato;
- considerare una condizione soddisfatta senza prova;
- aggirare un gate.

---

UNA SOLA ATTIVITÀ PR ALLA VOLTA

Per default è consentita:

- una sola attività attiva;
- una sola PR aperta relativa al workflow.

Se esiste già una PR aperta:

- task non correlato => "BLOCKED";
- fix della PR esistente => lavorare sullo stesso branch;
- mai aprire una seconda PR per aggirare la prima;
- mai lavorare direttamente su "main".

Una nuova PR deve partire dal "main" aggiornato dopo che la precedente è stata merged/closed, salvo policy esplicita diversa.

---

CURRENT HEAD

Lavorare esclusivamente sul current head della PR.

Se il riferimento utilizzato dall'agente non coincide con il current head:

"NEEDS_MANUAL".

Non assumere che il codice precedentemente analizzato sia ancora quello presente nel branch.

---

PHASE 0

Prima di qualsiasi patch safety-critical eseguire Phase 0.

Phase 0 deve identificare, quando applicabile:

- contratto del task;
- comportamento attuale;
- comportamento atteso;
- invarianti;
- file autoritativi;
- scope;
- rischi;
- dipendenze;
- test necessari;
- regressioni recenti;
- eventuali finding già esistenti.

Phase 0 assente o "NEEDS_MANUAL":

non patchare.

---

PATCH STRETTE

Modificare esclusivamente:

- bug reali;
- regressioni;
- blocker reali;
- requisiti espliciti del task;
- modifiche necessarie a preservare invarianti.

Non fare:

- riscritture inutili;
- refactoring non richiesti;
- cleanup generalizzato;
- "fix everything";
- modifiche cosmetiche durante una patch safety-critical.

La patch minima corretta è preferibile alla soluzione più grande.

---

SCOPE

Rispettare sempre:

- "files_allowed";
- "files_forbidden";
- task specification;
- policy del repository.

Le aree critiche devono essere considerate protette per default.

Esempi:

- core;
- runtime;
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

La classificazione effettiva deve essere determinata dal repository.

Violazione dello scope:

"FAILED".

---

MICRO-AUDIT

Dopo ogni patch e prima di test/commit/push:

1. controllare il diff;
2. verificare il task;
3. verificare i file modificati;
4. verificare lo scope;
5. verificare regressioni evidenti;
6. verificare invarianti;
7. verificare documentazione;
8. verificare test;
9. verificare eventuali bypass;
10. verificare eventuali secret;
11. verificare che non siano state introdotte modifiche non richieste.

Il micro-audit è obbligatorio.

---

PUSH / RESOLVE / RERUN

Nessun:

- push;
- resolve thread;
- rerun;
- merge;

deve essere effettuato automaticamente senza la relativa autorizzazione prevista dalla policy.

Quando presenti, utilizzare:

AUTO_PUSH_ENABLED
AUTO_RESOLVE_ENABLED
AUTO_RERUN_ENABLED

L'autorizzazione esplicita dell'owner può valere per la PR corrente secondo la policy.

Mai interpretare il silenzio dell'owner come autorizzazione.

---

DOCUMENTAZIONE

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

La documentazione deve descrivere il comportamento reale.

Mai documentare funzionalità non implementate.

Se la documentazione necessaria è fuori scope:

- non modificare arbitrariamente il file;
- richiedere estensione dell'allowlist;
- oppure dichiarare "NEEDS_MANUAL".

Se non serve modificare la documentazione:

"N/A — documentazione non impattata"

con motivazione.

---

HARD VERIFY — OBBLIGATORIO

Prima di dichiarare un task:

- implementato;
- completo;
- pronto;
- pronto al merge;

eseguire la verifica definita da:

"docs/hard_verify_spec.md"

quando presente.

La verifica deve includere, quando applicabile:

- contratto;
- current head;
- static audit;
- file autoritativi;
- test;
- compile;
- test mirati;
- wiring;
- integrazione;
- scope;
- fail-closed;
- documentazione;
- invarianti;
- build;
- packaging;
- deployment.

Il report deve contenere una delle etichette:

MISSING
PARTIAL
IMPLEMENTED_WITH_NOTE
FULLY_IMPLEMENTED
MERGED_BUT_NOT_FULLY_AUTOMATED

"PR merged" da solo non costituisce prova di implementazione completa.

---

AI PR REVIEW

Se il repository dispone di reviewer AI automatici, utilizzare le regole definite dalla configurazione e dalla documentazione del progetto.

Distinguere sempre:

Reviewer obbligatori

Reviewer che costituiscono un vero gate.

Reviewer advisory

Reviewer che forniscono feedback ma non costituiscono un gate obbligatorio.

Reviewer finali

Reviewer eventualmente richiesti per la validazione finale dell'intero range della PR.

Non inventare:

- reviewer;
- modelli;
- workflow;
- label;
- gate;

che il repository non possiede.

---

COSTO REVIEW AI

Quando vengono utilizzati modelli AI a consumo:

"max_tokens" / "MAX_OUTPUT_TOKENS" è un tetto massimo e non equivale automaticamente ai token addebitati.

Non usare un tetto basso come strategia primaria di risparmio.

Se una review viene troncata:

- aumentare il limite;
- mantenere sufficiente contesto;
- evitare chiamate duplicate.

Le strategie di risparmio corrette sono:

1. meno review duplicate;
2. meno push;
3. raggruppare i fix;
4. evitare rerun inutili;
5. usare reviewer costosi solo quando richiesto;
6. usare reasoning appropriato.

Non ridurre la qualità di un gate obbligatorio per risparmiare token.

---

REVIEW FINDINGS

A ogni check-in leggere:

- review bodies;
- inline comments;
- conversation comments;
- review threads;
- annotazioni CI;
- finding automatici.

Non limitarsi allo stato dei check.

Ogni finding deve essere classificato come:

"PATCH_REQUIRED"

Bug reale presente nel current head.

"EVIDENCE_RESOLVE"

Finding già coperto, outdated o dimostrabilmente non applicabile.

"SKIP"

Falso positivo, duplicato, cosmetico o fuori scope.

"NEEDS_MANUAL"

Ambiguo, rischioso o richiede decisione dell'owner.

I blocker non possono essere lasciati irrisolti.

---

PATCH DA REVIEW

Per ogni finding reale:

1. riprodurre il problema;
2. creare/aggiornare il test;
3. verificare il comportamento precedente;
4. applicare la patch minima;
5. verificare PASS;
6. eseguire test mirati;
7. eseguire hard verify;
8. fornire evidence nel finding.

Quando richiesto dal workflow, rispondere nel thread con:

Fatto in commit <SHA>
Test: PASS
Evidence: <file:riga/comando>

Per finding non applicabili:

Skipped / già coperto
Motivo: <motivazione>
Evidence: <prova>

Mai dichiarare risolto un finding senza evidence.

---

TEST HARD — OBBLIGATORI

Ogni modifica di codice deve avere test adeguati.

Ogni:

- nuova funzione;
- ramo;
- comportamento;
- modifica logica;

deve avere un test mirato quando tecnicamente applicabile.

Il test deve:

- utilizzare il codice reale;
- essere deterministico;
- essere riproducibile;
- fallire se la regressione ritorna;
- non usare "assert True";
- non essere esclusivamente mock-based;
- non usare "|| true";
- non dipendere da credenziali reali.

Per bug/finding:

test regression
→ FAIL sul comportamento precedente
→ patch
→ PASS

Preferire:

- stub;
- fake;
- simulation;
- temporary directories;
- database temporanei;
- fixture;
- mock delle sole integrazioni esterne.

Mai usare sistemi live nei test unitari.

---

MATRICE DI RESILIENZA UNIVERSALE

Per sistemi con stato o side effects verificare i casi pertinenti.

IDEMPOTENZA

- stesso input non produce duplicazioni;
- retry sicuri;
- operazioni ripetute non corrompono lo stato;
- stato persistente sopravvive al restart.

ERROR HANDLING

- timeout gestiti;
- errori gestiti;
- risposta ambigua => comportamento conservativo;
- errore parziale => rollback o stato esplicito;
- retry solo quando semanticamente sicuro.

PERSISTENZA

- write failure;
- rollback;
- crash recovery;
- backup;
- consistenza;
- migrazione.

CONCORRENZA

Verificare:

- race condition;
- doppia esecuzione;
- worker duplicati;
- lifecycle incoerente;
- shutdown incompleto.

CONFIGURAZIONE

Verificare:

- default sicuri;
- backward compatibility;
- config corrotta;
- save failure;
- secret handling.

INPUT

Verificare:

- input vuoto;
- input malformato;
- dati mancanti;
- valori fuori range;
- NaN/Inf quando applicabile;
- encoding inatteso;
- payload non fidato.

SICUREZZA

Verificare:

- autenticazione;
- autorizzazione;
- secret handling;
- sanitizzazione;
- injection;
- path traversal;
- privilege boundaries;
- logging sicuro.

AI / AGENT

Quando presente:

- tool access gated;
- input non fidato trattato come non affidabile;
- nessun bypass dei guardrail;
- dati insufficienti => comportamento conservativo;
- errori => comportamento conservativo;
- nessuna azione esterna senza validazione;
- nessun secret esposto al modello;
- nessun tool privilegiato senza autorizzazione.

---

TEST MANUALI

Ciò che richiede un ambiente reale deve essere dichiarato:

"MANUAL_ONLY"

Esempi:

- hardware;
- GUI;
- browser;
- device;
- produzione;
- API live;
- deployment;
- certificati;
- autenticazione reale;
- packaging specifico della piattaforma.

Mai dichiarare automaticamente testato ciò che non è stato realmente eseguito.

---

DATABASE / PERSISTENZA

Quando si modifica database o persistenza, verificare:

- compatibilità;
- migrazione;
- rollback;
- atomicità;
- concorrenza;
- crash recovery;
- backup;
- consistenza.

Le modifiche allo schema sono considerate breaking change salvo prova contraria.

Devono essere presenti, quando applicabili:

- approvazione del task;
- nota di migrazione;
- test compatibilità;
- rollback test.

Mai:

- drop silenzioso;
- riuso ambiguo di colonne;
- modifica incompatibile non documentata.

---

CONFIG / SETTINGS

Verificare:

- configurazione esistente caricata correttamente;
- nuove chiavi con default sicuri;
- backward compatibility;
- save failure;
- config corrotta;
- backup;
- secret handling;
- path portabili.

Mai introdurre un default che abiliti automaticamente una funzionalità pericolosa.

---

SECURITY

Qualsiasi modifica a:

- authentication;
- authorization;
- permissions;
- secrets;
- encryption;
- token;
- session;
- sandbox;
- command execution;
- external integrations;

deve essere considerata potenzialmente safety-critical.

Verificare:

- fail-closed;
- privilege boundaries;
- input validation;
- secret redaction;
- audit logging;
- least privilege;
- assenza di bypass.

---

RUNTIME / LIFECYCLE

Quando si modifica runtime o lifecycle verificare:

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

Nessun vecchio:

- worker;
- listener;
- poller;
- thread;
- processo;
- sessione;

deve sopravvivere accidentalmente a un nuovo ciclo.

START fallito:

nessuna sessione parzialmente attiva.

STOP:

teardown completo e verificabile.

---

API / INTEGRAZIONI ESTERNE

Verificare:

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

BUILD / PACKAGING / DEPLOYMENT

Verificare:

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

"Build not run in this environment"

Mai dichiarare artifact generati se non sono stati realmente generati.

La build non deve effettuare automaticamente:

- push;
- merge;
- deploy;

salvo policy esplicita.

---

UI / DESIGN HANDOFF

Se il progetto possiede un design handoff, esso è la fonte di verità per UI/UX.

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

aggiornare il relativo handoff nello stesso PR.

Se non esiste una superficie UI o la modifica è puramente interna:

"N/A"

con motivazione.

Mai lasciare la documentazione UI in contraddizione con il codice.

---

GATE DI CONSEGNA

Una PR non è consegnata semplicemente perché il codice compila.

È consegnata quando l'owner dispone delle informazioni necessarie per decidere.

1. REVIEW FINALE

Se la policy richiede reviewer finali sul range completo:

eseguirli prima del verdetto.

Se la review esistente copre già correttamente l'intero range:

non duplicare inutilmente una review costosa.

---

2. VERDETTO

Il verdetto deve essere:

READY_TO_MERGE

oppure:

NEEDS_MANUAL
CHECKS_PENDING
FAILED
PATCH_REQUIRED_LOOP_STOPPED

---

3. COSA È STATO FATTO

Il report deve spiegare:

- cosa cambia;
- quale problema risolve;
- quali rischi sono stati verificati;
- cosa è stato testato;
- cosa non è stato testato;
- eventuali regressioni;
- eventuali limitazioni.

Non limitarsi al changelog.

---

4. PROVA VISIVA

Quando la modifica riguarda una superficie visibile:

fornire prova visiva reale quando l'ambiente lo consente.

Mai presentare un mockup come screenshot reale.

Se non applicabile:

"N/A".

---

5. COSTO REVIEW

Se il repository dispone di uno strumento per contabilizzare le review:

calcolare il costo totale.

Segnalare review prive di dati di costo.

Non nascondere review non contabilizzate.

---

AUTO-MERGE — GATED

L'auto-merge è consentito esclusivamente quando TUTTE le condizioni previste dalla policy sono soddisfatte.

Condizioni minime:

1. tutti i check obbligatori current-head sono "SETTLED";
2. tutti i check obbligatori sono verdi;
3. zero blocker reali;
4. nessun "manual-review-required";
5. nessun "NEEDS_MANUAL" aperto;
6. nessun thread bloccante irrisolto;
7. PR able-to-merge;
8. branch protection soddisfatta;
9. PR non draft;
10. hard verify PASS;
11. test hard PASS;
12. scope pulito;
13. documentazione aggiornata;
14. reviewer finali obbligatori completati, se previsti.

Se anche una sola condizione manca:

NON auto-mergiare.

---

SAFETY-CRITICAL

Una modifica è safety-critical quando:

- "AGENTS.md" la classifica come tale;
- il progetto la classifica come tale;
- oppure la modifica può produrre conseguenze esterne significative se errata.

In assenza di classificazione specifica, considerare safety-critical almeno:

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

Default:

"SAFETY-CRITICAL => MERGE MANUALE OWNER"

salvo override esplicito valido.

---

OVERRIDE SAFETY-CRITICAL

Un override è valido solamente se:

- proviene esplicitamente dall'owner;
- è riferito al task corretto;
- è inequivocabile;
- è tracciato;
- non rimuove gli altri gate.

L'override può rimuovere solamente il divieto di auto-merge safety-critical.

Non può bypassare:

- test;
- check;
- reviewer;
- hard verify;
- scope;
- blocker;
- manual-review;
- branch protection;
- fail-closed.

---

NEED-MANUAL

Quando una decisione spetta all'owner:

1. fermarsi;
2. non aggirare il problema;
3. formulare una domanda precisa;
4. presentare le opzioni;
5. indicare il rischio;
6. annotare la domanda nella issue/task;
7. attendere la decisione;
8. registrare la decisione;
9. riprendere il lavoro dal punto corretto.

Mai interpretare il silenzio come approvazione.

---

REVIEWER IN USAGE-QUOTA / RATE-LIMIT

Per reviewer advisory:

- usage-quota => "ASSENTE";
- rate-limit => "ASSENTE";
- non aspettare indefinitamente;
- annotare l'assenza.

Per reviewer classificati come gate obbligatori:

seguire la policy specifica del repository.

Un gate obbligatorio non può essere ignorato solamente perché il provider è indisponibile, salvo carve-out esplicito.

---

POST-MERGE TRACKING

Review tardive possono arrivare dopo il merge.

Se un finding tardivo è reale:

1. identificare PR;
2. identificare head SHA;
3. identificare file/riga;
4. identificare reviewer;
5. classificare severità;
6. verificare eventuale Issue esistente;
7. creare Issue se necessaria;
8. creare fix PR dal "main" aggiornato;
9. eseguire Phase 0;
10. micro-audit;
11. test;
12. hard verify.

Non riaprire arbitrariamente una PR già mergiata.

---

REGOLA CONTRO IL RUMORE

Non creare commit per:

- falsi positivi;
- commenti cosmetici;
- preferenze di stile;
- naming soggettivo;
- formatting non sostanziale;
- finding diff-only dimostrabilmente errati.

Usare evidence.

Patchare solo problemi reali.

---

PARSIMONIA DEI PUSH

Un push può attivare:

- CI;
- reviewer AI;
- build;
- deployment checks;
- costi API.

Quindi:

- raggruppare i fix;
- evitare push cosmetici;
- evitare push per falsi positivi;
- evitare commit inutili;
- completare il lavoro prima del push quando possibile.

Preferire:

"un push completo"

rispetto a:

"molti push incrementali".

---

REGOLA D'ORO

Non cercare di "fare tutto".

Meglio:

patch piccola
+
comportamento prevedibile
+
test reale
+
evidence

che:

grande riscrittura
+
molti cambiamenti
+
test insufficienti

La catena generale da preservare, quando applicabile, è:

input
→ parsing / interpretazione
→ validazione
→ business logic
→ safety gates
→ side effect
→ riconciliazione / stato
→ persistenza

Se un progetto non possiede uno di questi livelli, non inventarlo.

Usare l'architettura reale del repository.

Qualsiasi modifica che possa rompere questa catena deve essere:

- bloccata;
- corretta con una patch stretta;
- oppure approvata esplicitamente dall'owner.

Mai sacrificare:

- sicurezza;
- integrità;
- idempotenza;
- consistenza;
- verificabilità;
- prevedibilità;

per ottenere velocità.
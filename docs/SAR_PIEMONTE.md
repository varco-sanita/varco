# Piemonte: la ricetta attraverso SIRPED, il SAR del CSI Piemonte

**Stato: scritto e verificato sulle specifiche, NON collaudato sul sistema regionale.**
Nessuna chiamata è mai partita verso i servizi della Regione Piemonte o del CSI Piemonte.
L'unico contatto è stato scaricare documenti pubblici, senza login:
- da `servizi.regione.piemonte.it`;
- l'allegato dell'avviso AP26_003 da `www.csipiemonte.it`;
- il kit A2F del Sistema TS da `sistemats1.sanita.finanze.it`.

Come è stato verificato è spiegato in fondo, in «Cosa è verificato e come».

## Fonti

Sono tutte della scheda «SIRPED» del catalogo dei servizi regionali, sezione «Documentazione»
(<https://servizi.regione.piemonte.it/catalogo/sistema-informativo-regionale-prescrizione-elettronica-dematerializzata-sirped>).
Tutte portano «Uso: Esterno». Nessuna dice «circolazione limitata».

| Documento | Cosa dice |
|---|---|
| **REL-STC-01 V04 del 02/03/2026**, *Accesso ai servizi delle ricette dematerializzate mediante autenticazione forte*, 38 pagine | il secondo fattore: Id-Sessione via mail o via OAuth2 |
| `api-docs_idsessione.yaml` | OpenAPI dei servizi OAuth2 dell'Id-Sessione |
| **SIRPED-01 V01 del 30/01/2026**, *Processo per l'autocertificazione dei gestionali* | la procedura (sez. «Cosa vuol dire certificata SIRPED») |
| il file xlsx della richiesta di autocertificazione | il modulo della procedura |
| il piano dei test **SIRPED-TES-01 V02** | i test della procedura |
| i due attestati di conformità (mail, OAuth2) | |
| **RE-SRS-SAR V05 dell'08/05/2018**, *Specifiche dei requisiti di integrazione SAR, Cartelle cliniche MMG/PLS* | il canale di base: RUPAR, cifratura, lotti, DPCM |
| **RE-TES-01 V02 del 24/10/2016**, piano dei test dell'integrazione delle cartelle cliniche | la certificazione di base |

Altre fonti:
- **AP26_003, Allegato 1**, *Specifiche tecniche del servizio*, avviso del CSI Piemonte sul flusso
  SIAP (sez. 9). La pagina principale dell'avviso non l'abbiamo trovata (`docs/BLOCCHI.md`).
- **Kit per lo sviluppo A2F del Sistema TS, ver. 20250902**: i WSDL e gli XSD di CreateAuth,
  CheckToken e RevokeAuth. REL-STC-01, par. 4.2: «I WSDL e gli XSD dei servizi sono gli stessi
  previsti da Sistema TS [A2F_STWS], con la personalizzazione regionale del contenuto di alcuni campi».

Il manifesto è `strumenti/fonti_specifiche.json`: gruppo `piemonte`, 11 voci con sha256. La copia si
scarica con `python strumenti/scarica_specifiche.py --gruppi piemonte`. Le specifiche non stanno nel
repository (`docs/TERZE_PARTI.md`).

L'indagine preliminare (`REGIONI.md`) citava REL-STC-01 **V02** del 10/12/2025. È superata dalla V04,
che è quella usata qui.

## 1. Il canale

In Piemonte il medico non chiama il SAC del MEF: chiama **SIRPED**, il SAR della Regione gestito dal
CSI Piemonte. SIRPED passa la ricetta al SAC «con la credenziale assegnata dal MEF al SAR»
(RE-SRS-SAR, par. 3.1.1). Il tracciato è quello del SAC, «in analogia al SAC».

| | |
|---|---|
| Protocollo | SOAP su HTTPS, TLS 1.2 (REL-STC-01, par. 4.1.3) |
| Tracciato | quello del SAC, **con i namespace del MEF**: l'esempio del par. 4.3.6 usa `http://invioprescrittorichiesta.xsd.dem.sanita.finanze.it` |
| Servizi di prescrizione | invio, visualizzazione, annullamento, lista degli NRE utilizzati; in più la richiesta dei lotti di NRE (RE-SRS-SAR, par. 4.2) |
| Servizi dell'Id-Sessione | CreateAuth, CheckToken, RevokeAuth (SOAP, XSD del kit A2F); OAuth2: `/oauth2/authorize`, `/oauth2/token`, `/.well-known/jwks.json`, `/sessionid/verify`, `/sessionid/revoke` |
| SOAPAction | non indicate da SIRPED. Il kit usa quelle dei WSDL del MEF e quelle del WSDL A2F |
| Collaudo | **non pubblicato**. L'unico nome di host è l'esempio del par. 4.3.6, `tst-rel-xxxx.csi.it`, con un segnaposto. Il YAML dice `servers: https://tbd/reloauthserver` |
| Produzione | **non pubblicata**: «le url di produzione dei servizi esposti dal SAR» le dà la Regione dopo l'autocertificazione (RE-TES-01, par. 2.1) |

Nel kit:
- il canale è `varco/trasporto/piemonte.py` (`CanalePiemonte`);
- l'Id-Sessione via mail è in `trasporto/piemonte_a2f.py`;
- OAuth2 è in `trasporto/piemonte_oauth2.py`;
- il servizio è `varco/ricetta/piemonte.py` (`RicettaPiemonte`).

Nessun URL di SIRPED è scritto nel codice. Il canale vuole gli URL servizio per servizio.

## 2. Autenticazione

### Primo fattore: RUPAR, sempre (nella modalità mail)

- **Credenziali RUPAR Piemonte** (utente, password, pin), una per medico, al posto di quelle del
  Sistema TS (RE-SRS-SAR, par. 3.1.1 e 6.2). La password scade ogni tre mesi (RE-TES-01, par. 2.1).
- **Pincode e CF dell'assistito cifrati con un certificato della Regione**, «emesso dalla
  Certification Authority Infocert». La cifratura è RSA PKCS#1 v1.5 più base64, come
  `openssl rsautl -encrypt -pkcs` (par. 3.1.2 e 6.4).
  - Il certificato **non è pubblico**: la Regione lo consegna con l'autocertificazione.
  - Non è SanitelCF. Per questo `RicettaPiemonte` e `ServizioIdSessione` **non hanno un cifratore
    predefinito**: va passato.

### Secondo fattore: due modalità

Lo chiedono il DM 8 giugno 2023 e il DM 27 febbraio 2025 (REL-STC-01, cap. 3). Ogni gestionale ne
realizza una, e la certifica con l'attestato corrispondente.

In tutte e due le modalità:
- il medico dev'essere censito nel **configuratore regionale degli operatori**, con ruolo,
  collocazione e profili (prescrizione, presa in carico, erogazione);
- l'Id-Sessione vale per una coppia «utente-gestionale-azienda»;
- un Id-Sessione nuovo **annulla il precedente**, anche se non è scaduto.

**1. Id-Sessione con mail certificata (MAIL)** (par. 3.1, 4.2)

1. Il medico certifica la sua mail sul Punto Unico di Accesso (PUA) con SPID, CIE o CNS, livello 2
   o più, e dà il consenso alle notifiche.
2. Il gestionale chiama **CreateAuth** con la Basic RUPAR.
   - Il pincode va cifrato in `identificativo/valore`, con `tipo = P` (par. 4.2.1; lo XSD A2F ammette
     due caratteri, «P=pincode»).
   - `contesto = RICETTA-DEM`.
   - `applicazione` porta i permessi richiesti: `prescrizione`, `erogazione`, `presa_in_carico`.
   - `infoAggiuntive/opzione APP` porta il **codice del gestionale**. Lo «rilascia la Regione
     Piemonte al momento dell'avvio dell'attività di autocertificazione» (par. 4.2.1).
3. SIRPED manda l'Id-Sessione (un UUID) per mail. Il medico lo copia nel gestionale.
4. Su ogni chiamata ai servizi della ricetta il gestionale manda (par. 4.2.5):
   - `Authorization: Basic <utente RUPAR:password>`;
   - `X-idSessione: Bearer <Id-Sessione>`;
   - `X-Gestionale: <codice gestionale>_<codice azienda>`.
5. **CheckToken** e **RevokeAuth** verificano e revocano.

Nell'**ambiente di test** CreateAuth restituisce l'Id-Sessione anche nella risposta, nelle
`comunicazioni`: `token`, `permessi`, `dataFineValidita`, `Working-mode = TEST`. Il kit lo legge
(`EsitoIdSessione.id_sessione_di_test`) **solo** se il Working-mode è `TEST`.

**2. Id-Sessione con OAuth2 (OAUTH2)** (par. 3.2, 4.3)

1. Authorization Code con **PKCE S256**: il gestionale apre `/oauth2/authorize` nel browser.
   - Il medico si autentica su GASP-RP-Salute con SPID, CIE o CNS di livello 2 o più.
   - Sceglie ruolo e collocazione e autorizza.
   - Torna alla `redirect_uri` con `code` e `state`.
2. `/oauth2/token` scambia il code con un **JWT RS256** che contiene l'Id-Sessione.
   - La chiave per la firma sta in `/.well-known/jwks.json`.
   - **Non c'è refresh token**: alla scadenza si ripete l'autorizzazione.
3. Su ogni chiamata ai servizi della ricetta: `X-OAuth2-Authorization: Bearer <JWT>`. **Niente
   Basic**, «eliminando la basic authentication (user, password e PIN)»: `pinCode` va **vuoto**
   (par. 4.3.6).
4. `/sessionid/verify` e `/sessionid/revoke` verificano e revocano.

Nel kit:
- `CanalePiemonte(ModalitaPiemonte.MAIL, credenziali=..., id_sessione=...)` oppure
  `CanalePiemonte(ModalitaPiemonte.OAUTH2, token_jwt=..., cf_medico=...)`. Le due configurazioni si
  escludono: Basic e JWT insieme non partono.
- `id_sessione` e `token_jwt` sono funzioni chiamate a ogni richiesta. Il kit non conserva segreti:
  li chiede a chi integra. `token_jwt` può restituire il `TokenPiemonte` di `scambia_codice` (è
  il modo consigliato: porta anche `expires_in`) oppure la sola stringa JWT.
- Il canale controlla, prima di mandare:
  - in OAuth2, che la scadenza sia nota e non passata: la più vicina tra `exp` del JWT e
    `expires_in` del `TokenPiemonte`. Con la sola stringa e un JWT senza `exp`, o con un `exp` non
    numerico, il canale non parte: non sa quando scade. Fino al 02/10/2026 un JWT senza `exp`
    passava sempre, anche con `expires_in` già trascorso (revisione esterna, punto 3). Un `exp`
    o un `nbf` non finito (`Infinity`) è come non numerico;
  - in OAuth2, che il JWT sia già valido: `nbf` (p. 30) non nel futuro, con un margine di 60 s per
    gli orologi (RFC 7519, par. 4.1.5). Prima nessuno lo guardava (giro 2 di revisione, N1);
  - in OAuth2, che lo `scope` del JWT contenga `prescrizione` e che il CF del token sia `cf_medico`;
  - in mail, che l'Id-Sessione abbia la forma di un UUID.
- `ServizioIdSessione` (mail) costruisce e legge CreateAuth, CheckToken e RevokeAuth.
- `ClientOAuth2Piemonte`:
  - costruisce l'URL di autorizzazione con `state` e PKCE;
  - controlla la callback (redirect, `state` a tempo costante, errore, `code`);
  - scambia il code: `scambia_codice` verifica la firma del JWT ricevuto sul JWKS prima di
    restituirlo (solo RS256/384/512; `none` e HMAC rifiutati) e controlla che `aud` sia il
    `client_id` del gestionale. Il JWKS lo chiede a `/.well-known/jwks.json`, o lo si passa
    (`jwks=`) per non chiederlo ogni volta. Rifiuta anche un JWT con `nbf` nel futuro. Un JWT che
    non passa è un `ErroreAutorizzazione`, anche quando JWT o JWKS sono malformati (`alg` non
    stringa, modulo RSA non valido, `exp` infinito): prima uscivano `TypeError`, `ValueError`,
    `OverflowError` (giro 2 di revisione, N5). Lo stesso per una risposta del servizio token con
    tipi sbagliati: `access_token` che non è una stringa, `scope` numero, `client_id` non stringa,
    `expires_in` NaN o infinito (giro 3: uscivano ancora `AttributeError` e simili).
    Fino al 02/10/2026 questa pagina lo diceva, ma `scambia_codice` controllava solo che il JWT
    fosse leggibile: la verifica era una funzione a parte (`verifica_firma`), da chiamare a mano
    (revisione esterna, punto 8);
  - chiama verify e revoke.

  Non apre il browser e non fa da server per la `redirect_uri`: quello è del gestionale.

## 3. Operazioni

Il contratto è lo stesso del SAC, `ServizioRicetta`, nella classe `RicettaPiemonte`:

| Contratto | SIRPED | Note |
|---|---|---|
| `invia` | invio prescritto | tracciato del SAC; in OAuth2 `pinCode` vuoto |
| `visualizza` | visualizza prescritto | per NRE, come nel SAC; `cf_assistito` ignorato |
| `annulla` | annulla prescritto | |
| `interroga_nre_utilizzati` | interroga NRE utilizzati | |

Le ricevute sono quelle del SAC, lette dallo stesso lettore.

Un controllo è in più: **il prescrittore della ricetta dev'essere il medico del canale**. Se c'è il
sostituto, conta il sostituto. Il controllo vale sempre, anche con `valida_localmente=False`.
Su visualizzazione, annullamento e lista degli NRE il kit accetta invece un `cf_medico` diverso da
quello del canale: nel SAC il sostituto annulla una ricetta indicando il titolare, e il kit non
impone una regola che la specifica non scrive. SIRPED fa lo stesso controllo sul CF autenticato, ma solo in
produzione (sez. 7, punto 22). Il kit lo fa sempre, così l'errore si vede già in collaudo.

## 4. Differenze dal SAC e dagli altri SAR

| | SAC (MEF) | SIST (Puglia) | SAR FVG (Insiel) | SIRPED (Piemonte) |
|---|---|---|---|---|
| Autenticazione | Basic TS + pincode + 2FA del MEF | WS-Security con la CNS | mTLS con CRS/CNS, o token federati | Basic **RUPAR** + pincode + Id-Sessione via mail, **oppure** solo JWT OAuth2 |
| 2FA | `Authorization2F` (A2F del MEF) | la CNS | la carta | `X-idSessione` (A2F con XSD del MEF, servizio regionale) o `X-OAuth2-Authorization` |
| Software | — | `datiApplicativo` | `User-Agent` + `prodottoCme` | `X-Gestionale` e `APP` = codice gestionale (dato dalla Regione) + azienda |
| Pincode | cifrato SanitelCF | assente | vuoto | cifrato col certificato **regionale**; vuoto in OAuth2 |
| CF dell'assistito | cifrato SanitelCF | in chiaro nel TLS | cifrato | cifrato col certificato regionale |
| Tracciato | XSD del MEF | CVP + CDA2 | SAC con namespace FVG | **XSD del MEF**, stessi namespace |
| Lotti NRE | facoltativi | — | MIR | lotti regionali da 1000 NRE (RE-SRS-SAR, par. 3.2, 4.2) |
| Collaudo | pubblicato | pubblicato | pubblicato | **non pubblicato** |
| Produzione | pubblicata | pubblicata | non pubblicata | non pubblicata |

## 5. Il modello dati ha tenuto, senza modifiche

SIRPED sta sotto lo **stesso** modello (`Ricetta`, `Riga`, `Prescrittore`, `Assistito`,
`CriteriNreUtilizzati`) e lo **stesso** contratto `ServizioRicetta`. **Zero campi nuovi, zero firme
cambiate.** Il codec è quello del SAC (`xml_sac`), senza modifiche.

- Tutto ciò che SIRPED chiede in più è del **canale**, non della ricetta:
  - credenziali RUPAR;
  - Id-Sessione o JWT;
  - codice gestionale e azienda (`GestionalePiemonte`);
  - adesione (`AdesionePiemonte`);
  - certificato di cifratura.
- **Il pincode vuoto dell'OAuth2** non ha richiesto un campo nuovo. Il codec cifra un segnaposto,
  e `RicettaPiemonte` svuota `<pinCode>` dopo la codifica. Il risultato valida contro gli XSD del MEF:
  `pinCode` vuoto sì, assente no.
- `visualizza(..., cf_assistito=)` si ignora come nel SAC.
- Un test (`test_il_modello_non_e_cambiato`) fissa l'elenco dei campi del modello. Se un giorno
  servisse cambiarlo per il Piemonte, il test lo farebbe vedere.

## 6. Ambiente di test e come ci si accede

Cosa dicono i documenti:
- l'ambiente di test è il SAR di test collegato al SAC di test del MEF, «via internet» (RE-TES-01,
  cap. 3);
- **i dati per il test li dà il CSI dopo la richiesta di autocertificazione**: «utenze, assistiti,
  ricettari, la CA Root Certificate, la chiave pubblica del certificato per la cifratura dei dati,
  le url di test» (RE-TES-01, par. 3.2). Per l'autenticazione forte c'è un «kit» diverso per la
  modalità mail e per l'OAuth2 (SIRPED-01, par. 3.2);
- i valori del modulo xlsx (colonne G e H) servono «esclusivamente» a configurare l'ambiente di
  test (SIRPED-01, par. 3.1). Le altre colonne valgono per test e produzione;
- il canale di supporto tecnico è **supporto.sar@csi.it** (SIRPED-01, cap. 4).

Quindi: **senza la procedura non c'è nemmeno l'indirizzo dell'ambiente di test.** Non esiste un
ambiente aperto come quello del MEF.

Nel kit il collaudo è chiuso da tre serrature (`varco/ambienti.py`):
1. `TrasportoHTTP(consenti_collaudo_regionale=True)`, lo stesso flag di SIST e FVG;
2. `TrasportoHTTP(collaudi_piemonte={"<host>"})`: l'host di collaudo **dichiarato per nome**. Visto
   che nessun host è pubblicato, il nome da solo non basta;
3. un'`AdesionePiemonte` (riferimento della richiesta di autocertificazione e codice gestionale) nel
   canale e nel client OAuth2.

L'URL di autorizzazione OAuth2 lo apre il browser, non il trasporto. Per questo il client applica la
guardia già quando lo costruisce: il medico non arriva ad autenticarsi su un sistema che il kit poi
non potrebbe chiamare.

**Regola della guardia.** Ogni host sotto `piemonte.it`, `csi.it`, `csipiemonte.it`,
`salutepiemonte.it`, `sistemapiemonte.it` o `ruparpiemonte.it` conta come **produzione**.
Fa eccezione un host con un'etichetta che comincia o finisce con `tst`, `test` o `collaudo` (come
`tst-rel-xxxx.csi.it`): quello è un collaudo e vuole le serrature 1 e 2.
- La regola è un'euristica, e per questo non basta da sola.
- Un host di produzione dichiarato come collaudo resta bloccato.
- Verso `localhost` basta un `gestionale_di_prova`.

## 7. Cose che nelle specifiche non tornano

Sono qui perché chi integra non perda tempo, e per chiederle al CSI. Nessuna è stata «corretta» di
nascosto nel codice. Dove si è dovuto scegliere, la scelta è dichiarata. I punti 1-4 sono fissati in
test che si accorgono se la specifica cambia.

**Schemi e campi (Id-Sessione via mail)**

1. **Tre permessi non stanno in `applicazione`.** Il par. 4.2.1 ammette più permessi separati da
   spazio. `prescrizione erogazione presa_in_carico` fa 39 caratteri, ma lo XSD A2F dice
   `applicazioneType`, `maxLength 30`. Il kit rifiuta prima di mandare.
2. **Il pincode cifrato potrebbe non stare in `valore`.** `identificativo/valore` è
   `stringTypeMax256`. Un cifrato RSA in base64 fa:
   - 172 caratteri con una chiave da 1024 bit (come SanitelCF);
   - 344 con una da 2048.

   Ci stanno solo chiavi fino a 1536 bit. La dimensione della chiave regionale non è pubblicata. Il
   kit rifiuta un cifrato oltre 256 caratteri con un messaggio che lo spiega. **Da chiedere.**
3. **`codSsa` «opzionale» non può essere vuoto.** `codSsaType` ha `minLength 5`: va omesso, non
   mandato vuoto. Il kit lo omette.
4. **Nell'esempio di errore del par. 4.2.4** c'è un `<errore>` direttamente sotto la radice, senza il
   contenitore `<errori>` dello XSD, e un «>» in più all'inizio del testo di `descrEsito`
   (`<a:descrEsito> >Errore di configurazione...`). Il lettore del kit accetta tutte e due le
   posizioni di `<errore>` (risposta sintetica `crea_errore_forma_esempio.xml`). Il «>» in più, se
   arrivasse davvero, resterebbe nel testo della descrizione: è solo testo, il kit non lo toglie.
5. **Refusi nei nomi dei campi della tabella:**
   - `livelloAautenticazione` e `modAautenticazione`;
   - il formato di `autenticazioneTs` scritto «HH:mm.ss.SSSS».
6. **I valori di `applicazione`** (`prescrizione`, ...) non sono quelli del kit A2F del MEF
   (`PRESCRITTORE`, `EROGATORE`). Il kit usa quelli regionali.

**OAuth2**

7. **L'esempio PKCE in shell è sbagliato** (par. 4.3.1): `openssl ... | tr -d "=+/"` **cancella**
   `+` e `/` invece di tradurli in `-` e `_` come vuole il base64url della RFC 7636. Abbiamo
   simulato l'esempio su verifier casuali:
   - circa il 73% dei verifier esce sotto i 43 caratteri, il minimo della RFC e della specifica
     stessa;
   - circa il 73% delle challenge non è quella della RFC.

   Un server conforme le rifiuterebbe. Il kit segue la RFC: il vettore di prova
   dell'appendice B torna. Il test `test_pkce_come_la_rfc_7636_e_non_come_l_esempio_in_shell`
   mostra la differenza.
8. **JWKS**: la tabella del par. 4.3.3 chiama «v» il modulo RSA, l'esempio (e la RFC 7517) «n». Il
   kit accetta tutte e due.
9. **`kid`**: la tabella dice «rel-oauth2-key», fisso. L'esempio di access_token ha in testata un
   UUID. Il kit, se il `kid` non corrisponde e il JWKS ha una sola chiave RSA, prova quella: decide
   la firma. Con più chiavi e nessun `kid` giusto rifiuta.
10. **Revoca: DELETE o GET?** Il testo e il piano dei test dicono DELETE. L'esempio e il YAML dicono
    GET. Il kit usa DELETE e lascia scegliere GET (`revoca_sessione(metodo="GET")`).
11. **`expires_in`** è descritto come «Unix time», ma l'esempio vale 7199, cioè una durata. Il kit
    tratta `expires_in` come durata se è sotto 10⁹ e come istante se è sopra, e prende la scadenza
    più vicina tra quella e l'`exp` del JWT.
12. **Percorso del JWKS**: `/.well-known/jwks.json` nella tabella,
    `/reloauthserver/.well-known/jwks.json` nell'esempio. Il kit lo costruisce dalla base che gli si
    dà.
13. **`X-Gestionale` in OAuth2**: il par. 4.3.6 non lo nomina e dice «Non valorizzare altri parametri
    negli header di autenticazione». Il kit non lo manda (il gestionale è già il `client_id` del
    token). **Da confermare.**
14. **La visualizzazione non è nell'elenco.** I par. 4.2.5 e 4.3.6 elencano i servizi che vogliono
    l'Id-Sessione o il JWT: invio, annullamento, lista degli NRE, servizi di erogazione. Manca
    «visualizza prescritto», ma il piano dei test ha casi di visualizzazione in tutte e due le
    modalità. Il kit manda Id-Sessione o JWT anche lì. **Da confermare.**

**Procedura e dati**

15. **Quale «azienda» per un fornitore di cartelle MMG?** `X-Gestionale` è `<codice>_<azienda>`.
    L'esempio usa `301`, un'ASL. SIRPED-01 dice che i fornitori certificano «per tutti i loro
    clienti» senza codice azienda. Il kit vuole un codice di tre cifre esplicito. **Da chiedere.**
16. **Codici d'errore non pubblicati**: solo `9998`. Il server finto usa testi suoi.
17. **Errori di Id-Sessione e JWT sui servizi di prescrizione: SOAP Fault o ricevuta `9999`?** Non
    detto. Il server finto usa il Fault, il kit gestisce tutti e due.
18. **Lotti**: la cartella numera le ricette con lotti regionali da 1000 NRE (RE-SRS-SAR, par. 3.2,
    4.2). Non è detto se SIRPED assegni un NRE quando la ricetta arriva senza. Il kit non gestisce i
    lotti (sez. 8).
19. **Promemoria**: RE-SRS-SAR, par. 4.4.3 rimanda al layout del MEF e aggiunge una riga regionale,
    «codice regionale (7 caratteri) – nominativo» del medico, sotto il codice fiscale. Non è detto se
    SIRPED restituisca il PDF del promemoria come il SAC.
20. **RE-SRS-SAR V05 (2018)** cita riferimenti MEF vecchi. Per il tracciato il kit usa le specifiche
    MEF correnti, che sono già nel kit.
21. **La password RUPAR scade ogni tre mesi** (RE-TES-01, par. 2.1): un invio fallito per password
    scaduta è normale amministrazione. Il kit riporta l'errore del server così com'è, non lo «ripara».
22. **Il controllo tra CF del prescrittore e CF del token** è attivo solo in produzione: «In ambiente
    di test tale controllo non sarà attivo» (SIRPED-TES-01, par. 3.2). Un gestionale può passare il
    collaudo e fallire in produzione. Il kit fa il controllo sempre (sez. 3).
23. **REGIONI.md cita la V02**, superata dalla V04 (sopra).
24. **Il manuale A2F del MEF** contiene nell'esempio un codice fiscale con carattere di controllo
    valido. Non è un documento piemontese. Non l'abbiamo copiato.

## 8. Cosa non è implementato

- **Lotti di NRE regionali** (`RichiestaLotto`, RE-SRS-SAR par. 4.2): lo schema non è pubblicato
  fra i documenti SIRPED.
- **Ricetta in regime DPCM** (ricetta rossa con `TipoInvio = RPS`, RE-SRS-SAR par. 3.5, 4.3): non è
  nel kit per nessun canale.
- **Presa in carico ed erogazione** (fase 2): il kit è per il prescrittore. Il permesso c'è, i
  servizi no.
- **La finestra del browser e il server della `redirect_uri`** per l'OAuth2: sono del gestionale. Il
  kit dà l'URL e controlla la callback.
- **La mail dell'Id-Sessione**: il medico la riceve e la copia. Il kit non legge la posta.
- **Il catalogo regionale delle prestazioni** (RE-SRS-SAR par. 3.4): il modello ha `codice_catalogo`,
  ma il kit non contiene il catalogo piemontese.
- **La riga regionale del promemoria** (codice regionale e nominativo del medico, RE-SRS-SAR
  par. 4.4.3): il kit non genera promemoria, restituisce il PDF che manda il servizio, se lo manda.
- **I collegamenti con l'anagrafe regionale degli assistiti** (AURA, RE-SRS-SAR cap. 5): fuori
  ambito.

## 9. Cosa è verificato e come

Tutto senza rete verso la Regione e verso il CSI.

**Schemi ufficiali**
- Ogni richiesta di prescrizione, nelle due modalità, valida contro gli **XSD del MEF** inclusi nel
  kit:
  - invio, anche col sostituto;
  - visualizzazione;
  - annullamento;
  - lista degli NRE.

  In OAuth2 `pinCode` è vuoto. Controllo negativo: senza `pinCode` lo XSD boccia.
- CreateAuth, CheckToken e RevokeAuth validano contro gli **XSD del kit A2F** (se scaricati).
  Controlli negativi, dove lo XSD morde:
  - tre permessi;
  - `codSsa` vuoto;
  - pincode cifrato con una chiave da 2048 bit.
- Namespace e SOAPAction A2F sono confrontati con `targetNamespace` e WSDL del kit.
- Le 12 risposte A2F sintetiche validano contro lo XSD A2F, tranne quella nella forma dell'esempio
  del par. 4.2.4: il test controlla proprio che **non** validi.

**Server finto** (`strumenti/piemonte_server_finto.py`, HTTP su 127.0.0.1)
- Sui servizi di prescrizione controlla:
  - SOAPAction;
  - XSD del MEF;
  - nella modalità mail: Basic RUPAR, `X-idSessione` (rilasciato, non scaduto, non revocato, dello
    stesso utente e gestionale) e `X-Gestionale` censito;
  - nella modalità OAuth2: JWT firmato, non scaduto, non revocato, e niente Basic;
  - pincode decifrato con la chiave «regionale» di prova;
  - a richiesta, prescrittore uguale all'utente autenticato.
- Sull'A2F:
  - un Id-Sessione nuovo invalida il precedente;
  - le comunicazioni di TEST;
  - gli stati 0/1/2 di CheckToken;
  - la doppia revoca.
- Sull'OAuth2:
  - `authorize` risponde con un redirect;
  - `token` controlla PKCE, code monouso e `redirect_uri`;
  - JWKS;
  - verify;
  - revoke in GET e DELETE.

**Giro completo** (`tests/unit/test_piemonte.py`)
- Mail: crea → invia → visualizza → lista → annulla e doppio annullamento → verifica → revoca →
  invio rifiutato.
- OAuth2: autorizza → token → firma verificata sul JWKS → invio con `pinCode` vuoto → verifica →
  revoca (200, poi 401) → annullamento rifiutato.
- Negativi:
  - credenziali e pincode sbagliati;
  - pincode cifrato col certificato sbagliato (SanitelCF invece di quello «regionale»);
  - Id-Sessione mancante, sconosciuto, sostituito da uno nuovo, scaduto, revocato;
  - gestionale sconosciuto o senza il permesso;
  - header mancanti;
  - Basic insieme al JWT;
  - sostituto e codice `1125`;
  - controllo del prescrittore (`1212`);
  - `state` alterato, `access_denied`, PKCE sbagliato, code riusato, `redirect_uri` diversa;
  - JWT alterato, `alg: none`, HMAC;
  - JWT di un altro medico, o con `sub` e `cfutente` diversi;
  - JWKS con più chiavi e JWT senza `kid`;
  - scadenza lato server.
- Revisione esterna del 02/10/2026 (`test_rev*`), controlli aggiunti al server finto dalla
  specifica:
  - scope concessi = richiesti ∩ profili dell'utente sul configuratore (p. 22-23); nessuno in
    comune: `access_denied`;
  - il code resta legato all'utente che ha autorizzato;
  - CheckToken e RevokeAuth solo per il gestionale (APP) a cui l'Id-Sessione è stato rilasciato;
  - revoca con un JWT scaduto: 401 (p. 36), anche se l'Id-Sessione è valido.
- Giro 2 di revisione (`tests/unit/test_revisione_giro2_sar_piemonte.py`):
  - JWT con `nbf` nel futuro (p. 30): rifiutato nel SAR, 401 in verify e revoke;
  - l'Id-Sessione del JWT deve appartenere alla coppia `sub`/`aud` del token (p. 30, cap. 3):
    un JWT di A con l'Id-Sessione di B, o di un altro gestionale, è «non rilasciato» nel SAR e 401 in
    verify e revoke;
  - SAR con sessione scaduta e JWT ancora valido: «Id-Sessione scaduto»;
  - `redirect_uri` registrata con una query (`?tenant=301`): il ritorno aggiunge `&code=...`;
  - il runner dei casi `PIE-*` non confonde `false` con `0` e un campo assente con `null`.

**Guardia**
- Host parametrizzati: produzione, collaudo, finti-simili (`attestazioni.regione.piemonte.it`,
  `latest.csi.it`, `csi.it.example.org`).
- Il collaudo vuole flag **e** dichiarazione **e** adesione (anche nel client OAuth2). La produzione
  resta bloccata anche col flag e la dichiarazione, compreso l'URL di autorizzazione per il browser.
- Blocco prima di aprire la rete, verificato con la rete vietata nel test, su:
  - servizi di prescrizione;
  - A2F;
  - OAuth2.

**Registro** (`varco/trasporto/registro.py`)
- Redatti nel corpo:
  - `userId` e `cfUtente`;
  - il `token` delle comunicazioni (l'Id-Sessione di TEST);
  - ogni UUID;
  - ogni JWT;
  - nei form e nel JSON OAuth2, `code`, `code_verifier`, `access_token`, `refresh_token` e
    `id_token`, anche se non hanno la forma di un JWT.
- Mascherati negli header: `X-idSessione` e `X-OAuth2-Authorization`. `X-Gestionale` resta in
  chiaro: è il codice del software, non un dato personale.

**Suite di conformità**
- 44 casi `PIE-*`:
  - lettura delle risposte A2F;
  - codifica di prescrizioni e A2F;
  - intestazioni;
  - PKCE (col vettore RFC 7636);
  - JWT.
- La parte XSD A2F richiede `--xsd-a2f` o `$VARCO_XSD_A2F`: senza, il passo è SALTATO, non verde.
- Gruppo di controllo: 17 casi con un'aspettativa cambiata risultano FALLITO.

Il server finto è scritto da noi leggendo la stessa specifica: **dimostra che il kit fa ciò che noi
abbiamo capito, non che SIRPED lo accetti.** Per questo serve il collaudo vero.

## 10. Cosa manca per il collaudo vero

Dalla Regione e dal CSI, nell'ordine in cui servono:

1. **Poter entrare nella procedura come progetto nuovo.** L'autocertificazione 2026 è scritta per i
   «gestionali precedentemente certificati». Un gestionale nuovo passa prima dal piano del 2016
   (RE-TES-01)? (sez. 11)
2. il **codice gestionale** (`APP`, `X-Gestionale`) e quale **codice azienda** usa un fornitore di
   cartelle MMG (sez. 7, punto 15);
3. il **kit di test**:
   - URL di test;
   - CA del server;
   - **certificato regionale di cifratura** (e quanti bit ha, sez. 7, punto 2);
   - utenze RUPAR di test con il pin;
   - medici e assistiti di test censiti nel configuratore;
   - mail certificata di prova per la modalità MAIL;
4. per l'OAuth2:
   - la registrazione del `client_id` e della `redirect_uri`;
   - un'identità SPID/CIE di test per GASP-RP-Salute;
5. le **risposte** su:
   - revoca DELETE o GET;
   - `expires_in`;
   - `kid`;
   - `X-Gestionale` in OAuth2;
   - Fault o `9999`;
   - codici d'errore (sez. 7);
6. se il kit deve gestire i **lotti regionali**, lo schema di `RichiestaLotto`.

Con questo, il kit si collauda così:
- `TrasportoHTTP(consenti_collaudo_regionale=True, collaudi_piemonte={"<host di test>"})`;
- un'`AdesionePiemonte` vera;
- `CanalePiemonte(url={...})` con gli URL del kit di test;
- la suite `conformita` (famiglia `piemonte`, con `--xsd-a2f`);
- il piano SIRPED-TES-01, eseguito e conservato con le richieste e le risposte, come chiede la
  procedura.

## 11. Cosa vuol dire «certificata SIRPED»

Vuol dire **autocertificata**. Non c'è un ente terzo che prova il software. Il fornitore esegue da
sé un piano di test sull'ambiente di test di SIRPED, lo firma e lo dichiara alla Regione. Poi la
Regione, tramite il CSI, gli dà ciò che serve per la produzione.

**La certificazione di base (2016)**, RE-TES-01 V02 del 24/10/2016, par. 2.1:
1. «la Regione fornisce il piano dei test ... ed i dati da utilizzare»;
2. «i casi di test vengono eseguiti in autonomia dai fornitori»;
3. il fornitore tiene agli atti «il piano dei test opportunamente redatto e firmato» con gli XML
   delle richieste e delle risposte;
4. comunica alla Regione e al CSI di aver completato il piano, con le eventuali «non applicabilità»
   motivate. Se il supporto tecnico trova incongruenze, la Regione ricontatta il fornitore;
5. «a conclusione con esito positivo», la Regione fornisce:
   - la CA Root del canale SSL;
   - la chiave pubblica per la cifratura dei dati;
   - gli URL di produzione.

Il piano contiene 139 codici di test distinti (contati sul testo estratto): lotti, ricette DM
farmaceutiche e specialistiche, ricette DPCM, stampa, visualizzazione, annullamento, lista degli NRE.

**L'autenticazione a più fattori (2026)**, SIRPED-01 V01 del 30/01/2026. Si applica ai gestionali
«già integrati ... e precedentemente certificati». Per loro:
1. mail a **supporto.sar@csi.it** con il modulo xlsx compilato;
2. il CSI manda il piano **SIRPED-TES-01** e il kit di test (diverso per mail e OAuth2);
3. il fornitore esegue i test **obbligatori** e quelli **consigliati**. Solo i consigliati possono
   essere «non applicabili». Il piano è superato quando tutti i test eseguiti hanno l'esito atteso;
4. conserva piano, richieste e risposte, «da esibire su richiesta della Regione»;
5. manda l'**attestato di conformità firmato** (mail o OAuth2) a sanitadigitale@regione.piemonte.it e
   supporto.sar@csi.it;
6. la Regione, tramite il CSI, chiede le informazioni tecniche e configura la produzione.

**Scadenze**: fase 1 (prescrizione) entro il **30/04/2026**; fase 2 (presa in carico ed erogazione)
entro il **30/09/2026**. Sono tutte e due passate alla data di questo documento (01/10/2026).

**Cosa non è pubblicato:**
- criteri di valutazione oltre al «tutti i test con esito atteso»;
- un elenco pubblico dei gestionali certificati;
- tempi di risposta della Regione e del CSI;
- **la procedura per un gestionale nuovo**. Il documento 2026 la prevede solo per chi è già
  certificato. Che il piano del 2016 valga ancora per un nuovo ingresso è una domanda da fare
  (sez. 10, punto 1).

**Per Varco vuol dire concretamente:** chi lo integra in una cartella clinica fa la procedura come
fornitore, col suo nome. Il kit gli dà:
- il modulo e la suite di conformità;
- gli scambi redatti, da allegare al piano;
- i difetti già noti della specifica (sez. 7).

Il kit **non** è «certificato SIRPED» e non lo dichiara.

## 12. L'avviso AP26_003 e il software aperto

L'Allegato 1 dell'avviso AP26_003 del CSI Piemonte riguarda il **flusso SIAP** (Sistema Informativo
dell'Assistenza Primaria, PNRR M6C2 1.3.2, DM 4 agosto 2025), non la ricetta.
- Il CSI coinvolge i fornitori delle cartelle cliniche MMG/PLS «già certificate per l'integrazione
  con il sistema SIRPED». Li considera «titolari e/o aventi la piena disponibilità dei relativi
  software (infungibilità dei servizi resi dagli stessi ai sensi delle Linee guida ANAC n. 8)».
- Il modulo SIAP va sviluppato entro il 20/07/2026 e collaudato dal CSI entro il 24/07/2026. Il
  corrispettivo si paga solo dopo un collaudo positivo.
- «Intesa la piena ed esclusiva titolarità del modulo in capo all'OE», le evoluzioni successive vanno
  nel contratto di manutenzione fra il fornitore e i medici.

Qui c'entra perché mostra **come la certificazione SIRPED diventa un requisito anche per altri
lavori**, e perché oggi la Regione paga integrazioni a fornitori «infungibili».

Il software aperto del CSI e della Regione esiste (organizzazione GitHub `regione-piemonte`, 117
repository):
- `fonti-fse`, `webappmed-fse`, `cod-fse`, `farab-fse`, `gatefire` (gateway di firma digitale) e
  `lcce` (configuratore operatori e PUA) sono **EUPL-1.2-or-later**, la stessa licenza di questo kit;
- `imr-fse` è GPL-2.0.

**SIRPED e un client di prescrizione non sono pubblicati**: non c'è codice da riusare per questo
canale. La licenza comune rende comunque il CSI un interlocutore naturale (`docs/PROPOSTA_PIEMONTE.md`).

---
Licenza di questo documento: CC-BY-4.0.

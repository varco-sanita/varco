# Registro delle modifiche

Formato: una sezione per versione, la più recente in alto. Versioni secondo
[Semantic Versioning](https://semver.org/lang/it/): finché la versione è 0.x l'interfaccia
può cambiare tra un rilascio e l'altro.

## [0.1.0] — 03/10/2026

Prima versione, col nome **Varco**. Repository: <https://github.com/varco-sanita/varco>
(pubblicato il 03/10/2026).

### SAR della Regione Umbria (PuntoZero) (03/10/2026)

- Quarto SAR regionale, il primo REST: `varco.ricetta.RicettaUmbria` (invio, visualizza, annulla, NRE utilizzati, richiesta del lotto NRE, dichiarazione di sostituzione, annullamento dopo un invio incerto), codec `varco.ricetta.json_umbria` sul JSON dell'OpenAPI pubblicata da PuntoZero, canale `varco.trasporto.CanaleUmbria` (mutua autenticazione TLS, JWT `Authorization` e `FSE-JWT-Signature` firmati RS256/384/512 con `x5c`, claim per servizio). Stesso modello dati e stesso contratto `ServizioRicetta`; consegna dalla stessa `trasporto.http.consegna`.
- Guardia: ogni host di `umbria.it` e `puntozeroscarl.it` è produzione, salvo l'host di test della wiki con il flag del collaudo regionale e un'`AdesioneUmbria`. Nessuna chiamata ai sistemi umbri, nemmeno al test; i certificati di test pubblici non si scaricano.
- `strumenti/umbria_server_finto.py`: server HTTPS locale con mTLS che verifica i due JWT e i corpi sugli schemi dell'OpenAPI; risposte sintetiche in `conformita/risposte/umbria/`.
- Conformità: famiglia `umbria`, 28 casi (`UMB-001`..`UMB-014`, `UMB-101`..`UMB-114`); con l'OpenAPI scaricata (`--gruppi umbria`, sha256 nel manifesto) le richieste si validano anche contro quella (`--openapi-umbria`, `$VARCO_OPENAPI_UMBRIA`).
- Documentazione: `docs/SAR_UMBRIA.md` (18 punti delle specifiche in sez. 7), bozza `docs/PROPOSTA_UMBRIA.md`. Dipendenza di test in più: PyYAML.
- Revisione esterna prima del merge (GPT-6 Astra, rapporto in `kit-mmg-review/2026-10-03-dopo-pubblicazione/revisione-umbria/`): quattro bug alti corretti, test `tests/unit/test_revisione_umbria.py`.
  - B1: senza adesione `CanaleUmbria` consegna con `consegna(..., solo_locale=True)`: per tutta la chiamata la guardia ammette solo localhost e il trasporto perde i permessi, quindi nessun redirect porta i JWT fuori da localhost, nemmeno col flag del collaudo. Guardie annidate (`TrasportoHTTP` dentro `consegna`): valgono i permessi più stretti, la guardia interna non li rimette più (trovato dalla verifica mirata).
  - B2: registro, nel JSON si tolgono le componenti del lotto NRE (`lotto`, `codLotto`, `codRagLotto`, `identificativoLotto`) e il testo di `esito`, `tipoErrore`, `nota`, `title`, anche dentro un oggetto; resta leggibile solo un codice di al più quattro cifre.
  - Dopo la seconda verifica mirata (limite di due raggiunto, `docs/BLOCCHI.md`): un thread avviato dentro una guardia annidata resta della chiamata quando quella annidata finisce; nel JSON dei servizi `umbria.*` il registro usa una allowlist (`CHIAVI_JSON_LEGGIBILI_UMBRIA`). Corretti con test, non riverificati da un revisore esterno.
  - B3: `TrasportoHTTP` tratta una risposta troncata o malformata (`http.client.HTTPException`) come errore di trasporto; dopo un invio qualunque guasto del trasporto è `InvioIncertoUmbria`.
  - B4: le ricevute si controllano contro gli schemi delle risposte dell'OpenAPI (`json_umbria.SCHEMI_RISPOSTE`, confrontati con l'OpenAPI da un test); fuori schema è `ErroreTrasporto` (dopo un invio `InvioIncertoUmbria`), non più `ConfigurazioneNonValida` né un esito «0000».
  - Medi e basso (server finto su `opzioni` e SmartCUP, `annulla` del contratto comune senza CF dell'assistito, tipi dei dati importati da JSON, durata frazionaria dei JWT): issue aperte.

### Correzioni delle issue del giro 3 (03/10/2026)

- CI verde su ubuntu, macOS e Windows (Python 3.11 e 3.12): file temporaneo di Saxon chiuso prima della lettura su Windows, `.gitattributes` senza conversione dei fine riga (#13).
- Puglia: CDA degli assicurati esteri con identificativi TEAM e personale, `displayName` del motivo di non sostituibilità, WS-Security accettata solo nell'header (#3, #4, #5).
- FVG: un CF ordinario che comincia per «STP» non è più scambiato per un codice STP; carta e medico controllati a ogni chiamata; campi solo farmaceutici rifiutati sulla specialistica (#6, #7, #8).
- Piemonte e conformità: il server finto rispetta i permessi del gestionale e rifiuta `nbf`/`exp` non finiti; l'esecutore Java accetta `errori_contengono: []` come il Python (#9, #10, #11).
- Registro e guardia: credenziali in dichiarazioni di namespace e commenti XML redatte, `code` redatto nel JSON OAuth2; i thread avviati durante una chiamata ereditano la guardia, quelli estranei non fanno più fallire una risposta già arrivata (#1, #2, #12).

### Nome: da kit-mmg a Varco (03/10/2026)

- Il progetto si chiama Varco: un varco è un'apertura in una barriera; Varco non abbatte i cancelli dei sistemi pubblici, apre un passaggio uguale per tutti.
- Pacchetto Python `varco` (prima `kit_mmg`), progetto `varco` in `pyproject.toml`, comando `varco-conformita` (prima `kit-mmg-conformita`).
- Variabili d'ambiente `VARCO_*` (prima `KITMMG_*`). Per questa versione le vecchie valgono ancora, se la nuova non c'è, con un avviso `varco.ambiente.VariabileDeprecata` (FutureWarning); dalla prossima spariscono. Test: `tests/unit/test_ambiente.py`.
- Identificativi di prova nei casi e nei server finti: `VARCO`, `VARCO-PROVA`, `VARCO_301`; User-Agent `varco/0.1 (+EUPL-1.2)`. JWT e JWKS sintetici di `conformita/risposte/piemonte/` rigenerati (`aud` e `clientid` `VARCO_301`, chiave nuova).
- `prove/` non è stata toccata: le prove precedenti alla rinomina usano `kit_mmg` (nota in `prove/INDICE.md`).

### Schemi HL7 fuori dal repository (03/10/2026)

- Gli 11 XSD del CDA R2 e lo schematron del PSS v4.0 non sono più nel repository né nel pacchetto: la HL7 IP Policy non ne autorizza la redistribuzione a un non membro, e lo schematron traduce una guida di HL7 Italia «All Rights Reserved» (`docs/TERZE_PARTI.md`). Si scaricano dalla fonte ufficiale (`it-fse-catalogs` @ `141be7f0`, sha256 fissati) con `strumenti/scarica_specifiche.py --gruppi cda-xsd`; il gruppo è nei default dello script e la CI lo scarica prima dei test.
- `ValidatoreLocale` li legge da `$VARCO_CDA_XSD` e `$VARCO_SCHEMATRON` o dalla cartella dello script; se mancano solleva `SchemiNonTrovati` con le istruzioni. Il motore di conformità non sceglie il validatore locale senza schemi (casi FSE SALTATI) e rende SALTATO il passo `codifica_sist_cda`.
- Nei test `tests/conftest.py` collega la cartella scaricata. Un test che usa gli schemi lo dichiara (`@pytest.mark.schemi_hl7`): se mancano PRIMA dell'esecuzione risulta SALTATO senza girare; dopo l'esecuzione nessun esito si cambia (giro 3 di revisione, sotto). Test: `tests/unit/test_schemi_hl7.py`.

### Giro 3 di revisione esterna — correzioni (03/10/2026)

- Verifica mirata finale (GPT-6 Astra, 03/10/2026): tutti i bug alti del giro 3 risultano chiusi. Ultima correzione: un IP risolto da un host consentito vale anche nella forma IPv4-mapped e viceversa (stessa destinazione, una sola chiave); la forma mappata di un IP di produzione resta bloccata. Test `test_g4_n1_*`.

Rapporti in `kit-mmg-review/2026-10-02-giro3/`. Ogni correzione ha un test che riproduce il controesempio del revisore (rosso prima, verde dopo) e test «famiglia» sulle varianti dello stesso principio.

#### Area 1 — guardia e registro (test: `tests/unit/test_revisione_giro3_sac_guardia_registro.py`)

- N1 (alto), guardia: l'host si valuta quando i byte stanno per partire. `socket.connect` verso un IP passa solo se l'IP è stato restituito dal modulo `socket` per un NOME che passa la guardia adesso (o è loopback, o c'è il flag di produzione): un IP mai risolto davanti alla guardia vale come IP letterale, cioè produzione. Ogni scrittura su una connessione `http.client` (evento `http.client.send`: urllib, requests/urllib3) rivaluta host e tunnel: una connessione di produzione già aperta nel pool è fermata prima che richiesta e `Authorization` partano.
- N2 (alto), registro: errori e header del meta passano da `Redattore.metadato`: XML e JSON scritti dentro il testo si leggono e si redigono come i corpi, a ogni profondità; markup che il parser non legge non si scrive (segnaposto). Vale anche per lo User-Agent; l'errore si legge dagli argomenti dell'eccezione come testo (stringhe, bytes decodificati, contenitori come JSON), non da `repr`, che raddoppiava le barre rovesciate: `pass\u0077ord` sfuggiva, anche dentro un argomento `bytes`. Varianti trovate dalla verifica mirata del giro 4.
- N3 (alto), registro: `faultstring` e `detail` dei SOAP Fault sono testo libero (redatti); il `faultstring` dentro `ErroreSOAP` si redige anche nel campo `errore` del meta. Il `faultcode` resta leggibile.
- N4 (alto), registro: una chiave JSON da credenziale o da dato personale classifica TUTTO il suo contenuto (`{"password":{"value":…}}` diventa un solo segnaposto); JSON scritto come stringa dentro JSON o XML si redige anch'esso, anche serializzato più volte (stringa JSON con escape) o emerso solo decodificando (percent-encoding, entità). In modalità in chiaro le credenziali si cercano anche nel testo decodificato (entità, percent-encoding): `&lt;password&gt;…` dentro un JSON restava leggibile (verifica del giro 4); un markup codificato che non si legge non si scrive.

#### Area 2 — FSE, i due bug del giro 2 chiusi a metà (test: `tests/unit/test_revisione_giro3_fse.py`)

- G3-N1 (giro 2 N4): il testo scritto dal kit (`righe_leggibili_pss`) porta l'impronta SHA-256 del contenuto del CDA dello stesso PSS (XML canonico, id tecnici casuali esclusi: `impronta_contenuto_cda`). `inietta_cda` la confronta: un CDA con stesso id e versione ma contenuto diverso è `TestoPdfIncoerente`, anche alla prima iniezione e anche con `testo_verificato=True`. Un PDF del kit di prima, senza impronta, accetta solo lo stesso CDA già allegato. I test del giro 2 (`test_g2_n4_*`) non sono cambiati: passano perché il testo porta l'impronta.
- G3-N2 (giro 2 N2): la sostituzione toglie dal name tree e da `/AF` ogni filespec che porta il CDA precedente (stesso oggetto stream o stessi byte, con qualunque nome); la verifica finale lo controlla su tutto il catalogo corrente.

#### Area 3 — Puglia (test: `tests/unit/test_revisione_giro3_sar_puglia.py`)

- Residuo alto del bug 2 del giro 1: dopo un `chkPrescrizione` riuscito qualunque errore (orologio e dati del canale, busta di registrazione, CDA, firma) torna come `EsitoInvioSAR` con `da_ripetere`, mai come eccezione; anche `ripeti_registrazione` restituisce l'esito invece di sollevare.

#### Area 5 — Piemonte (test: `tests/unit/test_revisione_giro3_sar_piemonte.py`, `test_revisione_giro3_conformita.py`)

- Residuo del giro 2 N5: `scambia_codice` controlla i tipi della risposta del servizio token (`access_token` stringa, `scope` stringa o lista di stringhe, `client_id`, `expires_in` numero finito) e ogni difetto è `ErroreAutorizzazione`; `scope` 0, `false` o `{}` non diventano più «nessuno scope» e un `expires_in` non numerico non è più preso per assente (verifica del giro 4); `leggi_jwt` rifiuta un valore che non è una stringa.
- Giro 2 N3 (= giro 3 N2): un prefisso atteso su un header assente è FALLITO (assente ≠ vuoto); un header sia in `intestazioni`/`intestazioni_prefisso` sia tra gli `assenti` è una contraddizione, senza distinguere le maiuscole.

#### Area 6 — conformità e test (test: `tests/unit/test_revisione_giro3_conformita.py`)

- N1 (alto): l'hook di `tests/conftest.py` non cambia più nessun esito dopo l'esecuzione. Salta solo i test marcati `schemi_hl7`, e solo se gli schemi mancano prima di eseguirli. 42 funzioni di test marcate (quelle che senza schemi risultavano saltate).
- N2 (alto): un passo senza `atteso`, o con `atteso` vuoto, rende il caso FALLITO, nel motore Python e in `EseguiCasiFse.java` (classi ricompilate); con `jsonschema` installato il motore valida anche il caso contro `caso.schema.json`. Cambiato `test_kit_e_conformita.py::test_pulizia_saltata_se_variabile_mancante`: il suo caso non era conforme (niente `riferimento`, `atteso` vuoto) e ora sarebbe FALLITO prima di arrivare alla variabile mancante che il test vuole provare.
- N3 (medio): con `Motore(validatore_fse=ValidatoreLocale())` esplicito e senza schemi, i casi FSE sono SALTATO, non ERRORE.
- Giro 2 N5: il resoconto è rigenerato con il generatore attuale: `resoconti/Resoconto_Varco_2026-10-03.pdf` (nome Varco, data e numeri dei test aggiornati, niente più «tutto quello che si poteva costruire da fonti pubbliche è costruito») sostituisce `Resoconto_Kit_MMG_2026-10-02.pdf`. I dati della pagina dei progetti simili, che mancavano, sono in `strumenti/resoconto_simili.json` (ricopiati dalla pagina 4 del PDF precedente). Comando: `python3 strumenti/resoconto_pdf.py resoconti/Resoconto_Varco_2026-10-03.pdf strumenti/resoconto_simili.json`.

### Giro 2 di revisione esterna — correzioni

#### Area 1 — SAC, guardia, registro (test: `tests/unit/test_revisione_giro2_sac_guardia_registro.py`)

- Residuo bug 4: `<messaggio>` (e nel SAC `esito`, `tipoErrore`) è testo libero e si redige nel registro predefinito; il `codice` della comunicazione resta leggibile.
- N1: la guardia resta accesa per tutto `invia` di un trasporto proprio (audit hook su richieste urllib/redirect, connect, DNS, anche nei thread del trasporto); `Risposta.url_finale` è il contratto per chi segue redirect e si rivaluta; un adattatore urllib che segue un 302 verso la produzione si ferma prima del connect, anche se ingoia l'eccezione.
- N2: «nel dubbio non scrivo» — un corpo che il registro non legge (XML troncato, UTF-16/BOM, byte non UTF-8, markup non ben formato, JSON rotto) diventa `[NON SCRITTO: …, N byte, hmac-sha256 …]` in ogni modalità, in chiaro compresa; tolto il ripiego a espressioni regolari sul corpo.
- N3: `descrGruppoEquival` redatto; nel tracciato SAC una allowlist (`TAG_SAC_LEGGIBILI`), ogni altro elemento SAC si redige; test che falliscono se un elemento degli XSD SAC o un campo testuale del modello non è classificato.
- N4: le credenziali si mascherano qualunque forma abbiano (tolta l'esenzione per `***`/`[REDATTO:`); l'idempotenza viene dall'accantonare i segnaposto della stessa passata.
- N5: nei tag sensibili si redigono anche gli attributi (`<birthTime value>`, `<telecom value>`, `<password value>` anche in chiaro) e quelli dei discendenti.

#### Area 2 — FSE (test: `tests/unit/test_revisione_giro2_fse.py`)

- N1 (residuo bug 2): il CDA si riconosce anche da `/DOS`, `/Mac` e `/Unix`. Il kit rifà la regola del dispatcher: `getFilename()` di PDFBox 2.0.26, priorità `/UF`, `/DOS`, `/Mac`, `/Unix`, `/F`, verificata con `javap`. Un PDF con `/F=b.xml` e `/DOS=cda.xml` ora dà `CdaGiaPresente`, e `estrai_cda` lo legge come il dispatcher. La verifica finale di `inietta_cda` non usa più l'estrattore dei candidati: rilegge il PDF prodotto con la regola del dispatcher su tutti gli allegati e controlla `/AF`.
- N2: con `sostituisci=True` il vecchio CDA esce da `/AF` anche se ha la chiave `cda.xml` e i nomi del file diversi. Si toglie lo stesso oggetto filespec, oppure un filespec con un nome di CDA.
- N3: se un allegato qualunque non ha lo stream in `/EF/F`, il dispatcher va in `NullPointerException` e non trova il CDA. Ora `inietta_cda` ed `estrai_cda` sollevano `PdfNonEstraibile` invece di dichiarare l'iniezione riuscita.
- N4: `inietta_cda` solleva `TestoPdfIncoerente` quando il testo scritto dal kit («Documento … - versione n») e il CDA hanno id o versione diversi. Con un testo di altra provenienza rifiuta la sostituzione che cambia documento, salvo `testo_verificato=True`. Nuova `pdf_pss(pss)`: testo e CDA dallo stesso PSS. Nuova prova `prove/20261002-fse-v2-coerente/`, con PDF v2 firmato di TEST e testo v2. Sostituisce i PDF sostituiti di `prove/20261002-fse-v2/`, che mostrano «versione 1» con dentro il CDA v2. Cambiato un test esistente, `tests/ufficiale/test_fse_revisione_ufficiale.py::test_sostituzione_del_cda_kit_e_dispatcher_estraggono_lo_stesso`: sostituiva il CDA v2 sotto un testo v1, cioè fissava proprio l'incoerenza N4. Ora il testo è v2 e l'allegato vecchio è v1.

#### Area 3 — SAR Puglia (test: `tests/unit/test_revisione_giro2_sar_puglia.py`)

- Residuo bug 2: la CNS tolta durante la firma WS-Security di `setRegistraPrescrizione` faceva uscire `RuntimeError` senza esito, con la prescrizione già allocata. Ora `ripeti_registrazione` intercetta qualunque errore della chiamata e restituisce l'`EsitoInvioSAR` con `da_ripetere` e il CDA firmato.
- N1: il CDA si scrive con l'anagrafica (`Paziente`) letta e controllata prima di `chkPrescrizione`, conservata in `EsitoInvioSAR.paziente`; se l'esito non la porta, la nuova lettura passa lo stesso controllo d'identità. Prima una seconda lettura del callback mescolava il CF della ricetta con nome e nascita di un altro.
- N2: `verifica_security` legge la finestra temporale solo dall'unico `wsu:Timestamp` di `Security`, che deve essere quello firmato (Specifiche SIST 4.03.27, par. 5.1.1). Un Timestamp fresco non firmato davanti a una firma scaduta non la rende più valida; il server finto risponde 000265.
- N3 (residuo bug 7): il server finto applica `dataErogazioneDal/Al` (javadoc CVP, getPrescrizioniIdentificate): una prescrizione mai erogata non esce da una ricerca per erogazione.

#### Area 4 — SAR FVG (test: `tests/unit/test_revisione_giro2_sar_fvg.py`)

- N1: gli endpoint di `CanaleFVG` sono di sola lettura (`url` è una proprietà su `MappingProxyType`) e `url_di` ricontrolla a ogni chiamata adesione, applicativo dell'adesione e, in modalità CNS, la carta verificata. Prima un canale costruito per localhost e poi puntato al collaudo inoltrava senza `AdesioneFVG` né carta.
- N2: `codCatalogoPrescr`, `tipoAccesso` e `numeroNota` su una farmaceutica sono rifiutati dal client (`problemi_fvg`) e dal server finto (p. 21). La nota con `tipoAmbulatorio` resta solo per le righe specialistiche.
- N3: un codice assistito che comincia per STP/ENI senza le 13 cifre non passa più come «altro», né nel client né nel server finto.
- N4: `interroga_nre_utilizzati` del FVG non impone più il tipo di prescrizione: è facoltativo nello XSD FVG. Il vincolo 1153 resta per il SAC (`CriteriNreUtilizzati.problemi(tipo_obbligatorio=...)`).
- N5: `codRegione` diverso da "060" è rifiutato da client e server finto (p. 16); il server conserva il valore ricevuto e la visualizzazione lo restituisce, invece della costante "060".
- N6: `versioneCR` si controlla con `[0-9]` invece di `\d`: le cifre Unicode («١.٤.٤») sono rifiutate in locale, come fa il server.

#### Area 5 — SAR Piemonte (test: `tests/unit/test_revisione_giro2_sar_piemonte.py`)

- N1: `nbf` del JWT (REL-STC-01, p. 30) controllato da `scambia_codice` (`ErroreAutorizzazione`), dal canale (`ContenutoJWT.non_ancora_valido`, margine 60 s) e dal server finto. Prima un token valido tra un'ora era accettato e usato subito.
- N2: nel server finto l'Id-Sessione del JWT deve essere della coppia `sub`/`aud` del token, nel SAR come in verify e revoke. Il SAR rifiuta inoltre una sessione scaduta anche quando il JWT è ancora valido. Prima un JWT di A con l'Id-Sessione di B, di un altro gestionale, autenticava, verificava e revocava.
- N3: il runner `PIE-*` confronta senza confondere booleani e numeri (`stesso_valore`: `false` ≠ `0`). Un campo atteso ma non osservato fallisce, anche se atteso a `null`.
- N4: il server finto aggiunge `code` e `state` con `&` quando la `redirect_uri` ha già una query.
- N5: `scambia_codice` trasforma in `ErroreAutorizzazione` anche JWT e JWKS malformati: `alg` non stringa, modulo RSA non valido, `exp`/`nbf` non finiti. `chiave_da_jwks` e `ContenutoJWT.scadenza` non lasciano più uscire `ValueError` né `OverflowError`.

#### Area 6 — Conformità, test, documenti (test: `tests/unit/test_revisione_giro2_conformita.py`)

Non toccati, per decisione separata: licenza HL7 (giro 1, bug 5) e completamenti di `publiccode.yml`/`SECURITY.md` (giro 1, bug 8).

- N1: un caso senza passi è FALLITO, nel motore Python (`Motore.esegui`) e nell'esecutore Java (`EseguiCasiFse.difettiCaso`). Lo schema ne vuole almeno uno; prima era SUPERATO e la riga di comando usciva con 0.
- N2: `EseguiCasiFse.verifica` (Java) rifiuta le aspettative sconosciute e quelle incoerenti (`valido` contro `esito`, `senza_errori` con `errori_contengono`), come prescrive `caso.schema.json`. Le classi in `strumenti/validatore-ufficiale/classi` sono ricompilate.
- N3: il controllo di coerenza su `nre_formato` non giudica più un segnaposto (`${nre_atteso}`) prima della sostituzione.
- N4: il mutation check giudica anche i casi online, con uno «specchio» dell'atteso originale e senza rete. Prima erano tutti SALTATO e un confronto sempre verde passava inosservato. Cambiato un test esistente, `tests/unit/test_conformita_mutazioni.py::test_ogni_mutante_di_un_caso_verde_e_fallito`: è lui il difetto segnalato (esentava `online` dalla soglia).
- N5: il generatore del resoconto (`strumenti/resoconto_pdf.py`) non dice più che da fonti pubbliche è tutto costruito: nomina le Regioni con specifiche pubbliche ancora da fare (oggi l'Umbria). Il PDF in `resoconti/` NON è rigenerato: manca il JSON dei progetti simili, che dà la pagina 4.


### Ricetta dematerializzata (prescrittore, SAC del MEF)

- Invio della ricetta farmaceutica e specialistica, visualizzazione, annullamento.
- Medico sostituto (`cfMedico2`), con controllo locale della coerenza tra credenziali e
  sostituto.
- Lista degli NRE utilizzati (`demInterrogaNreUtilizzati`) per NRE o per intervallo di
  date.
- Cifratura di CF e pincode con il certificato SanitelCF.
- Riconoscimento di rifiuti (`9999`), avvisi (`0001`) e SOAP Fault.
- Provato contro l'ambiente di **test** del MEF il 30/09/2026 (`prove/`).

### Ricetta in Piemonte: SIRPED (CSI Piemonte) — scritto e verificato sulle specifiche, NON collaudato

- `RicettaPiemonte`, `CanalePiemonte`, `ServizioIdSessione`, `ClientOAuth2Piemonte`. Specifiche
  REL-STC-01 V04 del 02/03/2026, RE-SRS-SAR V05, autocertificazione 2026; XSD del kit A2F del MEF.
- Credenziali RUPAR; pincode e CF cifrati col certificato regionale, passato esplicito (nessun
  default).
- Secondo fattore nelle due modalità regionali:
  - Id-Sessione via mail: CreateAuth, CheckToken, RevokeAuth; header `X-idSessione` e `X-Gestionale`;
  - OAuth2: Authorization Code con PKCE S256, verifica della firma del JWT sul JWKS, verify e revoke;
    `X-OAuth2-Authorization` senza Basic, `pinCode` vuoto.
- Invio, visualizzazione, annullamento, lista degli NRE col codec del SAC, senza modifiche.
- **Stesso modello, nessun campo nuovo; stesso contratto, nessuna firma cambiata; stesso codec.**
- Guardia: ogni host dei domini della Regione e del CSI è produzione. Un host con etichetta di
  collaudo vuole `consenti_collaudo_regionale=True`, la dichiarazione per nome
  (`TrasportoHTTP(collaudi_piemonte=...)`) **e** un'`AdesionePiemonte`.
- Registratore: `X-idSessione` e `X-OAuth2-Authorization` mascherati; UUID, JWT, `userId`,
  `cfUtente` e parametri OAuth2 redatti.
- Verificato senza la Regione:
  - XSD del MEF e XSD A2F;
  - 44 casi `PIE-*`;
  - server finto su 127.0.0.1 (`strumenti/piemonte_server_finto.py`): prescrizione, A2F, OAuth2.
- Difetti trovati nelle specifiche e cosa vuol dire «certificata SIRPED»: `docs/SAR_PIEMONTE.md`.
  Bozza di proposta, da non inviare: `docs/PROPOSTA_PIEMONTE.md`. Blocchi: `docs/BLOCCHI.md`.

### Ricetta in Friuli-Venezia Giulia: SAR di Insiel — scritto e verificato sulle specifiche, NON collaudato

- `RicettaFVG`, `CanaleFVG`. Specifiche Insiel Idof-dem-AT-01 dell'11/02/2026 e
  `wsdl_prescritto.zip`.
- Autenticazione:
  - mutua autenticazione TLS con la carta del medico, nel contesto TLS del trasporto;
  - controllo che il CF della carta sia quello del medico che invia;
  - in alternativa, i token della soluzione federata forniti da chi integra.
- Tracciato del SAC con namespace FVG, attributi `prodottoCme` e `versioneCR`, `pinCode` vuoto,
  facoltativi solo se valorizzati.
- Invio, visualizzazione, annullamento, verifica della posizione del sostituto. La lista degli NRE
  parte solo con un URL esplicito: l'endpoint non è pubblicato.
- `richiede_downgrade_mir`: codici 060120-060130, anche se arrivano come SOAP Fault.
- **Stesso modello, nessun campo nuovo; stesso contratto, nessuna firma cambiata.**
- Guardia: ogni host `*.fvg.it` e `*.insiel.it` è produzione, tranne i collaudi pubblicati, che
  vogliono `consenti_collaudo_regionale=True` **e** un'`AdesioneFVG`.
- Registratore: User-Agent (CF e postazione), `X-JWT-ASSERTION` e tag nuovi redatti.
- Verificato senza la Regione:
  - XSD ufficiali;
  - 29 casi `FVG-*`;
  - server finto in HTTPS con mutua autenticazione (`strumenti/fvg_server_finto.py`,
    `strumenti/genera_prove_fvg.py`).
- Non coperto il FSE regionale: la specifica è a circolazione limitata (`docs/TERZE_PARTI.md`).
- Difetti trovati nelle specifiche: `docs/SAR_FVG.md`, sezione 7. Bozza di proposta, da non
  inviare: `docs/PROPOSTA_FVG.md`.

### Ricetta in Puglia: SIST (SAR regionale) — scritto e verificato sulle specifiche, NON collaudato

- `RicettaSIST`, `CanaleSIST`, WS-Security con la CNS (`ChiaveOperatore`), CDA2 di
  prescrizione con firma CAdES (`FirmatarioCAdES`). Specifiche SIST pubblicate come 4.03.27 del
  16/09/2026.
- Stesso contratto `ServizioRicetta` e stesso modello. Campo nuovo `Assistito.codice_regione`;
  `visualizza(..., cf_assistito=)`; esiti `EsitoInvioSAR` e `EsitoVisualizzazioneSAR`
  (ricetta rossa, registrazione da ripetere). Motivi in `docs/ARCHITETTURA.md`.
- Guardia: produzione SIST e ogni host `*.puglia.it` bloccati come produzione. Il collaudo
  regionale è bloccato finché non ci sono `consenti_collaudo_regionale=True` **e**
  un'`AdesioneSIST`.
- Registratore: tag SIST redatti.
- Verificato senza la Regione:
  - richieste contro `CVPService.xsd`;
  - struttura confrontata con gli esempi ufficiali;
  - WS-Security con un verificatore indipendente;
  - giro completo con un server finto in locale (`strumenti/sist_server_finto.py`).

  Risposte di prova sintetiche. Dettagli e cosa manca: `docs/SAR_PUGLIA.md`; bozza per
  InnovaPuglia: `docs/PROPOSTA_PUGLIA.md`.

### FSE 2.0, lato documento

- Profilo Sanitario Sintetico in CDA2 HL7 Italia, dagli stessi oggetti della ricetta.
- Validazione locale (XSD e schematron ufficiali) e con il codice del validatore
  ufficiale del gateway, eseguito in locale come processo separato.
- PDF con il CDA allegato, iniezione in un PDF esistente, firma PAdES (provata solo con
  un certificato autofirmato di test).

### Suite di conformità

- 143 casi in JSON con schema, famiglie `offline`, `online`, `fse`, `sist`, `fvg`, `piemonte` (23 casi
  SIST, 29 FVG, 44 Piemonte, senza rete; la parte XSD vuole `--xsd-sist`, `--xsd-fvg`, `--xsd-a2f`); esecutore Python e esecutore Java dei casi FSE.

### Sicurezza e dati personali

- Guardia anti-produzione su SAC e gateway FSE (URL finale, nessun redirect, punto
  finale nell'host).
- Registratore degli scambi (`RegistratoreFile`) **redatto per default**: CF, pincode,
  NRE, codice di autenticazione, nomi, diagnosi, esenzioni e allegati PDF/base64 non
  finiscono su disco. In chiaro solo con identità di test verificate
  (`identita_di_test`) o con il flag esplicito `registra_dati_personali_in_chiaro=True`
  (CLI: `--registra-dati-personali-in-chiaro`).
- Dipendenza minima `cryptography>=49` (versioni precedenti con vulnerabilità note).

### Pubblicazione

- Materiale di terzi non redistribuito: si scarica dalle fonti ufficiali con hash e
  commit verificati (`strumenti/scarica_specifiche.py`, `strumenti/fonti_specifiche.json`).
- `NOTICE`, `docs/TERZE_PARTI.md`, `docs/NON_DISPOSITIVO_MEDICO.md`, `docs/MINACCE.md`,
  `SECURITY.md`, `CONTRIBUTING.md`.
- Integrazione continua (GitHub Actions): test su Linux, macOS e Windows con Python 3.11
  e 3.12, validazione di `publiccode.yml`, `pip-audit`. Nessuna chiamata al MEF in CI.
- Licenze: EUPL-1.2 per codice e suite, CC-BY-4.0 per la documentazione.

### Limiti noti

- Nessun canale verso il gateway FSE (servono i certificati Sogei); solo il PSS.
- SAR regionali: solo il SIST della Puglia, e non collaudato. Niente lotti NRE; niente flusso a due fattori di
  produzione.
- Provato in locale solo su macOS (Python 3.11, 3.12, 3.14); Linux e Windows solo
  tramite la CI, non ancora eseguita.

---
Licenza di questo documento: CC-BY-4.0.

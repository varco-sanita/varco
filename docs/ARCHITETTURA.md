# Architettura del kit (ricetta prescrittore e FSE 2.0 lato documento)

## Strati

```
  programma del medico
          │   Ricetta, Prescrittore, Assistito, Riga             (modello ricetta)
          │   ProfiloSanitarioSintetico, Paziente, Medico, ...   (modello FSE, costruito SOPRA quello della ricetta)
          ▼
  ServizioRicetta  (contratto: invia / visualizza / annulla / interroga_nre_utilizzati)
          │
  RicettaSAC ──── xml_sac (codec: modello ⇄ tracciato XML del SAC)
          │  └─── CifratoreSanitel (CF assistito e pincode)
          ▼
  CanaleSAC        (URL dell'ambiente, Basic auth, Authorization2F, SOAPAction)
          │
  soap             (busta SOAP 1.1, riconoscimento dei Fault)
          │
  TrasportoHTTP    (HTTPS, guardia produzione SAC, FSE, Regione Puglia, Regione FVG, Regione Piemonte e Regione Umbria, limite di frequenza, registrazione)

  RicettaSIST ─── xml_sist (codec CVP) + cda_sist (CDA2 di prescrizione) + FirmatarioCAdES
          ▼                                   (stesso contratto ServizioRicetta, SAR della Puglia)
  CanaleSIST       (datiOperatore, datiApplicativo/applDigest, SOAPAction, adesione)
          │
  wssecurity       (intestazione WS-Security firmata con la CNS: ChiaveOperatore)
          │
  soap + TrasportoHTTP  (gli stessi di sopra)

  RicettaFVG ──── xml_fvg (tracciato del SAC con namespace, attributi e tipi FVG) + CifratoreSanitel
          ▼                                   (stesso contratto ServizioRicetta, SAR del Friuli-Venezia Giulia)
  CanaleFVG        (User-Agent del par. 3.1, SOAPAction vuota, token federati; CF della carta = medico)
          │
  soap + TrasportoHTTP  (gli stessi di sopra; la carta del medico sta nel contesto TLS: mutua autenticazione)

  RicettaPiemonte ─ xml_sac (lo STESSO codec del SAC) + cifratore regionale passato esplicito
          ▼                                   (stesso contratto ServizioRicetta, SIRPED del Piemonte)
  CanalePiemonte   (MAIL: Basic RUPAR + X-idSessione + X-Gestionale | OAUTH2: solo X-OAuth2-Authorization)
          │        ServizioIdSessione (CreateAuth/CheckToken/RevokeAuth, XSD A2F) · ClientOAuth2Piemonte (PKCE, JWKS)
  soap + TrasportoHTTP  (gli stessi di sopra)

  RicettaUmbria ── json_umbria (codec JSON dell'OpenAPI di PuntoZero)
          ▼                                   (stesso contratto ServizioRicetta, SAR dell'Umbria; più lotto NRE e sostituzione)
  CanaleUmbria     (REST, mTLS + due JWT firmati: Authorization e FSE-JWT-Signature, claim per servizio)
          │
  consegna + TrasportoHTTP  (niente SOAP: JSON sullo stesso trasporto, stessa guardia)

  fse/cda_pss      (codec: PSS ⇄ CDA2 HL7 Italia)  ──►  fse/validazione (Validatore: locale | ufficiale)
                                                   ──►  fse/pdf (PDF con cda.xml, iniezione, Firmatario PAdES)
```

Ogni strato conosce solo quello sotto.

- Il **modello** non sa nulla di XML né di SOAP: i nomi dei tag del SAC
  (`codProdPrest`, `cfMedico1`, ...) compaiono solo in `ricetta/xml_sac.py`.
  Il CDA del PSS è un altro codec (`fse/cda_pss.py`) sopra lo stesso modello:
  `Paziente` contiene un `Assistito`, `Medico` un `Prescrittore`, e terapie, problemi
  ed esenzioni si ricavano da `Riga` e `Ricetta` (`Terapia.da_riga`,
  `Problema.da_ricetta`, `Esenzione.da_ricetta`). Il modello ricetta non è stato
  toccato per farlo.
- Il **trasporto** non sa nulla di ricette: `TrasportoHTTP` manda byte a un
  URL. Un canale SAR regionale o il gateway FSE (REST, JWT) si scrivono come
  altri "canali" sopra lo stesso trasporto, oppure con un altro trasporto che
  rispetta il protocollo `Trasporto` (un solo metodo, `invia(Richiesta) -> Risposta`).
- `ServizioRicetta` è un `Protocol`: il programma del medico dipende da quello.
  Un'implementazione SAR che rispetta lo stesso contratto si sostituisce senza
  riscrivere nulla, e la suite di conformità la collauda uguale.

## Scelte e motivi

**Solo libreria standard, più `cryptography`.** HTTP con `urllib`, XML con
`xml.etree`, TOML con `tomllib`. L'unica dipendenza di esercizio è
`cryptography`, perché la libreria standard non ha RSA. Per un bene comune
contano la durata nel tempo e la facilità di verifica: meno dipendenze vuol dire
meno cose da aggiornare e da controllare. `lxml` è facoltativo: serve solo per
validare contro gli XSD, e in esercizio non serve perché il SAC valida lato
server (risponde con un Fault `cvc-...`).

**Niente generatori di client dai WSDL (zeep e simili).** I servizi sono quattro e
le buste sono semplici. Scrivere il codec a mano rende esplicito l'ordine dei tag
(`xs:sequence`), testabile contro l'XSD e leggibile da chi deve collaudarlo.

**Errori e rifiuti sono cose diverse.** Un'eccezione (`ErroreSOAP`,
`ErroreTrasporto`) vuol dire "non ho parlato col SAC", o "il SAC non ha
nemmeno letto la richiesta" (credenziali, schema). Un rifiuto di merito (codice
`9999` più l'elenco degli errori) è un **esito** normale, con `ok == False`: il
gestionale lo deve mostrare al medico, non lo deve gestire come un guasto.

**Lettura tollerante ai namespace.** Le risposte del SAC usano prefissi diversi
da servizio a servizio (per esempio in `VisualizzaPrescrittoRicevuta` il
namespace di default è quello dei tipi dati). Il lettore confronta solo i
nomi locali dei tag: è più robusto se i prefissi cambiano.

**Controlli locali solo di forma.** `Ricetta.valida()` controlla i campi
obbligatori e i valori ammessi dal tracciato (par. 4.2.1), per non mandare
richieste destinate al rifiuto. Non entra mai nel merito clinico. Con
`valida_localmente=False` i controlli si saltano: serve alla suite per
collaudare le regole **del SAC**.

**Tutti i tag di testata sempre presenti.** Il par. 3.4 della specifica chiede
che "tutti i tag descritti nello schema XSD" siano presenti. La testata li
manda tutti, vuoti se non valorizzati, come fa il progetto SoapUI del kit. Fa
eccezione il blocco degli assicurati esteri, che si manda solo se valorizzato:
anche il progetto SoapUI lo omette. Nelle righe si mandano solo i campi
valorizzati più `quantita`, anche qui come negli esempi del kit. Il SAC ha
accettato entrambe le forme.

**Guardia anti-produzione sull'URL, non sull'impostazione.** Il blocco sta
in `TrasportoHTTP.invia` e guarda l'host finale, quindi ferma anche un URL di
produzione scritto a mano in configurazione. Solo `consenti_produzione=True`
(proprio `True`, non un valore qualunque che risulta vero) lo sblocca. In
più `CanaleSAC` si rifiuta di partire fuori dal test senza un id di sessione a
due fattori reale.

Una revisione a occhi freschi ha trovato due modi per aggirare la guardia, entrambi chiusi e coperti da test: un host scritto con il punto finale (`demservice.sanita.finanze.it.`, che è lo stesso host) e un redirect HTTP, che urllib avrebbe seguito senza ricontrollarlo. Ora il punto finale viene tolto prima del confronto, e il kit non segue **nessun** redirect: il SAC non ne fa, quindi un redirect diventa un errore.
Nel giro 2 (02/10/2026) un trasporto proprio con un normale opener urllib seguiva un 302 verso la
produzione, Authorization compresa: ora `consegna` tiene accesa la guardia per tutto `invia` (audit
hook su richieste urllib, connect, DNS) e rivaluta `Risposta.url_finale` se il trasporto la riporta.

La guardia vale anche per il **gateway FSE**: `modipa.fse.salute.gov.it` e qualunque
altro host `*.fse.salute.gov.it` senza `-val.` nel nome contano come produzione.
Passa solo l'ambiente di validazione `modipa-val.fse.salute.gov.it`. Oggi il kit non
chiama il gateway in nessun caso, ma quando ci sarà il canale sarà già coperto.

**Il sostituto si controlla in locale.** Con `valida_localmente` attivo, `invia`
rifiuta una ricetta con `cfMedico2` se le credenziali non sono del sostituto, e una
ricetta senza sostituto se le credenziali non sono del titolare. Il SAC risponderebbe
comunque (1212), ma così il medico lo vede prima.

**Limite di frequenza di processo.** Il limitatore è condiviso tra tutte le
istanze e distingue per host. Il minimo è mezzo secondo tra due richieste (non
più di 2 al secondo), il default è un secondo.

**TLS: certificati di sistema.** Il server di test oggi presenta un certificato
pubblico Sectigo. Il `.pem` autofirmato che sta nel kit MEF (`CertificatoSSLTest`)
è **scaduto il 4/9/2025** e non serve più. Si usa `ssl.create_default_context()`
e la verifica resta sempre attiva.

**Credenziali fuori dal codice.** Si leggono da variabili d'ambiente, da file
TOML o dal kit MEF (`kit_mef.py`, solo per i test). Il pincode resta in chiaro
in memoria e si cifra a ogni chiamata: il padding PKCS#1 v1.5 è casuale, quindi
ogni richiesta porta un cifrato diverso. Password e pincode non compaiono nel
`repr` delle credenziali.

**Registratore redatto per default.** Il registratore degli scambi è facoltativo, ma
quando c'è scrive su disco richieste e risposte: con dati veri vorrebbe dire CF in
chiaro e promemoria PDF. Per questo `RegistratoreFile` redige per default (tag
sensibili del tracciato, qualunque CF anche omocodico, allegati base64 e PDF, nome del
medico nelle comunicazioni) e lascia un'impronta HMAC con chiave casuale di processo,
così dentro una stessa esecuzione si segue un NRE senza poterlo ricostruire. Il chiaro
si ottiene in due soli modi: `identita_di_test`, che lo concede chiamata per chiamata
solo se host, utente e **tutti** i CF trovati (anche negli stream compressi del PDF)
sono di test, se non ci sono nomi o tessere senza CF (assistiti esteri) e se il CF
cifrato dell'assistito è confermato dal promemoria, altrimenti redige e scrive il
motivo; oppure
`registra_dati_personali_in_chiaro=True`, proprio `True`, con un avviso. Gli header di
autenticazione sono mascherati in ogni modalità.

**Ora italiana.** `dataCompilazione` si genera con il fuso `Europe/Rome`
anche se il computer è su un altro fuso.

## Scoperte sul servizio reale (30/09/2026)

Differenze tra la specifica, il kit e il comportamento dell'ambiente di test:

1. **`tipoErrore` vale `Bloccante`**, non `E` come scritto nella specifica
   (par. 4.2.1). Il kit accetta entrambe le forme (`Messaggio.gravita`) e
   tratta come bloccante qualunque valore che non riconosce.
2. **Il progetto SoapUI del kit genera la data con `hh`** (formato a 12 ore di
   Java). Dopo mezzogiorno manda quindi l'ora sbagliata, per esempio 04:44
   invece di 16:44. Il kit usa il formato a 24 ore, come vuole la specifica.
3. **La visualizzazione non restituisce il CF dell'assistito** (`<codiceAss/>`
   vuoto), mentre il promemoria PDF lo riporta in chiaro.
4. Il SAC restituisce nella visualizzazione `numsedute=0` anche per i farmaci,
   e scrive in `testata1` il nome del medico (`COGNOME_MEDICO=...;NOME_MEDICO=...`).
5. La specifica chiama il campo `progrPresc`, mentre l'XSD e le risposte usano
   `progPresc`.
6. Il SAC di test restituisce **sempre il PDF del promemoria**
   (`flagPromemoria=0`). La specifica lo presenta come campo "per futuro utilizzo".
7. Le comunicazioni hanno codici diversi a seconda del servizio: `0100` invio,
   `0200` visualizzazione, `0300` annullamento ("Nessuna comunicazione"). In
   Abruzzo compare anche la `0197`, la frase regionale da stampare nel promemoria.
8. **Sostituto.** Deve inviare lui: `cfMedico2` diverso dall'utente che invia →
   `1212`. Il titolare vede la ricetta del sostituto ma **non può annullarla**
   (`1125`, con un testo che parla di "visualizzazione non consentita"). Solo il
   sostituto la annulla.
9. **`tipoErrore="Avviso"`**: il SAC di test, visualizzando come sostituto, risponde
   `0001` con l'avviso `1024` (check digit del CF del sostituto di test sbagliato).
   Il kit lo tratta come avviso, non come errore.
10. **`InterrogaNreUtilizzati`.**
    - Per intervallo di date il tipo di prescrizione è **obbligatorio** (`1153`),
      anche se la specifica lo dà facoltativo. Il kit lo richiede già in locale.
    - Un NRE inesistente dà `0000` con lista vuota, non un errore.
    - `cfAssistito` non viene restituito.
    - La lista comprende le ricette annullate e quelle di tutti gli sviluppatori
      che usano la stessa utenza di test.
    - La ricetta del sostituto sta sotto il titolare: cercata come sostituto, la
      lista è vuota.
    - La SOAPAction del WSDL è `.../interroganreassociati.wsdl.../InterrogaNreUtilizzati`
      (con "associati"), non quella che il nome del servizio fa pensare.
11. **L'XSD ufficiale `TipiDatiInterrogaNreUtilizzati.xsd` non è valido**: mette
    `minOccurs="0"` su un `complexType` (`elencoNreUtilRecordType`), e lxml lo
    rifiuta. `varco/schemi` lo corregge **in memoria**, senza modificare il file,
    e alza un'eccezione se un giorno il difetto sparisce, così la correzione non
    resta lì a nascondere un file cambiato.

FSE 2.0, trovate con il codice ufficiale del validatore:

12. **Il tipo di allergia `DALLERGY`** (valido in HL7) non è nel dizionario del
    gateway (2.16.840.1.113883.1.11.19700) → `VOCABULARY_ERROR`. Il validatore
    locale, senza vocabolari, lo dava OK. Il modello ora ammette solo i sei codici
    del dizionario.
13. **I sistemi di codifica delle esenzioni (2.16.840.1.113883.2.9.6.1.22) e dei
    gruppi di equivalenza (…6.1.51) non sono censiti** nel dizionario del gateway.
    Il validatore lo scrive solo nel log ("Unknown CodeSystems") e l'esito resta
    `OK`: quei codici quindi **non vengono controllati** (un'esenzione `INVENTATO`
    passa). Dal 02/10/2026 `EsitoValidazione` lo dice nell'esito pubblico:
    `sistemi_non_verificati` e un avviso «VOCABOLARIO NON VERIFICATO» in `avvisi`,
    tutti e due anche in `a_dict()`. Prima l'informazione stava solo in `dettagli`.
14. La prescrizione farmaceutica in FSE la carica il **Sistema TS** (FAQ di
    it-fse-support), non il medico. Per questo il kit non genera il CDA della
    prescrizione.

## FSE 2.0: documento, validazione, PDF

**Il CDA si scrive con `xml.etree`**, come il tracciato SAC. `genera()` controlla
prima la forma (`ProfiloSanitarioSintetico.problemi_di_forma()`: sezioni obbligatorie
con voci *oppure* codice di assenza, stato e date coerenti, tipi e sistemi ammessi) e
rifiuta con `DocumentoNonValido`. Sono controlli di forma: niente interazioni, niente
consigli.

**Nessun dato clinico per difetto** (revisione esterna del 02/10/2026). Via di
somministrazione, stato delle voci e tipo di allergia non hanno valori predefiniti.
La via e lo stato lo schematron li vuole codificati (`routeCode[@code and
@codeSystem]`, ERRORE-b112; `statusCode` in ERRORE-73/b109), e il validatore
ufficiale respinge `routeCode nullFlavor="UNK"`. Per questo, se mancano, `genera()`
rifiuta e dice cosa manca. Il tipo di allergia invece si può omettere: diventa un
`value` non codificato con `nullFlavor="UNK"` e rimando al testo (ERRORE-b80, OK al
validatore ufficiale). L'inizio dello stato di assenza («nessuna allergia nota»,
«nessun problema noto») ora è `nullFlavor="UNK"`, non la data del documento.
`Terapia.da_riga`, `Problema.da_ricetta` ed `Esenzione.da_ricetta` prendono via e stato
come parametri: la ricetta non li contiene.

**Data di nascita.** ERRORE-17 vuole `birthTime/@value`. Il testo del messaggio cita
`nullFlavor="UNK"`, ma l'asserzione lo rifiuta: il validatore ufficiale dà
`SEMANTIC_ERROR`. Senza data di nascita il kit rifiuta prima di generare.

**Versioni successive alla prima.** Il modello ha `id_set` (l'extension del `setId`,
comune a tutte le versioni) e `id_documento_precedente` (l'id della versione
sostituita). Con `versione > 1` servono tutti e due, e `id_documento` deve essere
diverso da `id_set` (ERRORE-8). Il CDA porta `setId` distinto da `id` e un
`relatedDocument typeCode="RPLC"` con `parentDocument/id` e `setId`, dopo
`documentationOf` come vuole lo schema (ERRORE-9). Il validatore ufficiale dà OK alla
versione 2. Con `versione = 1` id e setId coincidono e `id_documento_precedente` va
omesso.

**Due validatori, stesso contratto** (`valida(xml) -> EsitoValidazione`, esiti come
quelli del gateway: `OK`, `SYNTAX_ERROR`, `SEMANTIC_ERROR`, `VOCABULARY_ERROR`).

- `ValidatoreLocale`: XSD CDA2 e schematron PSS v4.0 presi da it-fse-catalogs. Non sono nel
  pacchetto (licenza HL7, `docs/TERZE_PARTI.md`): li scarica `strumenti/scarica_specifiche.py
  --gruppi cda-xsd`, e la libreria li cerca in `$VARCO_CDA_XSD` e `$VARCO_SCHEMATRON` o nella
  cartella dello script; se mancano solleva `SchemiNonTrovati` con le istruzioni.
  Lo schematron si compila con lo skeleton ISO XSLT2 (lo stesso che usa ph-schematron
  nel gateway) ed esegue con Saxon (`saxonche`). **Non controlla i vocabolari**
  (`vocabolario_verificato=False`).
- `ValidatoreUfficiale`: esegue il **codice Java del validatore del gateway**
  (`it-fse-gtw-validator`, commit fissato), senza modificarlo. Al posto di MongoDB gli
  diamo repository in memoria caricati dai **dump pubblici** di it-fse-catalogs, cioè
  gli stessi dati che il gateway carica nel suo database. Il banco
  (`strumenti/validatore-ufficiale/src/ValidatoreUfficiale.java`) ripete il flusso del
  controller ufficiale: XSD, poi schematron, poi vocabolari.

**Il codice AGPL resta fuori dalla libreria.** Validatore e dispatcher del gateway sono
AGPL-3.0. La libreria li raggiunge solo lanciando `valida.sh` come processo separato e
leggendo il JSON in uscita; non importa né carica codice Java, e non contiene file
copiati da quei repository (`tests/unit/test_licenze.py`). Il banco Java è nostro
(EUPL-1.2); compilato col gateway diventa un'opera combinata, per questo le classi e i
repository non si distribuiscono. Dettagli: `docs/TERZE_PARTI.md`.

Perché così e non col container: Docker su questa macchina non c'è, e il container
"lite" del gateway chiede 16 GB di RAM (Kafka, Mongo, sette microservizi). Il codice
che decide l'esito però è lo stesso. Cosa resta fuori: il dispatcher come servizio
(JWT, hash dell'allegato, metadati) e la scelta della mappa FHIR.

**PDF.** `pdf_con_cda` scrive un PDF con la sola libreria standard e allega il CDA
come `cda.xml` (il nome che il dispatcher cerca in modalità ATTACHMENT). Il testo
leggibile (`righe_leggibili_pss`) riporta tutto quello che c'è nel CDA, campo per
campo: stato, date di fine, note, tipo di allergia, via, tutti i codici, versione e
documento sostituito. `test_pdf_riporta_ogni_valore_presente_nel_cda` controlla ogni
valore del modello che finisce nel CDA. Un carattere fuori da cp1252 solleva
`TestoNonRappresentabile` invece di diventare «?». `inietta_cda` aggiunge il CDA a un
PDF esistente con un aggiornamento incrementale, quindi il PDF del gestionale resta
intatto byte per byte.

**Un solo CDA per PDF.** Il dispatcher (`PDFUtility.extractContentFromAttachments`)
mette gli allegati in una mappa con le chiavi in minuscolo e ne tiene uno. Con due
`cda.xml` il kit rileggeva il primo e il dispatcher l'ultimo. Adesso:
- `inietta_cda` su un PDF che ha già un CDA solleva `CdaGiaPresente`. Conta come CDA un
  allegato con la chiave, o uno qualunque dei nomi `/UF`, `/DOS`, `/Mac`, `/Unix`, `/F`,
  uguale a `cda.xml`, maiuscole comprese. Il dispatcher legge il nome con
  `getFilename()` di PDFBox 2.0.26, che prende il primo presente nell'ordine `/UF`, `/DOS`,
  `/Mac`, `/Unix`, `/F` (verificato con `javap`);
- con `sostituisci=True` toglie il vecchio allegato da `/EmbeddedFiles` e da `/AF`. Da `/AF`
  toglie lo stesso oggetto filespec, oppure un filespec con un nome di CDA. Poi aggiunge il
  nuovo;
- il PDF prodotto si rilegge con la regola del dispatcher su **tutti** gli allegati. Se un
  allegato qualunque non ha lo stream in `/EF/F`, il dispatcher va in `NullPointerException`
  e non trova il CDA. In quel caso il kit solleva `PdfNonEstraibile` invece di restituire il
  PDF. Se il dispatcher leggerebbe un CDA diverso, il kit solleva `AllegatiCdaAmbigui`;
- `estrai_cda` usa la stessa regola. Solleva `AllegatiCdaAmbigui` se più allegati possono
  essere il CDA, invece di sceglierne uno. Solleva `PdfNonEstraibile` se il CDA c'è ma il
  dispatcher non lo troverebbe.

**Testo visibile e CDA dicono la stessa cosa.** Il testo scritto dal kit riporta
«Documento <root> / <id> - versione <n>». Quando c'è, `inietta_cda` controlla che il CDA
abbia lo stesso id e la stessa versione; se no solleva `TestoPdfIncoerente`. Id e versione non
bastano: il testo porta anche l'impronta SHA-256 del contenuto del CDA dello stesso PSS
(`impronta_contenuto_cda`: XML canonico, esclusi gli id tecnici casuali), e un CDA con lo stesso id
ma un contenuto diverso è rifiutato (giro 3, G3-N1). Un PDF del kit senza impronta accetta solo lo
stesso CDA già allegato. Nella sostituzione esce dal catalogo ogni filespec che porta il CDA
precedente, con qualunque nome, per riferimento allo stesso stream o per stessi byte (giro 3, G3-N2). Un testo di
altra provenienza il kit non lo sa leggere. In quel caso rifiuta una sostituzione che cambia
documento o versione, a meno che chi chiama passi `testo_verificato=True`. La strada per una
nuova versione è `pdf_pss(pss)`, che fa testo e CDA dallo stesso PSS. Le prove della v2 sono
in `prove/20261002-fse-v2-coerente/`. I PDF sostituiti di `prove/20261002-fse-v2/` mostrano
«versione 1» con dentro il CDA v2 (giro 2, N4).

Il test ufficiale verifica che, dopo la sostituzione, kit e dispatcher estraggano lo
stesso CDA (stesso SHA-256). La firma PAdES passa per un protocollo
`Firmatario`. L'implementazione di riferimento, `FirmatarioPKCS12`, usa pyHanko. Una
smart card o una firma remota si collegano scrivendo un altro `Firmatario`. Nelle
prove c'è solo un certificato **autofirmato di test**, dichiarato nel CN. Il PDF non
è dichiarato PDF/A-3.

Il CDA dentro i PDF prodotti lo ritrova il **codice del dispatcher ufficiale**
(`PDFUtility`), identico byte per byte (`strumenti/validatore-ufficiale/estrai_cda.sh`).

## Suite di conformità

La suite sta **fuori dal pacchetto Python**, in `conformita/` alla radice. È una
specifica a sé:

- `conformita/schema/caso.schema.json` (JSON Schema 2020-12) descrive il formato di un
  caso: operazioni, variabili `{{...}}`, profili di credenziali, forma dell'esito
  osservato e regole di confronto. Il testo delle descrizioni **è** la semantica.
  Chi scrive un esecutore in un altro linguaggio legge quello, non il Python;
- `ricetta.schema.json` e `pss.schema.json` descrivono i dati di ingresso. Un test
  verifica che restino allineati alle dataclass, campo per campo;
- `rapporto.schema.json` descrive il rapporto JSON dell'esecuzione;
- `casi/` (143 casi), `risposte/` (risposte reali del SAC; in `risposte/sist/`, `risposte/fvg/` e
  `risposte/piemonte/` risposte **sintetiche** del SIST, del SAR FVG e di SIRPED), `documenti/` (CDA con
  l'esito atteso), `dati/` (PSS in JSON da far generare all'implementazione).

Famiglie:

- **`offline`**: risposte **reali** registrate dal MEF e l'esito che un lettore corretto
  deve ricavarne; codifica di richieste che deve risultare valida contro l'XSD
  ufficiale. Non serve la rete.
- **`online`**: scenari contro un servizio vivo, sia percorsi riusciti sia rifiuti
  attesi (doppio annullamento, NRE inesistente, regole del SAC, sostituto, lista NRE,
  credenziali, schema). Il profilo `sostituto` salta (SALTATO) se mancano le sue
  credenziali.
- **`fse`**: `valida_documento` (un CDA e l'esito atteso del validatore) e `genera_pss`
  (dati JSON → CDA dell'implementazione → validatore → esito atteso). Se il validatore
  non controlla i vocabolari e l'aspettativa ne dipende, il passo è SALTATO, mai verde.
- **`sist`**: SIST della Puglia, senza rete. Ci sono due tipi di passo:
  - **lettura** di risposte sintetiche (`leggi_sist_*`);
  - **codifica** di `chkPrescrizione` e della ricerca contro `CVPService.xsd`, e del CDA2 di
    prescrizione contro lo schema CDA (`codifica_sist_*`).

  `CVPService.xsd` non sta nel repository e si passa con `--xsd-sist` o `$VARCO_XSD_SIST`:
  senza, la parte XSD di quei passi è SALTATO, mai verde.
- **`fvg`**: SAR del Friuli-Venezia Giulia, senza rete. `leggi_fvg` legge risposte sintetiche,
  `codifica_fvg` codifica le richieste e le valida contro gli XSD di Insiel, che si passano con
  `--xsd-fvg` o `$VARCO_XSD_FVG` (cartella `wsdl/sar`). Anche qui, senza gli XSD la parte XSD è
  SALTATO.
- **`piemonte`**: SIRPED del Piemonte, senza rete. Cinque tipi di passo:
  - `leggi_piemonte_a2f` legge risposte A2F sintetiche;
  - `codifica_piemonte` codifica le prescrizioni (contro gli XSD del MEF, sempre) e le richieste
    A2F (contro gli XSD del kit A2F, con `--xsd-a2f` o `$VARCO_XSD_A2F`: senza, SALTATO);
  - `intestazioni_piemonte` controlla gli header delle due modalità;
  - `pkce_piemonte` controlla la challenge S256;
  - `leggi_piemonte_jwt` verifica la firma di un JWT sul JWKS.

Per collaudare un'altra implementazione:

- ricetta: si passa un adattatore `crea(credenziali, valida_localmente) -> ServizioRicetta`
  con `--adattatore modulo:funzione`, **solo con `--famiglia online`**. Le famiglie offline,
  sist, fvg e piemonte eseguono i codec di questo kit (lettori e codifica), non un
  `ServizioRicetta`: con `--adattatore` la riga di comando si rifiuta (exit 2) invece di dare
  verdi che non riguardano l'implementazione indicata. Anche `tutte` si rifiuta, perché
  mescolerebbe i verdi dell'adattatore con quelli del kit;
- FSE: si passa un generatore `dati_json -> bytes CDA` con `--adattatore-fse`, **solo con
  `--famiglia fse`** (con le altre famiglie: rifiutato, exit 2).

Regole del confronto (le stesse in `caso.schema.json`):

- ogni aspettativa dichiarata si confronta. Un rifiuto locale non chiude il confronto: soddisfa
  solo `rifiuto_locale` (o `valido: false` per `genera_pss`), e le altre aspettative dello stesso
  passo risultano non rispettate. Un `rifiuto_locale` atteso su una risposta riuscita è FALLITO;
- un'aspettativa sconosciuta (un refuso), non applicabile all'operazione o in contraddizione con
  un'altra dello stesso passo rende il caso FALLITO prima ancora di eseguirlo. Vale per entrambi gli
  esecutori: fino al giro 2 di revisione `EseguiCasiFse.java` ignorava le chiavi che non conosceva;
- un segnaposto (`${nre_atteso}`) si giudica dopo la sostituzione: il controllo di coerenza sul
  formato dell'NRE atteso vale solo per i valori letterali;
- un caso senza passi è FALLITO, in Python e in Java (lo schema ne vuole almeno uno): prima era
  SUPERATO senza verificare niente;
- un passo senza `atteso`, o con `atteso` vuoto, rende il caso FALLITO, in Python e in Java; con
  `jsonschema` installato il motore Python valida anche l'intero caso contro `caso.schema.json`
  (giro 3 di revisione, n. 2);
- un prefisso atteso su un header che non c'è è FALLITO (assente non vuol dire vuoto); un header
  atteso e insieme dichiarato assente è una contraddizione, maiuscole comprese (giro 3, Piemonte N2);
- un prerequisito che manca è SALTATO anche quando il validatore è passato esplicitamente:
  `Motore(validatore_fse=ValidatoreLocale())` senza schemi HL7 dà SALTATO, non ERRORE (giro 3, n. 3);
- `xsd_valido: false` vuol dire «mi aspetto che NON validi», non «salta lo XSD». Se lo schema
  non c'è, il passo è SALTATO (FALLITO se un'altra aspettativa è già violata), mai SUPERATO;
- senza vocabolari, un esito OK o SEMANTIC_WARNING osservato non conferma né un «OK» atteso né
  un «VOCABULARY_ERROR» atteso: SALTATO. Con il validatore locale FSE-001, FSE-002, FSE-101 e
  FSE-102 sono quindi SALTATO; li giudica il validatore ufficiale.

Esito della riga di comando: 0 solo se almeno un caso è stato giudicato e nessuno è FALLITO o
ERRORE; 1 con FALLITO o ERRORE; 2 se la suite non è eseguibile (cartella inesistente, id di
`--solo` sconosciuti o di un'altra famiglia, nessun caso, tutti SALTATO) o le opzioni sono
incompatibili. Una suite vuota non è un verde.

La prova che il formato non dipende dal Python è `EseguiCasiFse.java`: legge gli stessi
file ed esegue i casi FSE col validatore ufficiale, senza codice in comune col kit.

Gruppi di controllo nei test:

- con un'aspettativa sbagliata il motore deve dare FALLITO, sia in Python sia in Java. Lo
  controlla un mutation check (`tests/unit/test_conformita_mutazioni.py`): ogni caso reale di ogni
  famiglia, alterato in memoria in quattro modi (rifiuto locale invertito, aspettativa invertita,
  codice sbagliato, errore atteso inesistente), deve dare FALLITO se l'originale è SUPERATO, e mai
  SUPERATO se l'originale è SALTATO. Gli stessi mutanti dei casi FSE girano sull'esecutore Java
  col validatore ufficiale (test `ufficiale`, se il banco è pronto). I casi online, senza rete,
  si giudicano con uno «specchio»: un adattatore che restituisce, passo per passo, l'esito che
  rispetta l'atteso ORIGINALE. Non prova un'implementazione, prova che il confronto online morde.
  Prima i casi online erano tutti SALTATO e il confronto non veniva mai chiamato (giro 2, N4);
- lo schema deve bocciare casi guasti;
- un generatore esterno difettoso deve essere bocciato.

Un domani lo Stato potrebbe pubblicare casi come questi come collaudo
ufficiale e trasparente. Chi dichiara un'integrazione allega il rapporto JSON,
e chiunque può rieseguirlo.

## SAR regionali: il SIST della Puglia

Il primo SAR del kit. Dettagli del canale, differenze dal SAC, discrepanze nelle specifiche e
cosa manca per il collaudo: `docs/SAR_PUGLIA.md`. **Scritto e verificato sulle specifiche,
NON collaudato sul sistema regionale.**

È un altro trasporto sotto lo **stesso** modello. `RicettaSIST` rispetta `ServizioRicetta`
e il programma del medico la usa come `RicettaSAC`.

**Il modello dati: cosa si è piegato e perché.**
- `Assistito.codice_regione` (facoltativo, 3 cifre): **l'unico campo nuovo del modello.**
  Il CDA2 di prescrizione pugliese vuole il codice **nazionale** dell'ASL dell'assistito, cioè
  regione + ASL (`160114`). Il SAC non ne ha bisogno, ma non c'era altro modo di ricavarlo
  dall'ASL (`114` esiste in più regioni). Aggiunto anche a `conformita/schema/ricetta.schema.json`.
- `ServizioRicetta.visualizza(nre, cf_medico=None, *, cf_assistito=None)`: **l'unico punto in
  cui il contratto si piega.** Il SIST identifica la prescrizione con NRE **e** CF
  dell'assistito («identificazione forte»). `RicettaSAC` accetta il parametro e lo ignora.
  `RicettaSIST` senza `cf_assistito` alza `ValueError` prima di chiamare.
- `Messaggio.gravita`: riconosce anche `C` (Critical) come bloccante. Il SIST classifica le
  anomalie C/W, non E/W.
- `EsitoInvioSAR` ed `EsitoVisualizzazioneSAR`: sottoclassi, non campi nuovi negli esiti del SAC.
  L'invio SIST è due chiamate e una firma, e se la seconda non riesce va ripetuta: servono
  `registrato`, `errore_registrazione` e il CDA firmato per ripeterla. `solo_ricetta_rossa`
  dice che il SAC non era disponibile.

**Cosa NON è entrato nel modello, e perché.**
- I codici regionali dei medici, la struttura e il ruolo dell'operatore, e l'applicativo
  censito sono configurazione del canale o del servizio. Una ricetta resta la stessa ricetta
  in Abruzzo e in Puglia.
- Nome, sesso, nascita e residenza dell'assistito, se servono, vengono dal `Paziente` del
  modello FSE, attraverso un risolutore (`anagrafica=`). Gli assistiti in anagrafe regionale
  bastano col CF.
- Oscuramento nel fascicolo, maggior tutela e Piano Care Puglia sono argomenti per nome di
  `RicettaSIST.invia`. Il contratto comune non li conosce.

**Errori.** I `SoapFaultException` applicativi (NRE non trovato, stato non annullabile, altro
medico, periodo mancante) diventano un Esito non riuscito, come i rifiuti del SAC. Quelli di
sicurezza e di sistema restano eccezioni.

**Firma.** Due firme diverse:
- **WS-Security** (RSA-SHA1 sul Timestamp, imposta dalla policy del server): dietro il
  protocollo `ChiaveOperatore`;
- **CAdES del CDA** (SHA-256, signing-certificate-v2): dietro `FirmatarioCAdES`.

Con la CNS vera si implementano su PKCS#11. Il kit include solo la variante da file .p12, per
le prove.

**Verifica senza la Regione.** `strumenti/sist_server_finto.py` è un server su 127.0.0.1 che
controlla le richieste come dice la specifica (WS-Security, SOAPAction, `CVPService.xsd`,
applDigest, CAdES, CDA). `asn1crypto` serve solo a lui, quindi non entra in `src/`. Le
risposte di prova sono **sintetiche** (`conformita/risposte/sist/`): la specifica non ne
pubblica.

## SAR regionali: il SAR del Friuli-Venezia Giulia (Insiel)

Il secondo SAR del kit. Dettagli del canale, differenze dal SAC e dal SIST, difetti delle
specifiche e cosa manca per il collaudo: `docs/SAR_FVG.md`. **Scritto e verificato sulle
specifiche, NON collaudato sul sistema regionale.**

È un altro trasporto sotto lo **stesso** modello. `RicettaFVG` rispetta `ServizioRicetta`.

**Il modello dati ha tenuto senza modifiche.** Nessun campo nuovo, nessuna firma cambiata. Il SAR
FVG usa il tracciato del SAC, e quello che chiede in più era già nel modello: catalogo regionale,
tipo di accesso, `testata2` per i RAO. Il resto è configurazione:
- `ApplicativoFVG`: ProdottoCME e versione del catalogo regionale;
- `PostazioneFVG`: i campi dello User-Agent;
- `AdesioneFVG`: l'accreditamento;
- il cifratore del CF dell'assistito, passato esplicito.

La verifica della posizione del sostituto, un servizio solo regionale, è un metodo in più di
`RicettaFVG`, fuori dal contratto comune, come gli argomenti regionali di `RicettaSIST.invia`.

**Il codec FVG non manda i tag facoltativi vuoti.** Verso il SAC il kit li manda tutti, anche vuoti,
come il progetto SoapUI del MEF (par. 3.4 della specifica MEF). Negli XSD FVG parecchi tipi hanno una
lunghezza minima, quindi un tag vuoto non valida: per esempio `tipoRic` (2 caratteri) e
`classePriorita` (1). I tag obbligatori ci sono sempre, `pinCode` compreso, vuoto perché «non
utilizzato». Il codec riusa ordine dei tag e valori di `xml_sac` (`campi_testata`, `_campi_riga`):
una ricetta resta una ricetta.

**Due cose che la specifica non decide, e il kit nemmeno.**
- *Certificato di cifratura del CF*: SanitelCF secondo la tabella dei campi, un certificato
  regionale non pubblico secondo il par. 4.6. `RicettaFVG` non ha un default: il cifratore si passa.
- *Dove arrivano i codici di downgrade 060120-060130*: non possono stare in `codEsito`, che ha 4
  cifre. `richiede_downgrade_mir` li cerca nei messaggi, e un SOAP Fault che li contiene diventa un
  esito non riuscito (`esito_da_fault_invio`), come i Fault applicativi del SIST.

**Mutua autenticazione con la carta.** La chiave del medico non passa dal canale: sta nel
`ssl.SSLContext` del trasporto, che esisteva già (`contesto_tls`). Il canale riceve solo il
certificato, per controllare che sia del medico che invia (par. 2.2). Con una CRS/CNS vera la chiave
non esce dalla carta, e la libreria standard non sa usarla: serve un provider OpenSSL per PKCS#11,
oppure un componente esterno. Non incluso, come la variante PKCS#11 del SIST.

**Modalità federata.** Il kit mette nei due header (`Authorization: Bearer`, `X-JWT-ASSERTION`) i
token che gli dà chi integra (`TokenFVG`). Come si creano lo dicono due allegati non pubblici: il
kit non li crea.

**FSE regionale: non coperto, per scelta.** Il middleware FSE di Insiel per il PSS ha una sola
specifica, *ISAD-FSE-SPT-02-2025*, «a circolazione limitata» e con tutti i diritti riservati. Un
modulo scritto su quel testo non si potrebbe pubblicare né verificare in chiaro, quindi non c'è
(`docs/SAR_FVG.md`, sez. 8; `docs/TERZE_PARTI.md`). Il PSS del kit resta quello del gateway
nazionale.

**Verifica senza la Regione.** `strumenti/fvg_server_finto.py` è un server HTTPS su 127.0.0.1 con
mutua autenticazione. Controlla le richieste come dice la specifica: certificato client e suo CF,
User-Agent, SOAPAction, XSD FVG, `prodottoCme`, `pinCode` vuoto, CF cifrato. Usa la sola libreria
standard più `cryptography` e, se gli si passano gli XSD, `lxml`. Le risposte di prova sono
**sintetiche** (`conformita/risposte/fvg/`): la specifica non ne pubblica.

## SAR regionali: SIRPED del Piemonte (CSI Piemonte)

Il terzo SAR del kit. Dettagli, difetti delle specifiche, cosa manca per il collaudo e cosa vuol dire
«certificata SIRPED»: `docs/SAR_PIEMONTE.md`. **Scritto e verificato sulle specifiche, NON
collaudato sul sistema regionale.**

**Il modello dati ha tenuto senza modifiche, e anche il codec.** SIRPED usa il tracciato del SAC con
i namespace del MEF: `RicettaPiemonte` usa `xml_sac` com'è. Cambia solo il canale:
- credenziali RUPAR e cifratore regionale, passato esplicito: il certificato non è pubblico e non è
  SanitelCF;
- il secondo fattore, in una delle due modalità.

**Il pincode vuoto dell'OAuth2 senza toccare il codec.** In OAuth2 il pincode non si manda, ma il
tag è obbligatorio negli XSD del MEF. `RicettaPiemonte` lascia codificare un segnaposto e poi svuota
`<pinCode>`. Il risultato valida contro gli XSD del MEF, e un test lo controlla.

**Il kit non conserva segreti.** Id-Sessione e JWT si chiedono a chi integra, con una funzione
chiamata a ogni richiesta, come i token del FVG. Il canale li controlla prima di mandare: forma
dell'Id-Sessione, scadenza del JWT, CF del token uguale al medico. La firma del JWT la verifica
`ClientOAuth2Piemonte` sul JWKS: solo RSA, niente `none` né HMAC.

**Una guardia più stretta, perché non c'è niente di pubblicato.** Nessun host di SIRPED è
pubblicato. Ogni host dei domini della Regione e del CSI è produzione, salvo un'etichetta di
collaudo (`tst`, `test`, `collaudo`). Il nome però è un'euristica: per un collaudo serve anche
dichiarare l'host (`TrasportoHTTP(collaudi_piemonte=...)`) oltre al flag del collaudo regionale.

**Verifica senza la Regione.** `strumenti/piemonte_server_finto.py` è un server HTTP su 127.0.0.1.
Implementa servizi di prescrizione, Id-Sessione A2F e OAuth2 (authorize, token con PKCE, JWKS,
verify, revoke), e controlla le richieste come dice la specifica. Le risposte di prova sono
**sintetiche** (`conformita/risposte/piemonte/`): la specifica non ne pubblica.

## SAR regionali: il SAR dell'Umbria (PuntoZero)

Il quarto SAR del kit, e il primo REST. Dettagli, difetti delle specifiche e cosa manca per il
collaudo: `docs/SAR_UMBRIA.md`. **Scritto e verificato sulle specifiche, NON collaudato sul sistema
regionale.**

**Il modello dati ha tenuto senza modifiche.** Il SAR umbro espone in JSON gli stessi servizi del
SAC (invio, visualizza, annulla, NRE utilizzati) più la richiesta del lotto NRE e la dichiarazione di
sostituzione. Cambia il codec: `json_umbria` scrive e legge il JSON dell'OpenAPI pubblicata da
PuntoZero, con i nomi dei campi del tracciato del SAC. Lo SmartCUP va in `testata2`.

**Due JWT per richiesta.** `Authorization` (Bearer) e `FSE-JWT-Signature`, firmati con il
certificato di firma (RS256/384/512, `x5c`). I claim cambiano per servizio (`action_id`,
`purpose_of_use`, `resource_hl7_type`) e stanno in una tabella sola (`CLAIM_PER_SERVIZIO`), copiata
dalla wiki. Il CF del soggetto è nel formato HL7 CX. Il firmatario è un `Protocol`: una chiave in un
PKCS#12 va bene per il test, una smart card si collega senza toccare il canale.

**Il 502/504 dopo un invio è un esito incerto, non un errore.** La wiki dice di annullare con lo
stesso NRE e reinviare con un NRE nuovo: il canale solleva `InvioIncertoUmbria` con NRE e CF, e
`RicettaUmbria.annulla_invio_incerto` fa la prima metà. Il nuovo invio lo decide il gestionale.

**La guardia copre i domini umbri.** Ogni host di `umbria.it` e `puntozeroscarl.it` è produzione,
salvo `api-salute-test.regione.umbria.it` con il flag del collaudo regionale e un'`AdesioneUmbria`
dichiarata nel canale. Senza adesione il canale accetta solo `localhost`.

**Verifica senza la Regione.** `strumenti/umbria_server_finto.py` è un server HTTPS su 127.0.0.1 con
mutua autenticazione: verifica i due JWT (firma, scadenze, `aud`, `iss` col CN del certificato, claim del servizio,
stesso `sub`), e il corpo contro gli schemi trascritti dall'OpenAPI. Se l'OpenAPI ufficiale è
scaricata (`strumenti/scarica_specifiche.py`, con sha256), la suite di conformità valida anche contro
quella. Le risposte di prova sono **sintetiche** (`conformita/risposte/umbria/`).

## Limiti noti e passi successivi

- SIST Puglia: **non collaudato** sul sistema regionale (serve l'adesione, vedi
  `docs/SAR_PUGLIA.md` §10). Non implementati: ricovero, ricetta bianca (CVPNSSN), IUP offline,
  erogazione, FSE regionale (SAML), `getRuoliStruttureOperatore`.
- SAR FVG: **non collaudato** sul sistema regionale (serve l'accreditamento Insiel, vedi
  `docs/SAR_FVG.md` §10). Non implementati: lotti NRE (schema non pubblicato), canale MIR e procedura
  di downgrade, creazione dei token federati, CNS su PKCS#11, FSE regionale (specifica a
  circolazione limitata). La lista degli NRE utilizzati parte solo con un URL esplicito: l'endpoint
  non è pubblicato.
- SIRPED Piemonte: **non collaudato** sul sistema regionale (serve l'autocertificazione del CSI,
  vedi `docs/SAR_PIEMONTE.md` §10-11). Non implementati: lotti NRE regionali, ricetta DPCM, presa in
  carico ed erogazione, la finestra del browser e la `redirect_uri` per l'OAuth2. Nessun URL è
  pubblicato: il canale vuole gli URL espliciti.
- SAR Umbria: **non collaudato** sul sistema regionale (serve l'adesione con PuntoZero, vedi
  `docs/SAR_UMBRIA.md` §10). Non implementati: ricette rosse DPCM (`dpcm-*`, schema della risposta
  non pubblicato), erogazione, firma con smart card (solo PKCS#12 incluso).
- Non implementati: richiesta lotti NRE, pre-autorizzazione del sostituto
  (`invioDichiarazioneSostituzioneMedico`, solo nelle regioni che la chiedono),
  `demServiceAnag`.
- **Gateway FSE: nessun canale.** Servono i certificati di autenticazione e di firma
  JWT rilasciati da Sogei. Per ora niente validazione né pubblicazione sul gateway
  vero, in nessun ambiente.
- PSS: solo le sezioni obbligatorie più le esenzioni. Mancano vaccinazioni, parametri
  vitali, organi mancanti e le altre sezioni facoltative. Un solo tipo di documento:
  niente referti, niente lettere di dimissione.
- PDF non dichiarato PDF/A-3. La firma è provata solo con un certificato autofirmato.
- Il validatore ufficiale gira fuori dal suo servizio (niente JWT, niente hash,
  niente metadati): il container vero va provato su una macchina con Docker e 16 GB.
- Password o pincode errati **non sono stati provati** sull'utenza di test
  condivisa: potrebbero bloccarla per tutti.
- Il flusso a due fattori di produzione non c'è. La guardia impedisce comunque
  di arrivarci per sbaglio.
- Il certificato SanitelCF incluso scade il **23/01/2027**: `CifratoreSanitel.scaduto()`
  lo segnala, e alla scadenza va sostituito con quello nuovo pubblicato.
- Il parsing XML usa `xml.etree` (niente entità esterne; expat moderno limita
  l'espansione delle entità). Se il kit dovesse accettare XML da fonti non
  fidate, conviene valutare `defusedxml`.

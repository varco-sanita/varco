# Puglia: la ricetta attraverso il SIST (SAR regionale)

**Stato: scritto e verificato sulle specifiche, NON collaudato sul sistema regionale.**
Nessuna chiamata è mai partita verso i sistemi della Regione Puglia. Come è stato verificato è
spiegato in fondo, in «Cosa è verificato e come».

Fonte: *Specifiche di integrazione SIST*, pacchetto pubblicato su
<https://sist.sanita.puglia.it/en/specifiche-integrazione> come «4.03.27 del 16/09/2026». Il
manifesto è `strumenti/fonti_specifiche.json` (gruppo `sist`, con sha256) e la copia si scarica
con `python strumenti/scarica_specifiche.py --gruppi sist`. Le specifiche non stanno nel
repository (`docs/TERZE_PARTI.md`).

Il pacchetto contiene:
- il documento `Specifiche di integrazione SIST_4_03_27.docx`;
- i WSDL e gli XSD (`wsdl-pddasl/`, `wsdl-dominio-regione/`);
- la javadoc dei servizi;
- la definizione dei CDA: `CDA2_Prescrizione` Release 12 del 28/12/2015, `Nuovi_LEA` ed esempi;
- `Allegati Tecnici.zip`: certificati dei server e la Nota Tecnica IUP.

## 1. Il canale

In Puglia il medico non chiama il SAC del MEF: chiama la **PDD ASL** del SIST, che fa da SAR. È
il SIST a passare i dati al SAC e a riportare NRE e codice di autenticazione.

| | |
|---|---|
| Protocollo | SOAP 1.1 su HTTPS (stack WSIT/Metro lato server) |
| Servizio della ricetta | `CVPService` (componente CVP, «Ciclo di Vita Prescrittivo») |
| Namespace | `www.sist.puglia.it/Schemas/PDD_SIST/SCATEL/` (senza `http://`, così nello schema) |
| SOAPAction | `urn:sist:pddsasl:bindings:1.0:CVPPortType#<operazione>` |
| Schema | `wsdl-pddasl/CVPService.xsd` |
| Collaudo | `https://pddasl-preprod.sanita.regione.rsr.rupar.puglia.it:8181/aslba_test/CVPService` (solo `aslba_test`) |
| Produzione | `https://pdd-virtasl.rmmg.rsr.rupar.puglia.it:8181/asl{ba,bt,br,le,fg,ta}/CVPService` |

Gli host stanno sulla **RUPAR**, la rete della pubblica amministrazione pugliese: da Internet
non si raggiungono. I WSDL scaricati portano un terzo indirizzo,
`wsit-virtasl.rmmg.rsr.rupar.puglia.it:8080`. Il kit lo tratta come produzione.

## 2. Autenticazione

Tre cose insieme. Il SIST non usa né utente e password né il pincode del SAC.

1. **TLS verso il server.** Il server di collaudo ha un certificato emesso da una CA privata di
   InnovaPuglia, `CN=CA_PREPROD`. Negli `Allegati Tecnici` c'è il certificato del server, ma
   **non quello della CA**: senza, la verifica TLS non si chiude. Il certificato di produzione
   negli allegati è **scaduto il 29/04/2026**. In più, lo ha emesso «Actalis Domain Validation
   Server CA G3», mentre l'intermedio allegato è «Organization Validated Server CA G3». Nel kit
   la CA si passa come `ssl.SSLContext` a `TrasportoHTTP(contesto_tls=...)`.

2. **WS-Security con la CNS del medico** (par. 5.1 e policy dei WSDL):
   - `AsymmetricBinding`, suite `Basic128`, Timestamp obbligatorio;
   - token X509 `AlwaysToRecipient`, cioè il certificato della CNS nel `BinarySecurityToken`;
   - firma del solo `wsu:Timestamp`: exc-c14n, digest SHA-1, RSA-SHA1;
   - `KeyInfo` con un riferimento diretto al token;
   - il server rifiuta i certificati che non sono nel suo truststore.

   Il codice fiscale nel certificato deve essere quello dell'operatore: altrimenti Fault 000231
   «Il codice fiscale della smart card non corrisponde a quello dell'operatore».

3. **Dati dell'operatore e dell'applicativo** in ogni richiesta:
   - `datiOperatore`: `codStruttura` (negli esempi `160114`), `codiceFiscale`,
     `ruoloIstituzionale` (`RIS000043` = MMG). Struttura e ruolo vengono da
     `getRuoliStruttureOperatore` (componente AAA), da chiamare prima (Appendice A, premessa).
   - `datiApplicativo`: `nome`, `produttore`, `versione`, più tre campi calcolati:
     - `nonce`: 20 caratteri alfanumerici casuali;
     - `created`: es. `2009-08-16T12:07:00+0100`, ora locale con lo scarto;
     - `applDigest` = base64(SHA-1(nonce + created + codice applicativo)).

     Il **codice applicativo** lo rilascia InnovaPuglia con l'adesione e non viaggia mai in
     chiaro. Un codice sbagliato dà Fault 000220 «L'applicativo non è autorizzato».

Nel kit:
- `varco/trasporto/wssecurity.py` scrive l'intestazione firmata e sa anche verificarla;
- `varco/trasporto/sist.py` (`CanaleSIST`) costruisce `datiOperatore` e `datiApplicativo`.
  Prima di partire controlla che il CF del certificato sia quello dell'operatore.

La chiave sta dietro il protocollo `ChiaveOperatore`, che ha due metodi: `certificato_der()` e
`firma_rsa_sha1(dati)`. Con la CNS reale va implementato su PKCS#11, dove la chiave non esce
dalla carta. `ChiavePKCS12` (chiave in un file .p12) serve per le prove con certificati di test.

## 3. Operazioni

Il contratto è lo stesso del SAC, cioè `ServizioRicetta` (`varco/ricetta/sist.py`, classe
`RicettaSIST`):

| Contratto | SIST | Note |
|---|---|---|
| `invia` | `chkPrescrizione`, poi `setRegistraPrescrizione` | Vedi sotto: due chiamate, una firma |
| `visualizza` | `getPrescrizioneIdentificata` | Servono **NRE e CF dell'assistito** (`cf_assistito=`) |
| `annulla` | `setAnnullaPrescrizione` | Esito TRUE/FALSE; Fault 000279 (non in stato «prescritta»), 000280 (di un altro medico) |
| `interroga_nre_utilizzati` | `getPrescrizioniIdentificate` | Solo per periodo, prescrittore, assistito e tipo. Niente NRE puntuale né lotto (rifiuto locale) |

**L'invio** (Appendice A, «Emettere prescrizione»):

1. `chkPrescrizione`: il SIST controlla e passa al SAC. La risposta può essere:
   - **IUP (l'NRE) e `codAutenticazione`**: si stampa il promemoria;
   - **solo IUP**: il SAC non era disponibile e si stampa la **ricetta rossa**
     (`EsitoInvioSAR.solo_ricetta_rossa`). Il campo `IUP` porta «l'identificativo della
     prescrizione (IUP/NRE)» (javadoc di `chkPrescrizione`): un NRE del MEF (15 caratteri) o uno
     IUP regionale (13 caratteri, Nota Tecnica IUP). Nel CDA i due si scrivono diversi
     (CDA2_Prescrizione p. 6 e 19): NRE con root `2.16.840.1.113883.2.9.4.3.8` e autorità `MEF`,
     IUP con root `2.16.840.1.113883.2.9.4.3.6` e autorità `Regione Puglia`, e in quel caso
     `setId` = `id`. Fino al 02/10/2026 il kit scriveva sempre l'OID dell'NRE;
   - **`elencoAnomalie`**: codici `C` (Critical, bloccante) e `W` (Warning).
2. Il programma del medico crea il **CDA2 di prescrizione** (`varco/ricetta/cda_sist.py`) e lo
   **firma in CAdES** (p7m, `FirmatarioCAdES`). Il SIST controlla:
   - che il firmatario sia l'operatore (000271) e l'autore del CDA (000272);
   - che la firma sia dello stesso giorno del CDA e successiva (000303, 000304).
3. `setRegistraPrescrizione` con il p7m, `oscurato` (oscuramento nel fascicolo) e `idPCP`
   (Piano Care Puglia). Dopo il passo 1 la ricetta **esiste già** al SAC: se questa chiamata
   fallisce non si rifà l'invio, si ripete la registrazione (`RicettaSIST.ripeti_registrazione`,
   `EsitoInvioSAR.da_ripetere`).

Perché il recupero sia completo (revisione esterna del 02/10/2026):
- tutto ciò che si può rifiutare in locale (compreso il motivo di non sostituibilità, che vale
  solo nel CDA) si rifiuta **prima** di `chkPrescrizione`;
- dopo un `chkPrescrizione` riuscito nessun errore esce come eccezione: se CDA, firma CAdES, la
  preparazione della richiesta di registrazione (orologio e dati del canale) o la sua firma
  WS-Security falliscono (per esempio la CNS tolta), torna un `EsitoInvioSAR` con `da_ripetere`
  (giro 2 di revisione: prima la firma WS-Security sfuggiva; giro 3: l'orologio del canale);
  lo stesso vale per `ripeti_registrazione`, salvo gli argomenti sbagliati di chi chiama;
- l'esito porta con sé `oscurato`, `id_pcp`, la maggior tutela, la ricetta e l'anagrafica
  (`Paziente`) controllata prima di `chkPrescrizione`: il CDA si scrive con quella, anche al
  recupero, e una nuova lettura del callback passa lo stesso controllo d'identità (giro 2);
  `ripeti_registrazione(esito)` rimanda gli stessi valori e, se la firma mancava, rifà CDA e
  firma con la data di oggi (000303). Passare un `oscurato` diverso da quello dell'invio è un
  errore.

Il SIST **non restituisce il PDF del promemoria**: lo stampa il programma del medico.

Gli errori applicativi arrivano come `SoapFaultException` con un codice a 6 cifre.

- **Diventano un Esito non riuscito**, come i rifiuti del SAC: `000004`, `000061`, `000279`,
  `000280`, `910002` (`FAULT_APPLICATIVI` in `ricetta/sist.py`).
- **Restano eccezioni** gli altri: sicurezza, applicativo non autorizzato, sistema.

## 4. Differenze dal SAC

| | SAC (MEF) | SIST (Puglia) |
|---|---|---|
| Rete | Internet | RUPAR |
| Autenticazione | Basic (utente/password) + pincode cifrato + 2FA | WS-Security con la CNS + codice applicativo |
| CF dell'assistito | Cifrato con il certificato SanitelCF | In chiaro dentro il TLS |
| Invio | Una chiamata | Due chiamate più la firma CAdES di un CDA2 |
| Promemoria PDF | Lo restituisce il SAC | Lo stampa il programma |
| Codici dei medici | CF | Codice regionale (6 cifre negli esempi) in `codMedicoPrescrittore`/`codMedicoSostituito`; il CF solo nell'operatore e nel CDA |
| Sostituzione | `cfMedico1` titolare, `cfMedico2` sostituto | Il prescrittore è chi ha la CNS (il sostituto); il titolare va in `codMedicoSostituito` e nel CDA come participant `LIC` |
| Esenzione | Assente se non esente | `NES00` |
| Date | `AAAA-MM-GG hh:mm:ss` | `GG/MM/AAAA HH:mm:ss` |
| Codice prestazione | Con i punti (`89.7`) | Senza punti (`897`) |
| Nota AIFA | Com'è | A 3 cifre (`001`) |
| ASL | Codice ASL | Codice nazionale = regione + ASL (`160114`); obbligatoria solo per gli assistiti SSN, facoltativa per STP, SASN e assicurati esteri (CDA2_Prescrizione p. 2) |
| Esito | `codEsitoInserimento` 0000/0001/9999 | Anomalie C/W oppure Fault; il kit ricava lo stesso codice |
| Visualizza | Per NRE | Per NRE **e** CF dell'assistito |

## 5. Il modello dati ha tenuto

Il trasporto SIST sta sotto lo **stesso** modello (`Ricetta`, `Riga`, `Prescrittore`,
`Assistito`). Le modifiche, e il loro perché, sono in `docs/ARCHITETTURA.md`, sezione «SAR
regionali: il SIST della Puglia». In breve:
- **Campi nuovi nel modello: uno.** `Assistito.codice_regione`, perché il CDA vuole il codice
  nazionale dell'ASL dell'assistito.
- **Il contratto si piega in un punto:** `visualizza(..., cf_assistito=)`.
- **Il resto sta fuori dal modello:** codici regionali, struttura, ruolo e applicativo sono
  configurazione del canale e del servizio.

## 6. Ambiente di collaudo e accesso

Cosa dice la specifica (par. «Endpoint di riferimento»):
- l'ambiente di collaudo è `pddasl-preprod...` e serve «per eseguire i test di integrazione da
  parte dei software terzi»;
- c'è solo `aslba_test`;
- il certificato del server sta negli `Allegati Tecnici`.

**Come ci si accede, la specifica non lo dice.** Secondo l'indagine preliminare
(`indagine-mmg-opensource/REGIONI.md`, fonte: nota FIMMG Bari del 2016) servono tre cose: la
VPN verso la RUPAR, un modulo di richiesta e una CNS; poi la software house fa i test e
InnovaPuglia la abilita. Nessun elenco pubblico dei software abilitati è stato trovato.

Il server rifiuta i certificati fuori dal suo truststore. L'esempio di richiesta della specifica
porta un certificato emesso da «Actalis CA per Autenticazione CNS - COLLAUDO». Per il collaudo
serve quindi con buona probabilità una CNS di collaudo, non una qualsiasi: **da chiedere**.

Nel kit il collaudo regionale è chiuso da due serrature (`varco/ambienti.py`):
- `TrasportoHTTP(consenti_collaudo_regionale=True)`, un flag distinto da quello della
  produzione;
- un'`AdesioneSIST` (riferimento dell'adesione + codice applicativo) nel `CanaleSIST`.

Tutti gli altri host `*.puglia.it` contano come produzione e vogliono
`consenti_produzione=True`. Verso `localhost` basta un applicativo di prova.

## 7. Cose che nelle specifiche non tornano

Sono scritte qui perché chi integra non perda tempo, e per chiederle a InnovaPuglia.
Nessuna è stata «corretta» di nascosto nel codice. Dove si è dovuto scegliere, la scelta è
dichiarata.

**Versione del pacchetto**

1. Tre versioni diverse per lo stesso pacchetto:
   - il link della pagina dice 4.03.27;
   - il file scaricato si chiama `specifiche SIST 4.02.27.zip`;
   - la copertina del documento dice «Versione 4.03 Release 24 del 29 Dicembre 2025»;
   - la storia delle revisioni arriva a 4.03.27.

**Esempi XML**

2. Molti esempi XML sono **modelli**, con valori vuoti o macro (`TODAY(...)`): non validano
   contro lo schema. Il confronto del kit con gli esempi è quindi **strutturale**: percorsi
   degli elementi. Le eccezioni sono elencate e motivate in `tests/unit/test_sist.py`.
3. Alcuni esempi **non sono XML ben formati**:
   - il `setRegistraPrescrizione.xml` della televisita ha una dichiarazione XML in mezzo al file;
   - gli esempi NSSN (ricetta bianca) hanno un prefisso `soapenv` non dichiarato e un errore in
     un attributo.

   Un test se ne accorge se vengono corretti.
4. **Dati personali apparenti negli esempi.** Alcuni esempi (`cda_numSedute.xml`, televisita)
   e il certificato nell'esempio di richiesta WS-Security riportano nomi e codici fiscali che
   sembrano di persone vere. Il kit non li copia: tutto è con identità di test.

**CDA**

5. **Codici LOINC:**
   - gli esempi usano `29305-0` / `18776-5`;
   - la tabella di `CDA2_Prescrizione` R12 usa `57833-6` / `57832-8`.

   Il kit segue la tabella.
6. **moodCode dell'osservazione specialistica:**
   - la tabella `ObservationPrestPrescitte_it` (CDA2_Prescrizione p. 195) dice `PRMS`;
   - l'esempio del 2024 `cda_numSedute.xml` usa `PRMS`;
   - gli esempi in linea della stessa specifica (p. 196 e seguenti) usano `RQO`.

   Il kit emette `PRMS`, come la tabella e l'esempio più recente. Fino al 02/10/2026 questo punto
   diceva il contrario («`RQO` nella tabella, il kit segue la tabella»): era la documentazione a
   sbagliare, non il codice (revisione esterna). Il server finto rifiuta `RQO` (Fault 000218).
7. **`tipoAccesso` nel CDA** (EVN/APT), tre fonti che non si accordano:
   - la javadoc dice 1 = primo accesso;
   - la tabella dice 0 → EVN e 1 → APT;
   - l'esempio della televisita usa lo stesso valore per entrambi.

   Il kit segue la tabella. **Da confermare.**
8. **Custode del documento:**
   - la tabella `CustodianOrganization_IT` dice root `2.16.840.1.113883.2.9.2` ed extension =
     codice ASL;
   - tutti gli esempi di prescrizione usano root `2.16.840.1.113883.2.9.2.160.4.2` ed extension
     `160114`.

   Il kit segue gli esempi di prescrizione. **Da confermare.**
9. L'ordine della sezione PRIORITA e i codici class/mood della prestazione variano tra gli
   esempi.

**Firma, SOAP e schema**

10. **Codifica della stringa `prescrizione`** in `setRegistraPrescrizione`: gli esempi hanno solo
    `$FIRMA_START{...}$FIRMA_END`. Il kit manda **base64 del p7m**, per analogia con
    `addDocument` del FSE (`BASE64(CDA2_P7M_DOCUMENT)`). **Da confermare: è la cosa più
    importante da chiedere.**
11. L'esempio Java di WS-Security crea i messaggi con una factory SOAP 1.2, ma i WSDL sono SOAP
    1.1. Il kit usa SOAP 1.1, come i WSDL.
12. `CVPService.xsd` è molto lasco: quasi tutti gli elementi sono `minOccurs="0"`. Validare
    contro lo schema dimostra nomi, ordine e tipi, non l'obbligatorietà.
13. La specifica non pubblica **nessuna risposta** del CVP: le risposte usate nei test sono
    sintetiche (`conformita/risposte/sist/LEGGIMI.md`).

**Allegati Tecnici**

14. Gli `Allegati Tecnici` contengono:
    - la «Nota Tecnica IUP.doc», con un avviso di copyright restrittivo: non la copiamo e non
      la redistribuiamo;
    - `copyv3.zip`, con keystore JKS: non sono stati aperti.

## 8. Cosa non è implementato

Ognuna è una scelta, non una dimenticanza:
- ricovero (`tipologia` 2), `codBranca`, `annotazione`;
- `getRuoliStruttureOperatore` (struttura e ruolo si passano all'`OperatoreSIST`);
- lo IUP «offline» quando `chkPrescrizione` non risponde (Nota Tecnica IUP);
- la ricetta bianca (componente CVPNSSN);
- l'erogazione;
- il **FSE regionale**: i servizi `IDocumentService` usano asserzioni SAML. È un altro lavoro;
- la **verifica della firma delle risposte** del server: il kit si affida al TLS (vedi
  `docs/MINACCE.md`).

## 9. Cosa è verificato e come

Tutto senza rete verso la Regione.

**Schema ufficiale**
- Ogni richiesta del kit (`chkPrescrizione` farmaceutica, specialistica, con anagrafica e con
  sostituto; registrazione, annullamento, visualizzazione, ricerca) valida contro
  `CVPService.xsd`.
- Controllo negativo: un tag estraneo o fuori ordine viene rifiutato.
- Le 13 risposte sintetiche non-Fault validano anch'esse contro lo schema.

**Esempi ufficiali**
- La struttura di `chkPrescrizione` e del CDA è confrontata con quella degli esempi della
  specifica.
- Tutto ciò che il kit emette e gli esempi non hanno è elencato, con il motivo.
- Gli elementi del nucleo degli esempi ci sono tutti.

**CDA2**
- Valida contro lo schema CDA (`POCD_MT000040`) del kit in quattro varianti: farmaceutica,
  specialistica, sostituto, maggior tutela.

**WS-Security**
- La busta firmata dal kit è verificata da un verificatore indipendente: canonicalizzazione
  exclusive c14n di lxml più `cryptography`.
- Controllo negativo: con il Timestamp toccato, con la firma alterata o fuori tempo la verifica
  fallisce. La finestra temporale si legge solo dal Timestamp firmato, unico in `Security`: un
  Timestamp fresco non firmato aggiunto a una busta scaduta non la rende valida (par. 5.1.1;
  giro 2 di revisione).

**Server finto** (`strumenti/sist_server_finto.py`)
- È un server HTTP su 127.0.0.1 che controlla ogni richiesta come da specifica:
  - WS-Security;
  - SOAPAction;
  - `CVPService.xsd`;
  - CF del certificato = operatore (000231);
  - `applDigest` (000220);
  - firma CAdES del CDA, firmatario = operatore = autore (000219, 000271, 000272);
  - data della firma = data del CDA (000303) e ora successiva (000304);
  - CDA contro lo schema e contro i vincoli di CDA2_Prescrizione che lo schema non vede, con
    gli OID scritti nel server e non presi dal kit (000218): id e setId per IUP e NRE, ASL per
    gli assistiti SSN, `moodCode` `PRMS`;
  - `oscurato` (xs:boolean) e `idPCP` conservati e restituiti; il server può rispondere con
    `1`/`0` invece di `true`/`false`;
  - ricerca con i filtri della javadoc (periodo di emissione e di erogazione, prescrittore, tipo,
    assistito, stato) e Fault 000061 senza periodo. Il server finto non eroga: con un filtro di
    erogazione una prescrizione mai erogata non esce.
- Il giro completo:
  - invio;
  - ricetta rossa;
  - anomalie;
  - registrazione fallita e ripetuta;
  - visualizzazione con CF giusto e sbagliato;
  - doppio annullamento;
  - ricerca;
  - sostituto;
  - applicativo e firmatario sbagliati.
- È in `tests/unit/test_sist.py` e in `prove/<data>-sist-server-finto/`.

**Suite di conformità**
- 23 casi `SIS-*` nel formato dichiarativo di `conformita/`. La parte XSD dei casi di codifica
  CVP richiede `--xsd-sist`: senza, il passo è SALTATO, non verde.

**Registro**
- I tag del SIST sono redatti (anagrafica, CF, NRE, codici dei medici, CDA). Un test lo
  controlla.

Il server finto è scritto da noi leggendo la stessa specifica: **dimostra che il kit fa ciò che
noi abbiamo capito, non che il SIST lo accetti.** Per questo serve il collaudo vero. La
revisione esterna del 02/10/2026 lo ha dimostrato: client e server condividevano gli stessi
fraintendimenti (IUP scritto come NRE, filtri di ricerca, ora della firma). I test
`test_rev*` di `tests/unit/test_sist.py` riproducono quei controesempi.

## 10. Cosa manca per il collaudo vero

Da InnovaPuglia e dalla Regione:
1. **adesione** al collaudo, con la procedura e il modulo;
2. **accesso alla RUPAR** (VPN) per l'host `pddasl-preprod`;
3. **codice applicativo** di collaudo;
4. una **CNS di collaudo** (o l'indicazione di quale certificato il truststore del collaudo
   accetta) e i dati di `getRuoliStruttureOperatore` per l'operatore di prova;
5. il certificato della **CA_PREPROD**, per la verifica TLS del server di collaudo;
6. **assistiti di test** e **codici regionali** dei medici di test (`codMedicoPrescrittore`);
7. le risposte ai punti 7, 8 e 10 della sezione 7: la codifica della stringa `prescrizione`,
   `tipoAccesso`, il custode.

Con questo, il kit si collauda così:
- `consenti_collaudo_regionale=True` più un'`AdesioneSIST` vera;
- la suite `conformita` (famiglia `sist`);
- gli scenari di `strumenti/genera_prove_sist.py` puntati al collaudo.

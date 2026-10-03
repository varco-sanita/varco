# Friuli-Venezia Giulia: la ricetta attraverso il SAR di Insiel

**Stato: scritto e verificato sulle specifiche, NON collaudato sul sistema regionale.**
Nessuna chiamata è mai partita verso i servizi della Regione Friuli-Venezia Giulia o di Insiel:
l'unico contatto è stato il download delle due specifiche pubbliche da `medicinrete.insiel.it`.
Come è stato verificato è spiegato in fondo, in «Cosa è verificato e come».

Fonti, entrambe scaricabili senza login da <https://medicinrete.insiel.it/>:

- *Specifiche di interfaccia applicativa del servizio SAR, Prescrizione ricetta dematerializzata*,
  Insiel, **Idof-dem-AT-01 dell'11/02/2026** (file `IDOF-DEM-00001-AT-16-01_v1.0.pdf`, 38 pagine).
  Ogni pagina dice «Documento a libera circolazione» e «© Tutti i diritti riservati. Proprietà
  INSIEL SpA»;
- `wsdl_prescritto.zip`: WSDL e XSD dei servizi in collaudo (date dei file nello zip: XSD 30/09/2020,
  WSDL 17/03/2021).

Il manifesto è `strumenti/fonti_specifiche.json` (gruppo `fvg`, con sha256) e la copia si scarica
con `python strumenti/scarica_specifiche.py --gruppi fvg`. Le specifiche non stanno nel repository
(`docs/TERZE_PARTI.md`).

Un terzo documento, *ISAD-FSE-SPT-02-2025* (il middleware FSE regionale per il Patient Summary),
porta la dicitura «Documento a circolazione limitata rivolto unicamente ai destinatari esplicitati».
Non lo usiamo: sezione 8.

## 1. Il canale

In FVG il medico non chiama il SAC del MEF: chiama il **SAR** della Regione, gestito da Insiel, che
passa la ricetta al SAC in modo sincrono e riporta NRE e codice di autenticazione. Il SAR «replica
i servizi esposti dal Sistema di Accoglienza Centrale non introducendo variazioni ai tracciati»
(par. 2.2).

| | |
|---|---|
| Protocollo | SOAP 1.1 su HTTPS, document/literal, TLS 1.2 o superiore (par. 2.3.2) |
| Servizi | `InvioPrescritto`, `VisualizzaPrescritto`, `AnnullaPrescritto`, `GestoreAutorizzazioni` (verifica del sostituto), `InterrogaNreUtil` |
| Namespace | `http://<messaggio>.xsd.dem.sanita.fvg.it-v1.0`; per la lista degli NRE senza versione |
| SOAPAction | vuota (`soapAction=''` nei WSDL) |
| Collaudo, carta CRS/CNS | `https://demtest.sanita.fvg.it/SARWs/<Servizio>Secure` |
| Collaudo, soluzione federata | `https://apiweb-collaudo.sanita.fvg.it:8243/prescrizione-ssn-test/1.0.0/<Servizio>`, token da `https://isweb-collaudo.sanita.fvg.it/oauth2/token` |
| Lotti NRE (MIR) | `https://sartest.sanita.fvg.it/MIRWs/RichiestaLotto` |
| Produzione | **non pubblicata** |

Gli host di collaudo sono pubblicati, ma si entra solo con i certificati: il cap. 4 dice che i
servizi «avvengono in mutua autenticazione».

## 2. Autenticazione

Niente utente e password del Sistema TS, niente pincode, niente `Authorization2F`. Due modalità
(par. 2.1).

1. **CRS/CNS, la modalità predefinita.** Mutua autenticazione TLS con il certificato della Carta
   Regionale dei Servizi o della Carta Operatore del medico. Il doppio fattore AAL2 che il SAC
   chiede è il possesso della carta più il PIN. Servono tre certificati (cap. 4):
   - quello del server;
   - quello della carta del medico;
   - quello per cifrare il CF dell'assistito (sotto).

   Il software deve inviare solo se il CF della carta nel lettore è quello del medico che invia
   (par. 2.2).

2. **Soluzione federata.** Il software autentica il medico da sé con un livello AAL2 e manda due
   header alla Piattaforma di Interoperabilità Regionale:
   - `Authorization: Bearer <access token>`;
   - `X-JWT-ASSERTION: <ID token>`.

   Come si costruiscono i due token lo dicono due allegati, *ISAD-ISR-Creazione ID Token
   Regionale* e *ISAD-ISR-Autorizzazione Applicativa Tramite Assertion Framework*, indicati come
   «da richiedere». **Non sono pubblici.**

In più, per tutte e due le modalità:

- **`User-Agent` obbligatorio** (par. 3.1):
  `<ProdottoCME>/<VersioneCME> <S.Operativo>/<Versione S.O.> <CFTitolare>/<DeviceId>`.
  - `ProdottoCME` lo attribuisce Insiel con l'accreditamento: la Tabella 1 elenca 17 codici prodotto
    di 12 fornitori.
  - `DeviceId` è il MAC address della postazione, oppure un altro identificativo, per esempio il
    numero di licenza.
- **Attributo `prodottoCme`** sulla radice di invio, visualizzazione, annullamento e verifica del
  sostituto (`use="required"` negli XSD).
- **Attributo `versioneCR`** su `ElencoDettagliPrescrizioni`: la versione del catalogo regionale
  delle prestazioni, «obbligatoriamente» per la specialistica, «comprensivo della terza cifra che
  identifica la "patch"» (par. 3.1, p. 13). Il kit lo manda solo lì. Lo XSD lo lascia facoltativo,
  la specifica no: una specialistica senza `ApplicativoFVG.versione_cr`, o con una versione senza
  patch (`1.4`), è un rifiuto locale di `RicettaFVG.invia`, anche con `valida_localmente=False`
  (il codec `xml_fvg.richiesta_invio`, chiamato da solo, la scrive se c'è ma non la impone). Fino
  al 02/10/2026 il kit la mandava senza e il server finto rispondeva `0000` (revisione esterna).
- **Catalogo regionale** (`codCatalogoPrescr`, p. 21): obbligatorio per la specialistica. Una
  stringa vuota vale come assente: il codec non manda tag vuoti, e prima del 02/10/2026 la riga
  partiva senza catalogo.
- **Cifratura del CF dell'assistito** (`codiceAss`): RSA PKCS#1 v1.5 e base64, come
  `openssl rsautl -encrypt -pkcs`. Con quale certificato, la specifica dice due cose diverse
  (sezione 7, punto 1).

Nel kit:
- `varco/trasporto/fvg.py` (`CanaleFVG`):
  - mette `User-Agent`, SOAPAction e, nella modalità federata, i due token;
  - controlla il CF della carta contro il medico che invia, alla costruzione e a ogni chiamata;
    `cf_medico` è di sola lettura e la modalità si normalizza all'enum `ModalitaFVG` (issue #7).
- La chiave della carta non passa dal canale: sta nel `ssl.SSLContext` del trasporto
  (`TrasportoHTTP(contesto_tls=...)`). Il canale riceve solo il certificato (DER), per il
  controllo del CF.
- La modalità federata accetta i token da un oggetto `TokenFVG` fornito da chi integra: il kit li
  trasporta, **non li crea**.

## 3. Operazioni

Il contratto è lo stesso del SAC, cioè `ServizioRicetta` (`varco/ricetta/fvg.py`, classe
`RicettaFVG`):

| Contratto | SAR FVG | Note |
|---|---|---|
| `invia` | `InvioPrescritto` | Stesso tracciato del SAC. NRE facoltativo: se manca lo assegna il SAC |
| `visualizza` | `VisualizzaPrescritto` | NRE e CF del medico, come nel SAC; `cf_assistito` non serve |
| `annulla` | `AnnullaPrescritto` | `cfMedico` = titolare della ricetta |
| `interroga_nre_utilizzati` | `InterrogaNreUtil` | Schema sì, **endpoint no**: il kit si ferma prima di chiamare se non gli si dà un URL |
| (solo FVG) `verifica_sostituto` | `GestoreAutorizzazioni` / `VerificaPosizioneMedicoSostituto` | Solo nel WSDL: il documento non lo descrive |

Le ricevute sono quelle del SAC, con il codice esito `0000`/`0001`/`9999`, gli errori `E`/`W` e le
comunicazioni. Il kit le legge con lo stesso lettore del SAC, che guarda solo i nomi locali dei tag.
La ricevuta d'invio può portare anche `ElencoNota` (DM 9/12/2015): per le prestazioni con numero
nota, la **tipologia di ambulatorio** dove erogarle, restituita dal SAC (p. 22). Il kit la mette
in `EsitoInvio.note` (`NotaPrestazione`: progressivo, codice prestazione, `tipo_ambulatorio`);
fino al 02/10/2026 la scartava.
Una comunicazione è tutta regionale: `0196` «CONTIENE FARMACI IN DPC NELLA REGIONE DI PRESCRIZIONE».

**Cose che la specifica mette a carico del programma del medico.** Il kit le riconosce ma non le fa:

- **Downgrade controllato in ricetta rossa** (par. 2.3.6). Per la specialistica, con i codici
  d'errore da `060120` a `060130` il programma deve emettere la ricetta rossa (MIR) senza
  disturbare il medico. `richiede_downgrade_mir(esito)` lo dice. Il canale MIR (Medici in Rete,
  DPCM 2008) non è nel kit.
- **Downgrade automatico** (par. 2.3.3). Scatta se il SAC non è raggiungibile o se la risposta
  supera una soglia (esempio: 8 secondi). In quel caso il programma deve:
  - mandare la ricetta su MIR con un **nuovo** NRE;
  - controllare che quella dematerializzata non sia stata accolta in ritardo;
  - se lo è stata, annullarla.

  Il kit dà il timeout (`TrasportoHTTP(timeout_s=...)`) e l'annullamento, non la procedura.
- **Accodamento** se manca la connessione in ambulatorio (par. 2.3.4).
- **Promemoria**: il layout è quello del Sistema TS, più le diciture regionali sulla validità per
  classe di priorità (par. 2.3.9).
- **Lotti di NRE** (par. 4.1). C'è la tabella dei campi, ma nello zip non ci sono né WSDL né XSD
  per `RichiestaLotto`: non implementato.

## 4. Differenze dal SAC e dal SIST

| | SAC (MEF) | SIST (Puglia) | SAR FVG (Insiel) |
|---|---|---|---|
| Rete | Internet | RUPAR | non detta; host con nomi pubblici `*.sanita.fvg.it`, in mutua autenticazione |
| Autenticazione | Basic + pincode cifrato + 2FA | WS-Security con la CNS + codice applicativo | mTLS con CRS/CNS, oppure token federati |
| Identificazione del software | nessuna | `datiApplicativo` + `applDigest` | `User-Agent` + attributo `prodottoCme` |
| Pincode | cifrato | assente | elemento obbligatorio ma vuoto («non utilizzato») |
| CF dell'assistito | cifrato con SanitelCF | in chiaro dentro il TLS | cifrato: SanitelCF o certificato regionale (sez. 7) |
| Tracciato | XSD del MEF | CVP proprio + CDA2 firmato CAdES | quello del SAC con namespace, attributi e tipi propri |
| Invio | una chiamata | due chiamate e una firma | una chiamata |
| Tag vuoti | il kit li manda tutti | — | i facoltativi si mandano solo se valorizzati: vuoti non validano |
| `numsedute` | sì | `numSedute` | **no**, il tracciato FVG non ce l'ha |
| Televisita | `prescrizione1 = TV;` | catalogo | codice di catalogo regionale; «TV» non ammesso (par. 4.2.2) |
| Catalogo | facoltativo | obbligatorio | `codCatalogoPrescr` obbligatorio per la specialistica, più `versioneCR` |
| RAO | — | — | indicazione clinica in `descrizioneDiagnosi`, `testata2 = R<classe>;P<progressivo>` (par. 4.2.3) |
| Visualizza | per NRE | per NRE **e** CF dell'assistito | per NRE |
| Sostituto | `cfMedico2` | CNS del sostituto | `cfMedico2`, carta del sostituto; più un servizio di verifica |
| Ricetta rossa | — | solo IUP se il SAC non risponde | downgrade MIR, con i codici 060120-060130 o con la soglia di tempo |
| Produzione | pubblicata | pubblicata | non pubblicata |

## 5. Il modello dati ha tenuto, senza modifiche

Il trasporto FVG sta sotto lo **stesso** modello (`Ricetta`, `Riga`, `Prescrittore`, `Assistito`)
e lo **stesso** contratto `ServizioRicetta`. **Zero campi nuovi, zero firme cambiate.** L'unica
aggiunta è nell'esito, ed è comune al SAC: `EsitoInvio.note` (`ElencoNota`, che c'è anche nella
ricevuta del MEF e il lettore comune scartava).

- Tutto ciò che il tracciato FVG chiede in più c'era già: `codice_catalogo` (`codCatalogoPrescr`),
  `tipo_accesso`, `testata2` (RAO), `nre`.
- Ciò che è del software o della postazione è configurazione del canale (`ApplicativoFVG`,
  `PostazioneFVG`, `AdesioneFVG`):
  - `prodottoCme` e `versioneCR`;
  - lo User-Agent;
  - il certificato di cifratura.
- `numsedute` esiste nel modello ma non nel tracciato FVG: rifiuto locale, non un campo tolto.
- `visualizza(..., cf_assistito=)`, il punto in cui il contratto si era piegato per la Puglia, qui
  non serve: il parametro si ignora come nel SAC.
- La verifica del sostituto è un metodo in più di `RicettaFVG`, fuori dal contratto comune.
- Il downgrade è una funzione sugli esiti (`richiede_downgrade_mir`), non un campo nuovo.

## 6. Ambiente di collaudo e accesso

Cosa dice la specifica:

- gli endpoint del cap. 5;
- la mutua autenticazione con la carta del medico (cap. 4);
- il codice ProdottoCME «attribuito da Insiel alla richiesta di accreditamento ai servizi SAR da
  parte del fornitore» (par. 3.1).

Una nuova minor release delle specifiche «implica l'attivazione di un nuovo percorso di
certificazione» (Tabella 2).

**Come ci si accede, la specifica non lo dice.** Non dice:

- quali certificati client accetta `demtest`;
- se servono carte di collaudo;
- quali medici e assistiti di test esistono;
- come si chiede l'accreditamento;
- dove sta l'elenco dei prodotti accreditati, a parte la Tabella 1.

Nel kit il collaudo regionale è chiuso da due serrature:
- il flag del trasporto, `consenti_collaudo_regionale=True` (lo stesso del SIST, distinto da
  quello della produzione). La guardia (`varco/ambienti.py`) scatta in
  `varco/trasporto/http.py::consegna`, l'unico punto in cui il canale consegna la richiesta a un
  trasporto, prima di `invia`: vale anche con un **trasporto proprio** al posto di
  `TrasportoHTTP`, che legge i permessi dichiarati dal trasporto. Fino al 02/10/2026 la guardia
  stava solo dentro `TrasportoHTTP`, e un trasporto proprio la saltava (revisione esterna, punto 1);
- un'`AdesioneFVG` (riferimento dell'accreditamento + ProdottoCME) nel `CanaleFVG`.

Le due serrature, e in modalità CNS la carta del medico controllata (par. 2.2), valgono a **ogni
chiamata**, non solo alla costruzione del canale: gli endpoint (`CanaleFVG.url`) sono di sola
lettura e `url_di` ricontrolla adesione e carta prima di consegnare. Prima si poteva costruire un
canale verso localhost e poi cambiarne l'endpoint verso il collaudo, senza adesione né carta
(revisione esterna, giro 2, N1).

Gli host di produzione non sono pubblicati. Per questo **ogni altro host `*.fvg.it` o `*.insiel.it`
conta come produzione** e vuole `consenti_produzione=True`. Verso `localhost` basta un applicativo
di prova.

## 7. Cose che nelle specifiche non tornano

Sono scritte qui perché chi integra non perda tempo, e per chiederle a Insiel. Nessuna è stata
«corretta» di nascosto nel codice. Dove si è dovuto scegliere, la scelta è dichiarata.

**Sicurezza e dati**

1. **Il certificato per cifrare il CF dell'assistito.** Due fonti nella stessa specifica:
   - la tabella dei campi (par. 4.2, `codiceAss`) dice «criptato tramite l'utilizzo del
     certificatoSanitelCF.cer»;
   - il par. 4.6 dice «un certificato fornito dalla regione Friuli Venezia Giulia», già
     consegnato ai fornitori con il progetto Medici in Rete, e non pubblicato.

   Il kit non sceglie: `RicettaFVG` vuole il cifratore esplicito. **Da chiedere: è la prima
   domanda.**
2. **Dati personali apparenti nell'esempio.** L'esempio di User-Agent del par. 3.1 riporta:
   - un codice fiscale con il carattere di controllo corretto;
   - un MAC address.

   Sembrano di una persona e di un computer veri. Il kit non li copia.
3. **Il formato dello User-Agent è ambiguo.** Lo spazio separa le tre coppie, ma il nome del
   sistema operativo nell'esempio ne contiene uno («WINDOWS NT/10.0»). Il kit accetta lo spazio
   solo nel nome del sistema operativo e rifiuta `/` e spazi negli altri campi.

**Tracciato e schemi**

4. **I codici di downgrade non stanno nello schema.** `060120`-`060130` hanno 6 cifre, ma
   `codEsito` è `codEsitoType` (`[0-9]{4}`). Una ricevuta con quei codici nell'elenco errori non
   valida contro lo XSD FVG. La specifica non dice dove arrivino. Il kit li riconosce:
   - nell'elenco errori;
   - nel testo di un SOAP Fault, che diventa un esito non riuscito invece di un'eccezione.
5. **Lo schema FVG è più vecchio di quello del SAC.** «Nessuna variazione ai tracciati», dice il
   par. 2.2, ma:
   - il `DettaglioPrescrizione` FVG non ha `numsedute`;
   - la ricevuta di visualizzazione non ha `ElencoNota`;
   - parecchi tipi sono più stretti (`tipoRic` 2 caratteri, `classePriorita`, `indicazionePrescr`,
     `altro` 1, `nre` 15). Un tag facoltativo **vuoto**, come lo manda il SAC, non valida.

   Il kit manda i facoltativi solo se valorizzati. **Da confermare: gli XSD dello zip (2020) sono
   quelli in collaudo oggi?**
6. **L'esempio completo del par. 3.1 non è valido.** Porta `<inv:classePriorita/>` vuoto, che lo
   XSD rifiuta. L'esempio breve non è nemmeno XML ben formato:
   - `<inv.ElencoDettagliPrescrizioni` con il punto;
   - virgolette tipografiche;
   - una chiusura senza prefisso;
   - il namespace scritto come modello `-%<VersioneAddOn>%`.
7. **`InvioPrescrittoRicevuta-v1.0.xsd`** dichiara un attributo globale `NewAttribute` (un residuo
   dell'editor) e importa due volte lo stesso namespace. Non cambia la validazione.
8. **Lista degli NRE utilizzati** (par. 4.5):
   - nessun endpoint: il WSDL punta a `http://localhost:8070/SARWs_FVG_TRUNK/InterrogaNreUtil` e
     il cap. 5 non la elenca;
   - namespace senza versione e niente `prodottoCme`, a differenza degli altri servizi;
   - la tabella dà `codLotto` «obbligatorio», lo schema facoltativo;
   - la tabella scrive `dataCompilazioneRicettaDa`, lo schema `...Dal`;
   - `codLotto` è di sole cifre (`[0-9]{1,7}`), mentre nel SAC il lotto è l'NRE senza il
     progressivo (12-13 caratteri).

   Il kit ricava `codLotto` dalle ultime 6-7 cifre secondo la composizione dell'NRE del par. 4.2.

   `tipoPrescr` è facoltativo nello schema FVG: qui il kit **non** impone il tipo, come fa invece
   per il SAC, dove il SAC di test risponde 1153 (giro 2 di revisione, N4). Cosa risponda il SAR
   reale a una ricerca senza tipo non è verificato.
   Il server finto lo cerca nell'NRE secondo la tabella del lotto (p. 15: 7 caratteri per i lotti
   da 100, 6 per quelli da 1000): è la stessa lettura, quindi il server finto **non** la verifica.
   **Da confermare.**

**Endpoint e versioni**

9. **Percorsi**: il cap. 5 scrive `/SARWs/...Secure`, i WSDL dello zip
   `/SARSpecialistiInterni/...Secure`. Il kit segue il documento, che è più recente.
10. **Il cap. 6 indica file che nello zip non ci sono con quei nomi.** Cita
    `invioprescritto\invioPrescritto.wsdl` e un `TipiDati.xsd` per servizio. Lo zip ha invece:
    - `sar/invioPrescritto/v1.0/invioPrescritto-v1.0.wsdl`;
    - un solo `SARWs/TipiDati-v1.0.xsd`.
11. **Versione dell'interfaccia.** I namespace dicono `v1.0`, il documento è la revisione 14
    (11/02/2026) e la storia delle revisioni salta dalla 8 alla 10. Quale sia oggi la
    `VersioneAddOn`, la specifica non lo dice esplicitamente.

**Testo**

12. **Il perimetro si contraddice.** Il cap. 1 e la nota del par. 4.2 parlano «della sola
    acquisizione dei dati sui farmaci». Il par. 2.3, invece, mette farmaceutica e specialistica
    entrambe in dematerializzata, e i parr. 2.3.6 e 4.2.3 trattano la specialistica.
13. Il par. 2.3.3 cita il «Decreto 2/11/2001»: dal contesto è il DM 2 novembre 2011.
14. **Il servizio `GestoreAutorizzazioni` non è descritto.** Il cap. 5 ne dà l'endpoint, lo zip il
    WSDL, ma il testo non dice né cosa fa né quando chiamarlo. Il kit lo espone com'è nello schema.
    Nella sua ricevuta il contenitore degli errori si chiama `ElencoErrori`, negli altri servizi
    `ElencoErroriRicette`: il kit legge entrambi.
15. **Nessuna risposta di esempio.** Le risposte usate nei test sono sintetiche
    (`conformita/risposte/fvg/LEGGIMI.md`).

**Accesso alle specifiche**

16. **La soluzione federata non si implementa con il materiale pubblico.** I due allegati sui token
    sono «da richiedere».
17. ***ISAD-FSE-SPT-02-2025* è a «circolazione limitata»**, eppure si scarica senza login da
    `medicinrete.insiel.it/allegati/`. Forse va spostato in un'area riservata, oppure
    riclassificato.

## 8. Cosa non è implementato

Ognuna è una scelta, non una dimenticanza:

- **FSE regionale per il PSS.** In FVG i gestionali dei MMG pubblicano il Patient Summary sul
  middleware FSE di Insiel, che lo inoltra al gateway nazionale (`indagine-mmg-opensource/INVENTARIO.md`).
  Non lo copriamo, per
  tre motivi:
  - l'unica specifica, *ISAD-FSE-SPT-02-2025*, è «a circolazione limitata rivolto unicamente ai
    destinatari esplicitati», cioè ai fornitori delle cartelle MMG/PLS, con tutti i diritti
    riservati;
  - non è materiale pubblico nel senso di questo progetto;
  - un modulo scritto su quel testo non si potrebbe documentare né verificare in chiaro.

  Ne abbiamo letto solo la copertina e l'indice, per verificare la dicitura, e cancellato la copia
  (`docs/TERZE_PARTI.md`). Il PSS del kit resta valido per il gateway nazionale. Il canale
  regionale si potrà scrivere quando Insiel renderà pubblica la specifica, o ci autorizzerà.
- **Lotti di NRE** (`RichiestaLotto`, MIR): manca lo schema.
- **Canale MIR** (ricetta rossa, DPCM 2008): fuori dalle specifiche pubblicate.
- **Creazione dei token federati**: allegati non pubblici. Il kit li trasporta e basta.
- **CNS su PKCS#11.** La libreria standard di Python fa la mutua autenticazione solo con una chiave
  leggibile da file. Con la carta vera serve una delle due:
  - un provider OpenSSL per PKCS#11;
  - un componente esterno che tenga la chiave sulla carta.

  Il kit funziona con qualunque `ssl.SSLContext`, ma questa parte non c'è.
- **Controllo carta/ricetta con `valida_localmente=False`.** Come per il SAC, i controlli locali si
  possono spegnere per collaudare le regole del server: in quel caso anche il confronto tra il medico
  autenticato e il prescrittore della ricetta (titolare o sostituto) salta. Il confronto tra carta e
  `cf_medico` del canale resta sempre.
- **Procedura di downgrade e recupero dopo timeout** (par. 2.3.3): a carico del programma del medico.

## 9. Cosa è verificato e come

Tutto senza rete verso la Regione e verso Insiel.

**Schemi ufficiali (XSD dello zip)**
- Ogni richiesta del kit valida contro lo XSD FVG del suo servizio:
  - invio farmaceutica, specialistica, sostituto con NRE, assistito estero;
  - visualizzazione, annullamento, verifica del sostituto;
  - lista degli NRE puntuale e per periodo.
- Controllo negativo: lo XSD boccia ciò che il kit evita apposta, cioè:
  - un tag facoltativo vuoto;
  - `numsedute`;
  - la mancanza di `prodottoCme`;
  - il namespace del SAC.
- I namespace del codec sono confrontati con i `targetNamespace` degli XSD.
- I difetti noti degli schemi (sez. 7) sono fissati in un test: se Insiel li corregge, il test se
  ne accorge.

**Risposte**
- Le 14 risposte sintetiche non-Fault validano contro gli XSD delle ricevute, tranne quella col
  codice 060120: il test controlla proprio che **non** validi.

**Server finto** (`strumenti/fvg_server_finto.py`)
- È un server HTTPS su 127.0.0.1 con **mutua autenticazione**. La CA, il certificato del server,
  le «carte» dei medici e il certificato di cifratura sono di prova, generati al momento.
- Su ogni richiesta controlla:
  - certificato client presente: senza, l'handshake non si chiude;
  - CF del certificato = medico che invia;
  - `User-Agent` del par. 3.1 con lo stesso ProdottoCME dell'attributo;
  - SOAPAction vuota;
  - radice giusta per l'endpoint;
  - XSD FVG;
  - `prodottoCme` accreditato;
  - `pinCode` vuoto;
  - CF dell'assistito decifrabile con la chiave del certificato di cifratura, e un «Codice
    Fiscale/STP/ENI/altro» (p. 18): CF, STP o ENI, o un altro codice fino a 16 caratteri; un STP
    vuole il tipo ricetta ST. Un codice che comincia per STP o ENI deve avere le 13 cifre: non
    passa come «altro» (giro 2, N3; la forma 3 lettere + 13 cifre non sta nella specifica FVG).
    Un CF ordinario ben formato (struttura e carattere di controllo) che comincia per STP o ENI,
    dal cognome, resta un CF (issue #6);
  - `codRegione` = "060" (p. 16), conservato e restituito dalla visualizzazione (giro 2, N5);
  - specialistica: `versioneCR` con la patch e `codCatalogoPrescr` in ogni riga (lo XSD non li
    impone, la specifica sì);
  - farmaceutica: niente `codCatalogoPrescr`, `tipoAccesso`, `numeroNota`, `condErogabilita`,
    `approprPrescrittiva`, `patologia`, che la p. 21 riserva alla specialistica (giro 2, N2; lo XSD
    li ammette);
  - specialistica: niente `nonSost`, `motivazNote`, `codMotivazione`, `notaProd`, che le pp. 20-21
    riservano alla farmaceutica (issue #8). Gli stessi controlli, e "060", li fa anche il client
    (`problemi_fvg`);
  - lista degli NRE: tutti i filtri (NRE, lotto, CF assistito, tipo, periodo).
- Restituisce `ElencoNota` con un `tipoAmbulatorio` di prova per le righe specialistiche con `numeroNota`.
- Il giro completo:
  - invio;
  - visualizzazione;
  - annullamento e doppio annullamento;
  - specialistica con RAO e catalogo;
  - DPC;
  - downgrade nelle due forme;
  - sostituto: carta sbagliata, invio, annullamento negato al titolare, verifica;
  - lista degli NRE;
  - carta assente, carta di un altro medico, prodotto non accreditato.
- È in `tests/unit/test_fvg.py` e in `prove/<data>-fvg-server-finto/` (scambi redatti e
  `riepilogo.json`).

**Guardia**
- Collaudo FVG bloccato senza il flag. Ogni altro host FVG o Insiel bloccato come produzione,
  anche col flag del collaudo. Blocco prima di aprire la rete, su tutti gli endpoint di tutte e due
  le modalità.

**Suite di conformità**
- 29 casi `FVG-*`: 16 di lettura e 13 di codifica. La parte XSD dei casi di codifica richiede
  `--xsd-fvg`: senza, il passo è SALTATO, non verde.

**Registro**
- Il corpo è redatto come nel SAC: CF, CF cifrato, NRE, codici; in più `cfMedicoTitolare` e
  `cfMedicoSostituto`.
- Dallo User-Agent si toglie la coppia CF del titolare/identificativo della postazione.
- `X-JWT-ASSERTION` è mascherato come gli header di autenticazione.

Il server finto è scritto da noi leggendo la stessa specifica: **dimostra che il kit fa ciò che
noi abbiamo capito, non che il SAR lo accetti.** Per questo serve il collaudo vero. La revisione
esterna del 02/10/2026 lo ha mostrato: client e server davano successo insieme a una specialistica
senza `versioneCR` o senza catalogo, il server ignorava tre filtri della lista degli NRE e
rifiutava gli STP. I test `test_rev*` di `tests/unit/test_fvg.py` riproducono quei controesempi.

## 10. Cosa manca per il collaudo vero

Da Insiel e dalla Regione:
1. la **procedura di accreditamento** e un codice **ProdottoCME** per Varco;
2. i **certificati di collaudo**:
   - certificato client (o carta CRS/Carta Operatore di collaudo) accettato da `demtest`;
   - CA del server di collaudo, se non è pubblica;
3. il **certificato per cifrare il CF dell'assistito**, o la conferma che è SanitelCF (sez. 7, punto 1);
4. **medici e assistiti di test**, con codici regionale, ASL e struttura coerenti col censimento
   regionale (par. 4.2, NB);
5. l'**endpoint della lista degli NRE utilizzati** e il significato di `codLotto`;
6. la conferma degli **XSD in uso** in collaudo, e di dove arrivano i codici 060120-060130;
7. per la soluzione federata, i due allegati ISAD-ISR sui token;
8. lo **schema del servizio dei lotti** (`RichiestaLotto`).

Con questo, il kit si collauda così:
- `consenti_collaudo_regionale=True` più un'`AdesioneFVG` vera;
- un `ssl.SSLContext` con il certificato di collaudo;
- la suite `conformita` (famiglia `fvg`);
- gli scenari di `strumenti/genera_prove_fvg.py` puntati al collaudo.

---
Licenza di questo documento: CC-BY-4.0.

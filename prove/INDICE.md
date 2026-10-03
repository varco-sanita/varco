# Prove: scambi reali con l'ambiente di TEST del MEF, validazioni ufficiali FSE, SAR regionali contro server finti

> **Nota sul nome (03/10/2026).** Il progetto si chiamava *kit-mmg* e dal 03/10/2026 si chiama
> **Varco**. Le prove precedenti alla rinomina sono rimaste come erano: nei comandi, nei percorsi,
> negli User-Agent (`kit-mmg/0.1`), negli identificativi di prova (`KITMMG`, `KITMMG-PROVA`,
> `KITMMG_301`) e nelle variabili d'ambiente (`KITMMG_*`) usano il nome vecchio e il pacchetto
> `kit_mmg`, che oggi è `varco`. È storia: non vanno corrette.

Le cartelle SAC contengono XML **scambiati davvero** con
`https://demservicetest.sanita.finanze.it` il 30/09/2026, usando solo le utenze
medico di test del kit MEF (`PROVAX00X00X000Y`, sostituto `PROVAX00X00X000Z`) e
l'assistito di test `PNIMRA70A01H501P`. Le cartelle FSE contengono documenti con
dati sintetici e l'esito reale del validatore ufficiale eseguito in locale.
Niente è stato scritto a mano.

Per ogni chiamata ci sono tre file: `..._richiesta.xml` (la busta SOAP inviata),
`..._risposta.xml` (quella ricevuta) e `..._meta.json` (URL, stato HTTP, durata,
header; `Authorization` e `Authorization2F` sono mascherati). Il nome inizia con
data e ora della chiamata.

CF assistito e pincode nelle richieste sono **cifrati** con il certificato
SanitelCF. La prova che la cifratura è giusta sta nel promemoria PDF restituito
dal SAC: riporta il CF in chiaro, quindi il SAC l'ha decifrato.

## `20260930-164416/`: le prove richieste (script `strumenti/genera_prove.py`)

| # | Operazione | Esito del SAC |
|---|---|---|
| 01 | Invio ricetta farmaceutica (principio attivo G3B) | `0000`, **NRE 1300A4019294833**, codice autenticazione `300920261644168700000050622782`, PDF promemoria (`promemoria_1300A4019294833.pdf`) |
| 02 | Visualizzazione | `0000`, stato processo **3** (da erogare) |
| 03 | Annullamento | `0000` |
| 04 | Visualizzazione dopo l'annullamento | `0000`, stato processo **4** (annullata) |
| 05 | Secondo annullamento | `9999`, errore `1120` "Operazione non consentita - Annullamento ricetta, stato non valido" (rifiuto atteso) |
| 06 | Invio ricetta specialistica (99.97.2, priorità P) | `0000`, NRE 1300A4019294834 |
| 07 | Annullamento della specialistica | `0000` |
| 08 | Visualizzazione di un NRE inesistente | `9999`, errore `5005` (rifiuto atteso) |
| 09 | Chiamata con utenza inesistente | SOAP Fault `Credenziali invalide` (rifiuto atteso) |

`riepilogo.json` riassume gli stessi passi in forma leggibile da programma.

## `20260930-1642-esplorazione/`: prime chiamate esplorative

Le prime chiamate fatte con la libreria, prima di scrivere script e suite.
Sono vere anche queste e contengono tre rifiuti utili:

- `..._164239_04_sac-invioPrescritto_*`: specialistica **senza diagnosi** inviata
  saltando i controlli locali → `9999`, errore `1233`.
- `..._164303_02_sac-invioPrescritto_*`: quantità `12` (lo schema ammette una
  cifra) → SOAP Fault `cvc-simple-type 1: ... integerType. Value is '12'`.
- `..._164304_03_sac-invioPrescritto_*`: ASL del medico `999` → `9999`, errore `1020`.

## `20260930-*-conformita/`: esecuzione completa della suite di conformità

21 casi su 21 superati (`rapporto.json`). **Difetto trovato qui e corretto**:
le chiamate 14 e 16 sono annullamenti inviati con NRE vuoto dalla "pulizia"
dei casi SAC-006 e SAC-007 (la ricetta era stata rifiutata, quindi non c'era
niente da annullare). Il SAC ha risposto `5005`. Ora la libreria rifiuta in locale
un NRE vuoto o malformato e il motore salta la pulizia se manca la variabile:
verificato rieseguendo SAC-006 e SAC-007, 2 chiamate invece di 4.

## `20260930-*-pytest/`: test di integrazione

Due esecuzioni: la seconda (`165650`) dopo aver chiuso le falle della guardia anti-produzione (redirect e punto finale nell'host). Gli scambi dei 4 test di integrazione (`KITMMG_INTEGRAZIONE=1 pytest tests/integrazione`).

## `20260930-171334-sostituto-nre/`: medico sostituto e lista NRE (script `strumenti/genera_prove_sostituto_nre.py`)

Titolare `PROVAX00X00X000Y`, sostituto `PROVAX00X00X000Z` (utenza di test del kit MEF,
`PosizioniTestMedicoSostituto.txt`). 15 chiamate, `riepilogo.json` le riassume.

| Passo | Cosa | Esito del SAC |
|---|---|---|
| S-1 | Invio del **sostituto**: cfMedico1 = titolare, cfMedico2 = sostituto, credenziali e pincode del sostituto, posizione del titolare (130/201/F) | `0000`, NRE 1300A4019294846 |
| S-2 | Visualizza come sostituto | `0001`: avviso `1024` "check digit del Codice Fiscale del campo cfMedico errato" (il CF di test del sostituto non ha il carattere di controllo giusto), stato 3 |
| S-3 | Visualizza come titolare | `0000`, stato 3, `cfMedico2` = sostituto |
| S-4 | Annulla come **titolare** | `9999`, errore `1125` (la specifica: solo il sostituto può annullare) |
| S-5 | Stessa ricetta con le credenziali del titolare | rifiuto **locale** del kit, nessuna chiamata |
| S-6 | La stessa mandata al SAC senza controlli locali | `9999`, errore `1212` "Il campo cfMedico2 deve coincidere con il CF dell'utente inviante" |
| N-0/N-1 | Invio del titolare, poi lista NRE per NRE puntuale | `0000`, 1 elemento |
| N-2 | Lista per date di oggi + tipo F | `0000`, 20 NRE (anche di altri sviluppatori: l'utenza di test è condivisa) |
| N-3 | Lista per date + CF assistito, **senza tipo** | `9999`, errore `1153` "Il tipo prescrizione è un campo obbligatorio" (la specifica lo dà facoltativo) |
| N-4 | Lista per NRE inesistente | `0000` e lista vuota, non un errore |
| N-5/N-6 | La ricetta del sostituto cercata come sostituto / come titolare | vuota / trovata con `cfMedico` = titolare |
| N-7 | Né NRE né date | rifiuto locale |
| Z | Pulizia: annullamenti (la ricetta del sostituto dal sostituto) | `0001`/`0000` |

## `20260930-172323-fse/`: FSE 2.0, lato documento (script `strumenti/genera_prove_fse.py`)

Nessuna chiamata al gateway. Documenti generati dal modello del kit con dati **sintetici**
(`kit_mmg/fse/esempi.py`), validati due volte: col validatore locale (XSD + schematron
ufficiali, lxml + Saxon) e col **codice del validatore ufficiale** del gateway
(`it-fse-gtw-validator`, vedi `strumenti/validatore-ufficiale/`), che controlla anche i
vocabolari. Esiti completi, messaggi compresi, in `esiti.json`.

| Documento | Locale | Ufficiale | Errore |
|---|---|---|---|
| `pss_01_completo.xml` (voci in tutte le sezioni + esenzione; terapia, problema, esenzione presi dalla ricetta) | OK | **OK** | |
| `pss_02_assenze.xml` (codici di assenza IPS) | OK | **OK** | |
| `pss_03_ko_vocabolario_tipo_allergia.xml` | OK | VOCABULARY_ERROR | `DALLERGY` non è nel dizionario 2.16.840.1.113883.1.11.19700 del gateway |
| `pss_04_ko_vocabolario_icd9.xml` | OK | VOCABULARY_ERROR | ICD-9-CM `000.00` inesistente |
| `pss_05_ko_senza_allergie.xml` | SEMANTIC_ERROR | SEMANTIC_ERROR | `ERRORE-b1..b4` |
| `pss_06_ko_cf_paziente.xml` | SEMANTIC_ERROR | SEMANTIC_ERROR | `ERRORE-52` |
| `pss_07_ko_ordine_xsd.xml` | SYNTAX_ERROR | SYNTAX_ERROR | `cvc-complex-type.2.4.a` |

Il primo tentativo del PSS completo **non** era valido: usava il tipo di allergia `DALLERGY`
(valido in HL7) e il validatore ufficiale l'ha rifiutato (VOCABULARY_ERROR). Il validatore locale,
che non controlla i vocabolari, l'aveva dato OK. Il modello ora ammette solo i sei codici del
dizionario del gateway; quel documento è rimasto come caso KO (`pss_03`).

PDF (`verifica_pdf.json`):

- `pss_01_completo.pdf`: PDF scritto dal kit con il CDA allegato come `cda.xml`;
- `pdf_gestionale_senza_cda.pdf` → `pdf_gestionale_con_cda_iniettato.pdf`: iniezione in un PDF
  esistente (aggiornamento incrementale);
- `*_firmato_TEST.pdf`: firma PAdES (ETSI.CAdES.detached) con un certificato **autofirmato di
  test** (`certificato_TEST_autofirmato.pem`, CN "kit-mmg CERTIFICATO DI TEST - NON VALIDO").
  Nessun valore legale. La chiave privata non è salvata.

Verifiche: il **codice del dispatcher ufficiale** (`PDFUtility.extractContentFromAttachments`,
nome allegato `cda.xml`) trova il CDA in tutti e quattro i PDF che lo contengono, identico
byte per byte (SHA-256), e non lo trova nel PDF senza allegato (gruppo di controllo). La
firma risulta integra e valida per pyHanko e per `pdfsig` (poppler); `pdfsig` dice
"Certificate issuer isn't Trusted", come deve essere per un certificato autofirmato. Il CDA
estratto dai PDF firmati, ripassato al validatore ufficiale: OK.

## `20260930-173427-conformita/`: suite completa, 47 casi su 47

`python -m kit_mmg.conformita.esegui --famiglia tutte`: 21 offline, 15 online contro il SAC
di test (sostituto e lista NRE compresi, con tutti gli XML scambiati), 11 FSE col validatore
ufficiale. `rapporto.json` segue `conformita/schema/rapporto.schema.json`.

## `20260930-173620-conformita-java/`: i casi FSE eseguiti in JAVA

Gli stessi file JSON di `conformita/casi` letti ed eseguiti da `EseguiCasiFse.java`, che
non condivide codice col Python del kit e usa il validatore ufficiale: 7 superati, 4
saltati (i casi `genera_pss`: in Java non c'è un generatore), 0 falliti. Gruppo di
controllo (`uscita_gruppo_di_controllo.txt`): con due aspettative sbagliate apposta
(`gruppo_di_controllo_*.json`) l'esecutore Java dà 2 FALLITO.

## `20260930-173527-pytest/`

Test di integrazione (`KITMMG_INTEGRAZIONE=1`), 6 su 6: i 4 di prima più sostituto e lista NRE.

## Cosa NON è stato provato, e perché

- **Password o pincode sbagliati sull'utenza di test condivisa**: non li ho provati.
  L'utenza `PROVAX00X00X000Y` la usano tutti gli sviluppatori d'Italia, e una
  serie di tentativi falliti potrebbe bloccarla. Per il caso "credenziali
  rifiutate" ho usato un'utenza che non esiste.
- **Pre-autorizzazione alla sostituzione** (`invioDichiarazioneSostituzioneMedico`): non
  implementata; serve solo nelle regioni che la chiedono.
- **Gateway FSE 2.0**: nessuna chiamata, né in validazione né in produzione. Servono
  certificati di autenticazione e firma rilasciati da Sogei (CSR a fse_support@sogei.it). Le
  validazioni in `*-fse/` sono fatte in locale col codice ufficiale.
- **Container del gateway** (`it-fse-gtw-test-container`): Docker non è installato su questa
  macchina e la versione "lite" chiede 16 GB di RAM (Kafka, Mongo, 7 microservizi compilati con
  Maven). Al suo posto: le classi del validatore e del dispatcher compilate dal sorgente, con
  i dump pubblici del loro database. Resta fuori il dispatcher come servizio (JWT, hash
  dell'allegato, metadati).
- **Firma del medico vera**: serve il suo certificato qualificato. Qui solo un certificato
  autofirmato di test.
- **SIST della Regione Puglia**: modulo sospeso il 03/10/2026 (`CHANGELOG.md`, 0.1.1). Le prove
  contro il server finto del SIST (`20261001-165725-sist-server-finto/`) sono state tolte con il
  modulo; nessuna chiamata era mai partita verso la Regione.

## `20261001-pubblicabilita/`: controlli prima della pubblicazione (01/10/2026)

Esiti dei controlli eseguiti in locale (nessuna chiamata al MEF):

| File | Cosa |
|---|---|
| `test-progetto-py3.11.txt`, `-py3.12.txt`, `-py3.14.txt` | `pytest` sull'albero completo: 261 superati, 9 saltati |
| `test-checkout-pulito-py3.11.txt`, `-py3.12.txt` | `pytest` e suite `offline`/`fse` su una copia con i soli file non esclusi da `.gitignore` (come la CI): 256 superati, 14 saltati |
| `pip-audit-*.txt`, `freeze-*.txt` | `pip-audit` 2.10.1 sugli ambienti 3.11, 3.12, 3.14: nessuna vulnerabilità nota; `pip-audit-controllo.txt`: `cryptography==41.0.0` viene segnalata (il controllo morde) |
| `publiccode.txt` | `publiccode-parser` v5.4.3 `-no-network`: 0 errori; un `softwareType` sbagliato viene bocciato |
| `actionlint.txt` | `actionlint` 1.7.7 sul workflow CI: 0 errori (shellcheck non installato) |
| `revisione_esterna_1.txt`, `revisione_esterna_2.txt` | revisione a occhi freschi di un secondo modello sui patti dichiarati. La prima: NON consegnabile (cancello del chiaro aggirabile con assistiti senza CF o CF cifrato; `motivazNote`; credenziali nell'URL; redirect nello script). Corretti con test; la seconda: consegnabile, con limiti residui scritti in `docs/MINACCE.md` |
| `scarica_specifiche.txt` | `strumenti/scarica_specifiche.py --tutto` da zero: 21 voci verificate; con un byte alterato in due copie, 2 voci bocciate ed exit 1; `scarica_specifiche_dopo_revisione.txt`: di nuovo da zero (gruppi `mef`, `fse-validatore`) dopo il blocco dei redirect non ufficiali |

## `20261002-fse-v2/` — FSE dopo la revisione esterna GPT-6 Astra del 02/10/2026

Prove rigenerate dopo le correzioni al modulo FSE (rapporto in `~/kit-mmg-review/2026-10-02/2-fse.md`): niente via di
somministrazione né stato inventati (rifiuto prima della generazione), CDA iniettato due volte rifiutato o sostituito
(kit e dispatcher ufficiale estraggono lo stesso CDA), PSS versione 2 con `setId` + `relatedDocument` RPLC valido per il
validatore ufficiale, PDF leggibile completo, avvisi sui vocabolari non verificati nell'esito pubblico, nascita non nota
rifiutata. Script: `genera_prove_fse_v2.py` nella stessa cartella. Le cartelle FSE precedenti restano come erano.
Nota: `conformita/documenti/pss_02_assenze.xml` è ancora quello generato col codice del 30/09 (valido; l'inizio delle
assenze era la data del documento, ora è «non noto»).

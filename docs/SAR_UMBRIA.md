# Umbria: la ricetta attraverso il SAR di PuntoZero

In Umbria il medico non chiama il SAC del MEF ma il **SAR regionale**, gestito da PuntoZero S.c.a r.l.,
la società in house della Regione. Il SAR «replica i servizi del SAC» come **API REST** (POST, JSON o
XML), con lo stesso payload del SAC, e si autentica come il gateway del **FSE 2.0**: mutua
autenticazione TLS e due JWT firmati.

Il modulo di Varco segue lo stesso metodo di quelli per Puglia, Friuli-Venezia Giulia e Piemonte:
stesso contratto (`ServizioRicetta`), stesso modello dati, trasporto separato, guardia, registro
redatto, server finto severo quanto la specifica, casi di conformità.

**Scritto e verificato sulle specifiche, NON collaudato sul sistema regionale.** Nessuna chiamata è
mai partita verso i sistemi umbri, nemmeno verso l'ambiente di test (sezione 6).

## Fonti

Tutto da `github.com/punto-zero/umbria-sar-support`, scaricato con
`strumenti/scarica_specifiche.py --gruppi umbria` (sha256 per i file, commit per la wiki), in
`specifiche/umbria/`, **fuori dal repository**:

- **wiki** (commit `acdbb373`, 15/06/2026): «Home» (autenticazione, JWT, Base URL, errori, cataloghi),
  «Prescrittori» (servizi, claim per servizio, SmartCUP, tempi dei controlli, check-list), «Erogatori»;
- **OpenAPI 3.1** `sar-open-api-prescrittore.yaml` (repository al commit `3cd93d86`, 15/02/2026):
  il contratto JSON leggibile da una macchina. Anche quelli dell'erogatore, solo per consultazione;
- **collection Postman** del prescrittore: la costruzione dell'NRE dal lotto (script);
- **allegati**: «Indicazione tempo controllo» (dispReg), tabelle RAO, schede di appropriatezza.

Non scarichiamo e non usiamo i **certificati di test** pubblicati nella cartella `certificati/`
(JKS e PFX con la chiave privata e la password `password`): i test del kit generano i propri.

**Licenza.** Il repository non dichiara una licenza. PuntoZero è una società consortile **a totale
capitale pubblico**, sottoscritto dalla Regione, dalle aziende sanitarie e da altre amministrazioni
umbre ([puntozeroscarl.it/azienda](https://puntozeroscarl.it/azienda/)): è una società a controllo
pubblico, quindi un soggetto dell'**art. 2, comma 2, lettera c) del CAD**. Per l'**art. 52, comma 2**
i dati e i documenti che questi soggetti pubblicano senza una licenza espressa «si intendono
rilasciati come dati di tipo aperto», salvo i dati personali
([Brocardi, art. 52](https://www.brocardi.it/codice-dell-amministrazione-digitale/capo-v/sezione-i/art52.html),
[art. 2](https://www.brocardi.it/codice-dell-amministrazione-digitale/capo-i/sezione-i/art2.html)).
Verificato il 03/10/2026. Il kit comunque non li ridistribuisce: si scaricano dalla fonte.

## 1. Il canale

| | |
|---|---|
| Protocollo | REST, `POST /sar/v1/servizi-prescrittore/<servizio>`, JSON (il kit usa JSON: l'OpenAPI dichiara solo `application/json`) |
| Host di test | `https://api-salute-test.regione.umbria.it/sar` (wiki, «Base URL») |
| Host di produzione | `https://api-salute.regione.umbria.it/sar` (bloccato dalla guardia) |
| Payload | quello del SAC, nomi dei campi compresi; ogni valore una stringa |
| Errori | HTTP 4xx/5xx con un corpo RFC 7807; errori applicativi del SAC = risposta 2xx con l'elenco degli errori |

Nel kit:
- `varco/trasporto/umbria.py` (`CanaleUmbria`): crea e firma i due JWT, mette gli header, manda il
  JSON attraverso `trasporto/http.py::consegna` (la guardia anche con un trasporto proprio),
  riconosce l'invio incerto (502, 504, nessuna risposta) e gli errori RFC 7807;
- `varco/ricetta/json_umbria.py`: codec JSON e controlli locali propri dell'Umbria;
- `varco/ricetta/umbria.py` (`RicettaUmbria`): il contratto `ServizioRicetta` più lotti NRE,
  dichiarazione di sostituzione e annullamento dopo un invio incerto.

## 2. Autenticazione

Come il FSE 2.0 (wiki, «Autenticazione»). Il software ha **due certificati** X.509:

1. **autenticazione**: per la mutua autenticazione TLS. Sta nel `ssl.SSLContext` del trasporto
   (`TrasportoHTTP(contesto_tls=...)`), come la carta del medico nel SAR FVG;
2. **firma**: firma i due JWT. Il kit lo usa attraverso il protocollo `FirmatarioJWT`
   (`FirmatarioJWTPKCS12` per un file .pfx/.p12; con un HSM basta implementare il protocollo).

I due token, rifatti a ogni chiamata (`jti` nuovo, durata predefinita 300 secondi):

| Header | iss | Claim |
|---|---|---|
| `Authorization: Bearer <JWT>` | `auth:<CN del certificato di firma>` | `sub` (CF dell'utente nel tipo CX: `CF^^^&2.16.840.1.113883.2.9.4.3.2&ISO`), `aud`, `iat`, `exp`, `jti` |
| `FSE-JWT-Signature: <JWT>` | `integrity:<CN>` | gli stessi, più `subject_organization_id` «100», `subject_organization` «Regione Umbria», `locality` (l'azienda sanitaria, formato XON), `subject_role` (APR per MMG e PLS), `subject_application_id/_vendor/_version` e i claim del servizio |

Header JWT: `alg` RS256, RS384 o RS512, `typ` «JWT», `x5c` col certificato di firma (DER, base64).

**Claim per servizio** (wiki «Prescrittori», tabelle «Valorizzazione dei custom claims»):

| Servizio | action_id | purpose_of_use | person_id, patient_consent |
|---|---|---|---|
| richiesta-lotto-nre | CREATE | non inviare | non inviare |
| dem-nre-utilizzati | READ | non inviare | non inviare |
| sostituzione-medico | CREATE | non inviare | non inviare |
| dem-invio-prescritto | CREATE | TREATMENT | CF dell'assistito, true |
| dem-visualizza-prescritto | READ | TREATMENT | CF dell'assistito, true |
| dem-annulla-prescritto | DELETE | UPDATE | CF dell'assistito, true |

Per visualizzare e annullare serve quindi anche il CF dell'assistito (`cf_assistito=`), che il
SAC non chiede: va nel token, non nel corpo.

## 3. Operazioni

| Contratto | Servizio | Note |
|---|---|---|
| `invia` | `dem-invio-prescritto` | NRE obbligatorio (`Ricetta.nre`, da un lotto), CF dell'assistito in chiaro, pinCode vuoto |
| `visualizza` | `dem-visualizza-prescritto` | `cf_assistito` per il token |
| `annulla` | `dem-annulla-prescritto` | `cf_assistito` per il token |
| `interroga_nre_utilizzati` | `dem-nre-utilizzati` | tipo facoltativo |
| `richiedi_lotto_nre` | `richiesta-lotto-nre` | «1» = 1000 NRE (consigliato a MMG e PLS), «0» = 100 |
| `dichiara_sostituzione` | `sostituzione-medico` | obbligatoria «entro il secondo trimestre 2026» |
| `annulla_invio_incerto` | `dem-annulla-prescritto` | dopo `InvioIncertoUmbria` |

**L'NRE lo mette il medico.** `LottoNRE` costruisce gli NRE come lo script della collection
Postman: `codRegione + codRagLotto + identificativoLotto + codLotto + progressivo`; un prefisso di 12
caratteri vale 1000 NRE (progressivo 000-999), di 13 vale 100 (00-99). Il kit **non** tiene il conto
degli NRE usati: lo deve fare chi integra, in modo persistente.

**Invio incerto.** «Nel caso di errori con HTTP status code 502 (Bad gateway) e 504 (Gateway
timeout), non essendo possibile stabilire se il SAC ha accettato la richiesta [...] è necessario
procedere all'invio di una request di annullamento ricetta dematerializzata con lo stesso NRE ed ad un
nuovo invio della ricetta con un diverso NRE» (wiki). Il kit solleva `InvioIncertoUmbria` (con NRE,
CF dell'assistito e del medico) anche quando la risposta non arriva affatto (timeout, rete):
`annulla_invio_incerto(errore)` fa l'annullamento, il nuovo NRE lo sceglie chi integra. L'NRE
dell'invio incerto non si riusa.

**Controlli locali propri dell'Umbria** (`json_umbria.problemi_umbria`, sempre attivi: senza, il
servizio non si può nemmeno chiamare):
- `codRegione` «100»;
- NRE presente, 15 caratteri, di un lotto umbro (comincia per «100»);
- CF dell'assistito (16 caratteri, non STP/ENI, non assicurato estero): serve al claim `person_id`;
- SmartCUP in `testata2` solo sulla specialistica, nel formato
  `SMARTCUP=SI;TEL=...;EMAIL=...;NOTECUP=...;`, al massimo 256 caratteri compresi gli encoding XML.

## 4. Differenze dal SAC e dagli altri SAR

| | SAC | SIST (Puglia) | SAR FVG | SIRPED (Piemonte) | SAR Umbria |
|---|---|---|---|---|---|
| Protocollo | SOAP | SOAP | SOAP | SOAP | **REST JSON** |
| Autenticazione | Basic + 2FA | WS-Security con la CNS | mTLS con la carta | RUPAR + Id-Sessione o JWT OAuth2 | **mTLS del software + 2 JWT firmati** |
| Chi è l'utente | credenziali del medico | CNS | carta | utente RUPAR / token | **claim `sub`** |
| CF dell'assistito | cifrato | in chiaro + CDA | cifrato | cifrato | **in chiaro** |
| NRE | lo dà il SAC | lo dà il SAC (chk) | lo dà il SAC | lo dà il SAC | **lo mette il medico, da un lotto** |
| Dopo un timeout | - | ripeti la registrazione | - | - | **annulla con lo stesso NRE, rinvia con un altro** |

## 5. Il modello dati ha tenuto, senza modifiche

Nessun campo nuovo. L'NRE del medico va in `Ricetta.nre`, che il modello aveva già per i lotti del
SAC; SmartCUP va in `Ricetta.testata2`, la check-list di appropriatezza in `Riga.prescrizione2`, i tempi
dei controlli in `Ricetta.disposizioni_regionali`: sono i campi che la wiki indica. Le aggiunte stanno
fuori dal modello: `LottoNRE` ed `EsitoLottoNRE` (il lotto) e il CF dell'assistito come argomento di
`visualizza` e `annulla` (serve al token, non al tracciato).

## 6. Ambiente di test e come ci si accede

L'host di test è pubblicato e i certificati di test sono pubblici: tecnicamente chiunque potrebbe
chiamarlo. **Non lo abbiamo fatto.** È un sistema della Regione, collegato al SAC, e usare i
certificati di qualcun altro non è un'adesione. La guardia del kit lo tratta come gli altri collaudi
regionali: servono

- il flag `TrasportoHTTP(consenti_collaudo_regionale=True)`;
- un'`AdesioneUmbria` nel canale (riferimento dell'accordo e applicativo).

Ogni altro host `*.umbria.it` o `*.puntozeroscarl.it`, compresa `api-salute.regione.umbria.it`, conta
come **produzione** ed è bloccato senza `consenti_produzione=True`. La procedura per i certificati di
produzione «verrà messa a disposizione a breve» (wiki): oggi non è pubblicata.

## 7. Cose che nelle specifiche non tornano

Scritte qui perché chi integra non perda tempo, e per chiederle a PuntoZero. Nessuna è stata
«corretta» di nascosto: dove si è dovuto scegliere, la scelta è dichiarata.

**Contratto JSON**

1. **Nomi delle proprietà del lotto.** L'OpenAPI dichiara `codRegione`, `identificativoLotto`,
   `cfmedico` (tutto minuscolo); la wiki e la collection Postman mandano `CodRegione`,
   `IdentificativoLotto`, `CFMedico`. In XML l'OpenAPI dà `CodRegione` e `IdentificativoLotto` ma
   nessun nome per `cfmedico`, l'esempio della wiki usa `<CFMedico>`. Il kit segue l'OpenAPI.
   **Da chiarire: è la differenza che può far fallire la prima chiamata.**
2. **Tipi.** Nell'OpenAPI ogni campo è `type: string`; gli esempi JSON della wiki e di Postman
   mandano numeri (`"nonEsente": 1`, `"quantita": 2`) e `null` (`"testata1": null`), e Postman manda
   `codRegione` senza virgolette in `dem-nre-utilizzati`. Il kit manda solo stringhe e omette i campi
   vuoti; il server finto rifiuta numeri e `null`.
3. **Formato.** La wiki dice che le API «supportano sia il formato JSON che XML»; l'OpenAPI dichiara
   solo `application/json` nelle richieste e `*/*` nelle risposte.
4. **Ricette rosse.** I servizi `dpcm-*` hanno una risposta «200 OK» senza schema: non si sa cosa
   leggere. Il kit non li implementa.
5. **Sostituzione.** L'OpenAPI vuole `pwd` obbligatorio; gli esempi non lo mandano. Il kit lo manda
   vuoto, come `pinCode`.
6. **Esito del lotto.** I valori di `codEsito` («00», «01» = riuscito) si ricavano solo dallo script
   Postman: né wiki né OpenAPI li elencano.

**Token**

7. **`aud`.** La tabella dice «da valorizzare con la base URL del servizio», cioè
   `https://api-salute-test.regione.umbria.it/sar`; gli esempi decodificati portano
   `https://api-salute-test.regione.umbria.it`, senza `/sar`. Il kit segue gli esempi. **Da confermare.**
8. **`purpose_of_use`.** La tabella generale lo dà «Obbligatorio» e «fisso a TREATMENT»; le tabelle
   dei servizi dicono «Non inviare» per lotto, NRE utilizzati e sostituzione, e «UPDATE» per
   l'annullamento. Il kit segue le tabelle dei servizi, più specifiche.
9. **`alg`.** «Valori ammessi: RS256, RS383, RS512»: RS383 è un refuso di RS384.
10. **`person_id` senza CF.** Per gli assistiti STP, ENI o assicurati esteri la specifica non dice
    cosa mettere nel claim (il tipo CX è definito con l'OID del CF). Il kit li rifiuta prima di
    chiamare.

**Wiki e allegati**

11. «Visualizza NRE utilizzati» ha la descrizione del lotto: «Consente al medico di richiedere un
    nuovo lotto NRE».
12. Il link alle specifiche del Sistema TS punta alla versione del 05/12/2024; quella in vigore è
    dell'08/07/2026.
13. **Tempi dei controlli** (`dispReg`): nell'allegato gli esempi («per un controllo a 6 mesi andrà
    indicato:») sono immagini. Non si legge se il valore è «6MM» o «06MM». Il kit passa il valore
    così com'è.
14. **Schede di appropriatezza** (`prescrizione2`): il PDF contiene solo immagini, il formato del
    campo non si estrae. Il kit passa `Riga.prescrizione2` così com'è.
15. **Invio incerto.** Dopo un 502/504 la specifica chiede di annullare con lo stesso NRE, ma non dice
    che risposta aspettarsi se il SAC la ricetta non l'aveva mai vista (nel server finto: «NRE
    inesistente», 5005), né se un timeout del client vale come un 504 (il kit lo tratta così).
16. **Errori RFC 7807.** C'è un solo esempio (`mw/validation-error`): l'elenco dei `type` e degli stati
    (401 per un token non valido?) non è documentato. Il server finto usa 400, 401, 404, 405, 415.
17. **Certificati di test con la chiave privata** in un repository pubblico: per un ambiente di
    test è una scelta legittima, ma vuol dire che chiunque può chiamare l'ambiente di test a nome del
    «produttore» d'esempio. Forse conviene dirlo esplicitamente nella wiki.

## 8. Cosa non è implementato

- ricette rosse (`dpcm-invio/annulla/visualizza-prescritto`): manca lo schema della risposta (punto 4);
- payload XML: il kit usa solo JSON (punto 3);
- servizi dell'erogatore;
- persistenza degli NRE usati e dei lotti: tocca a chi integra;
- la stampa del promemoria: «è obbligatorio utilizzare il promemoria restituito dal servizio SAR»
  (wiki): il kit lo restituisce (`EsitoInvio.pdf_promemoria`), la stampa la fa il programma del medico.

## 9. Cosa è verificato e come

- **Contro l'OpenAPI ufficiale** (quando le specifiche sono scaricate): tutte le richieste del kit
  validano contro gli schemi di `components.schemas`, con le proprietà non dichiarate vietate; le
  risposte sintetiche validano contro gli schemi delle ricevute; il server finto trascrive
  proprietà e obbligatori dell'OpenAPI e un test fallisce se divergono (`tests/unit/test_umbria.py`).
- **Server finto** (`strumenti/umbria_server_finto.py`), in HTTPS con mutua autenticazione: verifica
  i due JWT (firma col certificato x5c emesso dalla CA di prova, alg, typ, iss, aud, sub uguale nei
  due token, iat/exp, jti mai visto), i claim di ogni servizio, il corpo contro lo schema, e il
  merito della wiki (regione, pinCode vuoto, CF in chiaro uguale a `person_id`, NRE di un lotto del
  medico e mai usato, SmartCUP). Simula i 502/504 anche dopo aver accettato la ricetta.
- **Gruppi di controllo**: per ogni controllo del server c'è un test che lo viola e uno che lo
  rispetta; due mutazioni (sul client e sul server) fanno fallire la suite.
- **Giro completo**: lotto, invio farmaceutica e specialistica con SmartCUP, visualizzazione, NRE
  utilizzati, annullamento, sostituto col suo canale, dichiarazione di sostituzione, invio incerto con
  annullamento e nuovo NRE.
- **Conformità**: 28 casi `UMB-*` (codifica JSON e lettura di risposte SINTETICHE,
  `conformita/risposte/umbria/LEGGIMI.md`), eseguibili da chiunque:
  `python -m varco.conformita.esegui --famiglia umbria --openapi-umbria <sar-open-api-prescrittore.yaml>`.
- **Registro**: con `RegistratoreFile` nessun CF, nome, indirizzo, NRE, codice di autenticazione,
  telefono o email di SmartCUP, promemoria o JWT arriva su disco; `Authorization` e
  `FSE-JWT-Signature` sono mascherati.
- **Guardia**: host di test e di produzione, varianti (maiuscole, punto finale, domini simili),
  trasporto proprio senza flag: nessuna chiamata parte.

## 10. Cosa manca per il collaudo vero

1. Un **accordo con PuntoZero / Regione Umbria** per usare l'ambiente di test (`AdesioneUmbria`),
   con i certificati di autenticazione e firma intestati al progetto, non quelli d'esempio.
2. Le risposte ai punti 1, 7, 8 e 15 della sezione 7: sono quelle che possono far fallire le prime
   chiamate.
3. Un medico e un assistito di test coerenti col censimento regionale (la wiki ne elenca, ma sono
   della Regione: li useremo solo con l'accordo).
4. Il giro dei casi di test, se la Regione ne ha uno per la qualificazione delle cartelle.

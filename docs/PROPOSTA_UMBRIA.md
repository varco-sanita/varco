# BOZZA: proposta a PuntoZero e alla Regione Umbria

> **Bozza interna, da non inviare.** Da rileggere e firmare prima di qualunque uso.

**Oggetto:** un modulo aperto e gratuito per la ricetta dematerializzata attraverso il SAR
regionale: richiesta di accesso all'ambiente di test

Gentili colleghi di PuntoZero e della Regione Umbria,

vi scriviamo per Varco, un kit **open source** (licenza EUPL-1.2) che permette a qualsiasi
programma per i medici di medicina generale di parlare direttamente con i servizi pubblici
della sanità. Non ha fini commerciali ed è pensato per essere dato alla pubblica
amministrazione come bene comune: chiunque può leggerlo, usarlo e verificarlo.

## Cosa vi diamo

Abbiamo scritto un modulo per il SAR umbro seguendo il vostro repository
`punto-zero/umbria-sar-support`: la wiki, l'OpenAPI del prescrittore e la collection Postman. È
la documentazione più chiara che abbiamo trovato fra i SAR regionali, e ci ha permesso di lavorare
senza chiedervi niente prima. Il modulo copre:

- la mutua autenticazione TLS e i due JWT firmati (`Authorization` e `FSE-JWT-Signature`), con i
  claim di ogni servizio come nelle vostre tabelle;
- richiesta del lotto NRE, invio, visualizzazione, annullamento, NRE utilizzati, dichiarazione di
  sostituzione;
- la procedura dopo un 502 o un 504: annullamento con lo stesso NRE e nuovo invio con un NRE diverso;
- SmartCUP in `testata2`, con i controlli di formato della wiki.

Il programma del medico lo usa con la stessa interfaccia del canale nazionale (SAC) e degli altri
SAR che copriamo (Puglia, Friuli-Venezia Giulia, Piemonte).

## Cosa è già verificato

Senza toccare i vostri sistemi, nemmeno l'ambiente di test:

- tutte le richieste validano contro gli schemi della vostra OpenAPI;
- il giro completo gira contro un server di prova in locale, in HTTPS con mutua autenticazione, che
  verifica i due token e il corpo come dicono la wiki e l'OpenAPI;
- c'è una suite di conformità in JSON, 28 casi per il SAR umbro, che chiunque può rieseguire.

Lo diciamo con chiarezza: è **verificato sulle specifiche, non collaudato sul SAR**. I certificati
di test sono pubblici, ma non li abbiamo usati: ci sembra corretto chiedervelo prima.

## Cosa vi chiediamo

1. **L'accesso all'ambiente di test** `api-salute-test.regione.umbria.it` per il progetto, con
   certificati di autenticazione e di firma intestati a Varco, e la procedura che seguireste per
   un fornitore di cartella.
2. Qualche **conferma**, che può far fallire le prime chiamate:
   - nella richiesta del lotto i nomi giusti sono quelli dell'OpenAPI (`codRegione`,
     `identificativoLotto`, `cfmedico`) o quelli della wiki e di Postman (`CodRegione`,
     `IdentificativoLotto`, `CFMedico`)?
   - il claim `aud`: l'host (come negli esempi) o la base URL con `/sar` (come nella tabella)?
   - `purpose_of_use`: per l'annullamento UPDATE (tabella del servizio) o TREATMENT (tabella
     generale)? E per lotto e NRE utilizzati va omesso?
   - dopo un 502/504, se il SAC la ricetta non l'aveva mai ricevuta, che risposta dà l'annullamento?
3. Lo **schema della risposta** dei servizi delle ricette rosse (`dpcm-*`) e i valori di `codEsito`
   della richiesta del lotto.
4. Il formato testuale di `dispReg` per i controlli («6MM» o «06MM»?) e del campo `prescrizione2`
   delle schede di appropriatezza: negli allegati gli esempi sono immagini.

## Qualche segnalazione, con spirito di collaborazione

- L'OpenAPI dichiara ogni campo `type: string`, ma gli esempi JSON della wiki e di Postman mandano
  numeri (`nonEsente`, `quantita`) e `null`. Un client che segue l'OpenAPI e uno che segue gli
  esempi mandano cose diverse.
- L'OpenAPI della sostituzione vuole `pwd` obbligatorio, gli esempi non lo mandano.
- La wiki dice che le API accettano anche XML, l'OpenAPI dichiara solo `application/json`.
- «RS383» fra gli algoritmi ammessi è un refuso di RS384; «Visualizza NRE utilizzati» ha la
  descrizione della richiesta del lotto; il link alle specifiche del Sistema TS punta alla versione
  del 2024.
- Per gli assistiti senza codice fiscale (STP, ENI, assicurati esteri) non è detto cosa mettere in
  `person_id`.
- I certificati di test con la chiave privata sono in un repository pubblico. Per un ambiente di test
  va bene, ma forse conviene dirlo nella wiki, perché chiunque può chiamare il test a nome del
  produttore d'esempio.

L'elenco completo, con i riferimenti, è nella sezione 7 di `docs/SAR_UMBRIA.md`.

Siamo disponibili per un incontro, anche breve, e per adattare il modulo alle vostre indicazioni. Se
il collaudo va a buon fine, il modulo e i suoi casi di prova restano a disposizione della Regione,
di PuntoZero e di tutti gli sviluppatori.

Con i migliori saluti,

[nome e recapiti: DA COMPLETARE]

Repository: <https://github.com/varco-sanita/varco>

---
Licenza di questo documento: CC-BY-4.0.

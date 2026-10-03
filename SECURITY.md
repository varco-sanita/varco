# Segnalare una vulnerabilità

Grazie se trovi un problema di sicurezza. **Non aprire una issue pubblica**: chiunque la
leggerebbe prima che sia corretta.

## Come

Un solo canale: la **segnalazione privata di vulnerabilità** di GitHub (*private
vulnerability reporting*). Nella scheda *Security* del repository premi **«Report a
vulnerability»**, oppure vai direttamente a:

<https://github.com/varco-sanita/varco/security/advisories/new>

La segnalazione la vedono solo i manutentori, finché non decidiamo insieme di pubblicarla.
Non c'è un indirizzo email: non mandare segnalazioni di sicurezza per altre strade.

Scrivi:

- cosa succede e cosa ti aspettavi;
- i passi per riprodurlo, con **dati di test**: le identità pubbliche del kit MEF o dati
  sintetici. **Non mandare mai dati reali di pazienti o credenziali vere**, nemmeno per
  dimostrare il problema;
- versione di Varco, versione di Python, sistema operativo.

## Cosa aspettarti

Il progetto ha oggi **un solo manutentore, volontario**. Non ci sono tempi di risposta
garantiti. L'obiettivo è:

- conferma di ricezione entro 7 giorni;
- una prima valutazione entro 30 giorni;
- la correzione e una nota in `CHANGELOG.md`, con il tuo nome se lo vuoi.

Ti chiediamo di aspettare la correzione, o 90 giorni dalla segnalazione, prima di
rendere pubblico il problema.

## Cosa è nel perimetro

- Il codice di `src/varco/` e gli strumenti in `strumenti/`.
- In particolare:
  - aggirare la **guardia anti-produzione** (arrivare a `demservice.sanita.finanze.it`
    o al gateway FSE di produzione senza `consenti_produzione=True`);
  - **credenziali** (password, pincode, id di sessione) che finiscono in log, eccezioni,
    `repr` o file;
  - **dati personali** (CF, promemoria, dati sanitari) scritti su disco dal registratore
    senza il flag esplicito `registra_dati_personali_in_chiaro=True`;
  - cifratura SanitelCF sbagliata o aggirabile;
  - XML costruito in modo da iniettare campi nella richiesta al SAC;
  - verifica TLS disattivabile per sbaglio;
  - `strumenti/scarica_specifiche.py` che accetta un file o un commit diverso da quello
    fissato.
- Le dipendenze, se la vulnerabilità colpisce Varco così come le usa.

## Cosa non è nel perimetro

- I servizi del MEF, di Sogei, del Ministero della Salute e il loro codice
  (`it-fse-gtw-*`): segnalali a loro.
- L'ambiente di test del MEF: **non fare prove di carico, di forza bruta o con
  credenziali sbagliate sull'utenza di test condivisa**, la bloccheresti a tutti gli
  sviluppatori.
- Il programma del medico che usa Varco, il suo computer, la sua rete.
- Problemi che richiedono di avere già il controllo del computer su cui gira Varco.

Il modello delle minacce è in [`docs/MINACCE.md`](docs/MINACCE.md).

---
Licenza di questo documento: CC-BY-4.0.

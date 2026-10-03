# BOZZA: proposta alla Regione Piemonte e al CSI Piemonte

> **Bozza interna, da non inviare.** Da rileggere e firmare prima di qualunque uso.

**Oggetto:** un modulo aperto e gratuito per la ricetta dematerializzata attraverso SIRPED:
richiesta di accesso all'autocertificazione

Gentili colleghi della Regione Piemonte e del CSI Piemonte,

vi scriviamo per Varco. È un kit **open source** (licenza EUPL-1.2) che permette a qualsiasi
programma per i medici di medicina generale di parlare direttamente con i servizi pubblici della
sanità. Non ha fini commerciali ed è pensato per essere dato alla pubblica amministrazione come
bene comune. Chiunque può leggerlo, usarlo e verificarlo.

La licenza è la stessa che avete scelto per `fonti-fse`, `webappmed-fse`, `gatefire` e `lcce`
nell'organizzazione GitHub `regione-piemonte`. Questo rende il riuso reciproco semplice, in tutte e
due le direzioni.

## Cosa vi diamo

Abbiamo scritto un modulo per SIRPED seguendo i documenti pubblicati nella scheda SIRPED del
catalogo dei servizi regionali:
- *Accesso ai servizi delle ricette dematerializzate mediante autenticazione forte*, REL-STC-01 V04
  del 02/03/2026, e il YAML dei servizi OAuth2;
- il processo di autocertificazione, il piano dei test e gli attestati del 2026;
- i requisiti di integrazione RE-SRS-SAR V05 e il piano dei test RE-TES-01 V02.

Sono tutti «Uso: Esterno», e questo ci ha permesso di lavorare senza chiedervi niente prima.

Il modulo copre:
- le credenziali RUPAR e la cifratura di pincode e codice fiscale con il certificato regionale. Il
  kit non ha un certificato predefinito: va passato quello che fornite voi;
- l'**Id-Sessione con mail certificata**: CreateAuth, CheckToken e RevokeAuth sugli XSD del kit A2F
  del Sistema TS, e gli header `X-idSessione` e `X-Gestionale`;
- l'**Id-Sessione con OAuth2**:
  - Authorization Code con PKCE S256;
  - verifica della firma del JWT sul JWKS;
  - verify e revoke;
  - `X-OAuth2-Authorization` senza Basic, `pinCode` vuoto;
- invio, visualizzazione, annullamento e lista degli NRE utilizzati, col tracciato del SAC;
- il controllo, già sul programma del medico, che il prescrittore sia il medico autenticato. Il
  piano dei test dice che in produzione lo farà SIRPED: così l'errore emerge prima.

Il programma del medico lo usa con la stessa interfaccia del canale nazionale (SAC) e dei SAR di
Friuli-Venezia Giulia e Umbria.

## Cosa è già verificato

Senza toccare i vostri sistemi:
- le richieste validano contro gli XSD del MEF e contro quelli del kit A2F;
- il giro completo gira contro un server di prova in locale che applica i controlli scritti nella
  specifica, nelle due modalità;
- c'è una suite di conformità in JSON, 44 casi per SIRPED, che chiunque può rieseguire.

Lo diciamo con chiarezza: tutto questo è **verificato sulle specifiche, non collaudato su SIRPED**.
Il collaudo vero lo potete rendere possibile solo voi.

## Perché può servire alla Regione

Nell'allegato tecnico dell'avviso AP26_003, sul flusso SIAP, il CSI spiega che deve coinvolgere i
fornitori delle cartelle cliniche «già certificate per l'integrazione con il sistema SIRPED». Li
considera «infungibili» perché sono gli unici ad avere la piena disponibilità dei loro programmi. È
una situazione comune a tutte le regioni, non una scelta di qualcuno: i programmi dei medici sono
prodotti privati, e ogni integrazione nuova passa da chi li possiede.

Un modulo aperto, insieme a prove trasparenti, può ridurre un po' quella dipendenza nel tempo:
- il codice che parla con SIRPED è pubblico, e la Regione può leggerlo, provarlo e riusarlo;
- la suite di conformità e il server di prova sono pubblici. Chiunque, anche un fornitore piccolo o
  un'azienda sanitaria, può verificare il proprio programma prima di chiedervi l'autocertificazione.
  Questo riduce il lavoro di supporto;
- un fornitore che adotta il modulo non deve riscrivere il canale per ogni novità regionale. Lo
  aggiorna una volta sola, per tutti.

Non pensiamo che sostituisca i fornitori. Può abbassare la soglia d'ingresso per chi è nuovo, e dare
alla Regione uno strumento suo per verificare.

## Cosa vi chiediamo

1. **Se e come un progetto nuovo può entrare nell'autocertificazione.** Il processo SIRPED-01 del
   2026 è scritto per i «gestionali precedentemente certificati». Per chi comincia adesso vale ancora
   il piano RE-TES-01 del 2016, o c'è un percorso diverso?
2. **Il codice gestionale**, e quale codice azienda deve usare chi fornisce un programma per i
   medici di famiglia nel valore `<codice>_<azienda>`.
3. **Il kit di test**:
   - gli URL;
   - la CA del server;
   - il certificato regionale di cifratura;
   - utenze RUPAR di test;
   - medici e assistiti di test censiti nel configuratore;
   - per l'OAuth2, la registrazione del `client_id` e della `redirect_uri`.
4. Qualche **conferma** (dettagli in `docs/SAR_PIEMONTE.md`, sezione 7):
   - la revoca OAuth2 si chiama con DELETE, come dice il testo, o con GET, come nell'esempio e nel
     YAML?
   - `expires_in` è una durata o un istante?
   - `X-Gestionale` va mandato anche in OAuth2?
   - la visualizzazione della ricetta vuole l'Id-Sessione o il JWT, anche se non è negli elenchi dei
     par. 4.2.5 e 4.3.6?
   - gli errori di Id-Sessione e JWT arrivano come SOAP Fault o come esito `9999`, e con quali codici?

## Qualche segnalazione, con spirito di collaborazione

Leggendo le specifiche abbiamo notato alcune cose che forse vi è utile sapere. Le mandiamo come un
contributo, sapendo quanto lavoro c'è dietro documenti come questi.

- **L'esempio PKCE in shell del par. 4.3.1** usa `tr -d "=+/"`, che cancella `+` e `/` invece di
  tradurli in `-` e `_`. Con molti verifier il risultato è più corto di 43 caratteri, o non è la
  challenge della RFC 7636. Un fornitore che copia l'esempio potrebbe vedersi rifiutare il token
  senza capire perché. Basterebbe `tr '+/' '-_' | tr -d '='`.
- **Due vincoli degli XSD A2F** possono sorprendere:
  - i tre permessi insieme (39 caratteri) superano i 30 di `applicazione`;
  - un pincode cifrato con una chiave RSA da 2048 bit (344 caratteri) supera i 256 di `valore`.
  
  Quanti bit ha il certificato regionale?
- `codSsa` è descritto come opzionale, ma lo schema non lo accetta vuoto: va omesso.
- Il **JWKS** chiama il modulo «v» nella tabella e «n» nell'esempio. Il `kid` è fisso nella tabella
  e un UUID nell'esempio. Il percorso del JWKS cambia fra tabella ed esempio.
- L'esempio di errore del par. 4.2.4 non ha il contenitore `<errori>` dello schema.
- Due piccoli refusi nella tabella dei campi: «livelloAautenticazione», «modAautenticazione».

L'elenco completo, con i riferimenti, è in `docs/SAR_PIEMONTE.md`, sezione 7.

Siamo disponibili per un incontro, anche breve, e per adattare il modulo alle vostre indicazioni. Se
il collaudo va a buon fine, il modulo e i suoi casi di prova restano a disposizione della Regione, del
CSI e di tutti gli sviluppatori.

Con i migliori saluti,

[nome e recapiti: DA COMPLETARE]

---
Licenza di questo documento: CC-BY-4.0.

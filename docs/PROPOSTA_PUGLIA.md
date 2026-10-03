# BOZZA: proposta a InnovaPuglia e alla Regione Puglia

> **Bozza interna, da non inviare.** Da rileggere e firmare prima di qualunque uso.

**Oggetto:** un modulo aperto e gratuito per la ricetta dematerializzata attraverso il SIST:
richiesta di accesso all'ambiente di collaudo

Gentili colleghi di InnovaPuglia e della Regione Puglia,

vi scriviamo per Varco, un kit **open source** (licenza EUPL-1.2) che permette a qualsiasi
programma per i medici di medicina generale di parlare direttamente con i servizi pubblici
della sanità. Non ha fini commerciali ed è pensato per essere dato alla pubblica
amministrazione come bene comune: chiunque può leggerlo, usarlo e verificarlo.

## Cosa vi diamo

Abbiamo scritto un modulo per il SIST seguendo le vostre *Specifiche di integrazione*
pubblicate come versione 4.03.27. Le specifiche sono complete e pubbliche, e questo è un
merito raro. Il modulo copre:

- WS-Security con la CNS del medico, come da policy dei WSDL;
- `chkPrescrizione`, poi CDA2 di prescrizione firmato CAdES e `setRegistraPrescrizione`, con
  la ricetta rossa quando il SAC non risponde e la ripetizione della registrazione;
- `getPrescrizioneIdentificata`, `setAnnullaPrescrizione`, `getPrescrizioniIdentificate`.

Il programma del medico lo usa con la stessa interfaccia del canale nazionale (SAC). Per chi
sviluppa in Puglia il lavoro di integrazione diventa più corto e più uniforme.

## Cosa è già verificato

Senza toccare i vostri sistemi:

- tutte le richieste validano contro `CVPService.xsd`;
- la loro struttura è confrontata con gli esempi della specifica;
- il CDA valida contro lo schema CDA;
- la firma WS-Security è controllata da un verificatore indipendente;
- il giro completo (invio, visualizzazione, annullamento, ricerca, sostituto, errori) gira
  contro un server di prova in locale, che applica i controlli scritti nella specifica;
- c'è una suite di conformità in JSON che chiunque può rieseguire.

Lo diciamo con chiarezza: tutto questo è **verificato sulle specifiche, non collaudato sul
SIST**. Il collaudo vero lo potete rendere possibile solo voi.

## Cosa vi chiediamo

1. **L'accesso all'ambiente di collaudo** (`pddasl-preprod`, `aslba_test`) per il collaudo di
   accettazione: la procedura di adesione, l'accesso alla RUPAR e un codice applicativo di
   collaudo.
2. **Una CNS di collaudo**, o l'indicazione del certificato che il truststore del collaudo
   accetta, e gli **assistiti e i medici di test** con i loro codici regionali.
3. Il certificato della **CA_PREPROD**: negli Allegati Tecnici c'è il certificato del server di
   collaudo ma non quello della CA che lo ha emesso.
4. Tre **conferme** sulla specifica:
   - il formato della stringa `prescrizione` in `setRegistraPrescrizione`: noi mandiamo il p7m
     in base64;
   - la corrispondenza di `tipoAccesso` nel CDA (EVN/APT);
   - l'identificativo del custode del documento.

## Qualche segnalazione, con spirito di collaborazione

Leggendo il pacchetto abbiamo notato alcune cose che forse vi è utile sapere:

- alcuni esempi XML non sono ben formati;
- il certificato di produzione negli Allegati Tecnici risulta scaduto il 29/04/2026;
- i numeri di versione non coincidono tra il link, il nome del file e la copertina.

Inoltre, alcuni esempi (`cda_numSedute.xml`, quelli della televisita) e il certificato
nell'esempio di richiesta WS-Security riportano nomi e codici fiscali che **sembrano di persone
vere**. Se lo sono, forse conviene sostituirli con dati di prova. Nel nostro kit non li abbiamo
copiati.

L'elenco completo, con i riferimenti, è in `docs/SAR_PUGLIA.md`.

Siamo disponibili per un incontro, anche breve, e per adattare il modulo alle vostre
indicazioni. Se il collaudo va a buon fine, il modulo e i suoi casi di prova restano a
disposizione della Regione e di tutti gli sviluppatori.

Con i migliori saluti,

[nome e recapiti: DA COMPLETARE]

---
Licenza di questo documento: CC-BY-4.0.

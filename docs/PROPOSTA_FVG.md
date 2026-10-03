# BOZZA: proposta a Insiel e alla Regione Friuli-Venezia Giulia

> **Bozza interna, da non inviare.** Da rileggere e firmare prima di qualunque uso.

**Oggetto:** un modulo aperto e gratuito per la ricetta dematerializzata attraverso il SAR
regionale: richiesta di accreditamento per il collaudo

Gentili colleghi di Insiel e della Regione Friuli-Venezia Giulia,

vi scriviamo per Varco, un kit **open source** (licenza EUPL-1.2) che permette a qualsiasi
programma per i medici di medicina generale di parlare direttamente con i servizi pubblici
della sanità. Non ha fini commerciali ed è pensato per essere dato alla pubblica
amministrazione come bene comune: chiunque può leggerlo, usarlo e verificarlo.

## Cosa vi diamo

Abbiamo scritto un modulo per il SAR seguendo le vostre *Specifiche di interfaccia applicativa del
servizio SAR* (Idof-dem-AT-01 dell'11/02/2026) e gli schemi di `wsdl_prescritto.zip`. Sono
pubbliche e «a libera circolazione», e questo ci ha permesso di lavorare senza chiedervi niente
prima. Il modulo copre:

- la mutua autenticazione TLS con la carta del medico, e il controllo che la carta sia del medico
  che invia;
- lo `User-Agent` e l'attributo `prodottoCme` del par. 3.1, `versioneCR` per la specialistica;
- `InvioPrescritto`, `VisualizzaPrescritto`, `AnnullaPrescritto` e la verifica della posizione del
  sostituto;
- il riconoscimento dei codici di downgrade in ricetta rossa del par. 2.3.6;
- i controlli locali propri del FVG: televisita col codice di catalogo, catalogo obbligatorio per
  la specialistica, niente numero di sedute.

Il programma del medico lo usa con la stessa interfaccia del canale nazionale (SAC) e degli
altri SAR che copriamo (Piemonte, Umbria). Per chi sviluppa in Friuli-Venezia Giulia il lavoro di integrazione diventa più corto e
più uniforme.

## Cosa è già verificato

Senza toccare i vostri sistemi:

- tutte le richieste validano contro gli XSD di `wsdl_prescritto.zip`;
- il giro completo (invio, visualizzazione, annullamento, sostituto, downgrade, errori) gira contro
  un server di prova in locale, in HTTPS con mutua autenticazione, che applica i controlli scritti
  nella specifica;
- c'è una suite di conformità in JSON, 29 casi per il SAR FVG, che chiunque può rieseguire.

Lo diciamo con chiarezza: tutto questo è **verificato sulle specifiche, non collaudato sul SAR**.
Il collaudo vero lo potete rendere possibile solo voi.

## Cosa vi chiediamo

1. **L'accreditamento al collaudo**: la procedura, un codice **ProdottoCME** per Varco e i
   **certificati di collaudo** accettati da `demtest.sanita.fvg.it` (o una carta di collaudo).
2. **Il certificato per cifrare il codice fiscale dell'assistito.** La tabella del par. 4.2 parla
   di SanitelCF, il par. 4.6 di un certificato regionale consegnato con il progetto Medici in Rete.
   Quale va usato oggi?
3. **Medici e assistiti di test**, con codici regionali, ASL e struttura coerenti col vostro
   censimento.
4. Qualche **conferma**:
   - gli XSD dello zip (2020) sono quelli in uso in collaudo?
   - dove arrivano i codici di downgrade 060120-060130? Nello schema `codEsito` ha 4 cifre;
   - l'endpoint della lista degli NRE utilizzati, e cosa va in `codLotto`;
   - i percorsi giusti: `/SARWs/` come nel documento o `/SARSpecialistiInterni/` come nei WSDL?
5. Se possibile, lo **schema del servizio dei lotti** e i due allegati sui token della soluzione
   federata (ISAD-ISR), oggi «da richiedere».

## Qualche segnalazione, con spirito di collaborazione

Leggendo le specifiche abbiamo notato alcune cose che forse vi è utile sapere:

- l'esempio di XML del par. 3.1 non è ben formato. L'esempio completo porta un
  `<classePriorita/>` vuoto, che lo schema rifiuta;
- `InvioPrescrittoRicevuta-v1.0.xsd` dichiara un attributo globale `NewAttribute`, forse rimasto
  dall'editor;
- il cap. 6 indica nomi di file che nello zip non ci sono così; il par. 2.3.3 cita il «Decreto
  2/11/2001» invece del 2011;
- il cap. 1 e la nota del par. 4.2 parlano solo di farmaci, il resto del documento anche di
  specialistica.

Due cose ci sembrano più delicate:

- **L'esempio di User-Agent del par. 3.1** riporta un codice fiscale con il carattere di controllo
  corretto e un MAC address. Sembrano di una persona e di un computer veri. Se lo sono, forse
  conviene sostituirli con dati di prova. Nel nostro kit non li abbiamo copiati.
- **Il documento ISAD-FSE-SPT-02-2025** (FSE regionale, Patient Summary) porta la dicitura
  «circolazione limitata», ma si scarica senza login da `medicinrete.insiel.it/allegati/`. Noi
  l'abbiamo rispettata: non lo usiamo e non copriamo il canale FSE regionale. Se pensate di
  renderlo pubblico, o di autorizzarci, saremmo felici di estendere il modulo anche lì.

L'elenco completo, con i riferimenti, è in `docs/SAR_FVG.md`, sezione 7.

Siamo disponibili per un incontro, anche breve, e per adattare il modulo alle vostre
indicazioni. Se il collaudo va a buon fine, il modulo e i suoi casi di prova restano a
disposizione della Regione e di tutti gli sviluppatori.

Con i migliori saluti,

[nome e recapiti: DA COMPLETARE]

---
Licenza di questo documento: CC-BY-4.0.

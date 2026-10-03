# Varco non è un dispositivo medico

Stato al 02/10/2026, versione 0.1.0.

**Dichiarazione.** Varco, nel perimetro descritto qui sotto, non è un dispositivo
medico né un software dispositivo medico (MDSW) ai sensi del Regolamento (UE) 2017/745
(MDR), e non ha una destinazione d'uso medica. Non porta e non può portare la marcatura
CE come dispositivo medico. Non va usato per prendere decisioni cliniche.

Questa è la valutazione dell'autore, fatta sulla guida
[MDCG 2019-11](https://health.ec.europa.eu/system/files/2020-09/md_mdcg_2019_11_guidance_en_0.pdf)
(ottobre 2019, letta il 01/10/2026). Non è un parere di un organismo notificato né
dell'autorità competente. Chi integra il kit in un prodotto risponde della destinazione
d'uso **del suo** prodotto.

## Cosa fa

- **Trasmette** la ricetta dematerializzata al SAC del MEF: codifica in XML i dati che
  il medico ha già deciso (farmaco o prestazione, quantità, diagnosi, esenzione), cifra
  CF e pincode, manda la richiesta, legge la risposta. Visualizza e annulla.
- **Converte** gli stessi dati nel formato CDA2 del Profilo Sanitario Sintetico (FSE
  2.0): allergie, terapie, problemi, anamnesi familiare, esenzioni, così come il medico
  li ha scritti. Allega il CDA a un PDF e lo firma.
- **Controlla la forma**: campi obbligatori, valori ammessi dal tracciato, schema XSD,
  schematron e vocabolari ufficiali, coerenza tra credenziali e medico sostituto. Sono
  gli stessi controlli che fanno il SAC e il gateway FSE prima di accettare un
  documento.
- **Blocca** le chiamate verso la produzione.

## Cosa non fa

- Non controlla interazioni tra farmaci, allergie rispetto ai farmaci prescritti,
  controindicazioni, dosi massime o appropriatezza.
- Non suggerisce farmaci, dosaggi, diagnosi, esami o priorità.
- Non calcola punteggi, rischi o allarmi sul singolo paziente.
- Non interpreta immagini, segnali o risultati di laboratorio.
- Non sceglie al posto del medico: ogni codice clinico arriva dal programma che usa il
  kit e passa invariato.
- Non completa i dati clinici mancanti. Il modello del PSS non ha valori predefiniti
  per via di somministrazione, stato delle voci (attiva, conclusa, sospesa,
  interrotta) e tipo di allergia o intolleranza. Se il dato manca, il kit fa una di
  due cose:
  - lo scrive come **non noto**, quando la specifica lo ammette: tipo di allergia
    con valore non codificato (ERRORE-b80), date di inizio con `nullFlavor="UNK"`,
    anche l'inizio di «nessuna allergia nota» e «nessun problema noto», che prima
    prendeva la data del documento;
  - **rifiuta** di generare il documento e dice cosa manca, quando la specifica vuole
    un valore codificato senza alternativa: via di somministrazione (ERRORE-b112),
    stato, data di nascita (ERRORE-17). Il validatore ufficiale respinge sia
    `routeCode nullFlavor="UNK"` sia `birthTime nullFlavor="UNK"`.

  Fino al 01/10/2026 la via era «PO» (orale) e lo stato «attivo» per difetto, e il
  tipo di allergia era «ALG». La revisione esterna del 02/10/2026 lo ha segnalato e
  il codice è stato corretto. Restano valori predefiniti solo per dati non clinici:
  ruolo del medico «MMG», titolo «Dott.», Stato «100» (Italia) negli indirizzi,
  riservatezza «N». Chi li usa deve controllarli.
- Il PDF leggibile riporta tutto quello che c'è nel CDA allegato, campo per campo
  (note, stato, date di fine, via, tutti i codici). Un test lo verifica. Un carattere
  che il PDF minimale non sa scrivere fa fallire la generazione: non diventa un «?».

## Perché non è un MDSW (MDCG 2019-11)

Seguendo i passi decisionali della guida (sezione 3.3, figura 1):

1. **Passo 1** — è software: sì.
2. **Passo 2** — è un accessorio di un dispositivo o pilota un dispositivo: no.
3. **Passo 3** — compie sui dati un'azione diversa da archiviazione, archivio,
   comunicazione, ricerca semplice o compressione senza perdita? Per la ricetta **no**:
   è comunicazione («the flow of information from one point ... to another»). Per il PSS
   il kit cambia la rappresentazione dei dati, ma solo per **compatibilità** con il
   formato del FSE, non per uno scopo medico; la guida (sezione 3.1) dice che alterare la
   rappresentazione dei dati «for embellishment/cosmetic or compatibility purposes does
   not readily qualify the software as medical device software». I controlli di forma
   verificano che il documento rispetti lo standard, non dicono niente sul paziente.

Il percorso si ferma qui. Lo conferma l'allegato I della guida:

- i sistemi informativi «intended only to transfer, store, convert, format, archive
  data are not qualified as medical devices in themselves» (Annex I, c);
- i sistemi di comunicazione che trasferiscono «prescription, referrals, images,
  patient records» non rientrano nella definizione di dispositivo medico (Annex I, d);
- la cartella clinica elettronica che sostituisce quella di carta non è un dispositivo
  medico, ma può avere moduli che lo sono, per esempio «a medication module»
  (Annex I, c.1).

## Cosa lo farebbe diventare un dispositivo medico

Una funzione così cambierebbe la qualificazione del modulo che la contiene, e forse del
kit. Per questo **non va aggiunta** a questo repository senza una nuova valutazione
scritta, e chi la propone deve dirlo nella richiesta di modifica (vedi
`CONTRIBUTING.md`):

- un **modulo farmaci** che controlla interazioni, allergie rispetto al farmaco,
  controindicazioni o dosi massime, o che avvisa il medico su questi punti;
- un calcolo della **dose** per il singolo paziente (la guida cita i sistemi di
  pianificazione dei farmaci, per esempio la chemioterapia, come dispositivi medici);
- un **supporto alle decisioni**: raccomandazioni di diagnosi, prognosi, monitoraggio o
  terapia a partire dai dati del paziente (Annex I, b);
- **allarmi** o punteggi di rischio sul singolo paziente (Annex I, c.1.1);
- una scelta automatica di codici clinici (diagnosi, farmaco, priorità) a partire da
  testo libero o da altri dati, se il risultato orienta la decisione del medico;
- funzioni che usano i dati del PSS per dire qualcosa sul paziente invece di
  trasmetterli o formattarli.

Restano fuori, e quindi possibili senza cambiare qualificazione: nuovi canali di
trasmissione (SAR regionali, gateway FSE), nuovi formati documentali, nuovi controlli
di forma imposti dalle specifiche, archiviazione e ricerca semplice.

## Note

- La dichiarazione vale per il codice di questo repository. Un programma che usa il kit
  e ci aggiunge funzioni cliniche fa la sua valutazione.
- Se cambia la guida MDCG o il perimetro del kit, questa pagina va aggiornata **prima**
  del rilascio.

---
Licenza di questo documento: CC-BY-4.0.

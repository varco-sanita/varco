# Il dono: come regalare il kit alla sanità pubblica

Stato al 01/10/2026. Niente di quanto scritto qui è stato pubblicato o inviato.
Ogni affermazione presa da fuori ha la sua fonte. «Non trovato» vuol dire che l'ho
cercato e non l'ho trovato, non che non esiste.

## In breve

- **Cosa regaliamo**: il codice del kit (ricetta al SAC e Profilo Sanitario
  Sintetico FSE 2.0), la suite di conformità in JSON e le prove raccolte. Licenza
  EUPL-1.2. Non regaliamo un servizio, un server o assistenza.
- **Come**: pubblicandolo. Una PA può adottare software open source di terzi
  «senza necessità di stipulare alcuna convenzione» (Linee guida AgID su
  acquisizione e riuso, §3.9.1,
  <https://www.agid.gov.it/sites/agid/files/2024-05/lg-acquisizione-e-riuso-software-per-pa-docs_pubblicata.pdf>).
  Non serve un atto di donazione, e non serve una società.
- **Dove**: nel catalogo di Developers Italia, come «software open source di terzi»
  (art. 68 CAD). Diventa «software a riuso» (art. 69) solo se una PA lo prende in
  carico e lo pubblica lei
  (<https://developers.italia.it/it/riuso>, stesso PDF AgID).
- **Il vero ostacolo non è legale, è la fiducia**: nella valutazione comparativa la
  PA guarda se c'è un manutentore, quanto è vivo il progetto e quante altre PA lo
  usano (PDF AgID, fase 2.2). Oggi il kit ha un solo autore, nessun rilascio e
  nessun utente pubblico.

## 1. Cosa regaliamo

| Cosa | Dove sta | Nota |
|---|---|---|
| Libreria Python (ricetta, FSE, trasporto, cifratura) | `src/varco/` | ~4.000 righe, una sola dipendenza di esercizio (`cryptography`) |
| Suite di conformità | `conformita/` | 148 casi JSON con schema; rieseguibile in qualunque linguaggio |
| Esecutore Java dei casi FSE col validatore ufficiale | `strumenti/validatore-ufficiale/` | prova che il formato non dipende dal Python |
| Prove reali | `prove/` | XML scambiati con l'ambiente di **test** del MEF, esiti del validatore |
| Documentazione | `README.md`, `docs/ARCHITETTURA.md` | in italiano |
| Scheda per il catalogo | `publiccode.yml` | valida (`-no-network`); URL `https://github.com/varco-sanita/varco` |

Non regaliamo: un servizio ospitato (cambierebbe tutto, vedi la sezione sulla
sicurezza), consulenza o lavoro gratuito per una PA. Su quest'ultimo punto il
Codice dei contratti vieta le prestazioni d'opera intellettuale gratuite, salvo
casi eccezionali e motivati (art. 8 D.Lgs 36/2023,
<https://www.codiceappalti.it/DLGS_36_2023/Articolo_8__Principio_di_autonomia_contrattuale__Divieto_di_prestazioni_d%E2%80%99opera_intellettuale_a_titolo_gratuito_/12610>).
Il codice pubblicato è un'altra cosa: chiunque lo usa sotto licenza.

## 2. Il dono formale: serve un atto?

- **Per il codice pubblicato, no.** La licenza vale per chiunque, e le Linee guida
  dicono che si adotta senza convenzione (PDF AgID, §3.9.1).
- **Se una PA vuole un atto lo stesso** (per esempio per prendere il repository
  in carico), la base è l'art. 8 c. 3 D.Lgs 36/2023: le PA «possono ricevere per
  donazione beni o prestazioni rispondenti all'interesse pubblico senza obbligo di
  gara» (link sopra). Ogni azienda ha il suo regolamento; per esempio ASST Sette
  Laghi chiede il parere dei Servizi Informativi per hardware e software
  (<https://www.asst-settelaghi.it/documents/41522/1079823/Regolamento+per+l'accettazione+di+donazioni+(1).pdf/084d7bd1-ac20-2eb0-1ae8-7c40b4e4407d>).
- **Conflitto di interessi.** ANAC (parere del 3/3/2025) chiede che manchi un
  interesse economico del donante, «anche indiretto», e che la PA pubblichi le
  liberalità ricevute
  (<https://www.anticorruzione.it/documents/91439/280092172/Parere+anticorruzione+del+3+marzo+2025+-+fasc.790.2025.pdf/2fce8382-44c5-dcfa-c28d-2ebeda1bef89?t=1743076166610>).
  Per Lorenzo vuol dire: **non vendere servizi sul kit alla stessa PA** e dirlo per
  iscritto fin dall'inizio. Pareri ANAC specifici su software donato che crea
  dipendenza: non trovato.
- **Dipendenza (lock-in).** Il rischio per la PA è restare con un software che
  nessuno mantiene. La licenza aperta e la suite di conformità lo riducono (chiunque
  può riprenderlo e collaudarlo), ma non lo eliminano: vedi governance.
- **Precedente con atto formale**: Immuni, licenza d'uso concessa gratis, perpetua e
  irrevocabile da Bending Spoons alla Presidenza del Consiglio
  (<https://presidenza.governo.it/AmministrazioneTrasparente/BandiContratti/AccordiTraAmministrazioni/allegati/all3_Convenzione_gestione_app_Immuni.pdf>).
  Un privato singolo che abbia donato software sanitario a una PA con atto formale:
  non trovato.

## 3. Cosa serve a una PA per adottarlo davvero

- **Valutazione comparativa (art. 68 CAD).** Prima il riuso tra PA, poi l'open
  source di terzi, poi il resto. Criteri: conformità dichiarata (interoperabilità,
  dati personali, misure minime, accessibilità), presenza di un manutentore,
  vitalità del progetto (rilasci, comunità, sviluppatori), altre PA che lo usano,
  costo totale (PDF AgID, fase 2.2).
- **Sicurezza.** Misure minime ICT AgID (Circolare 2/2017), obbligatorie al livello
  minimo per tutte le PA
  (<https://www.csigbologna.it/referenze/legislazione/circolare-agid-2-2017-misure-minime-sicurezza-pa/>);
  Linee guida AgID per lo sviluppo del software sicuro
  (<https://www.agid.gov.it/it/sicurezza/cert-pa/linee-guida-sviluppo-del-software-sicuro>).
  NIS2: le ASL sono soggetti obbligati, ma l'obbligo è loro, non dell'autore
  (<https://lentepubblica.it/pa-digitale/categorizzazioni-della-direttiva-nis-2026-chi-rientra-tra-soggetti-essenziali-e-importanti/>).
  La qualificazione cloud ACN serve solo se il kit viene offerto come servizio
  ospitato
  (<https://www.privacystudio.it/qualificazione-cloud-pa-e-regolamento-acn-servizi-supporto-adeguamento/>):
  come libreria non serve.
- **GDPR.** Chi è titolare o responsabile dipende da chi tratta davvero i dati
  (EDPB 07/2020,
  <https://www.edpb.europa.eu/our-work-tools/our-documents/guidelines/guidelines-072020-concepts-controller-and-processor-gdpr_en>).
  Lorenzo, che pubblica codice e non tratta dati, non è né l'uno né l'altro. La DPIA
  la fa chi usa il kit (elenco del Garante:
  <https://www.garanteprivacy.it/documents/10160/0/ALLEGATO+1+Elenco+delle+tipologie+di+trattamenti+soggetti+al+meccanismo+di+coerenza+da+sottoporre+a+valutazione+di+impatto>);
  noi possiamo aiutarlo con una scheda su quali dati passano e dove.
- **Dispositivo medico.** Per la guida MDCG 2019-11 il software che solo trasferisce,
  archivia o formatta dati non è un dispositivo medico, ma un «medication module»
  potrebbe esserlo
  (<https://health.ec.europa.eu/system/files/2020-09/md_mdcg_2019_11_guidance_en_0.pdf>).
  Il kit non ha funzioni cliniche: ora è scritto in chiaro in
  `docs/NON_DISPOSITIVO_MEDICO.md` (e nel README), e va mantenuto così.
- **Accessibilità.** Il riferimento per il software è il cap. 11 della EN 301549
  (<https://www.agid.gov.it/it/design-servizi/accessibilita/linee-guida-accessibilita-pa>).
  Che una libreria senza interfaccia ne sia esclusa: non trovato.
- **FSE: accreditamento.** I software vanno accreditati presso il Ministero, una
  volta, «dal fornitore dell'applicativo» per ogni tipo di documento
  (<https://fascicolosanitario.regione.campania.it/sites/fascicolosanitario.regione.campania.it/files/allegati/user4/Accreditamento%20Gateway_FSE%202.0_11092023_v01.pdf>).
  Il repository di accreditamento è in dismissione dal 14/07/2026
  (<https://github.com/ministero-salute/it-fse-accreditamento/blob/main/README.md>).
  Che serva una società o una partita IVA: non trovato. In pratica il kit arriva in
  produzione sul FSE dentro un gestionale accreditato, non da solo.
- **Ricetta: SAR regionali.** Diverse Regioni hanno un proprio sistema di
  accoglienza che riceve le ricette prima del SAC, con specifiche loro: Toscana
  (<https://compliance.toscana.it/portale/it/scenari/scenario-eprescription-ricetta-dematerializzata-v3-2024/>),
  Piemonte, Lazio, Emilia-Romagna, Lombardia, Friuli Venezia Giulia, Sardegna. Il kit
  oggi parla **solo** col SAC. Elenco ufficiale completo delle Regioni con SAR: non
  trovato.

## 4. Checklist di adottabilità (verificata sul codice il 01/10/2026, aggiornata dopo la ripulitura)

Legenda: **fatto** · **parziale** · **manca**

| # | Requisito | Stato | Cosa ho verificato |
|---|---|---|---|
| 1 | Licenza aperta | **fatto** | `LICENSE` EUPL-1.2; `SPDX-License-Identifier` in tutti i file `.py` di `src/` (controllato da `tests/unit/test_licenze.py`); documentazione CC-BY-4.0 dichiarata in README e `NOTICE`, testo in `LICENSES/CC-BY-4.0.txt` |
| 2 | `publiccode.yml` valido | **parziale** | rivalidato il 01/10 con `publiccode-parser` v5.4.3 `-no-network`: 0 errori (gruppo di controllo: un `softwareType` sbagliato viene bocciato); la CI lo valida a ogni modifica. Il 03/10/2026: `url` e `landingURL` su `https://github.com/varco-sanita/varco`, `releaseDate` 2026-10-03, nessuna email; rivalidato `-no-network`: 0 errori. Con la rete l'unico errore è l'URL non ancora raggiungibile (repository non pubblicato) |
| 3 | Repository pubblico | **manca** | nessun repository, nessun controllo di versione (per scelta: niente pubblicazione finché la checklist non è chiusa). `.gitignore` pronto: un checkout pulito pesa 3,7 MB |
| 4 | Rilascio numerato e note di rilascio | **parziale** | `CHANGELOG.md` con la 0.1.0 del 03/10/2026, primo rilascio col nome Varco; versione `0.1.0` in `pyproject.toml` e `publiccode.yml`; nessun tag |
| 5 | Documentazione | **parziale** | `README.md`, `docs/ARCHITETTURA.md`, `docs/MINACCE.md`, `docs/NON_DISPOSITIVO_MEDICO.md`, `docs/TERZE_PARTI.md`, in italiano, con licenza CC-BY-4.0; niente in inglese |
| 6 | Test automatici | **fatto** | `pytest`: **261 superati, 9 saltati** (erano 218/9; +32 registratore, +7 scarico specifiche, +4 licenze) su Python 3.11, 3.12 e 3.14 (macOS). Checkout pulito senza `specifiche/`: 256 superati, 14 saltati (i 5 in più usano kit MEF o repository AGPL). Suite `offline` 21/21; suite `fse` 8 superati, 3 saltati senza Java |
| 7 | Integrazione continua (test a ogni modifica) | **parziale** | `.github/workflows/ci.yml`: Linux, macOS, Windows × Python 3.11, 3.12; suite senza rete; `publiccode.yml`; `pip-audit`; nessuna chiamata al MEF (`VARCO_INTEGRAZIONE=0` controllato); azioni fissate per commit, permessi in sola lettura. Verificato con `actionlint` 1.7.7 (0 errori, senza shellcheck); **mai eseguito su GitHub**: non c'è il repository |
| 8 | Secondo manutentore / comunità | **manca** | un solo autore. `CONTRIBUTING.md` c'è (niente dati reali, conformità, test); nessun codice di condotta |
| 9 | Canale per segnalare vulnerabilità | **parziale** | `SECURITY.md`: perimetro, cosa non fare sull'utenza di test, tempi obiettivo. Canale unico: segnalazione privata di GitHub su `https://github.com/varco-sanita/varco/security/advisories/new`, nessuna email (03/10/2026). Va attivata nelle impostazioni del repository quando sarà pubblicato |
| 10 | Materiale di terzi e licenze | **parziale** | `specifiche/` (130 MB) esclusa dal repository; `strumenti/scarica_specifiche.py` riscarica le 21 voci dalle fonti ufficiali con sha256 o commit fissati (provato da zero il 01/10: tutte verificate; il kit scaricato è identico alla copia locale). `NOTICE` e `docs/TERZE_PARTI.md` con fonte, licenza e motivo di ogni pezzo. Codice AGPL usato solo come processo esterno, controllato da test. Gli XSD HL7 e lo schematron del PSS sono usciti dal repository il 03/10/2026 e si scaricano (gruppo `cda-xsd`). XSD MEF e certificato restano, sulla base dell'art. 52 c. 2 CAD (verificato il 01/10) |
| 11 | Sicurezza del codice | **parziale** | guardia anti-produzione con test; HTTPS con verifica; credenziali mascherate. Nuovi: modello delle minacce (`docs/MINACCE.md`); `pip-audit` 2.10.1 su 3.11, 3.12, 3.14: nessuna vulnerabilità nota (controllo: `cryptography==41.0.0` dà 20 vulnerabilità); minimo alzato a `cryptography>=49`. Manca la verifica rispetto alle misure minime AgID |
| 12 | Dati personali | **parziale** | il kit non salva dati di suo. Il registratore ora **redige per default** (CF anche omocodici, pincode, NRE, codice di autenticazione, nomi, diagnosi, esenzioni, PDF e base64); in chiaro solo con identità di test verificate chiamata per chiamata, o col flag `registra_dati_personali_in_chiaro=True`; il cancello del chiaro rifiuta anche nomi/tessere senza CF e CF cifrati non confermati dal promemoria (falle trovate da una revisione esterna e chiuse). 31 test, compresi i file veri di `prove/` e una chiamata HTTP vera in locale; togliere la redazione ne fa fallire 11, la versione prima della revisione ne fallisce 9. Limiti residui scritti in `docs/MINACCE.md`. Manca una scheda privacy per chi integra (DPIA) |
| 13 | Nessuna funzione clinica (MDR) | **fatto** | dichiarazione esplicita «non è un dispositivo medico» in README e `docs/NON_DISPOSITIVO_MEDICO.md`: cosa fa, cosa non fa, passi della guida MDCG 2019-11 (letta sul PDF ufficiale), funzioni che cambierebbero la qualificazione; `CONTRIBUTING.md` chiede di dichiararle |
| 14 | Accessibilità | **non verificato** | libreria senza interfaccia; il PDF del PSS non è PDF/A-3 né verificato per l'accessibilità |
| 15 | Pronto per la produzione — ricetta | **manca** | produzione bloccata apposta; flusso a due fattori di produzione non implementato; nessun canale SAR regionale; niente lotti NRE |
| 16 | Pronto per la produzione — FSE | **manca** | nessun canale verso il gateway (servono i certificati Sogei); firma provata solo con certificato autofirmato; solo il PSS |
| 17 | Piattaforme | **parziale** | provato in locale solo su macOS (Python 3.11, 3.12, 3.14); Linux e Windows sono nella CI, non ancora eseguita |
| 18 | Una PA che lo usa | **manca** | nessuna |

## 5. I passi, in ordine

1. **Ripulire per la pubblicazione** (punti 9, 10, 12 della checklist). *Fatto il
   01/10/2026*: `specifiche/` esclusa e riscaricabile con verifica, `NOTICE` e
   `docs/TERZE_PARTI.md`, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`,
   `docs/MINACCE.md`, `docs/NON_DISPOSITIVO_MEDICO.md`, registratore redatto per
   default, CI pronta. Il 03/10/2026: schemi HL7 fuori dal repository, nome Varco, segnalazioni solo
   via GitHub (nessuna email).
2. **Pubblicare il repository** su una piattaforma pubblica, con un primo rilascio
   (`v0.1.0`) e le note, su `https://github.com/varco-sanita/varco` (`url` e `releaseDate` di
   `publiccode.yml` già scritti). Poi rivalidarlo **con la rete** e togliere `-no-network` dalla CI.
3. **Archiviarlo su Software Heritage** con «Save Code Now»: gratis, e resta anche
   se il repository sparisce
   (<https://www.softwareheritage.org/2019/01/10/save_code_now/>).
4. **Chiedere l'inserimento nel catalogo Developers Italia**: issue sul repository
   del Catalogo con il modulo per il software di terzi
   (<https://github.com/italia/catalogo-software/issues/new?&template=add_new_software.yaml>;
   guida: <https://developers.italia.it/it/guide/pubblicare-software-per-la-pa>).
   Le richieste di terzi aperte tra luglio e settembre 2026 sono ancora in attesa
   (<https://github.com/italia/catalogo-software/issues>): ci vorrà tempo.
5. **Presentarlo alla comunità tecnica del FSE** (vedi sotto, primo contatto).
6. **Mandare la mail a Sogei** già scritta (`docs/bozza_mail_sogei.md`) per i
   certificati di test del gateway, così si chiude il punto 16.
7. **Cercare la prima PA** e un secondo manutentore: sono i due punti che pesano
   di più nella valutazione comparativa.

## 6. Chi contattare per primo, e perché

1. **Il repository di supporto del FSE del Ministero della Salute e il canale Slack
   #fse di Developers Italia**
   (<https://github.com/ministero-salute/it-fse-support>, inviti Slack:
   <https://slack.developers.italia.it/>). È dove parlano i tecnici del FSE, è
   pubblico e non serve presentarsi a nessun ufficio. La stessa pagina linka già un
   client del gateway scritto da un singolo sviluppatore (`zukka77/gtwclient`):
   il precedente più vicino al kit. La suite di conformità e le scoperte sul
   validatore (es. `DALLERGY`, sistemi di codifica non censiti) sono contributi
   utili a loro, non una richiesta.
2. **Sogei, fse_support@sogei.it** (indirizzo dalla pagina sopra): senza i
   certificati il kit non può parlare col gateway. La bozza c'è già.
3. **Una Regione che pubblica già software FSE aperto**: Regione Piemonte / CSI
   Piemonte ha nel catalogo `webappmed-fse` e `farab-fse`
   (<https://github.com/regione-piemonte/webappmed-fse>). È chi capisce subito cosa
   è il kit e sa come si pubblica software sanitario. Attenzione: il Piemonte ha un
   SAR suo (SIRPED,
   <https://servizi.regione.piemonte.it/catalogo/sistema-informativo-regionale-prescrizione-elettronica-dematerializzata-sirped>),
   quindi per la ricetta servirebbe un canale in più.
4. **Toscana, per vicinanza**: il SAR è regionale e i gestionali si accreditano
   tramite CART (cartdesk@regione.toscana.it, copia grupposis@regione.toscana.it,
   dalla pagina compliance sopra). L'informatica delle aziende sanitarie la gestisce
   ESTAR (<https://www.estar.toscana.it/ns-estar/tecnologie-informatiche-e-sanitarie?id=232>).
   Chi sia titolare del FSE e del SAR toscano: non trovato in modo esplicito.
5. **Più avanti, AGENAS** (Agenzia nazionale per la sanità digitale, adotta le linee
   guida tecniche del FSE:
   <https://www.agenas.gov.it/agenzia-per-la-sanit%C3%A0-digitale/l-agenzia-asd>) e il
   **Dipartimento per la Trasformazione Digitale** (attuatore della misura PNRR sul
   FSE:
   <https://www.salute.gov.it/portale/pnrrsalute/dettaglioContenutiPNRRSalute.jsp?lingua=italiano&id=5879&area=PNRR-Salute&menu=investimenti>).
   Ha senso andarci quando c'è almeno una PA che lo usa: l'idea che lo Stato adotti la
   suite di conformità come collaudo ufficiale passa da loro.

Medici: lo SNAMI ha scritto che il Patient Summary «non è sostenibile» per i medici
di famiglia senza automazione
(<https://www.doctor33.it/articolo/67296/fascicolo-sanitario-elettronico-snami-patient-summary-non-sostenibile-per-i-medici-di-famiglia>):
è l'argomento più vicino al kit. Tavoli formali dei sindacati sui gestionali: non
trovato. Forum PA come canale per proporre soluzioni: non trovato.

## 7. Governance di lungo periodo

Il problema da risolvere: se Lorenzo sparisce, chi tiene vivo il kit?

- **Fondazione o associazione riconosciuta propria**: sconsigliata all'inizio. Per la
  personalità giuridica di un ente del Terzo settore servono almeno 15.000 €
  (associazione) o 30.000 € (fondazione)
  (<https://www.brocardi.it/codice-terzo-settore/titolo-iv/capo-ii/art22.html>).
- **Ospitalità presso un ente che esiste già**: la via meno costosa. Esempio italiano:
  OpenStreetMap Italia vive dentro Wikimedia Italia
  (<https://www.wikimedia.it/news/wikimedia-italia-e-diventata-ufficialmente-il-secondo-chapter-locale-di-openstreetmap-foundation-e-il-primo-a-unire-wikipedia-e-osm/>).
  Un ente adatto al kit in sanità: da cercare, non trovato.
- **Linux Foundation Public Health**: il sito accetta ancora progetti, ma le ultime
  notizie sono del 2022 (<https://www.lfph.io/join/host-your-project/>). Se sia
  attiva oggi: non trovato.
- **Presa in carico da una PA**: la soluzione più solida per la continuità. Il kit
  diventerebbe «software a riuso» di quella PA (art. 69 CAD), con un manutentore
  pubblico. È l'obiettivo, non il primo passo.
- **Finanziamenti senza società**: NGI Zero Commons Fund di NLnet accettava persone
  fisiche, ma l'ultimo bando si è chiuso il 1° giugno 2026
  (<https://nlnet.nl/commonsfund/>).

Cosa conviene a un privato senza società, in ordine: pubblicare e archiviare
(costo zero), trovare un secondo manutentore, poi una PA che lo prenda in carico o un
ente che lo ospiti. La fondazione propria solo se un giorno ci sono soldi e persone.

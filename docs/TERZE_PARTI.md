# Materiale di terzi

Stato al 03/10/2026: gli schemi HL7 (XSD del CDA e schematron del PSS) sono usciti dal repository e si scaricano (sezione 2, gruppo `cda-xsd`). Questo elenco dice, per ogni pezzo che non abbiamo scritto noi,
da dove viene, con che licenza, e se il repository pubblico lo **redistribuisce**
oppure lo **scarica dalla fonte**. Lo stesso elenco, in breve, sta in [`NOTICE`](../NOTICE).

Regola: si redistribuisce solo ciò che serve alla libreria per funzionare e che si può
ridare con le sue note di copyright. Tutto il resto lo scarica
`strumenti/scarica_specifiche.py` dalle fonti ufficiali, alla versione esatta usata per
scrivere e collaudare il kit, e lo verifica (sha256 per i file, commit per i repository).
La cartella `specifiche/` è esclusa dal repository (`.gitignore`).

```sh
python strumenti/scarica_specifiche.py              # gruppi mef, fse-validatore e cda-xsd
python strumenti/scarica_specifiche.py --gruppi sist   # specifiche SIST della Regione Puglia
python strumenti/scarica_specifiche.py --gruppi fvg    # specifiche SAR della Regione FVG (Insiel)
python strumenti/scarica_specifiche.py --gruppi piemonte   # specifiche SIRPED (Regione Piemonte, CSI) e kit A2F del MEF
python strumenti/scarica_specifiche.py --gruppi umbria     # OpenAPI e wiki del SAR umbro (PuntoZero), senza i certificati
python strumenti/scarica_specifiche.py --gruppi cda-xsd    # XSD HL7 del CDA R2 e schematron PSS (validazione locale, test)
python strumenti/scarica_specifiche.py --tutto      # anche il materiale di riferimento FSE, il SIST, il SAR FVG e SIRPED
python strumenti/scarica_specifiche.py --solo-verifica --tutto   # senza rete: controlla le copie
```

Esito del 01/10/2026: tutte le 21 voci scaricate da zero e verificate (più la voce `sist`, aggiunta e verificata lo stesso giorno); le copie locali
coincidono con il manifesto; un byte aggiunto a un file o a un repository viene
segnalato (gruppo di controllo). Le due voci `fvg` sono state aggiunte, scaricate da zero e
verificate lo stesso giorno. Così anche le 11 voci `piemonte`: scaricate da zero in una cartella
vuota, tutte verificate; un file alterato viene segnalato. Le 7 voci `umbria` (03/10/2026): scaricate da zero e verificate.

## 1. Redistribuito nel repository

| Cosa | Dove | Fonte | Licenza | Perché sta qui |
|---|---|---|---|---|
| 10 XSD del tracciato SAC (`InvioPrescritto*`, `VisualizzaPrescritto*`, `AnnullaPrescritto*`, `InterrogaNreUtil*`, `TipiDati*`) | `src/varco/schemi/` | kit di sviluppo prescrittore del MEF, cartella `wsdl/` (identici byte per byte) | nessuna licenza dichiarata dal MEF | la libreria valida le richieste contro lo schema ufficiale |
| Certificato pubblico SanitelCF 2024-2027 | `src/varco/certificati/SanitelCF-2024-2027.pem` | kit MEF, `SanitelCF-2024-2027.txt` (stessa chiave) | nessuna licenza dichiarata; è un certificato pubblico pensato per essere distribuito ai gestionali | senza, il kit non può cifrare CF e pincode |
| Skeleton ISO Schematron XSLT2 (2010-04-14) | `src/varco/fse/iso_schematron/` | risorse di `com.helger:ph-schematron` 5.6.5 (la libreria che usa il gateway), cartella `schematron/20100414-xslt2/` (identici) | © 2000-2010 Rick Jelliffe e Academia Sinica Computing Center: licenza permissiva in stile zlib, scritta in testa ai file | compilare lo schematron con Saxon |
| Risposte **sintetiche** del SIST (Puglia) | `conformita/risposte/sist/` | scritte da noi sullo schema `CVPService.xsd` (`strumenti/genera_risposte_sist.py`): nessuna risposta reale del SIST | EUPL-1.2 | la suite le usa come casi `sist`; lo dice `LEGGIMI.md` nella cartella |
| Risposte **sintetiche** del SAR FVG | `conformita/risposte/fvg/` | scritte da noi sugli XSD di `wsdl_prescritto.zip` (`strumenti/fvg_server_finto.py`): nessuna risposta reale del SAR FVG | EUPL-1.2 | la suite le usa come casi `fvg`; lo dice `LEGGIMI.md` nella cartella |
| Risposte **sintetiche** di SIRPED (Piemonte) e JWT/JWKS di prova | `conformita/risposte/piemonte/` | scritte da noi sugli XSD del kit A2F (`strumenti/piemonte_server_finto.py`); JWT firmato con una chiave generata al momento e non conservata: nessuna risposta reale di SIRPED | EUPL-1.2 | la suite le usa come casi `piemonte`; lo dice `LEGGIMI.md` nella cartella |
| Risposte reali dell'ambiente di **test** del SAC | `conformita/risposte/`, `prove/` | `demservicetest.sanita.finanze.it`, 30/09/2026, con le sole identità di test del kit | output del servizio del MEF, riprodotto come documentazione tecnica | la suite di conformità le usa come casi `offline` |
| Testo della licenza CC-BY-4.0 | `LICENSES/CC-BY-4.0.txt` | <https://creativecommons.org/licenses/by/4.0/legalcode.txt> | il testo stesso | licenza dei documenti |

Nei test e nelle prove compaiono le **identità pubbliche di test** del kit MEF (CF
`PROVAX00X00X000Y`, `PROVAX00X00X000Z`, `PNIMRA70A01H501P`). Nessuna password del kit sta
nel repository: si leggono dal kit scaricato (`varco/kit_mef.py`).

### Da verificare prima di pubblicare

- **Materiale pubblico senza licenza (XSD del MEF, certificato).** La base su cui contiamo è l'art. 52, comma 2, del CAD (D.Lgs 82/2005):
  dati e documenti pubblicati da una PA senza una licenza espressa si intendono
  rilasciati come dati di tipo aperto, salvo che si tratti di dati personali.
  **Verificato il 01/10/2026** sul testo riportato da
  [Brocardi](https://www.brocardi.it/codice-dell-amministrazione-digitale/capo-v/sezione-i/art52.html):
  vale per i soggetti dell'art. 2 c. 2 (MEF e Ministero della Salute lo sono) e questi
  file non contengono dati personali. Prudenza residua: se un titolare chiede la
  rimozione, i file passano in `strumenti/fonti_specifiche.json` e si scaricano come
  gli altri, come è stato fatto per gli schemi HL7 (punto sotto).
- **XSD HL7: verificato il 02/10/2026, la redistribuzione NON è chiaramente permessa.
  Decisione: escono dal repository e si scaricano dalla fonte (gruppo `cda-xsd`). Fatto il
  03/10/2026 (ultimo punto di questo elenco).**
  - Licenza nell'intestazione: solo 4 file su 11. `datatypes.xsd`, `datatypes-base.xsd`,
    `datatypes-rX-cs.xsd`: licenza in stile BSD a 4 clausole («Redistribution and use in
    source and binary forms, with or without modification, are permitted…», con la clausola
    di riconoscimento «This product includes software developed by Health Level Seven»).
    `POCD_MT000040UV02.xsd`: «Copyright (c) 2017 Health Level Seven International… The
    Eclipse Public License shall serve as the primary license». Questi si potrebbero ridare.
  - Senza intestazione: `CDA.xsd`, `NarrativeBlock.xsd`, `infrastructureRoot.xsd`,
    `voc.xsd` (HL7) e le estensioni italiane `sdtcExtension.xsd`, `pharmExtension.xsd`,
    `labExtension_1.2_gen.xsd`. Anche nel repository di HL7
    ([`HL7/CDA-core-2.0`](https://github.com/HL7/CDA-core-2.0), cartella
    `schema/extensions/SDTC/processable/coreschemas/`) `NarrativeBlock.xsd`,
    `infrastructureRoot.xsd` e `voc.xsd` non hanno intestazione, e il repository non ha un
    file di licenza (l'API di GitHub dà `license: null`).
  - Per tutto ciò che non ha una licenza propria vale la
    [HL7 IP Policy](https://www.hl7.org/legal/ippolicy.cfm) (letta dalla
    [copia Wayback del 01/02/2025](https://web.archive.org/web/20250201102322/https://www.hl7.org/legal/ippolicy.cfm),
    «Updated 04/19/2024»; il sito risponde 202 ai client senza browser). Il «Material» comprende
    gli standard «in any format (e.g., Word, PDF, HTML, XML, zip…)»; «Any use, copying or
    distribution … not specifically authorized below is strictly prohibited». I NON-MEMBERS
    possono usare il materiale per implementarlo e vendere prodotti «that implement, but do not
    directly incorporate, the Specified Material»; incorporarlo richiede l'iscrizione come
    ORGANIZATIONAL MEMBER; la redistribuzione è una «SPECIAL PERMISSION» con accordo scritto.
  - Le [FAQ sulla IP gratuita](https://www.hl7.org/about/faqs/freeip.cfm)
    ([copia Wayback del 23/04/2024](https://web.archive.org/web/20240423165840/https://www.hl7.org/about/faqs/freeip.cfm)):
    gratuita dal 1° aprile 2013, ma «Will HL7 standards be in the public domain? No»; i non
    membri possono distribuire «translations of HL7 material into executable code … that
    implements the material and is not regarded as a reproduction». Uno XSD copiato byte per
    byte è una riproduzione, non una traduzione in codice.
  - Estensioni italiane e schematron: HL7 Italia pubblica la guida del PSS con «Copyright ©
    2024 by HL7 Italia. All Rights Reserved» e senza licenza
    ([pagina del PSS](https://www.hl7.it/realm-italiano/profilo-sanitario-sintetico-pss/)).
    Il repository del Ministero che li contiene,
    [`it-fse-catalogs`](https://github.com/ministero-salute/it-fse-catalogs), non dichiara una
    licenza (GitHub: `NOASSERTION`). L'art. 52 del CAD copre il lavoro del Ministero, non il
    copyright di HL7 sugli schemi da cui derivano.
  - Nella pratica gli schemi CDA sono ridistribuiti in molti progetti aperti (per esempio
    [`oehf/ipf`](https://github.com/oehf/ipf) Apache-2.0,
    [`gematik/api-ePA`](https://github.com/gematik/api-ePA) Apache-2.0,
    [`IHE/ITI-Info`](https://github.com/IHE/ITI-Info) CC-BY-4.0,
    [`metriport/metriport`](https://github.com/metriport/metriport)), e HL7 li pubblica in un
    repository GitHub aperto. È tolleranza, non una licenza: non basta per toglierli da qui.
  - Fatto il 03/10/2026: `varco.fse.validazione` legge gli XSD dalla cartella di
    `$VARCO_CDA_XSD` e lo schematron da quella di `$VARCO_SCHEMATRON`; senza variabili, dalla
    cartella in cui li mette lo script (`fse/cda-xsd/` e `fse/schematron/`, il cui nome la
    libreria legge dalla costante `CARTELLA_PREDEFINITA` di `strumenti/scarica_specifiche.py`).
    Se mancano, l'errore `SchemiNonTrovati` dice quali file e come scaricarli. `cda-xsd` è nei
    gruppi di default dello script. Gli 11 XSD e lo schematron sono stati cancellati da `src/`,
    e le righe `risorse/*.sch` e `risorse/xsd/*.xsd` da `pyproject.toml`: **non sono più nel
    repository né nel pacchetto**. Nei test, `tests/conftest.py` collega la cartella scaricata e
    fa risultare SALTATO ogni test che li cerca senza trovarli (mai verde, mai rosso);
    `tests/unit/test_schemi_hl7.py` controlla che nessuna copia torni nel repository.
- **Schematron del PSS (`schematron_PSS_v4.0.sch`): uscito con gli XSD il 03/10/2026.** Traduce
  la guida PSS di HL7 Italia, «All Rights Reserved» e senza licenza; l'art. 52 del CAD copre il
  lavoro del Ministero, non il copyright di HL7 Italia. Si scarica dallo stesso repository e
  commit (`it-fse-catalogs` @ `141be7f0`, `schematron/`), con sha256 fissato nel manifesto.

## 2. Scaricato dalla fonte, non redistribuito

Manifesto con URL, hash e commit: [`strumenti/fonti_specifiche.json`](../strumenti/fonti_specifiche.json).

| Gruppo | Cosa | Fonte ufficiale | Licenza | Perché non lo redistribuiamo |
|---|---|---|---|---|
| `mef` | *Specifiche tecniche ricetta dematerializzata – prescrizione* (08/07/2026), *Specifiche per la stampa del promemoria* (05/12/2024) | portale Sistema TS, pagina «Documenti e specifiche tecniche – prescrittore» | nessuna dichiarata | non servono al codice; la versione aggiornata sta sempre sul portale |
| `mef` | *Kit per lo sviluppo – Ricetta elettronica – Prescrittore* (zip, aggiornato al 28/04/2026), scompattato in `specifiche/kit/` | stesso portale | nessuna dichiarata | contiene le **utenze e i pincode di test** condivisi tra gli sviluppatori: chi li usa li prende dal MEF |
| `fse-validatore` | `it-fse-gtw-validator` @ `fdf3854b` | <https://github.com/ministero-salute/it-fse-gtw-validator> | **AGPL-3.0** | codice del gateway: serve solo al banco di test esterno (sezione 4) |
| `fse-validatore` | `it-fse-gtw-dispatcher` @ `9cc8aa79` | <https://github.com/ministero-salute/it-fse-gtw-dispatcher> | **AGPL-3.0** | come sopra |
| `fse-validatore` | dump dei dizionari del gateway (`mongo-dump/*`) | `it-fse-catalogs` @ `141be7f0` | nessuna licenza del repository; i vocabolari contengono LOINC e UCUM, con le loro licenze (`LOINC_short_license.txt`, `UCUM_short_license.txt` nel repository) | dati di terzi con licenze proprie |
| `fse-riferimento` | `it-fse-catalogs` (solo `container`, `schema`, `schematron`, `transform`) @ `141be7f0` | <https://github.com/ministero-salute/it-fse-catalogs> | come sopra | riferimento |
| `fse-riferimento` | `it-fse-support` @ `e4fb8890` | <https://github.com/ministero-salute/it-fse-support> | nessuna dichiarata | documentazione ed esempi; un esempio di PSS serve ai test del validatore ufficiale |
| `fse-riferimento` | `it-fse-gtw-tools` @ `6967f3ab` | <https://github.com/ministero-salute/it-fse-gtw-tools> | BSD-3-Clause | riferimento |
| `fse-riferimento` | `it-fse-gtw-test-container` @ `d9e763a3` | <https://github.com/ministero-salute/it-fse-gtw-test-container> | nessuna dichiarata | riferimento |
| `sist` | *Specifiche di integrazione SIST* (zip, pubblicato come 4.03.27 del 16/09/2026; il file si chiama `specifiche SIST 4.02.27.zip`), scompattato in `specifiche/sist/`: documento, WSDL e XSD, javadoc, CDA2 di prescrizione con esempi, `Allegati Tecnici.zip` | <https://sist.sanita.puglia.it/en/specifiche-integrazione> (InnovaPuglia, Regione Puglia) | nessuna dichiarata | servono solo ai test (schema `CVPService.xsd`, esempi). Alcuni esempi riportano nomi e CF **che sembrano di persone vere**; gli `Allegati Tecnici` contengono la «Nota Tecnica IUP.doc» con un **avviso di copyright restrittivo** e keystore JKS: niente di questo entra nel repository |
| `fvg` | *Specifiche di interfaccia applicativa del servizio SAR, Prescrizione ricetta dematerializzata*, Idof-dem-AT-01 dell'11/02/2026 (PDF, «Documento a libera circolazione») e `wsdl_prescritto.zip` (WSDL e XSD dei servizi in collaudo), scompattato in `specifiche/fvg/wsdl/` | <https://medicinrete.insiel.it/allegati/> (Insiel, Regione Friuli-Venezia Giulia) | «© Tutti i diritti riservati. Proprietà INSIEL SpA» sul PDF; nessuna licenza sugli XSD | servono solo ai test (XSD) e alla documentazione. Insiel è una società della Regione: che valga l'art. 52 del CAD come per MEF e Ministero non l'abbiamo verificato, e il PDF riserva i diritti, quindi non redistribuiamo nemmeno gli XSD. L'esempio di User-Agent del par. 3.1 riporta un CF e un MAC address che sembrano veri: non li copiamo |
| `piemonte` | Documenti della scheda SIRPED del catalogo dei servizi regionali: REL-STC-01 V04 (02/03/2026) e `api-docs_idsessione.yaml`; processo, modulo xlsx, piano dei test SIRPED-TES-01 V02 e due attestati dell'autocertificazione 2026; RE-SRS-SAR V05 (2018); RE-TES-01 V02 (2016). Tutti «Uso: Esterno» | <https://servizi.regione.piemonte.it/catalogo/sistema-informativo-regionale-prescrizione-elettronica-dematerializzata-sirped> (Regione Piemonte, CSI Piemonte) | nessuna dichiarata | servono solo alla documentazione e ai test. Nessuno porta «circolazione limitata». Non li redistribuiamo per la stessa prudenza usata col FVG |
| `piemonte` | Allegato 1 dell'avviso AP26_003 del CSI Piemonte (flusso SIAP) | `www.csipiemonte.it`, percorso `indagini_di_mercato/2026/AP26_003/` | nessuna dichiarata | solo documentazione (`docs/SAR_PIEMONTE.md`, sez. 12). La pagina principale dell'avviso non è stata trovata (`docs/BLOCCHI.md`) |
| `piemonte` | *Kit per lo sviluppo A2F Sistema TS*, ver. 20250902 (zip), scompattato in `specifiche/piemonte/a2f/`: WSDL e XSD di CreateAuth/CheckToken/RevokeAuth, manuale | portale Sistema TS (MEF) | nessuna dichiarata | servono ai test (XSD A2F, che REL-STC-01 par. 4.2 dichiara identici per SIRPED). Contiene **utenze e pincode di test** del MEF, e il manuale ha un esempio con un CF dal carattere di controllo valido: niente di questo entra nel repository |
| `umbria` | Repository `punto-zero/umbria-sar-support` @ `3cd93d86`: OpenAPI 3.1 del prescrittore e dell'erogatore, collection Postman, allegati; wiki @ `acdbb373` (autenticazione, JWT, Base URL, servizi del prescrittore). File singoli da `raw.githubusercontent.com`, con sha256 | <https://github.com/punto-zero/umbria-sar-support> (PuntoZero S.c.a r.l., Regione Umbria) | nessuna dichiarata. PuntoZero è una società a totale capitale pubblico (Regione Umbria al 73,04 %, <https://www.puntozeroscarl.it/azienda>): soggetto dell'art. 2 c. 2 lett. c del CAD, quindi per l'art. 52 c. 2 i dati che pubblica senza licenza sono di tipo aperto | servono ai test (validazione contro l'OpenAPI, facoltativa) e alla documentazione. Non li redistribuiamo lo stesso: basta il manifesto. **I certificati di test** (`certificati/*.jks`, `*.pfx`, con le chiavi private) **non si scaricano**: il kit non li usa e i test generano i propri |
| `cda-xsd` | 11 XSD del CDA R2 usati da `ValidatoreLocale`, scaricati in `specifiche/fse/cda-xsd/` (sha256 identici alle copie che erano nel pacchetto fino al 03/10/2026) | `it-fse-catalogs` @ `141be7f0`, cartella `schema/POCD_MT000040UV02/` | © Health Level Seven; licenza nell'intestazione solo per 4 file, gli altri sotto la [HL7 IP Policy](https://www.hl7.org/legal/ippolicy.cfm) | la HL7 IP Policy non autorizza un non membro a ridistribuirli (sezione 1, «Da verificare»). Aggiunti e verificati il 02/10/2026: scaricati da zero in una cartella vuota, 11/11 verificati; un byte aggiunto a `voc.xsd` viene segnalato (HASH DIVERSO) |
| `cda-xsd` | Schematron del PSS v4.0 (`schematron_PSS_v4.0.sch`), scaricato in `specifiche/fse/schematron/` (sha256 identico alla copia che era nel pacchetto fino al 03/10/2026) | `it-fse-catalogs` @ `141be7f0`, cartella `schematron/` (<https://raw.githubusercontent.com/ministero-salute/it-fse-catalogs/141be7f0a1c75f4a0a07f979da48a83471d07ef4/schematron/schematron_PSS_v4.0.sch>) | nessuna licenza; traduce la guida PSS di HL7 Italia, «Copyright © 2024 by HL7 Italia. All Rights Reserved» | stesso motivo degli XSD (sezione 1, «Da verificare»). Aggiunto e verificato il 03/10/2026: il gruppo `cda-xsd` (12 file) scaricato da zero in una cartella vuota, 12/12 verificati |
| `fse-riferimento` | Test Case di accreditamento del PSS (7 file) | `it-fse-accreditamento` @ `d937255f`, cartella `Test Case/` | nessuna dichiarata | riferimento; il resto del repository (materiale dei fornitori) non si scarica |

Non si scarica più `portale.html`: era una copia della pagina del portale, la fonte è
l'URL stesso.

Se una fonte cambia, lo script non sovrascrive niente: lascia il file nuovo accanto con
suffisso `.non-verificato` ed esce con errore. Va guardato a mano e il manifesto
aggiornato insieme al codice che ne dipende.

## 3. Documenti a «circolazione limitata»: non si usano

Regola: un documento che porta la dicitura «circolazione limitata», o un'altra che ne restringe i
destinatari, **non entra nel kit**, anche se si scarica senza login. In pratica:
- non si redistribuisce;
- non si mette nel manifesto (nessuno script lo scarica);
- non se ne citano brani;
- non ci si scrive codice sopra.

Al massimo se ne riporta l'esistenza, il titolo e la dicitura, per spiegare perché una parte manca.

Caso concreto, 01/10/2026: *Specifiche di integrazione, Integrazione per l'invio al FSE regionale dei
Patient Summary*, Insiel, **ISAD-FSE-SPT-02-2025** v1.8 (`medicinrete.insiel.it/allegati/`).
- Ogni pagina dice «Documento a circolazione limitata rivolto unicamente ai destinatari
  esplicitati», cioè «ai fornitori delle cartelle MMG/PLS che producono i patient summary», e
  «© Tutti i diritti riservati».
- Lo abbiamo scaricato una volta per controllare la dicitura. Ne abbiamo letto **solo copertina e
  indice**, poi abbiamo cancellato la copia locale e il testo estratto.
- Per questo il kit **non copre il canale FSE regionale del FVG** (`docs/SAR_FVG.md`, sez. 8).
- Quello che ne sapevamo prima, cioè che esiste, a chi è rivolto e che usa la smartcard del medico,
  viene dall'indagine preliminare (`indagine-mmg-opensource/INVENTARIO.md`), non dal kit.

Se Insiel lo rende pubblico o ci autorizza per iscritto, la voce passa nel manifesto come le altre.

Piemonte, 01/10/2026: abbiamo controllato tutti i documenti SIRPED usati e l'allegato AP26_003.
Portano «Uso: Esterno» (o «uso: Esterno»), nessuno «circolazione limitata». Nessuno è stato escluso
per questa regola.

Software aperto della Regione Piemonte e del CSI (organizzazione GitHub `regione-piemonte`): abbiamo
guardato nomi, descrizioni e licenze dei repository (`fonti-fse`, `webappmed-fse`, `cod-fse`,
`farab-fse`, `gatefire`, `lcce` in EUPL-1.2-or-later, `imr-fse` in GPL-2.0). Non ne abbiamo
copiato niente: un client di SIRPED non c'è.

## 4. Il codice AGPL non entra nella libreria EUPL

Il codice del gateway FSE del Ministero (`it-fse-gtw-validator`,
`it-fse-gtw-dispatcher`) è AGPL-3.0. Il kit lo usa **solo come strumento di test
esterno**, e così deve restare:

1. **La libreria Python non lo importa e non lo carica.** `varco.fse.validazione.ValidatoreUfficiale`
   lancia `strumenti/validatore-ufficiale/valida.sh` come **processo separato** e legge
   il risultato JSON dallo standard output. Nessun bridge Java (JPype, Py4J), nessun
   `.jar` nel pacchetto, nessun percorso di `specifiche/` scritto in `src/` (l'unico legame con la
   copia scaricata è la cartella degli schemi HL7, che non sono AGPL: sezione 1). Il validatore
   ufficiale è facoltativo: senza, la libreria funziona e usa `ValidatoreLocale`.
2. **Nessun file AGPL è copiato** in `src/`, `conformita/` o `strumenti/`.
3. **Il banco Java** (`strumenti/validatore-ufficiale/src/*.java`) è codice nostro, che
   chiama le classi del validatore e del dispatcher. Il sorgente è EUPL-1.2 e non
   contiene codice AGPL. Compilato insieme al codice del gateway diventa un'opera
   combinata: per questo le classi compilate (`classi/`) e i repository del gateway
   **non si distribuiscono** (`.gitignore`) e ciascuno li ricostruisce in locale con
   `prepara.sh`. Se un giorno qualcuno distribuisse il banco compilato, potrebbe farlo
   sotto AGPL-3.0: l'EUPL-1.2 la elenca tra le licenze compatibili (art. 5 e appendice).
4. **Chi distribuisce la libreria** (pacchetto Python, `src/`) distribuisce solo codice
   EUPL-1.2 e il materiale della sezione 1.

I punti 1 e 2 sono controllati da `tests/unit/test_licenze.py`: SPDX EUPL-1.2 su ogni
modulo, import solo da libreria standard e dipendenze dichiarate, nessun aggancio a
`specifiche/` o a bridge Java, nessun file identico (sha256) a un file dei repository
AGPL. Gruppo di controllo: un `README.md` del validatore copiato in `strumenti/` fa
fallire il test.

---
Licenza di questo documento: CC-BY-4.0.

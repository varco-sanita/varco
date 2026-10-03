# Varco

Un kit **aperto e gratuito** che permette a qualsiasi programma di parlare
direttamente con i servizi pubblici della sanità (Sistema Tessera Sanitaria
del MEF, Fascicolo Sanitario Elettronico 2.0, in futuro INPS), senza passare
per un gestionale commerciale.

Il nome: un varco è un'apertura in una barriera: Varco non abbatte i cancelli dei sistemi
pubblici, apre un passaggio uguale per tutti.

Due moduli, sullo **stesso modello dati**:

- **Ricetta dematerializzata**, dalla parte del medico prescrittore: invio al SAC
  (il sistema centrale del MEF), visualizzazione, annullamento, medico sostituto,
  lista degli NRE utilizzati. Per la Puglia, lo stesso attraverso il SIST regionale, per il
  Friuli-Venezia Giulia attraverso il SAR di Insiel, per il Piemonte attraverso SIRPED (CSI
  Piemonte) e per l'Umbria attraverso il SAR di PuntoZero: scritti e verificati sulle specifiche,
  **non collaudati** sui sistemi delle Regioni.
- **FSE 2.0, lato documento**: il Profilo Sanitario Sintetico (PSS) in CDA2 HL7
  Italia, generato dagli stessi oggetti della ricetta, validato con gli schemi, lo
  schematron e il **codice del validatore ufficiale** del gateway, messo in un PDF e
  firmato PAdES. L'invio al gateway non c'è ancora: servono certificati Sogei.

Codice e suite di conformità con licenza **EUPL-1.2** (testo in [`LICENSE`](LICENSE)),
documentazione con licenza **CC-BY-4.0** ([`LICENSES/CC-BY-4.0.txt`](LICENSES/CC-BY-4.0.txt)).
Nessun fine commerciale. **Non è un dispositivo medico** (sotto).

## Limiti noti

I 12 difetti trovati dalla revisione esterna del giro 3 (registro, guardia, Puglia, FVG, Piemonte,
conformità) sono corretti, ognuno con un test che prima falliva (issue chiuse dalle PR #13-#17).
I difetti aperti stanno nelle issue con l'etichetta `bug`:
<https://github.com/varco-sanita/varco/issues?q=is%3Aissue+is%3Aopen+label%3Abug>.

I moduli regionali (Puglia, FVG, Piemonte, Umbria) sono verificati sulle specifiche e su server di
prova locali, **non collaudati** sui sistemi delle Regioni: per ognuno serve un'adesione. Il gateway
FSE non ha ancora un canale (servono i certificati di Sogei).

**Puglia, domanda aperta.** Le specifiche SIST si scaricano senza login e il documento non ha
diciture, ma la sezione «Integratori» del portale, dove stanno, si dichiara riservata e legata a un
accordo di riservatezza per le terze parti. Lo abbiamo visto il 03/10/2026, dopo aver scritto il
modulo. Finché InnovaPuglia non risponde il modulo Puglia non cresce (`docs/BLOCCHI.md`).

Fino al 03/10/2026 il progetto si chiamava *kit-mmg*: il pacchetto Python era `kit_mmg` e le
variabili d'ambiente avevano il prefisso `KITMMG_`. Ora sono `varco` e `VARCO_*`; per la
versione 0.1 le vecchie variabili valgono ancora, con un avviso di deprecazione
(`varco.ambiente`), poi spariscono.

## Perché esiste

Oggi un medico di famiglia prescrive e alimenta il Fascicolo attraverso il suo
gestionale, e il collegamento coi servizi pubblici sta chiuso lì dentro. Se cambia
programma, o se ne vuole scrivere uno suo, riparte da zero. Le specifiche però sono
pubbliche.

Questo kit le implementa una volta sola, in chiaro, e ci mette sopra una
**suite di conformità pubblica**: casi "mando questo, mi aspetto quest'altro" in
JSON, con uno schema che li specifica, che chiunque può rieseguire contro la propria
implementazione in qualsiasi linguaggio.

- **Per i medici**: uno strumento gratuito, dati in un formato portabile (JSON)
  che non dipende da nessun fornitore.
- **Per lo Stato**: una sola integrazione riusabile e un collaudo trasparente,
  con risultati che chiunque può verificare.

## Cosa funziona oggi (verificato il 30/09/2026)

Ricetta, contro l'ambiente di **test** del MEF:

- invio della ricetta farmaceutica e specialistica → NRE, codice di
  autenticazione, promemoria PDF;
- visualizzazione, annullamento;
- **medico sostituto** (cfMedico2): prescrive il sostituto con le sue credenziali,
  il titolare vede la ricetta ma non può annullarla (errore 1125), solo il
  sostituto la annulla;
- **lista degli NRE utilizzati** (`demInterrogaNreUtilizzati`): per NRE puntuale o
  per intervallo di date;
- cifratura di CF e pincode con il certificato SanitelCF, riconoscimento di rifiuti
  (`9999`), avvisi (`0001`) e SOAP Fault.

FSE 2.0, lato documento, **in locale** (nessuna chiamata al gateway):

- CDA2 del **Profilo Sanitario Sintetico**: allergie, terapie, lista dei problemi,
  anamnesi familiare (voci oppure codici di assenza), esenzioni. Terapie, problemi
  ed esenzioni si ricavano dalle stesse `Riga` e `Ricetta` del modulo ricetta;
- validazione col **codice ufficiale** del validatore del gateway
  (`it-fse-gtw-validator`: XSD, schematron PSS v4.0, vocabolari), eseguito in locale
  con i dump pubblici del suo database. I PSS generati risultano **OK**;
- validazione leggera senza Java (XSD + schematron ufficiali con lxml e Saxon);
- PDF con il CDA allegato come `cda.xml`, o iniettato in un PDF esistente;
  il **codice del dispatcher ufficiale** lo ritrova identico;
- firma PAdES con un'interfaccia `Firmatario`. Provata **solo con un certificato
  autofirmato di test**: la firma vera richiede il certificato del medico.

La prescrizione farmaceutica **non** la generiamo in CDA: in FSE la inserisce il
Sistema TS dopo averla convertita (it-fse-support, FAQ), non il medico.

Le prove, cioè gli XML veri scambiati col MEF e gli esiti veri del validatore
ufficiale, sono in [`prove/`](prove/INDICE.md).

**Non c'è** nessun supporto clinico: niente interazioni tra farmaci, niente
consigli terapeutici. I controlli locali sono solo di forma.

### Puglia (SIST): scritto e verificato sulle specifiche, NON collaudato sul sistema regionale

In Puglia il medico non chiama il SAC ma il **SIST**, il SAR della Regione
(InnovaPuglia). `RicettaSIST` rispetta lo stesso contratto di `RicettaSAC`, sullo
stesso modello dati. Ecco come lavora:

- WS-Security firmata con la CNS del medico;
- `chkPrescrizione` → NRE e codice di autenticazione, oppure solo NRE (ricetta rossa);
- CDA2 di prescrizione firmato CAdES e registrato con `setRegistraPrescrizione`, con la
  ripetizione se fallisce;
- visualizzazione con NRE e CF dell'assistito, annullamento, ricerca per periodo.

**Nessuna chiamata è mai partita verso la Regione.** Il modulo è verificato in quattro modi:

- contro lo schema ufficiale `CVPService.xsd`;
- contro la struttura degli esempi della specifica;
- con un verificatore indipendente della firma WS-Security;
- con un server finto in locale che controlla le richieste come dice la specifica.

Le risposte di prova sono **sintetiche**, perché la specifica non ne pubblica. Per il
collaudo vero servono l'adesione, l'accesso alla RUPAR, una CNS di collaudo e il codice
applicativo da InnovaPuglia. Tutto in [`docs/SAR_PUGLIA.md`](docs/SAR_PUGLIA.md).

### Friuli-Venezia Giulia (SAR di Insiel): scritto e verificato sulle specifiche, NON collaudato sul sistema regionale

In FVG il medico chiama il **SAR** della Regione, gestito da Insiel. Il SAR usa il tracciato del
SAC, con namespace, attributi e tipi suoi. `RicettaFVG` rispetta lo stesso contratto di
`RicettaSAC`, sullo stesso modello dati, **senza nessun campo nuovo**. Ecco come lavora:

- mutua autenticazione TLS con la carta del medico (CRS o Carta Operatore). In alternativa la
  soluzione federata, con i token che fornisce chi integra: il kit li porta, non li crea;
- `User-Agent` con il codice prodotto dato da Insiel, attributo `prodottoCme`, CF dell'assistito
  cifrato;
- invio, visualizzazione, annullamento, verifica della posizione del sostituto;
- riconoscimento dei codici di downgrade in ricetta rossa (060120-060130).

**Nessuna chiamata è mai partita verso i servizi della Regione o di Insiel** (solo il download
delle specifiche pubbliche). Il modulo è verificato in tre modi:

- contro gli XSD ufficiali di `wsdl_prescritto.zip`;
- con 29 casi di conformità;
- con un server finto in locale, in HTTPS con mutua autenticazione, che controlla le richieste come
  dice la specifica.

Le risposte di prova sono **sintetiche**. Mancano:
- l'endpoint della lista degli NRE e lo schema dei lotti;
- il certificato per cifrare il CF, che la specifica indica in due modi diversi.

Il FSE regionale non è coperto: la sua specifica è «a circolazione limitata». Per il collaudo
servono l'accreditamento, il codice prodotto e i certificati di Insiel. Tutto in
[`docs/SAR_FVG.md`](docs/SAR_FVG.md).

### Piemonte (SIRPED, CSI Piemonte): scritto e verificato sulle specifiche, NON collaudato sul sistema regionale

In Piemonte il medico chiama **SIRPED**, il SAR della Regione gestito dal CSI Piemonte. SIRPED usa
il tracciato del SAC con i namespace del MEF. `RicettaPiemonte` rispetta lo stesso contratto di
`RicettaSAC`, sullo stesso modello dati e con lo stesso codec, **senza nessun campo nuovo**. Ecco
come lavora:

- credenziali **RUPAR** del medico; pincode e CF dell'assistito cifrati con il certificato della
  Regione, che non è pubblico: il kit non ha un default;
- secondo fattore in una delle due modalità regionali:
  - **Id-Sessione via mail**: CreateAuth, CheckToken e RevokeAuth sugli XSD del kit A2F del MEF,
    header `X-idSessione` e `X-Gestionale`;
  - **OAuth2**: Authorization Code con PKCE, firma del JWT verificata sul JWKS, `pinCode` vuoto;
- invio, visualizzazione, annullamento, lista degli NRE.

**Nessuna chiamata è mai partita verso i servizi della Regione o del CSI** (solo il download delle
specifiche pubbliche). Il modulo è verificato in tre modi:

- contro gli XSD del MEF e quelli del kit A2F;
- con 44 casi di conformità;
- con un server finto in locale che controlla le richieste come dice la specifica, nelle due
  modalità.

Non ci sono URL pubblicati, né di collaudo né di produzione: li dà il CSI con l'autocertificazione,
che oggi è scritta per i gestionali già certificati. Le risposte di prova sono **sintetiche**. Tutto,
compreso cosa vuol dire «certificata SIRPED», in [`docs/SAR_PIEMONTE.md`](docs/SAR_PIEMONTE.md).

### Umbria (SAR di PuntoZero): scritto e verificato sulle specifiche, NON collaudato sul sistema regionale

In Umbria il medico chiama il **SAR** della Regione, gestito da PuntoZero. Il SAR replica i servizi del
SAC come **API REST** in JSON e si autentica come il gateway del FSE 2.0. `RicettaUmbria` rispetta lo
stesso contratto di `RicettaSAC`, sullo stesso modello dati, **senza nessun campo nuovo**. Ecco come
lavora:

- mutua autenticazione TLS con il certificato del software, e due JWT firmati a ogni chiamata
  (`Authorization` e `FSE-JWT-Signature`) con i claim di ogni servizio;
- l'NRE lo mette il medico, da un lotto chiesto al SAR (`richiedi_lotto_nre`, `LottoNRE`); il CF
  dell'assistito va in chiaro;
- invio, visualizzazione, annullamento, lista degli NRE, dichiarazione di sostituzione;
- dopo un 502, un 504 o un timeout sull'invio, `InvioIncertoUmbria`: si annulla con lo stesso NRE e si
  rifà l'invio con un NRE diverso, come chiede la specifica.

**Nessuna chiamata è mai partita verso i sistemi della Regione o di PuntoZero**, nemmeno verso
l'ambiente di test, i cui certificati sono pubblici (solo il download delle specifiche da GitHub).
Il modulo è verificato in quattro modi:

- contro gli schemi dell'OpenAPI ufficiale;
- con 28 casi di conformità;
- con un server finto in locale, in HTTPS con mutua autenticazione, che verifica i due token e il
  corpo come dicono la wiki e l'OpenAPI;
- con un registro che non scrive dati personali né token.

Le risposte di prova sono **sintetiche**. Per il collaudo serve un accordo con PuntoZero e la
Regione. Tutto, con i punti delle specifiche da chiarire, in [`docs/SAR_UMBRIA.md`](docs/SAR_UMBRIA.md).

## Non è un dispositivo medico

Il kit **non è un dispositivo medico** né un software dispositivo medico ai sensi del
Regolamento (UE) 2017/745, e non va usato per prendere decisioni cliniche.

- **Cosa fa**: trasmette al SAC i dati che il medico ha già deciso, li converte nel
  formato del Fascicolo (CDA2), controlla che rispettino tracciati e schemi ufficiali,
  allega e firma il PDF.
- **Cosa non fa**: non controlla interazioni, allergie rispetto ai farmaci,
  controindicazioni o dosi; non suggerisce farmaci, diagnosi o esami; non calcola rischi
  né genera allarmi sul paziente.
- **Perché**: per la guida MDCG 2019-11 il software che solo comunica, archivia o
  converte i dati per compatibilità non è un dispositivo medico (passo decisionale 3;
  allegato I, sistemi informativi e di comunicazione).
- **Cosa lo cambierebbe**: un modulo farmaci con controlli di interazioni, allergie o
  dosi, un calcolo della dose, un supporto alle decisioni, allarmi o punteggi di rischio
  sul singolo paziente. Funzioni così non entrano nel kit senza una nuova valutazione.

Il ragionamento completo è in
[`docs/NON_DISPOSITIVO_MEDICO.md`](docs/NON_DISPOSITIVO_MEDICO.md). È la valutazione
dell'autore, non un parere di un organismo notificato.

## Provarlo

Serve Python 3.11 o successivo.

1. **Installa**:

   ```sh
   python3 -m venv .venv
   .venv/bin/pip install -e ".[test]"
   ```

2. **Scarica le specifiche, poi i test automatici e la suite senza rete**. Gli schemi HL7
   (XSD del CDA R2 e schematron del PSS) non stanno nel repository: la licenza di HL7 non
   ne permette la redistribuzione ([`docs/TERZE_PARTI.md`](docs/TERZE_PARTI.md)). Lo script
   li prende dalla fonte ufficiale (`ministero-salute/it-fse-catalogs`, a un commit fissato)
   e ne verifica lo sha256. Senza, i test che li usano risultano **saltati** e
   `ValidatoreLocale` si ferma con un messaggio che dice come scaricarli. Se li tieni
   altrove: `VARCO_CDA_XSD` (cartella degli XSD) e `VARCO_SCHEMATRON` (cartella dello schematron).

   ```sh
   .venv/bin/python strumenti/scarica_specifiche.py --gruppi cda-xsd   # schemi HL7 (12 file, pochi KB)
   .venv/bin/python strumenti/scarica_specifiche.py                     # oppure tutto il default: anche kit MEF e gateway FSE
   .venv/bin/python -m pytest
   .venv/bin/python -m varco.conformita.esegui --famiglia offline
   .venv/bin/python -m varco.conformita.esegui --famiglia fse      # PSS: XSD + schematron in locale
   ```

3. **Validatore ufficiale FSE** (facoltativo, serve Java 21 e Maven; rete solo verso
   GitHub e Maven Central, una volta):

   ```sh
   export JAVA_HOME=/percorso/jdk-21
   strumenti/validatore-ufficiale/prepara.sh      # compila validatore e dispatcher ufficiali
   strumenti/validatore-ufficiale/valida.sh documento.xml
   .venv/bin/python -m varco.conformita.esegui --famiglia fse   # ora usa il validatore ufficiale
   strumenti/validatore-ufficiale/esegui_casi_fse.sh              # gli stessi casi, eseguiti in Java
   JAVA_HOME=... .venv/bin/python strumenti/genera_prove_fse.py    # rigenera le prove FSE
   ```

4. **Ricetta vera nell'ambiente di test**: serve il *Kit per lo sviluppo – Ricetta
   elettronica – Prescrittore* del MEF (contiene le utenze di test), che il repository
   non redistribuisce. Lo scarica dalla pagina ufficiale
   [Documenti e specifiche tecniche – prescrittore](https://sistemats1.sanita.finanze.it/portale/it/ricetta-elettronica/documenti-e-specifiche-tecniche-prescrittore),
   ne verifica l'hash e lo scompatta in `specifiche/kit/`:

   ```sh
   .venv/bin/python strumenti/scarica_specifiche.py --gruppi mef
   export VARCO_KIT_MEF=specifiche/kit/kit-ricetta-dematerializzata-datamatrix
   .venv/bin/python strumenti/genera_prove.py                  # invio, visualizza, annulla, rifiuti
   .venv/bin/python strumenti/genera_prove_sostituto_nre.py    # sostituto e lista NRE
   .venv/bin/python -m varco.conformita.esegui --famiglia tutte --kit $VARCO_KIT_MEF --registra prove/mia-prova
   VARCO_INTEGRAZIONE=1 .venv/bin/python -m pytest tests/integrazione
   ```

## Usarlo da un programma

Ricetta:

```python
from varco import CanaleSAC, Credenziali, RicettaSAC
from varco.ricetta import Assistito, CriteriNreUtilizzati, Prescrittore, Ricetta, Riga, TipoPrescrizione

servizio = RicettaSAC(CanaleSAC(Credenziali.da_env()))   # VARCO_UTENTE, VARCO_PASSWORD, VARCO_PINCODE

ricetta = Ricetta(
    prescrittore=Prescrittore("PROVAX00X00X000Y", codice_regione="130", codice_asl="201", codice_specializzazione="F"),
    assistito=Assistito(codice_fiscale="PNIMRA70A01H501P", provincia="AQ", asl="201"),
    tipo=TipoPrescrizione.FARMACEUTICA,
    righe=[Riga(1, codice_gruppo_equivalenza="G3B",
                descrizione_gruppo_equivalenza="LEVETIRACETAM 500MG 60 UNITA' USO ORALE")],
)
esito = servizio.invia(ricetta)
if esito.ok:
    print(esito.nre, esito.codice_autenticazione)
else:
    for errore in esito.errori:
        print(errore.codice, errore.testo)

servizio.visualizza(esito.nre).stato_processo           # "3"
servizio.interroga_nre_utilizzati(CriteriNreUtilizzati("130", nre=esito.nre)).ricette
servizio.annulla(esito.nre).ok                          # True
```

Il **sostituto** prescrive con le sue credenziali e la posizione del titolare:
`Prescrittore(..., codice_fiscale_sostituto="PROVAX00X00X000Z")` e un `RicettaSAC`
creato con le credenziali del sostituto. Se credenziali e cfMedico2 non combaciano,
il kit rifiuta prima di chiamare (il SAC risponderebbe 1212).

FSE, Profilo Sanitario Sintetico:

```python
from varco.fse import cda_pss, esempi, pdf
from varco.fse.modello import Problema, Stato, Terapia
from varco.fse.validazione import ValidatoreLocale, ValidatoreUfficiale

pss = esempi.pss_completo()                          # dati SINTETICI; vedi varco/fse/esempi.py
Terapia.da_riga(ricetta.righe[0], via="PO", stato=Stato.ATTIVO)  # via e stato li sceglie il medico: il kit non li deduce
Problema.da_ricetta(ricetta, stato=Stato.ATTIVO)                    # la diagnosi della ricetta come problema

xml = cda_pss.genera_xml(pss)                         # CDA2; prima controlla la forma
ValidatoreLocale().valida(xml).esito                  # "OK" (XSD + schematron, senza vocabolari)
ValidatoreUfficiale().valida(xml).esito               # "OK" (codice del gateway, con vocabolari)

documento = pdf.pdf_con_cda(pdf.righe_leggibili_pss(pss), xml)       # oppure pdf.inietta_cda(pdf_esistente, xml)
firmato = pdf.FirmatarioPKCS12("medico.p12", b"password").firma_pades(documento)
```

(I valori qui sopra sono quelli di **test** del kit MEF, non dati reali.)

Le credenziali non si scrivono mai nel codice: si leggono da variabili
d'ambiente (`Credenziali.da_env()`) o da un file TOML (`Credenziali.da_file()`,
vedi [`config.esempio.toml`](config.esempio.toml)).

## Sicurezza: la produzione è bloccata

Il kit **si rifiuta di chiamare la produzione**: `demservice.sanita.finanze.it` o
qualunque host `*.sanita.finanze.it` senza "test" nel nome, e il gateway FSE
`modipa.fse.salute.gov.it` o qualunque host `*.fse.salute.gov.it` che non sia
l'ambiente di validazione `-val`. Il blocco scatta sull'URL finale, prima di aprire
la connessione, e il kit non segue redirect. Con un trasporto proprio la guardia resta accesa
per tutto l'invio: ogni redirect, risoluzione DNS e connect verso un host vietato si ferma prima
del socket (`trasporto.http.consegna`); un IP che il kit non ha visto risolvere da un nome
consentito vale come produzione, e una connessione `http.client` già aperta si ricontrolla a ogni
scrittura, prima che partano richiesta e credenziali. Per sbloccarlo servono
`TrasportoHTTP(consenti_produzione=True)` **e** un id di sessione a due fattori reale.

**Regione Puglia (SIST).** La produzione (`pdd-virtasl.rmmg.rsr.rupar.puglia.it`)
e qualunque host `*.puglia.it` contano come produzione. Il **collaudo** regionale
(`pddasl-preprod.sanita.regione.rsr.rupar.puglia.it`) non è un ambiente libero come quello
del MEF: è un sistema della Regione, e il kit lo blocca finché non ci sono due cose:

- un flag suo, `TrasportoHTTP(consenti_collaudo_regionale=True)`;
- un'adesione dichiarata nel canale, `AdesioneSIST` (riferimento e codice applicativo
  rilasciati da InnovaPuglia).

**Regione Friuli-Venezia Giulia (SAR).** Gli host di produzione non sono pubblicati. Per questo
ogni host `*.fvg.it` o `*.insiel.it` conta come produzione, tranne i collaudi indicati dalla
specifica (`demtest.sanita.fvg.it`, `sartest.sanita.fvg.it`, `apiweb-collaudo.sanita.fvg.it`,
`isweb-collaudo.sanita.fvg.it`). Anche quelli sono sistemi della Regione e restano bloccati finché
non ci sono il flag `consenti_collaudo_regionale=True` e un'`AdesioneFVG` (riferimento
dell'accreditamento e codice ProdottoCME rilasciati da Insiel).

**Regione Piemonte (SIRPED).** Nessun host è pubblicato. Ogni host sotto `piemonte.it`, `csi.it`,
`csipiemonte.it`, `salutepiemonte.it`, `sistemapiemonte.it` o `ruparpiemonte.it` conta come
produzione. Fa eccezione un host con un'etichetta che comincia o finisce con `tst`, `test` o
`collaudo`, come l'esempio della specifica `tst-rel-xxxx.csi.it`. Quello resta bloccato finché non
ci sono tre cose:
- il flag `consenti_collaudo_regionale=True`;
- l'host dichiarato per nome, `TrasportoHTTP(collaudi_piemonte={...})`;
- un'`AdesionePiemonte` nel canale.

**Regione Umbria (SAR di PuntoZero).** L'host di produzione (`api-salute.regione.umbria.it`) e ogni
host `*.umbria.it` o `*.puntozeroscarl.it` contano come produzione. L'host di test
(`api-salute-test.regione.umbria.it`) è un sistema della Regione: anche se i certificati di test sono
pubblici, resta bloccato finché non ci sono il flag `consenti_collaudo_regionale=True` e
un'`AdesioneUmbria` nel canale.

Inoltre: al massimo una richiesta al secondo verso lo stesso host, solo HTTPS.

## Dati personali: il registratore è redatto

Il kit non salva niente di suo. Il registratore facoltativo degli scambi
(`RegistratoreFile`, o `--registra` della suite) **redige per default**: codici
fiscali, pincode, NRE, codice di autenticazione, nomi, indirizzi, diagnosi, esenzioni,
testi liberi (anche i messaggi del servizio) e ogni allegato PDF o base64 (il promemoria riporta il CF
in chiaro) non arrivano su disco. Nel tracciato SAC si scrive leggibile solo ciò che sta in una
allowlist (codici, flag, date); un corpo che il registro non sa leggere (XML troncato, UTF-16) non si
scrive affatto: resta un segnaposto con lunghezza e impronta. Header di autenticazione e credenziali nell'URL sono sempre
mascherati; i file nascono leggibili solo dall'utente.

- `RegistratoreFile(cartella, identita_di_test=kit_mef.identita_di_test())`: scrive in
  chiaro **solo** le chiamate che usano esclusivamente le identità di test del kit MEF
  (host di test, utente di test, nessun altro CF nemmeno dentro il PDF, nessun nome o
  tessera senza CF, CF cifrato dell'assistito confermato dal promemoria). Le altre le
  redige e scrive il motivo nel meta. È quello che usano le prove.
- `RegistratoreFile(cartella, registra_dati_personali_in_chiaro=True)` (CLI:
  `--registra-dati-personali-in-chiaro`): **tutto in chiaro**, anche con dati reali.
  Serve una base giuridica; il kit avvisa e lo scrive in ogni meta.

Un log redatto è pseudonimizzato, non anonimo: va tenuto in locale e cancellato quando
non serve. Minacce e misure: [`docs/MINACCE.md`](docs/MINACCE.md).

## Com'è fatto

```
src/varco/
  ricetta/       modello dati, codec XML del SAC, servizio (invio, visualizza, annulla, lista NRE);
                 SIST Puglia: xml_sist (codec CVP), cda_sist (CDA2 di prescrizione), sist (RicettaSIST)
                 SAR FVG: xml_fvg (tracciato del SAC con namespace e attributi FVG), fvg (RicettaFVG)
                 SIRPED Piemonte: piemonte (RicettaPiemonte, sul codec del SAC)
                 SAR Umbria: json_umbria (codec JSON dell'OpenAPI, LottoNRE), umbria (RicettaUmbria)
  fse/           PSS: modello (sopra quello della ricetta), codec CDA2, validazione, PDF e firma
  trasporto/     HTTPS + SOAP + canale SAC + canale SIST e WS-Security + canale FVG
                 + canale Piemonte (piemonte, piemonte_a2f, piemonte_oauth2)
                 + canale Umbria (umbria: mTLS e due JWT firmati): separato dal modello
  cifratura.py   SanitelCF (RSA PKCS#1 v1.5)
  conformita/    esecutore Python dei casi
  schemi/        XSD ufficiali del kit MEF
conformita/      LA SUITE: casi JSON, schema dei casi, risposte reali, documenti, dati
strumenti/
  genera_prove*.py            prove reali in prove/ (genera_prove_sist.py: contro il server finto)
  sist_server_finto.py        server SIST finto su 127.0.0.1, per le prove senza la Regione
  fvg_server_finto.py         server SAR FVG finto su 127.0.0.1 (HTTPS, mutua autenticazione); genera_prove_fvg.py
  piemonte_server_finto.py    SIRPED finto su 127.0.0.1 (servizi di prescrizione, Id-Sessione A2F, OAuth2)
  umbria_server_finto.py      SAR Umbria finto su 127.0.0.1 (HTTPS, mutua autenticazione, verifica dei due JWT)
  scarica_specifiche.py       scarica e verifica il materiale di terzi (fonti_specifiche.json)
  validatore-ufficiale/       banco del validatore e del dispatcher UFFICIALI FSE (Java) + esecutore Java dei casi
tests/unit, tests/integrazione (rete), tests/ufficiale (validatore ufficiale)
prove/           XML reali scambiati col MEF, esiti reali delle validazioni
docs/ARCHITETTURA.md   scelte e motivazioni
docs/SAR_PUGLIA.md     il SIST della Puglia: canale, differenze dal SAC, collaudo
docs/SAR_FVG.md        il SAR del Friuli-Venezia Giulia: canale, differenze dal SAC e dal SIST, collaudo
docs/SAR_PIEMONTE.md   SIRPED del Piemonte: canale, 2FA regionale, collaudo, «certificata SIRPED»
docs/SAR_UMBRIA.md     il SAR dell'Umbria: REST, JWT, lotti NRE, invio incerto, difetti delle specifiche
docs/BLOCCHI.md        dove il lavoro si è fermato e perché
docs/MINACCE.md, docs/NON_DISPOSITIVO_MEDICO.md, docs/TERZE_PARTI.md
specifiche/      (non nel repository) materiale di terzi: strumenti/scarica_specifiche.py
```

Dipendenze di esercizio: solo `cryptography`. Facoltative: `lxml` (XSD), `saxonche`
(schematron XSLT2), `pyHanko` (iniezione in PDF esistenti e firma PAdES).

Le scelte e i loro perché sono in [`docs/ARCHITETTURA.md`](docs/ARCHITETTURA.md).

## Fonti

Solo materiale pubblico. Il repository **non redistribuisce** specifiche, kit MEF e
codice del gateway, né gli schemi HL7 (XSD del CDA R2 e schematron del PSS):
`strumenti/scarica_specifiche.py` li scarica in `specifiche/` dalle
fonti ufficiali, alla versione usata per il kit, e ne verifica hash e commit. Cosa
invece è incluso (XSD del SAC, scheletro ISO Schematron, certificato SanitelCF), con licenze e motivi:
[`docs/TERZE_PARTI.md`](docs/TERZE_PARTI.md) e [`NOTICE`](NOTICE).

- Sistema TS: *Web services per la trasmissione delle ricette dematerializzate –
  parte 1: prescrizione*, versione 08/07/2026, e il kit di
  sviluppo prescrittore (WSDL, XSD, SoapUI, utenze di test, certificato SanitelCF);
- Ministero della Salute su GitHub:
  `it-fse-catalogs` (XSD del CDA, schematron del PSS, dump dei dizionari del gateway),
  `it-fse-support` (documentazione, esempi di CDA validi),
  `it-fse-gtw-validator` e `it-fse-gtw-dispatcher` (codice del gateway, AGPL-3.0,
  usato senza modificarlo), `it-fse-accreditamento` (solo la cartella `Test Case`
  del PSS: nessun materiale dei fornitori), `it-fse-gtw-tools`, `it-fse-gtw-test-container`;
- Regione Puglia (InnovaPuglia): *Specifiche di integrazione SIST* 4.03.27;
- Regione Friuli-Venezia Giulia (Insiel): *Specifiche di interfaccia applicativa del servizio SAR*
  Idof-dem-AT-01 dell'11/02/2026 e `wsdl_prescritto.zip`. Non usiamo i documenti «a circolazione
  limitata» ([`docs/TERZE_PARTI.md`](docs/TERZE_PARTI.md), sezione 3);
- Regione Piemonte e CSI Piemonte: REL-STC-01 V04 del 02/03/2026 e YAML OAuth2, processo, piano dei
  test e attestati dell'autocertificazione 2026, RE-SRS-SAR V05, RE-TES-01 V02 (tutti «Uso:
  Esterno»), allegato tecnico dell'avviso AP26_003; per l'Id-Sessione via mail, il kit A2F del
  Sistema TS;
- Regione Umbria e PuntoZero: repository `punto-zero/umbria-sar-support` (wiki, OpenAPI del
  prescrittore, collection Postman, allegati), a commit fissati. Il repository non ha una licenza:
  vale l'art. 52, comma 2, del CAD ([`docs/SAR_UMBRIA.md`](docs/SAR_UMBRIA.md)). I certificati di
  test pubblicati non si scaricano e non si usano.

## Licenze, sicurezza, contributi

- Codice e suite di conformità: **EUPL-1.2** ([`LICENSE`](LICENSE)). Documentazione
  (questo file, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `docs/`):
  **CC-BY-4.0** ([`LICENSES/CC-BY-4.0.txt`](LICENSES/CC-BY-4.0.txt)). Materiale di
  terzi: [`NOTICE`](NOTICE).
- Il codice AGPL-3.0 del gateway FSE non fa parte della libreria: si usa solo come
  strumento di test esterno, in un processo separato
  ([`docs/TERZE_PARTI.md`](docs/TERZE_PARTI.md), sezione 4).
- Vulnerabilità: [`SECURITY.md`](SECURITY.md). Regole per contribuire (niente dati
  reali): [`CONTRIBUTING.md`](CONTRIBUTING.md). Modifiche: [`CHANGELOG.md`](CHANGELOG.md).

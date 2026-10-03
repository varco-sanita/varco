# Blocchi

Cose su cui il lavoro si è fermato dopo due tentativi. Sono qui perché chi riprende non rifaccia gli
stessi giri, e per chiederle a chi le sa.

## Piemonte: la pagina principale dell'avviso CSI AP26_003 (01/10/2026)

**Cosa serviva.** Il testo dell'avviso e la sua data: scadenza delle manifestazioni d'interesse, esito,
eventuali importi. Ci interessava per `docs/PROPOSTA_PIEMONTE.md`.

**Cosa abbiamo.** Solo l'Allegato 1, *Specifiche tecniche del servizio*
(`AP26_003_A01_Specifiche tecniche servizio_e_ Allegato_CCE_MMG_PLS - SIAP_def_.pdf`, sotto
`www.csipiemonte.it/sites/default/files/inline_download/indagini_di_mercato/2026/AP26_003/`). È nel
manifesto, gruppo `piemonte`. I fatti citati in `docs/SAR_PIEMONTE.md`, sez. 12, vengono tutti da lì.

**Tentativi.**
1. Pagine dell'avviso ricavate dal percorso dell'allegato: 404. Gli URL esatti di questo primo giro
   non li abbiamo annotati.
2. `https://www.csipiemonte.it/it/indagini-di-mercato` e `https://www.csipiemonte.it/it/content/ap26003`:
   404 tutte e due.

**Cosa resta da fare.** Chiedere il link al CSI, o cercarlo nella sezione «Amministrazione
trasparente» del sito. Senza la pagina non citiamo importi né esiti: la proposta parla solo di ciò
che l'allegato dice.

## Registro: verifica esterna di N2 (errori nel meta) non chiusa dopo due rilanci (03/10/2026)

**Cosa serviva.** Che il revisore esterno (GPT-6 Astra, verifica mirata del giro 4) dichiarasse CHIUSO
il bug N2 del giro 3: credenziali in XML o JSON dentro un'eccezione o un header finivano nel meta.

**Tentativi.**
1. Prima correzione (`Redattore.metadato`, `Redattore.errore`): il revisore ha trovato due varianti,
   lo User-Agent e un JSON con chiave `pass\u0077ord` passato da `repr(e)`.
2. Seconda correzione (User-Agent, argomenti dell'eccezione invece di `repr`): il revisore ha trovato
   JSON serializzato più volte (stringa JSON con escape). Corretto anche questo (`_con_json`).
3. Terza verifica: resta un argomento `bytes` letto con `repr` (`RuntimeError(rb'{"pass\u0077ord":…}')`).
   Corretto dopo la verifica (ogni argomento letto come testo: bytes decodificati, contenitori come
   JSON, altrimenti `str`), con test `test_g4r3_*`; il limite di due rilanci della verifica è stato
   raggiunto, quindi questa ultima correzione NON è stata riverificata da un revisore esterno.

**Cosa resta da fare.** Rilanciare la verifica mirata di N2
(`kit-mmg-review/2026-10-03-giro4/lancia_verifica.sh 1-sac-guardia-registro`). Il punto debole è la
famiglia «testo che contiene strutture codificate»: ogni nuova codifica (escape, base64, compressione)
è una variante. Un'alternativa più radicale, da decidere: nel meta scrivere del messaggio d'errore solo
il tipo dell'eccezione e un'impronta, mai il testo.

## Puglia: le specifiche SIST stanno in un'area che il portale dichiara riservata (03/10/2026)

**Cosa abbiamo trovato.** Le *Specifiche di integrazione SIST* si scaricano senza login da
<https://sist.sanita.puglia.it/en/specifiche-integrazione>, e il documento non porta diciture di
riservatezza. Ma quella pagina sta nella sezione «Integratori», e la pagina della sezione
(<https://sist.sanita.puglia.it/en/integratori>, letta il 03/10/2026) dice: «Tutta la documentazione
pubblicata all'interno di questa pagina, ivi compreso il materiale software messo a disposizione per gli
integratori di terze parti, è da considerarsi RISERVATO e vincolato "all'Accordo di riservatezza Terze
Parti"». Quando abbiamo scritto il modulo (01/10/2026) non l'avevamo visto: `docs/TERZE_PARTI.md` dava
«nessuna licenza dichiarata».

**Perché è un blocco.** La regola del kit (`docs/TERZE_PARTI.md`, sez. 3) è che un documento che
restringe i destinatari non entra nel kit. Il modulo Puglia (codec, CDA di prescrizione, server finto,
casi `SIS-*`) è scritto su quelle specifiche, e non abbiamo firmato nessun accordo. Non è un dubbio che
possiamo sciogliere da soli: dipende da InnovaPuglia.

**Cosa abbiamo fatto.** Niente di nuovo sul modulo Puglia finché non c'è una risposta. La domanda è la
prima della mail preparata per InnovaPuglia (helpdesk SIST), che parte solo col via del manutentore.

**Cosa resta da decidere.** Se InnovaPuglia conferma che le specifiche sono pubbliche, si aggiorna
`docs/TERZE_PARTI.md` con la risposta. Se serve l'accordo, il modulo Puglia va tolto dal repository
(o sospeso) finché un accordo non permette un client open source.

---
Licenza di questo documento: CC-BY-4.0.

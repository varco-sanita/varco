# Risposte SAR FVG: SINTETICHE

Le risposte nella cartella superiore (`conformita/risposte/*.xml`) sono state registrate davvero
dall'ambiente di test del MEF. **Queste no.**

Le specifiche del SAR della Regione Friuli-Venezia Giulia (Insiel, *Idof-dem-AT-01*
dell'11/02/2026, e `wsdl_prescritto.zip`) descrivono i campi delle ricevute ma non pubblicano
nessuna risposta. Queste buste le abbiamo scritte noi con `strumenti/fvg_server_finto.py`
(`python strumenti/fvg_server_finto.py` le riscrive qui), seguendo gli XSD FVG delle ricevute.
`tests/unit/test_fvg.py` controlla che il Body di ognuna validi contro quegli XSD (quando le
specifiche sono scaricate), con **un'eccezione voluta**:

- `invio_downgrade_060120.xml` porta il codice di downgrade `060120` (par. 2.3.6) dentro
  `ElencoErroriRicette`. Lo schema FVG vuole `codEsito` di 4 cifre (`codEsitoType`), quindi questa
  busta NON valida: è un difetto della specifica, non del file. `invio_downgrade_fault_060125.xml`
  mostra l'altra forma possibile, un SOAP Fault. La specifica non dice quale delle due usi il SAR:
  il kit le riconosce entrambe.

Cosa NON sappiamo e queste risposte quindi non dimostrano:

- il testo esatto dei messaggi e dei faultstring del SAR;
- i codici d'errore del SAR: qui 1020, 1120, 1125, 5005 come nell'ambiente di test del SAC;
- il formato reale di `codAutenticazione` (qui 30 cifre);
- se il SAR restituisce il PDF del promemoria come il SAC (qui no: `flagPromemoria` assente);
- i namespace e i prefissi reali delle risposte (qui quelli degli XSD; il lettore del kit guarda
  solo i nomi locali dei tag).

Identità usate: solo quelle pubbliche di test del kit MEF. Nessun dato degli esempi della
specifica è stato copiato.

Quando ci sarà un collaudo vero, le risposte registrate andranno in una cartella a parte,
accanto a queste.

---
Licenza di questo documento: CC-BY-4.0.

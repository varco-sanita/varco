# Risposte SIRPED (Regione Piemonte): SINTETICHE

Le risposte nella cartella superiore (`conformita/risposte/*.xml`) sono state registrate davvero
dall'ambiente di test del MEF. **Queste no.**

Le specifiche di SIRPED (REL-STC-01 V04 del 02/03/2026, piano dei test SIRPED-TES-01 V02) descrivono
i campi di CreateAuth, CheckToken e RevokeAuth ma non pubblicano nessuna busta, e solo un codice
d'errore (`9998`, «Errore di configurazione nella chiamata al servizio», par. 4.2.4). Queste buste
le abbiamo scritte noi con `strumenti/piemonte_server_finto.py`
(`python strumenti/piemonte_server_finto.py` le riscrive qui), seguendo gli XSD del kit A2F del
Sistema TS, che la specifica regionale dichiara identici. `tests/unit/test_piemonte.py` controlla che
ognuna validi contro quegli XSD (quando le specifiche sono scaricate), con **un'eccezione voluta**:

- `crea_errore_forma_esempio.xml` riproduce la forma dell'esempio del par. 4.2.4: `<errore>`
  direttamente sotto la radice, senza il contenitore `<errori>` dello XSD. NON valida: serve a
  provare che il lettore del kit la legge lo stesso.

Le ricevute dei servizi di prescrizione (invio, visualizzazione, annullamento, lista NRE) qui non
ci sono: SIRPED risponde col tracciato del SAC (RE-SRS-SAR V05, par. 3.5.1), e la lettura è già
provata sulle risposte **reali** del SAC della cartella superiore (casi `OFF-*`).

`jwks.json`, `jwks_campo_v.json`, `jwt_valido.txt` e `jwt_alterato.txt` servono ai casi `PIE-4xx`:
un JWT firmato RS256 con una chiave generata al momento (`--anche-jwt`), la stessa chiave in due
forme (il modulo in «n» come nella RFC 7517 e nell'esempio, oppure in «v» come nella tabella del
par. 4.3.3), e lo stesso JWT col payload cambiato. La chiave privata non è stata conservata: a ogni
rigenerazione i file cambiano tutti insieme.

Cosa NON sappiamo e queste risposte quindi non dimostrano:

- i codici e i testi d'errore di SIRPED (qui `9998` per tutto);
- se gli errori dell'Id-Sessione o del JWT sui servizi di prescrizione arrivano come SOAP Fault o
  come rifiuto `9999` nella ricevuta (il server finto usa il Fault);
- il formato di `dataFineValidita` nelle comunicazioni (qui `GG/MM/AAAA hh:mm:ss`, come
  `lastRevokePreviousDate` del par. 4.2.3);
- se SIRPED restituisce il PDF del promemoria come il SAC.

Identità usate: solo quelle pubbliche di test del kit MEF. Nessun dato degli esempi delle
specifiche è stato copiato.

---
Licenza di questo documento: CC-BY-4.0.

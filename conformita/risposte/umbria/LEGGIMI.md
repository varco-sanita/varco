# Risposte SAR Umbria: SINTETICHE

Le risposte nella cartella superiore (`conformita/risposte/*.xml`) sono state registrate davvero
dall'ambiente di test del MEF. **Queste no.**

Le specifiche del SAR della Regione Umbria (PuntoZero, `github.com/punto-zero/umbria-sar-support`:
wiki e OpenAPI `sar-open-api-prescrittore.yaml`) descrivono le risposte solo come schemi: nessuna
risposta reale è pubblicata. Questi file JSON li abbiamo scritti noi con
`strumenti/umbria_server_finto.py` (`python strumenti/umbria_server_finto.py` li riscrive qui).
`tests/unit/test_umbria.py` controlla che ognuno validi contro lo schema dell'OpenAPI (quando le
specifiche sono scaricate), con **tre eccezioni volute**, che servono a collaudare il rifiuto:

- `invio_quantita_numero.json`: `codEsitoInserimento` è un numero, l'OpenAPI vuole una stringa;
- `annulla_senza_nre.json`: manca `nre`, che l'OpenAPI dichiara obbligatorio;
- `lotto_incompleto.json`: esito `00` senza `codRagLotto` e `codLotto`.

Cosa NON sappiamo e queste risposte quindi non dimostrano:

- i codici d'errore del SAR (qui 1101, 1102, 5005, 1120 come nel SAC o inventati per il server finto);
- il formato reale di `codAutenticazione` (qui 30 cifre) e di `dataInserimento`;
- se il SAR mette `pdfPromemoria` come base64 (l'OpenAPI dice `format: byte`, quindi sì);
- il formato reale di `codLotto` e `codRagLotto` (qui `0A` e 6 o 7 cifre, come nell'esempio della
  specifica MEF par. 4.1 e nella costruzione dell'NRE della collection Postman).

Identità usate: solo quelle di test del kit (`PROVAX00X00X000Y`, `PROVAX00X00X000Z`) e un CF
sintetico per l'assistito. I dati del paziente di test della Regione Umbria non sono stati copiati.

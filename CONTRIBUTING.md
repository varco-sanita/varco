# Contribuire a Varco

Grazie. Il kit è un bene comune per i medici di medicina generale e per la sanità
pubblica: le regole qui sotto servono a tenerlo sicuro, verificabile e adottabile da una
PA. Valgono per codice, documenti, casi di conformità e prove.

## 1. Mai dati reali

- **Niente dati di pazienti veri**: né codici fiscali, né nomi, né ricette, né
  promemoria, né documenti FSE, nemmeno «oscurati a mano». Usa solo:
  - le identità **pubbliche di test** del kit MEF (`PROVAX00X00X000Y`, `PNIMRA70A01H501P`, ...);
  - dati **sintetici** come quelli di `varco/fse/esempi.py`.
- **Niente credenziali vere**: niente password, pincode, certificati o chiavi del medico.
  Anche le credenziali di test del kit MEF non vanno copiate nel repository: il codice
  le legge dal kit scaricato (`varco/kit_mef.py`).
- **Niente log in chiaro**: se alleghi uno scambio col SAC, registralo con
  `RegistratoreFile(..., identita_di_test=kit_mef.identita_di_test())` (in chiaro solo
  se usa esclusivamente identità di test) o redatto (default). Mai con
  `registra_dati_personali_in_chiaro=True`.
- Se per sbaglio hai pubblicato un dato reale: scrivi subito in privato (vedi
  `SECURITY.md`), non con una issue.

## 2. Ambienti e conformità

- Solo l'ambiente di **test** del MEF. La guardia anti-produzione non si indebolisce:
  una modifica che la tocca deve avere un test che prova che la produzione resta
  bloccata.
- Sull'utenza di test condivisa: niente password o pincode sbagliati, niente raffiche.
  Il trasporto non scende sotto mezzo secondo tra due richieste.
- Le regole del tracciato vengono dalla specifica ufficiale. Se il servizio reale si
  comporta diversamente, scrivilo in `docs/ARCHITETTURA.md` («Scoperte sul servizio
  reale») con la prova in `prove/`.
- Un cambiamento di comportamento verso il SAC o il FSE si accompagna con un **caso
  della suite di conformità** (`conformita/casi/`), valido contro lo schema.
- **Nessuna funzione clinica** (interazioni, dosi, controindicazioni, raccomandazioni,
  allarmi): cambierebbe la qualificazione del kit come dispositivo medico. Se la
  proponi, dillo nel titolo della richiesta di modifica e leggi prima
  [`docs/NON_DISPOSITIVO_MEDICO.md`](docs/NON_DISPOSITIVO_MEDICO.md).

## 3. Materiale di terzi

- Non aggiungere copie di specifiche, kit MEF o repository del Ministero: si scaricano
  con `strumenti/scarica_specifiche.py`. Una nuova fonte va nel manifesto
  `strumenti/fonti_specifiche.json`, con sha256 o commit, e in `docs/TERZE_PARTI.md`.
- Il codice AGPL del gateway FSE non si copia e non si importa nella libreria: si usa
  solo come processo esterno (`tests/unit/test_licenze.py` lo controlla).

## 4. Test

```sh
python -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest -q
.venv/bin/python -m varco.conformita.esegui --famiglia offline
```

- Il numero di test superati **non deve scendere**. Un test che salta deve dire perché.
- Ogni correzione di un difetto porta un test che falliva prima.
- I test di integrazione col MEF (`VARCO_INTEGRAZIONE=1`) si lanciano solo a mano, mai
  in CI.
- Python 3.11 e 3.12 su Linux, macOS e Windows: lo verifica la CI.

## 5. Stile

- Solo libreria standard più `cryptography` per l'esercizio; una nuova dipendenza va
  motivata in `docs/ARCHITETTURA.md`.
- Ogni file Python inizia con `# SPDX-License-Identifier: EUPL-1.2`.
- Codice, commenti e documenti in italiano semplice; i nomi del tracciato restano come
  nella specifica.

## 6. Licenza dei contributi

Contribuendo accetti che il tuo contributo sia distribuito con la licenza del progetto:
**EUPL-1.2** per codice e suite di conformità, **CC-BY-4.0** per la documentazione.

---
Licenza di questo documento: CC-BY-4.0.

# SPDX-License-Identifier: EUPL-1.2
"""Dati SINTETICI di prova per il Profilo Sanitario Sintetico. Nessun dato reale.

- Codici fiscali: quelli di TEST pubblicati dal MEF nel kit prescrittore
  (medico PROVAX00X00X000Y, assistito PNIMRA70A01H501P). Nomi, indirizzi e
  recapiti sono inventati e lo dicono ("Di Prova", "Via di Prova").
- Codici clinici (ICD-9-CM, ATC, esenzioni): scelti tra quelli presenti nei
  dizionari del gateway (it-fse-catalogs, dump del 29/09/2026), solo perché
  esistono. Non descrivono un caso clinico e non sono un consiglio terapeutico.

Il PSS "completo" è costruito dagli stessi oggetti della ricetta (Prescrittore,
Assistito, Riga, diagnosi ed esenzione della Ricetta): è la prova che il
modello dati è uno solo.

Via di somministrazione e stato di ogni voce sono scritti qui in chiaro, come li
darebbe il medico: il kit non ha valori predefiniti per i dati clinici.
"""

from __future__ import annotations

import datetime as dt

from ..ricetta.modello import Assistito, Prescrittore, Ricetta, Riga, TipoPrescrizione
from .modello import (
    Allergia,
    AnamnesiFamiliare,
    Custode,
    Esenzione,
    Indirizzo,
    Medico,
    Paziente,
    Problema,
    ProfiloSanitarioSintetico,
    Stato,
    Terapia,
)

PRESCRITTORE = Prescrittore("PROVAX00X00X000Y", "130", "201", "F")
ASSISTITO = Assistito(codice_fiscale="PNIMRA70A01H501P", provincia="AQ", asl="201")

ROMA = Indirizzo("Roma", "058091", provincia="RM", codice_regione="120")
L_AQUILA = Indirizzo("L'Aquila", "066049", via="Via di Prova 1", cap="67100", provincia="AQ", codice_regione="130")

PAZIENTE = Paziente(ASSISTITO, "Paziente", "Di Prova", "M", dt.date(1970, 1, 1), luogo_nascita=ROMA, residenza=L_AQUILA)
MEDICO = Medico(PRESCRITTORE, "Medico", "Di Prova", telefono="0000000000", email="medico.di.prova@example.invalid")
CUSTODE = Custode(
    "2.16.840.1.113883.2.9.4.1.2", "130201", "ASL DI PROVA 201 (dato di test)",
    Indirizzo("L'Aquila", "066049", via="Via di Prova 2", codice_regione="130"),
)

DATA = dt.datetime(2026, 9, 30, 17, 0, 0)

# La stessa ricetta farmaceutica mandata al SAC nelle prove (principio attivo G3B)
RICETTA = Ricetta(
    PRESCRITTORE,
    ASSISTITO,
    TipoPrescrizione.FARMACEUTICA,
    [Riga(1, codice_gruppo_equivalenza="G3B", descrizione_gruppo_equivalenza="LEVETIRACETAM 500MG 60 UNITA' USO ORALE")],
    data_compilazione=DATA,
    codice_diagnosi="401.9",
    descrizione_diagnosi="Ipertensione essenziale non specificata",
    codice_esenzione="031",
)


def pss_completo(id_documento: str = "PSS-TEST-0001") -> ProfiloSanitarioSintetico:
    """Tutte e quattro le sezioni obbligatorie con voci, più le esenzioni. Terapia, problema ed
    esenzione vengono dalla ricetta."""
    return ProfiloSanitarioSintetico(
        PAZIENTE, MEDICO, CUSTODE, id_documento, DATA,
        allergie=[Allergia("Amoxicillina", "J01CA04", "ATC", "ALG", inizio=dt.date(2015, 3, 1), stato=Stato.ATTIVO)],
        terapie=[
            Terapia("Ramipril", codice_atc="C09AA05", via="PO", inizio=dt.date(2020, 1, 10), stato=Stato.ATTIVO),
            Terapia.da_riga(RICETTA.righe[0], via="PO", stato=Stato.ATTIVO, inizio=DATA.date()),
        ],
        problemi=[Problema.da_ricetta(RICETTA, stato=Stato.ATTIVO, inizio=dt.date(2020, 1, 10))],
        anamnesi_familiare=[AnamnesiFamiliare("FTH", "410.00", "Infarto miocardico acuto", sesso="M", eta_insorgenza=60)],
        esenzioni=[Esenzione.da_ricetta(RICETTA, stato=Stato.ATTIVO, inizio=dt.date(2020, 2, 1))],
    )


def pss_assenze(id_documento: str = "PSS-TEST-0002") -> ProfiloSanitarioSintetico:
    """Nessuna allergia, terapia, problema o familiarità nota: i codici di assenza IPS."""
    return ProfiloSanitarioSintetico(
        PAZIENTE, MEDICO, CUSTODE, id_documento, DATA,
        allergie_assenti="no-known-allergies",
        terapie_assenti="no-known-medications",
        problemi_assenti="no-known-problems",
        anamnesi_familiare_assente="no-known-problems",
    )

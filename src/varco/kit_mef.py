# SPDX-License-Identifier: EUPL-1.2
"""Lettura delle utenze e degli assistiti di TEST dal kit di sviluppo pubblico del MEF.

Nessuna credenziale sta nel codice: si leggono dai file del kit
(`Servizi Prescrittore/PosizioniTestMedico.xlsx`, `assistitoTest.txt`).
Percorso del kit: argomento esplicito oppure variabile VARCO_KIT_MEF.

L'xlsx è letto con la sola libreria standard (è uno zip di XML).
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .ambiente import leggi
from .credenziali import Credenziali
from .errori import ConfigurazioneNonValida
from .ricetta.modello import Prescrittore

# Utenza volutamente inesistente, usata per provare il rifiuto delle credenziali
# (non si prova mai una password sbagliata sull'utenza condivisa del kit).
UTENTE_INESISTENTE = "UTENTEINESISTENT"

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@dataclass(frozen=True)
class PosizioneTest:
    codice_fiscale: str
    regione: str
    codice_regione: str
    codice_asl: str
    codice_struttura: str | None
    codice_specializzazione: str

    def prescrittore(self) -> Prescrittore:
        return Prescrittore(
            codice_fiscale=self.codice_fiscale,
            codice_regione=self.codice_regione,
            codice_asl=self.codice_asl,
            codice_specializzazione=self.codice_specializzazione,
            codice_struttura=self.codice_struttura,
        )


def cartella_kit(percorso: str | Path | None = None) -> Path:
    p = percorso or leggi("VARCO_KIT_MEF")
    if not p:
        raise ConfigurazioneNonValida("Percorso del kit MEF non indicato (argomento o VARCO_KIT_MEF)")
    p = Path(p)
    if not (p / "Servizi Prescrittore" / "PosizioniTestMedico.xlsx").exists():
        raise ConfigurazioneNonValida(f"Kit MEF non trovato in {p}")
    return p


def _righe_xlsx(percorso: Path) -> list[list[str | None]]:
    with zipfile.ZipFile(percorso) as z:
        condivise: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            radice = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in radice.findall("m:si", _NS):
                condivise.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        foglio = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    righe = []
    for row in foglio.iter(f"{{{_NS['m']}}}row"):
        celle: dict[int, str | None] = {}
        for c in row.findall("m:c", _NS):
            rif = c.get("r", "")
            lettere = re.match(r"[A-Z]+", rif).group(0)
            col = 0
            for ch in lettere:
                col = col * 26 + (ord(ch) - 64)
            v = c.find("m:v", _NS)
            testo: str | None
            if c.get("t") == "s" and v is not None:
                testo = condivise[int(v.text)]
            elif c.get("t") == "inlineStr":
                testo = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
            else:
                testo = v.text if v is not None else None
            celle[col] = testo.strip() if isinstance(testo, str) else testo
        massimo = max(celle) if celle else 0
        righe.append([celle.get(i) for i in range(1, massimo + 1)])
    return righe


def posizioni_medico(kit: str | Path | None = None) -> list[PosizioneTest]:
    righe = _righe_xlsx(cartella_kit(kit) / "Servizi Prescrittore" / "PosizioniTestMedico.xlsx")
    out = []
    for r in righe[1:]:
        r = r + [None] * (6 - len(r))
        if not r[0]:
            continue
        out.append(PosizioneTest(r[0], r[1] or "", r[2] or "", r[3] or "", r[4] or None, r[5] or ""))
    return out


def credenziali_medico(kit: str | Path | None = None) -> Credenziali:
    """Legge userId, password e pincode in chiaro dal blocco AUTENTICAZIONE dell'xlsx."""
    valori: dict[str, str] = {}
    for r in _righe_xlsx(cartella_kit(kit) / "Servizi Prescrittore" / "PosizioniTestMedico.xlsx"):
        if len(r) >= 9 and r[7] and r[8]:
            chiave = r[7].strip().rstrip(":").strip().lower()
            valori[chiave] = str(r[8]).strip()
    try:
        return Credenziali(utente=valori["userid"], password=valori["password"], pincode=valori["pincode chiaro"])
    except KeyError as e:
        raise ConfigurazioneNonValida(f"Nel kit manca il campo {e} delle credenziali di test") from e


def assistiti_test(kit: str | Path | None = None) -> dict[str, list[str]]:
    """{'TOSCANA': ['CRLCRL81H08F032N'], 'ABRUZZO': [...], ...} dai CF di assistitoTest.txt."""
    testo = (cartella_kit(kit) / "assistitoTest.txt").read_text(encoding="latin-1")
    out: dict[str, list[str]] = {}
    regione = None
    for riga in testo.splitlines():
        riga = riga.strip()
        m = re.match(r"ASSISTIT[OI] (.+?)(?: con .*)?:?$", riga)
        if m and not riga.startswith("ASSISTITO_"):
            regione = m.group(1).rstrip(":").strip().upper()
            continue
        if regione and re.fullmatch(r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]", riga):
            out.setdefault(regione, []).append(riga)
    return out


def credenziali_sostituto(kit: str | Path | None = None) -> Credenziali:
    """Utenza del medico SOSTITUTO di test (Servizi Prescrittore/PosizioniTestMedicoSostituto.txt).

    Il file indica cfMedico, password e pincode in chiaro; l'utenza Sistema TS è il CF stesso.
    È iscritto all'albo in Abruzzo e non ha posizioni aperte con ASL: prescrive solo
    come sostituto di un titolare (specifica, par. 4.2.1).
    """
    testo = (cartella_kit(kit) / "Servizi Prescrittore" / "PosizioniTestMedicoSostituto.txt").read_text(encoding="latin-1")
    valori: dict[str, str] = {}
    for riga in testo.splitlines():
        chiave, sep, valore = riga.partition(":")
        if sep and valore.strip():
            valori[chiave.strip().lower()] = valore.strip()
    try:
        return Credenziali(utente=valori["cfmedico"], password=valori["password"], pincode=valori["pincode chiaro"])
    except KeyError as e:
        raise ConfigurazioneNonValida(f"Nel file del sostituto manca il campo {e}") from e


def identita_di_test(kit: str | Path | None = None) -> frozenset[str]:
    """Tutte le identità PUBBLICHE di test del kit: utenze medico, sostituto, CF delle
    posizioni e degli assistiti. Serve a `RegistratoreFile(identita_di_test=...)`, che
    scrive in chiaro una chiamata solo se usa esclusivamente queste identità."""
    out = {credenziali_medico(kit).utente, credenziali_sostituto(kit).utente, UTENTE_INESISTENTE}
    out |= {p.codice_fiscale for p in posizioni_medico(kit)}
    for cfs in assistiti_test(kit).values():
        out |= set(cfs)
    return frozenset(out)

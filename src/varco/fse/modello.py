# SPDX-License-Identifier: EUPL-1.2
"""Modello dati del Profilo Sanitario Sintetico (PSS), costruito SOPRA il modello della ricetta.

Non è un secondo modello parallelo: il paziente porta dentro l'`Assistito` della
ricetta, il medico porta il `Prescrittore`, una terapia si ricava da una `Riga`,
un problema dalla diagnosi di una `Ricetta`, un'esenzione dal suo codice di
esenzione. Qui si aggiunge solo quello che il tracciato SAC non ha (nome e
cognome separati, sesso, data e luogo di nascita, indirizzi codificati ISTAT) e
le sezioni cliniche del PSS.

Niente supporto clinico: il kit non decide, non suggerisce e non controlla il
merito (interazioni, dosaggi, coerenza tra diagnosi e terapie). Riporta codici e
testi scelti dal medico e controlla solo la forma richiesta dalle specifiche.

Nessun dato clinico per difetto: via di somministrazione, stato delle voci e
tipo di allergia non hanno valori predefiniti. Se il medico non li fornisce, il
kit o li omette nel modo che la specifica PSS ammette (tipo di allergia: valore
non codificato), oppure rifiuta di generare il documento con un messaggio che
dice cosa manca (via e stato: lo schematron li vuole codificati, senza
nullFlavor). Mai un valore scelto dal kit al posto del medico.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum

from ..errori import ErroreKit
from ..ricetta.modello import Assistito, Prescrittore, Ricetta, Riga


class DocumentoNonValido(ErroreKit):
    def __init__(self, problemi: list[str]):
        self.problemi = list(problemi)
        super().__init__("Documento non valido: " + "; ".join(self.problemi))


class Stato(str, Enum):
    """statusCode HL7 (ActStatus) usato dal PSS per allergie, terapie, problemi, esenzioni."""

    ATTIVO = "active"
    CONCLUSO = "completed"
    SOSPESO = "suspended"
    INTERROTTO = "aborted"

    @property
    def richiede_fine(self) -> bool:
        # schematron PSS: 'high' obbligatorio se completed|aborted, vietato se active|suspended
        return self in (Stato.CONCLUSO, Stato.INTERROTTO)


# Value set per dire "nessuna informazione" / "nessuno noto" (IPS), dal dump ufficiale dei dizionari
ASSENZA_ALLERGIE = {
    "no-known-allergies": "No known allergies",
    "no-allergy-info": "No information about allergies",
    "no-known-medication-allergies": "No known medication allergies",
    "no-known-environmental-allergies": "No known environmental allergies",
    "no-known-food-allergies": "No known food allergies",
}
ASSENZA_TERAPIE = {
    "no-known-medications": "No known medications",
    "no-medication-info": "No information about medications",
}
ASSENZA_PROBLEMI = {
    "no-known-problems": "No known problems",
    "no-problem-info": "No information about current problems",
}

SISTEMI_AGENTE = {"ATC", "AIC", "ALLERGENE"}  # schematron PSS, ERRORE-b99
# ObservationIntoleranceType come lo carica il gateway (dizionario 2.16.840.1.113883.1.11.19700, dump
# it-fse-catalogs del 29/09/2026). DALLERGY/FALLERGY/EALLERGY esistono in HL7 ma il gateway li rifiuta
# (VOCABULARY_ERROR, verificato col validatore ufficiale il 30/09/2026).
TIPI_ALLERGIA = {"ALG", "OINT", "DINT", "FINT", "EINT", "NAINT"}


@dataclass(frozen=True)
class Indirizzo:
    """Indirizzo con i codici che il PSS vuole (country/censusTract = codici ISTAT)."""

    comune: str
    codice_istat_comune: str  # censusTract, 6 cifre
    via: str | None = None
    cap: str | None = None
    provincia: str | None = None  # sigla (county)
    codice_regione: str | None = None  # state, es. "130"
    stato: str = "100"  # country: codice ISTAT dello Stato ("100" = Italia)


@dataclass(frozen=True)
class Paziente:
    """L'assistito della ricetta, più i dati anagrafici che il PSS richiede."""

    assistito: Assistito
    nome: str
    cognome: str
    sesso: str  # "M", "F" o "UN" (HL7 AdministrativeGender)
    data_nascita: _dt.date | None
    luogo_nascita: Indirizzo | None = None
    residenza: Indirizzo | None = None

    @property
    def codice_fiscale(self) -> str | None:
        return self.assistito.codice_fiscale


@dataclass(frozen=True)
class Medico:
    """Il medico autore e firmatario: il `Prescrittore` della ricetta più nome e recapiti."""

    prescrittore: Prescrittore
    nome: str
    cognome: str
    telefono: str | None = None
    email: str | None = None
    titolo: str | None = "Dott."
    ruolo: str = "MMG"  # value set assignedAuthorCode_PSSIT: MMG o PLS

    @property
    def codice_fiscale(self) -> str:
        return self.prescrittore.codice_fiscale


@dataclass(frozen=True)
class Custode:
    """Organizzazione che custodisce il documento (representedCustodianOrganization)."""

    id_radice: str  # OID dell'identificativo
    id_estensione: str
    nome: str
    indirizzo: Indirizzo | None = None
    telefono: str | None = None


@dataclass(frozen=True)
class Allergia:
    agente: str  # descrizione testuale dell'agente
    agente_codice: str | None = None
    agente_sistema: str | None = None  # "ATC", "AIC" o "ALLERGENE" (allergeni non farmaci)
    # ObservationIntoleranceType (2.16.840.1.113883.1.11.19700). None = non indicato dal medico:
    # il CDA porta un valore non codificato (nullFlavor UNK + rimando al testo), come ammette ERRORE-b80.
    tipo: str | None = None
    inizio: _dt.date | None = None
    stato: Stato | None = None  # obbligatorio (ERRORE-73): il kit non lo deduce
    fine: _dt.date | None = None
    note: str | None = None


@dataclass(frozen=True)
class Terapia:
    descrizione: str
    codice_aic: str | None = None
    codice_atc: str | None = None
    codice_gruppo_equivalenza: str | None = None
    # HL7 RouteOfAdministration (2.16.840.1.113883.5.112). Obbligatoria e codificata nel PSS
    # (ERRORE-b112 vuole routeCode con @code e @codeSystem: niente nullFlavor). Nessun valore
    # predefinito: se manca, il documento non si genera.
    via: str | None = None
    inizio: _dt.date | None = None
    stato: Stato | None = None  # obbligatorio (ERRORE-b109): il kit non lo deduce
    fine: _dt.date | None = None

    @classmethod
    def da_riga(cls, riga: Riga, *, via: str | None = None, stato: Stato | None = None,
                inizio: _dt.date | None = None, fine: _dt.date | None = None,
                codice_atc: str | None = None) -> "Terapia":
        """La stessa riga della ricetta, riportata come terapia nel PSS. Via e stato li dà il medico:
        la ricetta non li contiene e il kit non li deduce (senza, il documento non si genera)."""
        return cls(
            descrizione=riga.descrizione or riga.descrizione_gruppo_equivalenza or "",
            codice_aic=riga.codice,
            codice_atc=codice_atc,
            codice_gruppo_equivalenza=riga.codice_gruppo_equivalenza,
            via=via,
            inizio=inizio,
            stato=stato,
            fine=fine,
        )


@dataclass(frozen=True)
class Problema:
    codice_icd9: str | None  # ICD-9-CM, lo stesso sistema del campo codDiagnosi della ricetta
    descrizione: str
    inizio: _dt.date | None = None
    stato: Stato | None = None  # obbligatorio: il kit non lo deduce
    fine: _dt.date | None = None

    @classmethod
    def da_ricetta(cls, ricetta: Ricetta, *, stato: Stato | None = None, inizio: _dt.date | None = None,
                   fine: _dt.date | None = None) -> "Problema":
        return cls(codice_icd9=ricetta.codice_diagnosi, descrizione=ricetta.descrizione_diagnosi or "", inizio=inizio,
                   stato=stato, fine=fine)


@dataclass(frozen=True)
class AnamnesiFamiliare:
    parentela: str  # RoleCode HL7 (2.16.840.1.113883.5.111), es. FTH padre, MTH madre
    codice_icd9: str | None
    descrizione: str
    sesso: str | None = None
    eta_insorgenza: int | None = None


@dataclass(frozen=True)
class Esenzione:
    codice: str  # catalogo nazionale esenzioni (2.16.840.1.113883.2.9.6.1.22), come codEsenzione della ricetta
    descrizione: str | None = None
    inizio: _dt.date | None = None
    stato: Stato | None = None  # obbligatorio: il kit non lo deduce
    fine: _dt.date | None = None

    @classmethod
    def da_ricetta(cls, ricetta: Ricetta, *, stato: Stato | None = None, inizio: _dt.date | None = None,
                   fine: _dt.date | None = None) -> "Esenzione | None":
        if not ricetta.codice_esenzione:
            return None
        return cls(ricetta.codice_esenzione, inizio=inizio, stato=stato, fine=fine)


@dataclass(frozen=True)
class ProfiloSanitarioSintetico:
    """Il PSS: le quattro sezioni obbligatorie (allergie, terapie, problemi, anamnesi
    familiare) più le esenzioni. Per ogni sezione obbligatoria: o delle voci, o un
    codice di assenza ("nessuna nota", "nessuna informazione")."""

    paziente: Paziente
    autore: Medico
    custode: Custode
    id_documento: str  # extension dell'id (unica nel dominio di id_radice)
    data: _dt.datetime
    allergie: tuple[Allergia, ...] = ()
    allergie_assenti: str | None = None
    terapie: tuple[Terapia, ...] = ()
    terapie_assenti: str | None = None
    problemi: tuple[Problema, ...] = ()
    problemi_assenti: str | None = None
    anamnesi_familiare: tuple[AnamnesiFamiliare, ...] = ()
    anamnesi_familiare_assente: str | None = None
    esenzioni: tuple[Esenzione, ...] = ()
    versione: int = 1
    # Versioni successive alla prima (ERRORE-8, ERRORE-9 dello schematron): setId è l'identificativo
    # comune a tutte le versioni del documento, diverso dall'id di questa versione; il documento
    # sostituito si indica in relatedDocument (RPLC). Per la versione 1 id_set coincide con id_documento.
    id_set: str | None = None  # extension del setId; None = id_documento (solo versione 1)
    id_documento_precedente: str | None = None  # extension dell'id della versione sostituita (versione > 1)
    id_radice: str | None = None  # default: 2.16.840.1.113883.2.9.2.<regione>.4.4
    riservatezza: str = "N"  # N normale, R riservato, V molto riservato

    def __post_init__(self):
        for nome in ("allergie", "terapie", "problemi", "anamnesi_familiare", "esenzioni"):
            v = getattr(self, nome)
            if not isinstance(v, tuple):
                object.__setattr__(self, nome, tuple(v))

    @property
    def set_id(self) -> str:
        return self.id_set or self.id_documento

    @property
    def radice_id(self) -> str:
        """OID per gli id dei documenti gestiti in regione: 2.16.840.1.113883.2.9.2.[REGIONE].4.4
        (it-fse-support, integrazione-gateway: la prima cifra 0 del codice regione si omette)."""
        if self.id_radice:
            return self.id_radice
        return f"2.16.840.1.113883.2.9.2.{int(self.autore.prescrittore.codice_regione)}.4.4"

    def problemi_di_forma(self) -> list[str]:
        """Controlli di forma prima di generare il CDA. Lista vuota = ok. Nessun controllo clinico."""
        p: list[str] = []
        pz = self.paziente
        if not pz.codice_fiscale or len(pz.codice_fiscale) != 16:
            p.append("paziente: codice fiscale di 16 caratteri")
        if not pz.nome or not pz.cognome:
            p.append("paziente: nome e cognome")
        if pz.sesso not in ("M", "F", "UN"):
            p.append("paziente: sesso M, F o UN")
        if pz.data_nascita is None:
            # ERRORE-17 vuole birthTime/@value: il testo dello schematron cita nullFlavor="UNK", ma
            # l'asserzione lo rifiuta (verificato col validatore ufficiale). Il kit non inventa la data.
            p.append("paziente: data di nascita obbligatoria (ERRORE-17 vuole birthTime/@value; "
                     "nullFlavor UNK è respinto dal validatore ufficiale)")
        if not isinstance(self.versione, int) or isinstance(self.versione, bool) or self.versione < 1:
            p.append("versione: intero da 1 in su")
        elif self.versione == 1:
            if self.id_set and self.id_set != self.id_documento:
                p.append("versione 1: id_set deve coincidere con id_documento (ERRORE-8)")
            if self.id_documento_precedente:
                p.append("versione 1: non sostituisce nessun documento, id_documento_precedente va omesso")
        else:
            if not self.id_set:
                p.append(f"versione {self.versione}: serve id_set, l'identificativo comune a tutte le versioni (ERRORE-8)")
            elif self.id_set == self.id_documento:
                p.append(f"versione {self.versione}: id_documento deve essere diverso da id_set (ERRORE-8)")
            if not self.id_documento_precedente:
                p.append(f"versione {self.versione}: serve id_documento_precedente, l'id della versione "
                         "sostituita (relatedDocument RPLC, ERRORE-9)")
            elif self.id_documento_precedente == self.id_documento:
                p.append(f"versione {self.versione}: id_documento_precedente deve essere diverso da id_documento")
        if pz.residenza and not (pz.residenza.via and pz.residenza.cap):
            p.append("residenza: il PSS vuole anche via e CAP (ERRORE-11)")
        if len(self.autore.codice_fiscale) != 16:
            p.append("medico: codice fiscale di 16 caratteri")
        if not (self.autore.telefono or self.autore.email):
            p.append("medico: almeno un recapito (telefono o email, ERRORE-40)")
        if self.autore.ruolo not in ("MMG", "PLS"):
            p.append("medico: ruolo MMG o PLS")
        if self.riservatezza not in ("N", "R", "V"):
            p.append("riservatezza: N, R o V")
        for nome, voci, assenza, ammessi in (
            ("allergie", self.allergie, self.allergie_assenti, ASSENZA_ALLERGIE),
            ("terapie", self.terapie, self.terapie_assenti, ASSENZA_TERAPIE),
            ("problemi", self.problemi, self.problemi_assenti, ASSENZA_PROBLEMI),
            ("anamnesi familiare", self.anamnesi_familiare, self.anamnesi_familiare_assente, ASSENZA_PROBLEMI),
        ):
            if bool(voci) == bool(assenza):
                p.append(f"{nome}: servono delle voci OPPURE un codice di assenza (sezione obbligatoria)")
            if assenza and assenza not in ammessi:
                p.append(f"{nome}: codice di assenza non ammesso {assenza!r} (ammessi: {sorted(ammessi)})")
        for i, a in enumerate(self.allergie, 1):
            if a.agente_codice and a.agente_sistema not in SISTEMI_AGENTE:
                p.append(f"allergia {i}: sistema dell'agente tra {sorted(SISTEMI_AGENTE)}")
            if a.tipo is not None and a.tipo not in TIPI_ALLERGIA:
                p.append(f"allergia {i}: tipo non ammesso {a.tipo!r}")
        for i, t in enumerate(self.terapie, 1):
            if not (t.codice_aic or t.codice_atc or t.codice_gruppo_equivalenza):
                p.append(f"terapia {i}: serve un codice AIC, ATC o di gruppo di equivalenza (ERRORE-b114)")
            if not t.via:
                p.append(f"terapia {i}: via di somministrazione obbligatoria (codice HL7 RouteOfAdministration, "
                         "ERRORE-b112 non ammette nullFlavor): il kit non la deduce")
        for gruppo, voci in (("allergia", self.allergie), ("terapia", self.terapie), ("problema", self.problemi),
                             ("esenzione", self.esenzioni)):
            for i, v in enumerate(voci, 1):
                if not isinstance(v.stato, Stato):
                    p.append(f"{gruppo} {i}: stato obbligatorio (active, completed, suspended, aborted): "
                             "il kit non lo deduce")
                    continue
                if v.stato.richiede_fine and not v.fine:
                    p.append(f"{gruppo} {i}: stato {v.stato.value} richiede la data di fine")
                if not v.stato.richiede_fine and v.fine:
                    p.append(f"{gruppo} {i}: stato {v.stato.value} non ammette la data di fine")
        return p

    def valida(self) -> None:
        problemi = self.problemi_di_forma()
        if problemi:
            raise DocumentoNonValido(problemi)

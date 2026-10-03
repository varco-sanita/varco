# SPDX-License-Identifier: EUPL-1.2
"""Codec: `Ricetta` -> CDA2 di prescrizione della Regione Puglia (per setRegistraPrescrizione del SIST).

Fonti, tutte nelle Specifiche di integrazione SIST v4.03.27:
  - `definizione dei CDA/CDA2_Prescrizione.pdf` (Versione 03.00, Release 12, 28/12/2015);
  - `CDA2_Prescrizione_Nuovi_LEA.pdf` (repeatNumber = numero di sedute, 21/02/2024);
  - la storia delle revisioni del documento principale (4.02.32: LOINC 57833-6 e 57832-8);
  - gli esempi in `definizione dei CDA/esempi CDA/` (il più recente e valido: numSedute, 2024).

Dove la specifica e gli esempi non coincidono abbiamo scelto, e lo scriviamo in
docs/SAR_PUGLIA.md ("Discordanze"): LOINC nuovi (57833-6, 57832-8) e non 29305-0/18776-5
degli esempi del 2015 e del 2022; observation specialistica con moodCode "PRMS", come la
tabella `ObservationPrestPrescitte_it` (CDA2_Prescrizione p. 195) e l'esempio `cda_numSedute.xml`
del 2024 (gli esempi in linea della specifica, p. 196 e seguenti, dicono "RQO");
tipoAccesso 0 -> encounter "EVN", 1 -> "APT" come nella tabella della specifica.

Solo libreria standard. Nessun controllo clinico: il CDA riporta ciò che il medico ha deciso.
"""

from __future__ import annotations

import datetime as _dt
import xml.etree.ElementTree as ET

from ..errori import RicettaNonValida
from .modello import Ricetta, Riga, TipoPrescrizione, TipoVisita
from .xml_sist import codice_nazionale_asl, codice_prestazione_sist, nota_aifa_sist

HL7 = "urn:hl7-org:v3"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
ET.register_namespace("xsi", XSI)
ET.register_namespace("", HL7)  # come in fse/cda_pss.py: HL7 namespace di default

OID_TEMPLATE_PRESCRIZIONE = "2.16.840.1.113883.2.9.10.2.1"
OID_NRE = "2.16.840.1.113883.2.9.4.3.8"  # MEF, numero di ricetta elettronica
# IUP, l'identificativo univoco regionale (CDA2_Prescrizione p. 6, id_IT, e p. 19, setId_IT:
# root "2.16.840.1.113883.2.9.4.3.6", assigningAuthorityName "Regione Puglia"). La javadoc di
# chkPrescrizione dice che il campo IUP porta "l'identificativo della prescrizione (IUP/NRE)".
OID_IUP = "2.16.840.1.113883.2.9.4.3.6"
OID_CODICE_AUTENTICAZIONE = "2.16.840.1.113883.2.9.2.4.3.20"
OID_CF = "2.16.840.1.113883.2.9.4.3.2"
OID_MEDICO_PUGLIA = "2.16.840.1.113883.2.9.2.160.4.2"  # codice regionale del medico, ASL/AO
OID_ASL_NAZIONALE = "2.16.840.1.113883.2.9.4.1.1"
OID_STP_PUGLIA = "2.16.840.1.113883.2.9.2.160.4.1"
OID_TESSERA_TEAM = "2.16.840.1.113883.2.9.4.3.1"
OID_IDENT_PERSONALE_ESTERO = "2.16.840.1.113883.2.9.4.3.3"
OID_TESSERA_SASN = "2.16.840.1.113883.2.9.4.1.20"
OID_LOINC = "2.16.840.1.113883.6.1"
OID_TIPO_DOC = "2.16.840.1.113883.2.9.6.1.25"
OID_CLASSE_RICETTA = "2.16.840.1.113883.2.9.6.1.45"
OID_TIPOLOGIA_PRESCRIZIONE = "2.16.840.1.113883.2.9.6.1.46"
OID_TIPO_RICETTA = "2.16.840.1.113883.2.9.6.1.47"
OID_RISERVATEZZA = "2.16.840.1.113883.5.25"
OID_GENERE = "2.16.840.1.113883.5.1"
OID_ACTCODE = "2.16.840.1.113883.5.4"
OID_AIC = "2.16.840.1.113883.2.9.6.1.5"
OID_GRUPPO_EQUIVALENZA = "2.16.840.1.113883.2.9.6.1.51"
OID_NOTE_AIFA = "2.16.840.1.113883.2.9.6.1.99"
OID_MOTIVO_NON_SOST = "2.16.840.1.113883.2.9.6.1.52"
OID_ACT_CLASS = "2.16.840.1.113883.5.6"
OID_SOSTITUZIONE_HL7 = "2.16.840.1.113883.5.1070"
OID_PRESTAZIONI_PUGLIA = "2.16.840.1.113883.2.9.2.160.6.11"
OID_CATALOGO_PUGLIA = "2.16.840.1.113883.2.9.2.160.6.12"
OID_PRESTAZIONI_ITALIA = "2.16.840.1.113883.2.9.6.1.11"
OID_ICD9CM = "2.16.840.1.113883.6.2"  # così nella specifica pugliese (il PSS nazionale usa .6.103)
OID_PRIORITA = "2.16.840.1.113883.2.9.99"
OID_SEZIONI = "2.16.840.1.113883.2.9.10.2.1.1"
OID_ESENZIONI_PUGLIA = "2.16.840.1.113883.2.9.2.160.6.22"
OID_ESTENSIONE_ACTCODE = "2.16.840.1.113883.2.9.5.1.4"

LOINC = {TipoPrescrizione.FARMACEUTICA: "57833-6", TipoPrescrizione.SPECIALISTICA: "57832-8"}
TEMPLATE = {TipoPrescrizione.FARMACEUTICA: "ITPRF_PRESC_FARMA-001", TipoPrescrizione.SPECIALISTICA: "ITPRF_PRESC_SPEC-001"}
NOME_TIPO = {TipoPrescrizione.FARMACEUTICA: "PRESCRIZIONE FARMACEUTICA", TipoPrescrizione.SPECIALISTICA: "PRESCRIZIONE SPECIALISTICA"}
QUALIFICATORE_TIPO = {TipoPrescrizione.FARMACEUTICA: "3400-1", TipoPrescrizione.SPECIALISTICA: "3400-2"}
INCONTRO = {TipoVisita.AMBULATORIALE: "AMB", TipoVisita.DOMICILIARE: "HH"}
INDICAZIONE = {"S": "Suggerita", "H": "Ricovero"}
CLASSI_SASN = {"NA", "ND", "NE", "NX"}
PRIORITA = {"U": "URGENTE", "B": "BREVE", "D": "DIFFERIBILE", "P": "PROGRAMMATA"}
MOTIVI_NON_SOST = {"1", "2", "3", "4"}  # CDA2_Prescrizione, ObservationCodDettaglioMotivoNS_IT
# displayName è «required» (CDA2_Prescrizione, p. 162). La specifica pugliese dà UNA sola descrizione:
# "2" = «Obiettive difficoltà di assunzione» (p. 162 ed esempi CDA). Per 1, 3 e 4 non ne pubblica e le
# linee guida di altre Regioni numerano diversamente (Sardegna, DGR 13/4 del 31/03/2015: 2 = complessità
# della terapia): il kit NON inventa la corrispondenza, scrive una descrizione neutra col codice e il
# riferimento normativo. Domanda aperta a InnovaPuglia (docs/SAR_PUGLIA.md, difetti delle specifiche).
DESCRIZIONE_MOTIVI_NON_SOST = {
    "1": "Motivo di non sostituibilità 1 (art. 15, comma 11-bis, DL 95/2012)",
    "2": "Obiettive difficoltà di assunzione",
    "3": "Motivo di non sostituibilità 3 (art. 15, comma 11-bis, DL 95/2012)",
    "4": "Motivo di non sostituibilità 4 (art. 15, comma 11-bis, DL 95/2012)",
}
TIPI_ASSICURATI_ESTERI = ("UE", "EE", "NE", "NX")
# Nota Tecnica IUP (Allegati Tecnici), par. 2.1: 13 simboli di un alfabeto di 31 (dieci cifre e
# le lettere maiuscole italiane, con X al posto di O). L'NRE del MEF ha 15 caratteri.
ALFABETO_IUP = "0123456789ABCDEFGHILMNXPQRSTUVZ"


def e_iup(identificativo: str) -> bool:
    """True per uno IUP regionale (13 caratteri), False per un NRE (15). Altro: RicettaNonValida."""
    if len(identificativo) == 15 and identificativo.isascii() and identificativo.isalnum():
        return False
    if len(identificativo) == 13 and all(c in ALFABETO_IUP for c in identificativo):
        return True
    raise RicettaNonValida([f"identificativo della prescrizione né NRE (15 caratteri) né IUP (13): {identificativo!r}"])


def _q(tag: str) -> str:
    return f"{{{HL7}}}{tag}"


def _el(parent: ET.Element | None, tag: str, testo: str | None = None, **attr: str | None) -> ET.Element:
    attributi = {("{%s}type" % XSI if k == "xsi_type" else k): v for k, v in attr.items() if v is not None}
    e = ET.Element(_q(tag), attributi) if parent is None else ET.SubElement(parent, _q(tag), attributi)
    if testo is not None:
        e.text = testo
    return e


def _ts(d: _dt.datetime) -> str:
    return d.strftime("%Y%m%d%H%M%S")


def codice_regionale_prestazione(codice: str) -> str:
    """Codice regionale (nomenclatore senza punti), "in caso di lunghezza inferiore a 5
    caratteri devono essere anteposti nella stringa gli spazi necessari" (4.02.23)."""
    return codice_prestazione_sist(codice).rjust(5)


def problemi_righe_cda(ricetta: Ricetta) -> list[str]:
    """Ciò che rende impossibile scrivere il CDA e si sa PRIMA di chkPrescrizione: va controllato
    prima, perché dopo il controllo la ricetta esiste già al SAC (revisione esterna 02/10/2026)."""
    p = []
    for i, r in enumerate(ricetta.righe, start=1):
        if r.non_sostituibile and r.codice_motivazione_non_sost not in MOTIVI_NON_SOST:
            p.append(f"riga {i}: motivo di non sostituibilità ammesso 1-4 (CDA2_Prescrizione)")
    if ricetta.tipo_visita not in INCONTRO:
        p.append(f"tipo visita senza codice di incontro nel CDA: {ricetta.tipo_visita}")
    a = ricetta.assistito
    if a.tipo_ricetta in TIPI_ASSICURATI_ESTERI:
        # CDA2_Prescrizione pp. 23-25: per gli assicurati da istituzioni estere i due id (tessera TEAM e
        # identificativo personale, con la sigla della nazione) sono OBBLIGATORI (issue #3)
        mancanti = [n for n, v in (("stato_estero", a.stato_estero), ("num_ident_tessera", a.num_ident_tessera),
                                   ("num_ident_personale", a.num_ident_personale)) if not v]
        if mancanti:
            p.append(f"assicurato estero ({a.tipo_ricetta}): mancano {', '.join(mancanti)} "
                     "(id1_Estero_IT e id2_Estero_IT obbligatori, CDA2_Prescrizione pp. 23-25)")
    return p


def problemi_cda(ricetta: Ricetta, nre: str | None) -> list[str]:
    p = []
    if not nre:
        p.append("CDA SIST: serve l'NRE (o lo IUP) restituito da chkPrescrizione")
    return p + problemi_righe_cda(ricetta)


def _record_target(cd: ET.Element, ricetta: Ricetta, paziente) -> None:
    a = ricetta.assistito
    ruolo = _el(_el(cd, "recordTarget"), "patientRole")
    tipo = a.tipo_ricetta
    if tipo in TIPI_ASSICURATI_ESTERI:
        # Assicurato da istituzioni estere: l'id principale sono la tessera TEAM e l'identificativo
        # personale, SEMPRE entrambi (pp. 24-25), anche se il chiamante ha un CF: accanto all'id principale
        # possono stare solo tessera sanitaria e SASN (p. 23), quindi il CF qui non va (issue #3).
        # problemi_righe_cda() rifiuta prima di chkPrescrizione se uno dei due manca.
        stato = a.stato_estero or ""
        _el(ruolo, "id", root=OID_TESSERA_TEAM, extension=f"{stato}.{a.num_ident_tessera or ''}",
            assigningAuthorityName="SSN-MIN SALUTE-5001")
        _el(ruolo, "id", root=OID_IDENT_PERSONALE_ESTERO, extension=f"{stato}.{a.num_ident_personale or ''}",
            assigningAuthorityName=a.istituzione_competente or "SSN-MIN SALUTE-500001")
    elif a.codice_fiscale and a.codice_fiscale.upper().startswith("STP"):
        _el(ruolo, "id", root=OID_STP_PUGLIA, extension=a.codice_fiscale, assigningAuthorityName="Regione Puglia")
    elif a.codice_fiscale:
        _el(ruolo, "id", root=OID_CF, extension=a.codice_fiscale, assigningAuthorityName="MEF")
    if a.num_tessera_sasn:
        _el(ruolo, "id", root=OID_TESSERA_SASN, extension=a.num_tessera_sasn, assigningAuthorityName="Ministero della Salute")
    residenza = getattr(paziente, "residenza", None)
    if a.oscura_dati:
        # anonimato (art. 87 D.Lgs. 196/2003): elementi senza valore e nullFlavor="MSK" sul padre
        _el(ruolo, "addr", nullFlavor="MSK")
    elif residenza is not None:
        addr = _el(ruolo, "addr")
        if residenza.provincia:
            _el(addr, "county", residenza.provincia)
        _el(addr, "city", residenza.comune)
        if residenza.cap:
            _el(addr, "postalCode", residenza.cap)
        if residenza.via:
            _el(addr, "streetAddressLine", residenza.via)
    paz = _el(ruolo, "patient")
    if paziente is not None:
        if a.oscura_dati:
            nome = _el(paz, "name", nullFlavor="MSK")
            _el(nome, "given")
            _el(nome, "family")
        else:
            nome = _el(paz, "name")
            _el(nome, "given", paziente.nome)
            _el(nome, "family", paziente.cognome)
        if paziente.sesso in ("M", "F"):
            _el(paz, "administrativeGenderCode", code=paziente.sesso, codeSystem=OID_GENERE, displayName=paziente.sesso)
        if paziente.data_nascita:
            _el(paz, "birthTime", value=paziente.data_nascita.strftime("%Y%m%d"))


def _medico_ids(parent: ET.Element, cf: str, codice_regionale: str | None) -> None:
    _el(parent, "id", root=OID_CF, extension=cf, assigningAuthorityName="MEF")
    if codice_regionale is not None:
        _el(parent, "id", root=OID_MEDICO_PUGLIA, extension=codice_regionale, assigningAuthorityName="REGIONE PUGLIA")


def _nome_persona(parent: ET.Element, tag: str, persona) -> None:
    if persona is None:
        return
    p = _el(parent, tag)
    n = _el(p, "name")
    _el(n, "given", persona.nome)
    _el(n, "family", persona.cognome)


def _sezione_esenzione(corpo: ET.Element, codice: str) -> None:
    s = _el(_el(corpo, "component"), "section", ID="ESENZIONI")
    _el(s, "code", code="ESENZIONI-001", codeSystem=OID_SEZIONI, codeSystemName="ITDOCCDA_SECTIONCODE", displayName="Esenzioni")
    testo = _el(s, "text", "Esente per: ")
    _el(testo, "content", codice, ID="e1")
    act = _el(_el(s, "entry", typeCode="DRIV"), "act", classCode="ACT", moodCode="EVN")
    c = _el(act, "code", code=codice, codeSystem=OID_ESENZIONI_PUGLIA, codeSystemName="Codifica Esenzioni")
    _el(_el(c, "originalText"), "reference", value="e1")


def _testo_prescrizioni(sezione: ET.Element, ricetta: Ricetta) -> None:
    t = _el(sezione, "text")
    rqo = _el(t, "list", ID="RQO")
    _el(rqo, "caption", "Richieste:" if ricetta.tipo is TipoPrescrizione.FARMACEUTICA else "Prestazioni richieste:")
    for i, r in enumerate(ricetta.righe, start=1):
        descrizione = r.descrizione or r.descrizione_gruppo_equivalenza or r.codice or ""
        _el(_el(rqo, "item"), "content", descrizione, ID=f"a{i}")
    diag = _el(t, "list", ID="DIAG")
    _el(diag, "caption", "Problemi:")
    problema = ricetta.descrizione_diagnosi or ricetta.codice_diagnosi
    _el(_el(diag, "item"), "content", problema, ID="b1")


def _riga_farmaco(sezione: ET.Element, r: Riga, i: int) -> None:
    sa = _el(_el(sezione, "entry"), "substanceAdministration", classCode="SBADM", moodCode="RQO")
    farmaco = _el(_el(_el(sa, "consumable"), "manufacturedProduct"), "manufacturedLabeledDrug")
    if r.codice:
        codice = _el(farmaco, "code", code=r.codice, codeSystem=OID_AIC, codeSystemName="AIC", displayName="AIC")
    else:
        # art. 15 c. 11-bis DL 95/2012: solo gruppo di equivalenza -> nullFlavor="NA"
        codice = _el(farmaco, "code", nullFlavor="NA")
    _el(_el(codice, "originalText"), "reference", value=f"a{i}")
    if r.codice_gruppo_equivalenza:
        _el(codice, "translation", code=r.codice_gruppo_equivalenza, codeSystem=OID_GRUPPO_EQUIVALENZA,
            codeSystemName="Tabella gruppo di equivalenza", displayName=r.descrizione_gruppo_equivalenza)
    sp = _el(_el(sa, "entryRelationship", typeCode="COMP"), "supply", classCode="SPLY", moodCode="RQO")
    _el(sp, "independentInd", value="false")
    _el(sp, "quantity", unit="1", value=str(r.quantita))
    if r.non_sostituibile:
        obs = _el(_el(sa, "entryRelationship", typeCode="SUBJ", inversionInd="true"), "observation",
                  classCode="OBS", moodCode="EVN")
        _el(obs, "code", code="SUBST", codeSystem=OID_ACT_CLASS, codeSystemName="HL7 ActClass", displayName="Substitution")
        _el(obs, "value", xsi_type="CE", codeSystem=OID_SOSTITUZIONE_HL7, codeSystemName="HL7Substance Admin Substitution")
        motivo = _el(_el(obs, "entryRelationship", typeCode="RSON"), "observation", classCode="OBS", moodCode="EVN")
        _el(motivo, "code", code=r.codice_motivazione_non_sost, codeSystem=OID_MOTIVO_NON_SOST,
            displayName=DESCRIZIONE_MOTIVI_NON_SOST[r.codice_motivazione_non_sost])  # required, p. 162 (issue #4)
    nota = nota_aifa_sist(r.nota_aifa)
    if nota:
        act = _el(_el(sa, "entryRelationship", typeCode="REFR"), "act", classCode="ACT", moodCode="EVN")
        _el(act, "code", code=nota, codeSystem=OID_NOTE_AIFA, codeSystemName="Note Aifa")
    if r.note:
        act = _el(_el(sa, "entryRelationship", typeCode="COMP"), "act", classCode="ACT", moodCode="EVN")
        _el(act, "code", code="motivazNote", codeSystem=OID_ESTENSIONE_ACTCODE, codeSystemVersion="1",
            codeSystemName="Estensione Vocabolario ActCode", displayName="Note sulle motivazioni")
        _el(act, "text", r.note)


def _riga_prestazione(sezione: ET.Element, r: Riga, i: int, ricetta: Ricetta) -> None:
    obs = _el(_el(sezione, "entry"), "observation", classCode="OBS", moodCode="PRMS")
    codice = _el(obs, "code", code=codice_regionale_prestazione(r.codice or ""), codeSystem=OID_PRESTAZIONI_PUGLIA,
                 codeSystemName="Catalogo Prestazioni della regione Puglia", codeSystemVersion="-")
    _el(_el(codice, "originalText"), "reference", value=f"a{i}")
    if r.codice_catalogo:
        _el(_el(codice, "qualifier"), "value", code=r.codice_catalogo, codeSystem=OID_CATALOGO_PUGLIA,
            codeSystemName="Catalogo prestazioni della Regione Puglia")
    if r.codice and "." in r.codice:
        _el(codice, "translation", code=r.codice, codeSystem=OID_PRESTAZIONI_ITALIA, codeSystemName="Catalogo Prestazioni Italia")
    if r.num_sedute:
        _el(obs, "repeatNumber", value=str(r.num_sedute))
    sp = _el(_el(obs, "entryRelationship", typeCode="COMP"), "supply", classCode="SPLY", moodCode="RQO")
    _el(sp, "independentInd", value="false")
    _el(sp, "quantity", unit="1", value=str(r.quantita))
    if ricetta.codice_diagnosi or ricetta.descrizione_diagnosi:
        problema = _el(_el(obs, "entryRelationship", typeCode="RSON"), "observation", classCode="OBS", moodCode="EVN")
        _el(problema, "code", code=ricetta.codice_diagnosi, codeSystem=OID_ICD9CM, codeSystemName="ICD9-CM",
            codeSystemVersion="2002")
        if ricetta.descrizione_diagnosi:
            _el(problema, "text", ricetta.descrizione_diagnosi)
    if r.tipo_accesso is not None:
        enc = _el(_el(obs, "entryRelationship", typeCode="SAS"), "encounter", classCode="ENC",
                  moodCode="EVN" if r.tipo_accesso == "0" else "APT")
        _el(enc, "code", code="19974", codeSystem=OID_ACTCODE, codeSystemName="ActEncounterCode")
    if r.note_prestazione:
        act = _el(_el(obs, "entryRelationship", typeCode="SUBJ", inversionInd="true"), "act", classCode="ACT", moodCode="EVN")
        _el(act, "code", code="48767-8", codeSystem=OID_LOINC, codeSystemName="LOINC", codeSystemVersion="2.19",
            displayName="Annotation Comment")
        _el(act, "text", r.note_prestazione)


def genera(
    ricetta: Ricetta,
    nre: str,
    codice_autenticazione: str | None,
    *,
    codice_regionale_prescrittore: str,
    codice_regionale_sostituito: str | None = None,
    paziente=None,
    medico=None,
    maggior_tutela: bool = False,
    creato: _dt.datetime | None = None,
) -> ET.Element:
    """Il CDA2 di prescrizione, con l'NRE e il codice di autenticazione di chkPrescrizione.

    `paziente`: facoltativo, `fse.modello.Paziente` (nome, sesso, nascita, residenza).
    `medico`: facoltativo, `fse.modello.Medico` (nome e cognome del prescrittore).
    `maggior_tutela`: confidentialityCode "V" (dati soggetti a maggior tutela dell'anonimato).
    `creato`: effectiveTime; il SIST controlla che la firma sia dello stesso giorno e successiva (000303, 000304).
    """
    problemi = problemi_cda(ricetta, nre)
    if problemi:
        raise RicettaNonValida(problemi)
    creato = creato or ricetta.data_compilazione
    pr, a = ricetta.prescrittore, ricetta.assistito
    sostituto = pr.codice_fiscale_sostituto
    autore_cf = sostituto or pr.codice_fiscale

    cd = _el(None, "ClinicalDocument")
    _el(cd, "realmCode", code="IT")
    _el(cd, "typeId", root="2.16.840.1.113883.1.3", extension="POCD_MT000040")
    _el(cd, "templateId", root=OID_TEMPLATE_PRESCRIZIONE, extension=TEMPLATE[ricetta.tipo])
    iup = e_iup(nre)
    if iup:  # CDA2_Prescrizione p. 6, "Esempio: di tag id avvalorato con un IUP"
        _el(cd, "id", root=OID_IUP, extension=nre, assigningAuthorityName="Regione Puglia")
    else:  # p. 6, "Esempio di tag id avvalorato con un NRE"
        _el(cd, "id", root=OID_NRE, extension=nre, assigningAuthorityName="MEF")
    code = _el(cd, "code", code=LOINC[ricetta.tipo], codeSystem=OID_LOINC, codeSystemName="LOINC",
               codeSystemVersion="2.19", displayName=NOME_TIPO[ricetta.tipo])
    tr = _el(code, "translation", code="3400", codeSystem=OID_TIPO_DOC, codeSystemName="ITCDADOC_TYPECODE",
             codeSystemVersion="1", displayName="Prescrizione")
    _el(_el(tr, "qualifier"), "value", code=QUALIFICATORE_TIPO[ricetta.tipo], codeSystem=OID_TIPO_DOC,
        codeSystemName="ITCDADOC_TYPECODE", codeSystemVersion="1", displayName=NOME_TIPO[ricetta.tipo])
    sasn = a.tipo_ricetta in CLASSI_SASN
    tr2 = _el(code, "translation", code="SASN" if sasn else "SSN", codeSystem=OID_CLASSE_RICETTA,
              codeSystemName="ITCDADOC_TYPECODE", displayName="RICETTA SASN" if sasn else "RICETTA SSN")
    if ricetta.indicazione in INDICAZIONE:
        _el(_el(tr2, "qualifier"), "value", code=ricetta.indicazione, codeSystem=OID_TIPOLOGIA_PRESCRIZIONE,
            codeSystemName="Tipologia di prescrizione", displayName=INDICAZIONE[ricetta.indicazione])
    _el(_el(tr2, "qualifier"), "value", code=a.tipo_ricetta or "IT", codeSystem=OID_TIPO_RICETTA, codeSystemName="Tipo ricetta")
    _el(cd, "effectiveTime", value=_ts(creato))
    _el(cd, "confidentialityCode", code="V" if maggior_tutela else "N", codeSystem=OID_RISERVATEZZA,
        codeSystemName="Confidentiality")
    _el(cd, "languageCode", code="it-IT")
    # CDA2_Prescrizione p. 19 (setId_IT): tre esempi, uno per caso
    if codice_autenticazione:
        _el(cd, "setId", root=OID_CODICE_AUTENTICAZIONE, extension=codice_autenticazione, assigningAuthorityName="MEF")
    elif iup:  # "setId avvalorato con un IUP": alla prima creazione setId e id coincidono
        _el(cd, "setId", root=OID_IUP, extension=nre, assigningAuthorityName="Regione Puglia")
    else:  # "setId avvalorato con un NRE"
        _el(cd, "setId", root=OID_CODICE_AUTENTICAZIONE, extension=nre, assigningAuthorityName="MEF")
    _el(cd, "versionNumber", value="1")
    _record_target(cd, ricetta, paziente)

    autore = _el(cd, "author")
    _el(autore, "time", value=_ts(creato))
    assegnato = _el(autore, "assignedAuthor")
    _medico_ids(assegnato, autore_cf, codice_regionale_prescrittore)
    if medico is not None and not sostituto:
        _nome_persona(assegnato, "assignedPerson", medico)
    custode = _el(_el(_el(cd, "custodian"), "assignedCustodian"), "representedCustodianOrganization")
    _el(custode, "id", root=OID_MEDICO_PUGLIA, extension=codice_nazionale_asl(pr.codice_regione, pr.codice_asl))
    la = _el(cd, "legalAuthenticator")
    _el(la, "time", value=_ts(creato))
    _el(la, "signatureCode", code="S")
    _medico_ids(_el(la, "assignedEntity"), autore_cf, None)

    asl = codice_nazionale_asl(a.codice_regione, a.asl)
    if asl:
        ente = _el(_el(_el(cd, "participant", typeCode="IND"), "associatedEntity", classCode="GUAR"), "scopingOrganization")
        _el(ente, "id", root=OID_ASL_NAZIONALE, extension=asl, assigningAuthorityName="SSN-MIN SALUTE-500001",
            displayable="true")
    if sostituto:
        ent = _el(_el(cd, "participant", typeCode="IND"), "associatedEntity", classCode="LIC")
        _medico_ids(ent, pr.codice_fiscale, codice_regionale_sostituito)
    if a.societa_navigazione:
        ent = _el(_el(cd, "participant", typeCode="IND"), "associatedEntity", classCode="CON")
        _el(ent, "code", code="EMPLOYER", codeSystem="1.3.5.1.4.1.19376.1.5.3.3", codeSystemName="IHERoleCode")
        _el(_el(ent, "scopingOrganization"), "name", a.societa_navigazione)
    if a.data_scadenza_tessera and a.tipo_ricetta in ("UE", "EE", "NE", "NX"):
        part = _el(cd, "participant", typeCode="IND")
        _el(part, "functionCode", code="FULINRD", codeSystem="2.16.840.1.113883.5.88", codeSystemName="ParticipationFunction")
        tempo = _el(part, "time")
        _el(tempo, "low", nullFlavor="UNK")
        _el(tempo, "high", value=a.data_scadenza_tessera.replace("-", ""))
        org = _el(_el(part, "associatedEntity", classCode="GUAR"), "scopingOrganization")
        if a.istituzione_competente:
            _el(org, "name", a.istituzione_competente)
        if a.stato_estero:
            _el(_el(org, "addr"), "country", a.stato_estero)

    incontro = _el(_el(cd, "componentOf"), "encompassingEncounter")
    _el(incontro, "code", code=INCONTRO[ricetta.tipo_visita], codeSystem=OID_ACTCODE)
    _el(incontro, "effectiveTime", value=_ts(creato))

    corpo = _el(_el(cd, "component"), "structuredBody")
    if ricetta.codice_esenzione:
        _sezione_esenzione(corpo, ricetta.codice_esenzione)
    sezione = _el(_el(corpo, "component"), "section", ID="PRESCRIZIONI")
    _el(sezione, "code", code=LOINC[ricetta.tipo], codeSystem=OID_LOINC, codeSystemName="LOINC", codeSystemVersion="2.19",
        displayName="Medication Prescribed")
    _testo_prescrizioni(sezione, ricetta)
    for i, r in enumerate(ricetta.righe, start=1):
        if ricetta.tipo is TipoPrescrizione.FARMACEUTICA:
            _riga_farmaco(sezione, r, i)
        else:
            _riga_prestazione(sezione, r, i, ricetta)
    if ricetta.tipo is TipoPrescrizione.SPECIALISTICA and ricetta.classe_priorita:
        s = _el(_el(corpo, "component"), "section", ID="PRIORITA")
        proc = _el(_el(s, "entry"), "procedure", classCode="PROC", moodCode="PRMS")
        v = ricetta.classe_priorita.value
        _el(proc, "priorityCode", code=v, codeSystem=OID_PRIORITA, codeSystemName="Priorita' della prescrizione",
            codeSystemVersion="1", displayName=PRIORITA[v])
    if ricetta.disposizioni_regionali:
        s = _el(_el(corpo, "component"), "section")
        _el(s, "code", code="48767-8", codeSystem=OID_LOINC, codeSystemName="LOINC", codeSystemVersion="2.19",
            displayName="Annotation Comment")
        _el(s, "title", "Annotazioni")
        _el(_el(s, "text"), "content", ricetta.disposizioni_regionali, ID="dispReg")
        act = _el(_el(s, "entry"), "act", moodCode="EVN", classCode="ACT")
        _el(act, "code", code="DispReg", codeSystem=OID_ESTENSIONE_ACTCODE, codeSystemName="Estensione Vocabolario ActCode",
            displayName="Disposizioni regionali")
        _el(_el(act, "text"), "reference", value="#dispReg")
    return cd


def genera_xml(ricetta: Ricetta, nre: str, codice_autenticazione: str | None, **kw) -> bytes:
    return ET.tostring(genera(ricetta, nre, codice_autenticazione, **kw), encoding="utf-8", xml_declaration=True)


# ------------------------------------------------------------------ lettura (getPrescrizioneIdentificata)


def righe_dal_cda(cda: str | bytes) -> tuple[dict[str, str], ...]:
    """Le righe di un CDA di prescrizione (il SIST lo restituisce in chiaro in cdaInstance).

    Restituisce, per ogni voce: codice, sistema (OID), descrizione (dal testo narrativo),
    quantita, e per le prestazioni codice_catalogo e num_sedute.
    """
    radice = ET.fromstring(cda.encode("utf-8") if isinstance(cda, str) else cda)
    testi = {c.get("ID"): "".join(c.itertext()).strip() for c in radice.iter(_q("content")) if c.get("ID")}
    righe = []
    for entry in radice.iter(_q("entry")):
        voce = entry.find(_q("substanceAdministration"))
        if voce is not None:
            codice = voce.find(f"{_q('consumable')}/{_q('manufacturedProduct')}/{_q('manufacturedLabeledDrug')}/{_q('code')}")
        else:
            voce = entry.find(_q("observation"))
            if voce is None or voce.get("moodCode") == "EVN":
                continue
            codice = voce.find(_q("code"))
        if codice is None:
            continue
        riga: dict[str, str] = {}
        if codice.get("code"):
            riga["codice"] = codice.get("code").strip()
            riga["sistema"] = codice.get("codeSystem", "")
        ge = codice.find(_q("translation"))
        if ge is not None and ge.get("codeSystem") == OID_GRUPPO_EQUIVALENZA:
            riga["gruppo_equivalenza"] = ge.get("code", "")
        rif = codice.find(f"{_q('originalText')}/{_q('reference')}")
        if rif is not None and testi.get((rif.get("value") or "").lstrip("#")):
            riga["descrizione"] = testi[(rif.get("value") or "").lstrip("#")]
        cat = codice.find(f"{_q('qualifier')}/{_q('value')}")
        if cat is not None and cat.get("codeSystem") == OID_CATALOGO_PUGLIA:
            riga["codice_catalogo"] = cat.get("code", "")
        q = voce.find(f"{_q('entryRelationship')}/{_q('supply')}/{_q('quantity')}")
        if q is not None and q.get("value"):
            riga["quantita"] = q.get("value")
        rip = voce.find(_q("repeatNumber"))
        if rip is not None and rip.get("value"):
            riga["num_sedute"] = rip.get("value")
        righe.append(riga)
    return tuple(righe)

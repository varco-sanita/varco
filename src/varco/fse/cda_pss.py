# SPDX-License-Identifier: EUPL-1.2
"""Codec: ProfiloSanitarioSintetico -> CDA2 HL7 Italia (Profilo Sanitario Sintetico).

Qui, e solo qui, stanno OID, templateId, codici LOINC e ordine degli elementi.
Fonti pubbliche: schematron ufficiale del gateway FSE 2.0
(it-fse-catalogs/schematron/schematron_PSS_v4.0.sch), schema CDA POCD_MT000040UV02
(it-fse-catalogs/schema), esempi validi pubblicati dal Ministero
(it-fse-support/doc/esempi/CDA/PSS.xml, it-fse-accreditamento PSS476/477).

Solo libreria standard. Gli identificativi delle voci (id delle entry) sono UUID:
lo schema CDA li ammette come radice (tipo uid).

Nessun dato clinico aggiunto: ogni codice, data o stato scritto nel CDA viene dal
modello. Quando un dato non c'è si usa la forma "non noto" che lo schematron
ammette (nullFlavor UNK su date e tipo di allergia), mai un valore plausibile.
"""

from __future__ import annotations

import datetime as _dt
import uuid
import xml.etree.ElementTree as ET

from .modello import (
    ASSENZA_ALLERGIE,
    ASSENZA_PROBLEMI,
    ASSENZA_TERAPIE,
    Allergia,
    AnamnesiFamiliare,
    Esenzione,
    Indirizzo,
    Medico,
    Problema,
    ProfiloSanitarioSintetico,
    Stato,
    Terapia,
)

HL7 = "urn:hl7-org:v3"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
ET.register_namespace("xsi", XSI)
# HL7 come namespace di default nel documento (xmlns="urn:hl7-org:v3"), come negli esempi ufficiali.
# default_namespace= di tostring non si può usare: rifiuta gli attributi senza namespace.
ET.register_namespace("", HL7)

# --- OID e codici (fonte: schematron PSS v4.0 e dizionari del gateway)
OID_CF = "2.16.840.1.113883.2.9.4.3.2"  # codice fiscale (MEF)
OID_LOINC = "2.16.840.1.113883.6.1"
OID_ICD9CM = "2.16.840.1.113883.6.103"
OID_ATC = "2.16.840.1.113883.6.73"
OID_AIC = "2.16.840.1.113883.2.9.6.1.5"
OID_GE = "2.16.840.1.113883.2.9.6.1.51"  # gruppi di equivalenza
OID_ALLERGENI = "2.16.840.1.113883.2.9.77.22.11.2"  # allergeni non farmaci
OID_GENERE = "2.16.840.1.113883.5.1"
OID_RISERVATEZZA = "2.16.840.1.113883.5.25"
OID_RUOLO_AUTORE = "2.16.840.1.113883.2.9.77.22.11.13"  # assignedAuthorCode_PSSIT
OID_VIA = "2.16.840.1.113883.5.112"  # RouteOfAdministration
OID_PARENTELA = "2.16.840.1.113883.5.111"  # RoleCode
OID_TIPO_INTOLLERANZA = "2.16.840.1.113883.1.11.19700"
OID_ACTCODE = "2.16.840.1.113883.5.4"
OID_ESENZIONI = "2.16.840.1.113883.2.9.6.1.22"
OID_ASSENZA_ALLERGIE = "2.16.840.1.113883.11.22.9"
OID_ASSENZA_TERAPIE = "2.16.840.1.113883.11.22.15"
OID_ASSENZA_PROBLEMI = "2.16.840.1.113883.11.22.17"
OID_ASSENZA_ANAMNESI_CODE = "2.16.840.1.113883.2.9.77.22.11.9"

TEMPLATE_PSS = "2.16.840.1.113883.2.9.10.1.4.1.1"
# Versione del templateId: quella degli esempi di accreditamento validati con lo schematron 4.0
# (PSS476/PSS477, checklist 8.2.7). Lo schematron richiede solo che l'extension ci sia (ERRORE-4).
VERSIONE_TEMPLATE_PSS = "1.4"

SISTEMA_AGENTE = {"ATC": (OID_ATC, "ATC"), "AIC": (OID_AIC, "AIC"), "ALLERGENE": (OID_ALLERGENI, "Allergeni (No Farmaci)")}
DESCRIZIONE_GENERE = {"M": "Maschio", "F": "Femmina", "UN": "Non differenziato"}


def _q(tag: str) -> str:
    return f"{{{HL7}}}{tag}"


def _el(parent: ET.Element | None, tag: str, testo: str | None = None, **attr) -> ET.Element:
    attr = {("{%s}type" % XSI if k == "xsi_type" else k): v for k, v in attr.items() if v is not None}
    e = ET.Element(_q(tag), attr) if parent is None else ET.SubElement(parent, _q(tag), attr)
    if testo is not None:
        e.text = testo
    return e


def ts_datetime(d: _dt.datetime) -> str:
    """TS HL7 con fuso: AAAAMMGGhhmmss+hhmm. Senza fuso si assume Europe/Rome."""
    if d.tzinfo is None:
        try:
            from zoneinfo import ZoneInfo

            d = d.replace(tzinfo=ZoneInfo("Europe/Rome"))
        except Exception:
            d = d.astimezone()
    return d.strftime("%Y%m%d%H%M%S%z")


def ts_data(d: _dt.date) -> str:
    return d.strftime("%Y%m%d")


def _uid() -> str:
    return str(uuid.uuid4())


def _id(parent: ET.Element) -> None:
    _el(parent, "id", root=_uid())


def _indirizzo(parent: ET.Element, ind: Indirizzo, uso: str | None = None) -> ET.Element:
    a = _el(parent, "addr", use=uso)
    _el(a, "country", ind.stato)
    if ind.codice_regione:
        _el(a, "state", ind.codice_regione)
    if ind.provincia:
        _el(a, "county", ind.provincia)
    _el(a, "city", ind.comune)
    _el(a, "censusTract", ind.codice_istat_comune)
    if ind.cap:
        _el(a, "postalCode", ind.cap)
    if ind.via:
        _el(a, "streetAddressLine", ind.via)
    return a


def _telecom(parent: ET.Element, telefono: str | None, email: str | None, uso: str = "WP") -> None:
    if telefono:
        _el(parent, "telecom", use=uso, value=f"tel:{telefono}")
    if email:
        _el(parent, "telecom", use=uso, value=f"mailto:{email}")


def _nome_persona(parent: ET.Element, nome: str, cognome: str, titolo: str | None = None) -> None:
    n = _el(parent, "name")
    _el(n, "family", cognome)
    _el(n, "given", nome)
    if titolo:
        _el(n, "prefix", titolo)


def _entita_medico(parent: ET.Element, m: Medico) -> None:
    _el(parent, "id", root=OID_CF, extension=m.codice_fiscale, assigningAuthorityName="MEF")
    _telecom(parent, m.telefono, m.email)
    p = _el(parent, "assignedPerson")
    _nome_persona(p, m.nome, m.cognome, m.titolo)


def _stato(parent: ET.Element, stato: Stato | None) -> None:
    # Senza stato (solo con valida=False) niente statusCode: il kit non ne sceglie uno.
    if stato is not None:
        _el(parent, "statusCode", code=stato.value)


def _intervallo(parent: ET.Element, inizio: _dt.date | None, stato: Stato | None, fine: _dt.date | None) -> None:
    et = _el(parent, "effectiveTime")
    if inizio:
        _el(et, "low", value=ts_data(inizio))
    else:
        _el(et, "low", nullFlavor="UNK")
    if stato is not None and stato.richiede_fine and fine:
        _el(et, "high", value=ts_data(fine))


class _Narrativa:
    """Blocco narrativo di una sezione: una lista di voci con ID, a cui le entry rimandano."""

    def __init__(self, sezione: ET.Element, prefisso: str):
        self.testo = _el(sezione, "text")
        self.lista = _el(self.testo, "list")
        self.prefisso = prefisso
        self.n = 0

    def voce(self, testo: str) -> str:
        self.n += 1
        ident = f"{self.prefisso}-{self.n}"
        item = _el(self.lista, "item")
        _el(item, "content", testo, ID=ident)
        return f"#{ident}"


def _sezione(body: ET.Element, template: str, codice: str, nome_codice: str, titolo: str, prefisso: str):
    comp = _el(body, "component", typeCode="COMP")
    sez = _el(comp, "section", ID=prefisso.upper())
    _el(sez, "templateId", root=template)
    _el(sez, "id", root=_uid())
    _el(sez, "code", code=codice, codeSystem=OID_LOINC, codeSystemName="LOINC", displayName=nome_codice)
    _el(sez, "title", titolo)
    return sez, _Narrativa(sez, prefisso)


def _riferimento(parent: ET.Element, rif: str) -> None:
    t = _el(parent, "text")
    _el(t, "reference", value=rif)


# ------------------------------------------------------------------ sezioni


def _sezione_allergie(body: ET.Element, pss: ProfiloSanitarioSintetico) -> None:
    sez, narr = _sezione(body, "2.16.840.1.113883.2.9.10.1.4.2.1", "48765-2", "Allergie, Reazioni Avverse",
                         "Allergie e Intolleranze", "all")
    if pss.allergie_assenti:
        rif = narr.voce(ASSENZA_ALLERGIE[pss.allergie_assenti])
        entry = _el(sez, "entry")
        act = _el(entry, "act", classCode="ACT", moodCode="EVN")
        _el(act, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.1.1")
        _id(act)
        _el(act, "code", nullFlavor="NA")
        _el(act, "statusCode", code="active")
        # da quando valga l'assenza non lo dice il medico: non noto (ERRORE-b74), non la data del documento
        et = _el(act, "effectiveTime")
        _el(et, "low", nullFlavor="UNK")
        er = _el(act, "entryRelationship", typeCode="SUBJ")
        obs = _el(er, "observation", classCode="OBS", moodCode="EVN")
        _el(obs, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.1.4")
        _id(obs)
        _el(obs, "code", code="OINT", codeSystem=OID_ACTCODE, codeSystemName="ObservationIntoleranceType",
            displayName="Intolerance")
        _riferimento(obs, rif)
        _el(obs, "statusCode", code="completed")
        et = _el(obs, "effectiveTime")
        _el(et, "low", nullFlavor="UNK")
        _el(obs, "value", xsi_type="CD", code=pss.allergie_assenti, codeSystem=OID_ASSENZA_ALLERGIE,
            codeSystemName="Absent or Unknown Allergies", displayName=ASSENZA_ALLERGIE[pss.allergie_assenti])
        return
    for a in pss.allergie:
        _allergia(sez, narr, a)


def _allergia(sez: ET.Element, narr: _Narrativa, a: Allergia) -> None:
    rif = narr.voce(a.agente + (f" - {a.note}" if a.note else ""))
    entry = _el(sez, "entry")
    act = _el(entry, "act", classCode="ACT", moodCode="EVN")
    _el(act, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.1.1")
    _id(act)
    _el(act, "code", nullFlavor="NA")
    _stato(act, a.stato)
    _intervallo(act, a.inizio, a.stato, a.fine)
    er = _el(act, "entryRelationship", typeCode="SUBJ")
    obs = _el(er, "observation", classCode="OBS", moodCode="EVN")
    _el(obs, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.1.3")
    _id(obs)
    _el(obs, "code", code="52473-6", codeSystem=OID_LOINC, codeSystemName="LOINC",
        displayName="Allergia o causa della reazione")
    _riferimento(obs, rif)
    _el(obs, "statusCode", code="completed")
    et = _el(obs, "effectiveTime")
    if a.inizio:
        _el(et, "low", value=ts_data(a.inizio))
    else:
        _el(et, "low", nullFlavor="UNK")
    if a.fine:
        _el(et, "high", value=ts_data(a.fine))
    if a.tipo:
        _el(obs, "value", xsi_type="CD", code=a.tipo, codeSystem=OID_TIPO_INTOLLERANZA,
            codeSystemName="ObservationIntoleranceType")
    else:
        # tipo non indicato dal medico: valore non codificato con rimando al testo (ERRORE-b80)
        v = _el(obs, "value", xsi_type="CD", nullFlavor="UNK")
        ot = _el(v, "originalText")
        _el(ot, "reference", value=rif)
    part = _el(obs, "participant", typeCode="CSM")
    ruolo = _el(part, "participantRole", classCode="MANU")
    ent = _el(ruolo, "playingEntity", classCode="MMAT")
    if a.agente_codice:
        oid, nome_sistema = SISTEMA_AGENTE[a.agente_sistema]
        cod = _el(ent, "code", code=a.agente_codice, codeSystem=oid, codeSystemName=nome_sistema, displayName=a.agente)
    else:
        cod = _el(ent, "code", nullFlavor="NI")
    ot = _el(cod, "originalText")
    _el(ot, "reference", value=rif)


def _sezione_terapie(body: ET.Element, pss: ProfiloSanitarioSintetico) -> None:
    sez, narr = _sezione(body, "2.16.840.1.113883.2.9.10.1.4.2.2", "10160-0", "HISTORY OF MEDICATION USE",
                         "Terapie farmacologiche", "ter")
    if pss.terapie_assenti:
        rif = narr.voce(ASSENZA_TERAPIE[pss.terapie_assenti])
        entry = _el(sez, "entry")
        sa = _el(entry, "substanceAdministration", classCode="SBADM", moodCode="EVN")
        _el(sa, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.2.3")
        _id(sa)
        _el(sa, "code", code=pss.terapie_assenti, codeSystem=OID_ASSENZA_TERAPIE, codeSystemName="IPSNoMedsInfo",
            displayName=ASSENZA_TERAPIE[pss.terapie_assenti])
        _riferimento(sa, rif)
        # consumable è obbligatorio per lo schema CDA (SubstanceAdministration): prodotto "non applicabile"
        cons = _el(sa, "consumable")
        mp = _el(cons, "manufacturedProduct")
        mm = _el(mp, "manufacturedMaterial")
        _el(mm, "code", nullFlavor="NA")
        return
    for t in pss.terapie:
        _terapia(sez, narr, t)


def _terapia(sez: ET.Element, narr: _Narrativa, t: Terapia) -> None:
    rif = narr.voce(t.descrizione)
    entry = _el(sez, "entry")
    sa = _el(entry, "substanceAdministration", classCode="SBADM", moodCode="EVN")
    _el(sa, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.2.1")
    _id(sa)
    _riferimento(sa, rif)
    _stato(sa, t.stato)
    et = _el(sa, "effectiveTime", xsi_type="IVL_TS")
    if t.inizio:
        _el(et, "low", value=ts_data(t.inizio))
    else:
        _el(et, "low", nullFlavor="UNK")
    if t.stato is not None and t.stato.richiede_fine and t.fine:
        _el(et, "high", value=ts_data(t.fine))
    if t.via:  # obbligatoria (ERRORE-b112): senza, genera() con valida=True rifiuta; qui non si inventa
        _el(sa, "routeCode", code=t.via, codeSystem=OID_VIA, codeSystemName="HL7 RouteOfAdministration")
    cons = _el(sa, "consumable")
    mp = _el(cons, "manufacturedProduct", classCode="MANU")
    _el(mp, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.2.2")
    mm = _el(mp, "manufacturedMaterial")
    _el(mm, "templateId", root="2.16.840.1.113883.10.22.4.3")
    codici = [(t.codice_aic, OID_AIC, "AIC"), (t.codice_atc, OID_ATC, "ATC"), (t.codice_gruppo_equivalenza, OID_GE, "GE")]
    codici = [c for c in codici if c[0]]
    principale, *traduzioni = codici
    cod = _el(mm, "code", code=principale[0], codeSystem=principale[1], codeSystemName=principale[2],
              displayName=t.descrizione)
    ot = _el(cod, "originalText")
    _el(ot, "reference", value=rif)
    for codice, oid, nome in traduzioni:
        _el(cod, "translation", code=codice, codeSystem=oid, codeSystemName=nome)


def _sezione_problemi(body: ET.Element, pss: ProfiloSanitarioSintetico) -> None:
    sez, narr = _sezione(body, "2.16.840.1.113883.2.9.10.1.4.2.4", "11450-4", "Lista dei Problemi",
                         "Lista dei problemi", "pro")
    if pss.problemi_assenti:
        rif = narr.voce(ASSENZA_PROBLEMI[pss.problemi_assenti])
        entry = _el(sez, "entry")
        act = _el(entry, "act", classCode="ACT", moodCode="EVN")
        _el(act, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.4.1")
        _id(act)
        _el(act, "code", nullFlavor="NA")
        _el(act, "statusCode", code="active")
        et = _el(act, "effectiveTime")
        _el(et, "low", nullFlavor="UNK")  # come per le allergie: l'inizio dell'assenza non è noto (ERRORE-b151)
        er = _el(act, "entryRelationship", typeCode="SUBJ", inversionInd="false")
        obs = _el(er, "observation", classCode="OBS", moodCode="EVN")
        _el(obs, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.4.2")
        _id(obs)
        _el(obs, "code", code="75326-9", codeSystem=OID_LOINC, codeSystemName="LOINC", displayName="Problema")
        _riferimento(obs, rif)
        _el(obs, "statusCode", code="completed")
        et = _el(obs, "effectiveTime")
        _el(et, "low", nullFlavor="UNK")
        _el(obs, "value", xsi_type="CD", code=pss.problemi_assenti, codeSystem=OID_ASSENZA_PROBLEMI,
            codeSystemName="IPSNoProbsInfos", displayName=ASSENZA_PROBLEMI[pss.problemi_assenti])
        return
    for p in pss.problemi:
        _problema(sez, narr, p)


def _problema(sez: ET.Element, narr: _Narrativa, p: Problema) -> None:
    rif = narr.voce(p.descrizione + (f" ({p.codice_icd9})" if p.codice_icd9 else ""))
    entry = _el(sez, "entry")
    act = _el(entry, "act", classCode="ACT", moodCode="EVN")
    _el(act, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.4.1")
    _id(act)
    _el(act, "code", nullFlavor="NA")
    _stato(act, p.stato)
    _intervallo(act, p.inizio, p.stato, p.fine)
    er = _el(act, "entryRelationship", typeCode="SUBJ", inversionInd="false")
    obs = _el(er, "observation", classCode="OBS", moodCode="EVN")
    _el(obs, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.4.2")
    _id(obs)
    _el(obs, "code", code="75326-9", codeSystem=OID_LOINC, codeSystemName="LOINC", displayName="Problema")
    _riferimento(obs, rif)
    _el(obs, "statusCode", code="completed")
    et = _el(obs, "effectiveTime")
    if p.inizio:
        _el(et, "low", value=ts_data(p.inizio))
    else:
        _el(et, "low", nullFlavor="UNK")
    if p.fine:
        _el(et, "high", value=ts_data(p.fine))
    if p.codice_icd9:
        v = _el(obs, "value", xsi_type="CD", code=p.codice_icd9, codeSystem=OID_ICD9CM, codeSystemName="ICD-9-CM",
                displayName=p.descrizione)
    else:
        v = _el(obs, "value", xsi_type="CD", nullFlavor="NI")
    ot = _el(v, "originalText")
    _el(ot, "reference", value=rif)


def _sezione_anamnesi(body: ET.Element, pss: ProfiloSanitarioSintetico) -> None:
    sez, narr = _sezione(body, "2.16.840.1.113883.2.9.10.1.4.2.16", "10157-6",
                         "Storia di malattie di membri familiari", "Anamnesi familiare", "fam")
    if pss.anamnesi_familiare_assente:
        narr.voce(ASSENZA_PROBLEMI[pss.anamnesi_familiare_assente])
        entry = _el(sez, "entry")
        obs = _el(entry, "observation", classCode="OBS", moodCode="EVN")
        _el(obs, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.16.4")
        _id(obs)
        _el(obs, "code", code="75326-9", codeSystem=OID_ASSENZA_ANAMNESI_CODE, codeSystemName="LOINC",
            displayName="Problema")
        _el(obs, "statusCode", code="completed")
        _el(obs, "value", xsi_type="CD", code=pss.anamnesi_familiare_assente, codeSystem=OID_ASSENZA_PROBLEMI,
            codeSystemName="IPSNoProbsInfos", displayName=ASSENZA_PROBLEMI[pss.anamnesi_familiare_assente])
        return
    # un organizer per familiare, con una osservazione per patologia
    per_familiare: dict[tuple[str, str | None], list[AnamnesiFamiliare]] = {}
    for v in pss.anamnesi_familiare:
        per_familiare.setdefault((v.parentela, v.sesso), []).append(v)
    for (parentela, sesso), voci in per_familiare.items():
        entry = _el(sez, "entry")
        org = _el(entry, "organizer", classCode="CLUSTER", moodCode="EVN")
        _el(org, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.16.1")
        _id(org)
        _el(org, "code", code="10157-6", codeSystem=OID_LOINC, codeSystemName="LOINC",
            displayName="Storia di malattie di membri familiari")
        _el(org, "statusCode", code="completed")
        subj = _el(org, "subject", typeCode="SBJ")
        rs = _el(subj, "relatedSubject", classCode="PRS")
        _el(rs, "code", code=parentela, codeSystem=OID_PARENTELA, codeSystemName="RoleCode")
        if sesso:
            s = _el(rs, "subject")
            _el(s, "administrativeGenderCode", code=sesso, codeSystem=OID_GENERE,
                codeSystemName="HL7 AdministrativeGender", displayName=DESCRIZIONE_GENERE.get(sesso))
        for v in voci:
            rif = narr.voce(f"{parentela}: {v.descrizione}" + (f" ({v.codice_icd9})" if v.codice_icd9 else ""))
            comp = _el(org, "component")
            obs = _el(comp, "observation", classCode="OBS", moodCode="EVN")
            _el(obs, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.16.2")
            _id(obs)
            _el(obs, "code", code="52797-8", codeSystem=OID_LOINC, codeSystemName="LOINC",
                displayName="Diagnosi codice ICD")
            _riferimento(obs, rif)
            _el(obs, "statusCode", code="completed")
            _el(obs, "effectiveTime", nullFlavor="UNK")
            if v.codice_icd9:
                val = _el(obs, "value", xsi_type="CD", code=v.codice_icd9, codeSystem=OID_ICD9CM,
                          codeSystemName="ICD-9-CM", displayName=v.descrizione)
            else:
                val = _el(obs, "value", xsi_type="CD", nullFlavor="NI")
            ot = _el(val, "originalText")
            _el(ot, "reference", value=rif)
            if v.eta_insorgenza is not None:
                er = _el(obs, "entryRelationship", typeCode="SUBJ")
                o2 = _el(er, "observation", classCode="OBS", moodCode="EVN")
                _el(o2, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.16.3")
                _el(o2, "code", code="35267-4", codeSystem=OID_LOINC, codeSystemName="LOINC",
                    displayName="Età diagnosi patologia")
                _el(o2, "statusCode", code="completed")
                _el(o2, "value", xsi_type="PQ", value=str(v.eta_insorgenza), unit="a")


def _sezione_esenzioni(body: ET.Element, pss: ProfiloSanitarioSintetico) -> None:
    if not pss.esenzioni:
        return
    sez, narr = _sezione(body, "2.16.840.1.113883.2.9.10.1.4.2.17", "57827-8",
                         "Motivo di esenzione dal co-pagamento", "Esenzioni", "ese")
    for e in pss.esenzioni:
        narr.voce(f"Esenzione {e.codice}" + (f" - {e.descrizione}" if e.descrizione else ""))
        entry = _el(sez, "entry")
        act = _el(entry, "act", classCode="ACT", moodCode="EVN")
        _el(act, "templateId", root="2.16.840.1.113883.2.9.10.1.4.3.17.1")
        _id(act)
        _el(act, "code", code=e.codice, codeSystem=OID_ESENZIONI, codeSystemName="Esenzioni",
            displayName=e.descrizione)
        _stato(act, e.stato)
        _intervallo(act, e.inizio, e.stato, e.fine)


# ------------------------------------------------------------------ documento


def genera(pss: ProfiloSanitarioSintetico, *, valida: bool = True) -> ET.Element:
    """ClinicalDocument del PSS. Con valida=True prima controlla la forma (DocumentoNonValido)."""
    if valida:
        pss.valida()
    cd = _el(None, "ClinicalDocument")
    _el(cd, "realmCode", code="IT")
    _el(cd, "typeId", root="2.16.840.1.113883.1.3", extension="POCD_MT000040UV02")
    _el(cd, "templateId", root=TEMPLATE_PSS, extension=VERSIONE_TEMPLATE_PSS)
    _el(cd, "id", root=pss.radice_id, extension=pss.id_documento)
    _el(cd, "code", code="60591-5", codeSystem=OID_LOINC, codeSystemName="LOINC", displayName="Profilo Sanitario Sintetico")
    _el(cd, "title", "Profilo Sanitario Sintetico")
    quando = ts_datetime(pss.data)
    _el(cd, "effectiveTime", value=quando)
    _el(cd, "confidentialityCode", code=pss.riservatezza, codeSystem=OID_RISERVATEZZA, codeSystemName="HL7 Confidentiality")
    _el(cd, "languageCode", code="it-IT")
    # setId: comune a tutte le versioni; coincide con id solo nella versione 1 (ERRORE-8)
    _el(cd, "setId", root=pss.radice_id, extension=pss.set_id)
    _el(cd, "versionNumber", value=str(pss.versione))

    # paziente
    pz = pss.paziente
    rt = _el(cd, "recordTarget")
    pr = _el(rt, "patientRole")
    _el(pr, "id", root=OID_CF, extension=pz.codice_fiscale, assigningAuthorityName="MEF")
    if pz.residenza:
        _indirizzo(pr, pz.residenza, "H")
    pat = _el(pr, "patient")
    _nome_persona(pat, pz.nome, pz.cognome)
    _el(pat, "administrativeGenderCode", code=pz.sesso, codeSystem=OID_GENERE,
        codeSystemName="HL7 AdministrativeGender", displayName=DESCRIZIONE_GENERE.get(pz.sesso))
    if pz.data_nascita:
        _el(pat, "birthTime", value=ts_data(pz.data_nascita))
    else:
        _el(pat, "birthTime", nullFlavor="UNK")
    if pz.luogo_nascita:
        bp = _el(pat, "birthplace")
        pl = _el(bp, "place")
        _indirizzo(pl, pz.luogo_nascita)

    # autore
    m = pss.autore
    au = _el(cd, "author")
    _el(au, "time", value=quando)
    aa = _el(au, "assignedAuthor")
    _el(aa, "id", root=OID_CF, extension=m.codice_fiscale, assigningAuthorityName="MEF")
    _el(aa, "code", code=m.ruolo, codeSystem=OID_RUOLO_AUTORE, codeSystemName="assignedAuthorCode_PSSIT",
        displayName={"MMG": "Medico di Medicina Generale", "PLS": "Pediatra di Libera Scelta"}[m.ruolo])
    _telecom(aa, m.telefono, m.email)
    ap = _el(aa, "assignedPerson")
    _nome_persona(ap, m.nome, m.cognome, m.titolo)

    # custode
    c = pss.custode
    cu = _el(cd, "custodian")
    ac = _el(cu, "assignedCustodian")
    org = _el(ac, "representedCustodianOrganization")
    _el(org, "id", root=c.id_radice, extension=c.id_estensione)
    _el(org, "name", c.nome)
    if c.telefono:
        _el(org, "telecom", use="WP", value=f"tel:{c.telefono}")
    if c.indirizzo:
        _indirizzo(org, c.indirizzo)

    # firmatario: il medico stesso
    la = _el(cd, "legalAuthenticator")
    _el(la, "time", value=quando)
    _el(la, "signatureCode", code="S")
    ae = _el(la, "assignedEntity")
    _entita_medico(ae, m)

    doc_of = _el(cd, "documentationOf")
    se = _el(doc_of, "serviceEvent")
    _el(se, "effectiveTime", value=quando)

    # versione > 1: il documento sostituisce la versione precedente (ERRORE-9, ERRORE-9a)
    if pss.id_documento_precedente:
        rd = _el(cd, "relatedDocument", typeCode="RPLC")
        pd = _el(rd, "parentDocument")
        _el(pd, "id", root=pss.radice_id, extension=pss.id_documento_precedente)
        _el(pd, "setId", root=pss.radice_id, extension=pss.set_id)

    comp = _el(cd, "component")
    body = _el(comp, "structuredBody", moodCode="EVN", classCode="DOCBODY")
    _sezione_allergie(body, pss)
    _sezione_terapie(body, pss)
    _sezione_problemi(body, pss)
    _sezione_anamnesi(body, pss)
    _sezione_esenzioni(body, pss)
    return cd


def genera_xml(pss: ProfiloSanitarioSintetico, *, valida: bool = True) -> bytes:
    cd = genera(pss, valida=valida)
    ET.indent(cd, space="  ")
    return ET.tostring(cd, encoding="utf-8", xml_declaration=True)

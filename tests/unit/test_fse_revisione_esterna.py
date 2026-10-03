# SPDX-License-Identifier: EUPL-1.2
"""Controesempi della revisione esterna del modulo FSE (2026-10-02, 2-fse.md), uno per rilievo.

Ogni test riproduce il controesempio della revisione: falliva sul codice precedente, passa ora.
I controlli col validatore e col dispatcher UFFICIALI sono in tests/ufficiale/test_fse_revisione_ufficiale.py.
"""

import dataclasses
import datetime as dt
import io
import re
import xml.etree.ElementTree as ET

import pytest

from varco.fse import cda_pss, esempi
from varco.fse.json import pss_a_dict, pss_da_dict
from varco.fse.modello import (
    Allergia,
    AnamnesiFamiliare,
    DocumentoNonValido,
    Esenzione,
    Indirizzo,
    Problema,
    Stato,
    Terapia,
)
from varco.fse.validazione import ValidatoreLocale, ValidatoreUfficiale, dipendenze_locali_presenti

H = {"h": "urn:hl7-org:v3"}
serve_validatore = pytest.mark.skipif(not dipendenze_locali_presenti(), reason="servono lxml e saxonche (extra fse)")


def _sezione(cd, codice: str):
    return next(s for s in cd.iterfind(".//h:section", H) if s.find("h:code", H).get("code") == codice)


def _con_terapia(t: Terapia):
    return dataclasses.replace(esempi.pss_completo(), terapie=(t,))


# ------------------------------------------------------------------ 1. nessun dato clinico inventato


def test_terapia_senza_via_non_diventa_orale():
    """Controesempio 1: Terapia("Farmaco TEST", codice_atc="B01AB05") generava routeCode PO."""
    t = Terapia("Farmaco TEST", codice_atc="B01AB05", stato=Stato.ATTIVO)
    assert t.via is None
    pss = _con_terapia(t)
    assert any("via di somministrazione obbligatoria" in p for p in pss.problemi_di_forma())
    with pytest.raises(DocumentoNonValido, match="via di somministrazione"):
        cda_pss.genera_xml(pss)
    # anche forzando la generazione (valida=False, solo per documenti KO di prova) nessuna via compare
    cd = ET.fromstring(cda_pss.genera_xml(pss, valida=False))
    assert cd.find(".//h:routeCode", H) is None


def test_da_riga_non_deduce_via_ne_stato():
    t = Terapia.da_riga(esempi.RICETTA.righe[0])
    assert (t.via, t.stato) == (None, None)
    t = Terapia.da_riga(esempi.RICETTA.righe[0], via="PO", stato=Stato.ATTIVO)
    assert (t.via, t.stato) == ("PO", Stato.ATTIVO)


@pytest.mark.parametrize("classe,campi", [
    (Terapia, ("via", "stato")),
    (Allergia, ("tipo", "stato")),
    (Problema, ("stato",)),
    (Esenzione, ("stato",)),
])
def test_nessun_valore_clinico_predefinito(classe, campi):
    """Ricerca sistematica di altri default clinici: via, stato e tipo di allergia non hanno valori predefiniti."""
    predefiniti = {f.name: f.default for f in dataclasses.fields(classe)}
    assert {c: predefiniti[c] for c in campi} == {c: None for c in campi}


@pytest.mark.parametrize("voce", [
    dict(allergie=(Allergia("X", "J01CA04", "ATC", "ALG"),)),
    dict(terapie=(Terapia("X", codice_atc="C09AA05", via="PO"),)),
    dict(problemi=(Problema("401.9", "x"),)),
    dict(esenzioni=(Esenzione("031"),)),
], ids=["allergia", "terapia", "problema", "esenzione"])
def test_stato_mancante_rifiutato(voce):
    pss = dataclasses.replace(esempi.pss_completo(), **voce)
    with pytest.raises(DocumentoNonValido, match="stato obbligatorio"):
        cda_pss.genera_xml(pss)
    xml = cda_pss.genera_xml(pss, valida=False)
    assert b'<statusCode code="active"' not in xml.split(b"<section")[0]  # intestazione: nessuno stato
    n_stati = xml.count(b'statusCode code="active"')
    n_stati_completo = cda_pss.genera_xml(esempi.pss_completo()).count(b'statusCode code="active"')
    assert n_stati < n_stati_completo  # la voce senza stato non ha preso "active"


def test_allergia_senza_tipo_non_diventa_allergia():
    """Il tipo (allergia o intolleranza) è un dato clinico: senza, valore non codificato (ERRORE-b80), non ALG."""
    a = Allergia("Amoxicillina", "J01CA04", "ATC", inizio=dt.date(2015, 3, 1), stato=Stato.ATTIVO)
    assert a.tipo is None
    pss = dataclasses.replace(esempi.pss_completo(), allergie=(a,))
    assert pss.problemi_di_forma() == []
    cd = ET.fromstring(cda_pss.genera_xml(pss))
    obs = _sezione(cd, "48765-2").find(".//h:observation", H)
    v = obs.find("h:value", H)
    assert v.get("code") is None and v.get("nullFlavor") == "UNK"
    assert v.find("h:originalText/h:reference", H).get("value").startswith("#all-")


def test_assenze_senza_data_di_inizio_inventata():
    """L'inizio dello stato "nessuna allergia nota" non era fornito: il kit scriveva la data del documento."""
    cd = ET.fromstring(cda_pss.genera_xml(esempi.pss_assenze()))
    data_doc = esempi.DATA.strftime("%Y%m%d")
    for codice in ("48765-2", "11450-4"):
        act = _sezione(cd, codice).find("h:entry/h:act", H)
        low = act.find("h:effectiveTime/h:low", H)
        assert low.get("value") != data_doc and low.get("nullFlavor") == "UNK"


@serve_validatore
@pytest.mark.schemi_hl7
def test_allergia_senza_tipo_e_assenze_valide_in_locale():
    a = Allergia("Amoxicillina", "J01CA04", "ATC", inizio=dt.date(2015, 3, 1), stato=Stato.ATTIVO)
    for pss in (dataclasses.replace(esempi.pss_completo(), allergie=(a,)), esempi.pss_assenze()):
        e = ValidatoreLocale().valida(cda_pss.genera_xml(pss))
        assert e.esito == "OK", e.errori


# ------------------------------------------------------------------ 2. iniezione CDA duplicata


def _pdf_con_due_cda(vecchio: bytes, nuovo: bytes) -> bytes:
    """Come faceva il vecchio inietta_cda: un secondo cda.xml accanto al primo."""
    from pyhanko.pdf_utils import embed
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    from varco.fse import pdf

    w = IncrementalPdfFileWriter(io.BytesIO(pdf.pdf_con_cda(["test"], vecchio)))
    ef = embed.EmbeddedFileObject.from_file_data(w, data=nuovo, mime_type="text/xml")
    embed.embed_file(w, embed.FileSpec(file_spec_string="cda.xml", file_name="cda.xml", embedded_data=ef))
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def test_iniezione_su_pdf_che_ha_gia_il_cda_rifiutata():
    """Controesempio 2: inietta_cda su un PDF con cda.xml ne aggiungeva un secondo."""
    pytest.importorskip("pyhanko")
    from varco.fse import pdf

    originale = pdf.pdf_con_cda(["test"], b"<vecchio/>")
    with pytest.raises(pdf.CdaGiaPresente, match="sostituisci=True"):
        pdf.inietta_cda(originale, b"<nuovo/>")


def test_iniezione_con_sostituzione_lascia_un_solo_cda():
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("pyhanko")
    from varco.fse import pdf

    originale = pdf.pdf_con_cda(["test"], b"<vecchio/>")
    nuovo = pdf.inietta_cda(originale, b"<nuovo/>", sostituisci=True)
    assert nuovo.startswith(originale)  # sempre aggiornamento incrementale
    assert pdf.estrai_cda(nuovo) == b"<nuovo/>"
    r = pypdf.PdfReader(io.BytesIO(nuovo))
    assert dict(r.attachments) == {"cda.xml": [b"<nuovo/>"]}
    af = r.trailer["/Root"]["/AF"]
    assert len(af) == 1 and af[0].get_object()["/EF"]["/F"].get_object().get_data() == b"<nuovo/>"
    # nome del file in maiuscolo: per il dispatcher (che mette le chiavi in minuscolo) è lo stesso allegato
    maiuscolo = pdf.pdf_con_cda(["test"], b"<vecchio/>", nome_allegato="CDA.XML")
    with pytest.raises(pdf.CdaGiaPresente):
        pdf.inietta_cda(maiuscolo, b"<nuovo/>")


def test_estrazione_rifiuta_due_cda_invece_di_scegliere():
    """Controesempio 2: estrai_cda restituiva il primo, il dispatcher l'ultimo."""
    pytest.importorskip("pyhanko")
    from varco.fse import pdf

    with pytest.raises(pdf.AllegatiCdaAmbigui):
        pdf.estrai_cda(_pdf_con_due_cda(b"<vecchio/>", b"<nuovo/>"))
    with pytest.raises(pdf.CdaGiaPresente):
        pdf.inietta_cda(_pdf_con_due_cda(b"<vecchio/>", b"<nuovo/>"), b"<terzo/>")


# ------------------------------------------------------------------ 3. versioni successive alla prima


def pss_versione_2():
    return dataclasses.replace(esempi.pss_completo("PSS-TEST-0001-V2"), versione=2, id_set="PSS-TEST-0001",
                               id_documento_precedente="PSS-TEST-0001")


def test_versione_2_setid_distinto_e_relateddocument_rplc():
    """Controesempio 3: versione=2 dava id == setId e nessun relatedDocument (ERRORE-8, ERRORE-9)."""
    pss = pss_versione_2()
    assert pss.problemi_di_forma() == []
    cd = ET.fromstring(cda_pss.genera_xml(pss))
    radice = "2.16.840.1.113883.2.9.2.130.4.4"
    assert cd.find("h:id", H).attrib == {"root": radice, "extension": "PSS-TEST-0001-V2"}
    assert cd.find("h:setId", H).attrib == {"root": radice, "extension": "PSS-TEST-0001"}
    assert cd.find("h:versionNumber", H).get("value") == "2"
    rd = cd.findall("h:relatedDocument", H)
    assert len(rd) == 1 and rd[0].get("typeCode") == "RPLC"
    assert rd[0].find("h:parentDocument/h:id", H).attrib == {"root": radice, "extension": "PSS-TEST-0001"}
    # ordine dello schema CDA: relatedDocument dopo documentationOf, prima di component
    figli = [c.tag.split("}")[1] for c in cd]
    assert figli.index("documentationOf") < figli.index("relatedDocument") < figli.index("component")
    assert pss_da_dict(pss_a_dict(pss)) == pss


@pytest.mark.parametrize("modifica,frammento", [
    (dict(versione=2), "serve id_set"),
    (dict(versione=2, id_set="PSS-TEST-0001"), "deve essere diverso da id_set"),
    (dict(versione=2, id_set="SET"), "serve id_documento_precedente"),
    (dict(versione=1, id_set="ALTRO"), "versione 1: id_set"),
    (dict(versione=1, id_documento_precedente="X"), "versione 1: non sostituisce"),
    (dict(versione=0), "versione: intero"),
])
def test_versioni_incoerenti_rifiutate(modifica, frammento):
    pss = dataclasses.replace(esempi.pss_completo(), **modifica)
    assert any(frammento in p for p in pss.problemi_di_forma()), pss.problemi_di_forma()
    with pytest.raises(DocumentoNonValido):
        cda_pss.genera_xml(pss)


@serve_validatore
@pytest.mark.schemi_hl7
def test_versione_2_valida_in_locale():
    e = ValidatoreLocale().valida(cda_pss.genera_xml(pss_versione_2()))
    assert e.esito == "OK", e.errori


# ------------------------------------------------------------------ 4. il PDF leggibile riporta tutto il CDA


def pss_pieno():
    """Ogni campo valorizzato, con valori riconoscibili, e una voce per ogni stato."""
    base = esempi.pss_completo("PSS-PIENO-V3")
    bergamo = Indirizzo("Bergamo", "016024", via="Via Sentinella 7", cap="24121", provincia="BG", codice_regione="030")
    return dataclasses.replace(
        base,
        versione=3, id_set="PSS-PIENO", id_documento_precedente="PSS-PIENO-V2", riservatezza="R",
        paziente=dataclasses.replace(base.paziente, nome="Nomepaziente", cognome="Cognomepaziente", sesso="F",
                                     residenza=bergamo),
        autore=dataclasses.replace(base.autore, nome="Nomemedico", cognome="Cognomemedico", titolo="Dott.ssa",
                                   telefono="0351234567", email="sentinella@example.invalid", ruolo="PLS"),
        custode=dataclasses.replace(base.custode, telefono="0359876543"),
        allergie=(
            Allergia("Amoxicillina", "J01CA04", "ATC", "ALG", inizio=dt.date(2015, 3, 1), stato=Stato.CONCLUSO,
                     fine=dt.date(2019, 6, 30), note="ANNOTAZIONE_TEST_DA_NON_PERDERE"),
            Allergia("Polline di betulla", None, None, None, stato=Stato.SOSPESO),
        ),
        terapie=(
            Terapia("Farmaco TEST", codice_atc="B01AB05", codice_aic="012345678", via="SQ", inizio=dt.date(2020, 1, 1),
                    stato=Stato.CONCLUSO, fine=dt.date(2025, 1, 1)),
            Terapia("Altro farmaco", codice_gruppo_equivalenza="G3B", via="PO", stato=Stato.INTERROTTO,
                    fine=dt.date(2024, 2, 2)),
        ),
        problemi=(Problema("401.9", "Ipertensione essenziale non specificata", inizio=dt.date(2018, 5, 5),
                           stato=Stato.CONCLUSO, fine=dt.date(2021, 7, 7)),),
        anamnesi_familiare=(AnamnesiFamiliare("MTH", "410.00", "Infarto miocardico acuto", sesso="F",
                                              eta_insorgenza=67),),
        esenzioni=(Esenzione("031", "Ipertensione (descrizione di test)", inizio=dt.date(2020, 2, 1),
                             stato=Stato.SOSPESO),),
    )


def _foglie(v, percorso=""):
    if isinstance(v, dict):
        for k, x in v.items():
            yield from _foglie(x, f"{percorso}.{k}")
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from _foglie(x, f"{percorso}[{i}]")
    else:
        yield percorso, v


def _testo_pdf(dati: bytes) -> str:
    pypdf = pytest.importorskip("pypdf")
    r = pypdf.PdfReader(io.BytesIO(dati))
    return " ".join(" ".join(p.extract_text().split()) for p in r.pages)


def test_pdf_riporta_note_stato_e_fine():
    """Controesempio 4 della revisione, esatto: terapia conclusa e nota sull'allergia."""
    from varco.fse import pdf

    pss = dataclasses.replace(
        esempi.pss_completo(),
        allergie=(Allergia("Amoxicillina", "J01CA04", "ATC", "ALG", inizio=dt.date(2015, 3, 1), stato=Stato.ATTIVO,
                           note="ANNOTAZIONE_TEST_DA_NON_PERDERE"),),
        terapie=(Terapia("Farmaco TEST", codice_atc="B01AB05", via="PO", inizio=dt.date(2020, 1, 1),
                         stato=Stato.CONCLUSO, fine=dt.date(2025, 1, 1)),),
    )
    cda = cda_pss.genera_xml(pss)
    testo = _testo_pdf(pdf.pdf_con_cda(pdf.righe_leggibili_pss(pss), cda))
    assert b"ANNOTAZIONE_TEST_DA_NON_PERDERE" in cda and "ANNOTAZIONE_TEST_DA_NON_PERDERE" in testo
    riga = re.search(r"- Farmaco TEST .*?(?= - Lista| Lista)", testo).group(0)
    assert "concluso" in riga and "2025-01-01" in riga and "via PO" in riga


def test_pdf_riporta_ogni_valore_presente_nel_cda():
    """Controllo sistematico, campo per campo: ogni valore del modello che finisce nel CDA deve
    comparire anche nel testo leggibile del PDF (date in forma ISO)."""
    from varco.fse import pdf

    pss = pss_pieno()
    assert pss.problemi_di_forma() == []
    cda = cda_pss.genera_xml(pss).decode("utf-8")
    testo = _testo_pdf(pdf.pdf_con_cda(pdf.righe_leggibili_pss(pss), cda.encode()))
    controllati, mancanti = [], []
    for percorso, v in _foglie(pss_a_dict(pss)):
        s = str(v)
        nel_cda = s.replace("&", "&amp;").replace("'", "&apos;")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(T[\d:]+)?", s):
            nel_cda = re.sub(r"[-:T]", "", s)
            s = s.replace("T", " ")
        if nel_cda not in cda and s not in cda:
            continue  # non è nel CDA (es. provincia/ASL dell'assistito della ricetta)
        controllati.append(percorso)
        if s not in testo:
            mancanti.append((percorso, s))
    assert not mancanti, mancanti
    # il controllo deve avere morso davvero: note, fine, stato, via, tutti i codici, versione
    for atteso in (".allergie[0].note", ".terapie[0].fine", ".terapie[0].stato", ".terapie[0].via",
                   ".terapie[0].codice_aic", ".terapie[0].codice_atc", ".allergie[0].tipo",
                   ".id_documento_precedente", ".id_set", ".esenzioni[0].descrizione",
                   ".anamnesi_familiare[0].eta_insorgenza", ".autore.email", ".paziente.residenza.via"):
        assert atteso in controllati, atteso


def test_pdf_assenze_e_dati_non_noti_detti_come_tali():
    from varco.fse import pdf

    righe = " ".join(pdf.righe_leggibili_pss(esempi.pss_assenze()))
    assert "No known allergies (no-known-allergies)" in righe
    pieno = " ".join(pdf.righe_leggibili_pss(pss_pieno()))
    assert "Polline di betulla (agente non codificato) - tipo non indicato - inizio non noto" in pieno


def test_pdf_non_sostituisce_caratteri_in_silenzio():
    from varco.fse import pdf

    with pytest.raises(pdf.TestoNonRappresentabile):
        pdf.pdf_con_cda(["Paziente: Łukasz"], None)


# ------------------------------------------------------------------ 5. vocabolari non controllati nell'esito pubblico


GREZZO_PSS_01 = {  # risultato del banco ufficiale archiviato in prove/20260930-172323-fse/esiti.json
    "file": "pss_01_completo.xml",
    "typeIdExtension": "POCD_MT000040UV02",
    "schematron": {"templateIdRoot": "2.16.840.1.113883.2.9.10.1.4.1.1", "versione": "4.0", "errori": [], "avvisi": []},
    "vocabolario": {"valido": True, "messaggio": "Almeno uno dei seguenti vocaboli non è censito: "},
    "esito": "OK",
    "messaggi": [],
    "log_avvisi": ["Unknown CodeSystems found during the validation: [2.16.840.1.113883.2.9.6.1.51, 2.16.840.1.113883.2.9.6.1.22]"],
    "log_errori": [],
}


def test_sistemi_non_verificati_nell_esito_pubblico():
    """Controesempio 5: a_dict() diceva esito OK, avvisi [] e vocabolario_verificato True."""
    pubblico = ValidatoreUfficiale()._esito(GREZZO_PSS_01).a_dict()
    assert pubblico["esito"] == "OK"  # l'esito resta quello del gateway
    assert pubblico["sistemi_non_verificati"] == ["2.16.840.1.113883.2.9.6.1.51", "2.16.840.1.113883.2.9.6.1.22"]
    avviso = [a for a in pubblico["avvisi"] if "VOCABOLARIO NON VERIFICATO" in a]
    assert len(avviso) == 1
    assert "2.16.840.1.113883.2.9.6.1.51 (gruppi di equivalenza)" in avviso[0]
    assert "2.16.840.1.113883.2.9.6.1.22 (esenzioni)" in avviso[0]


def test_nessun_avviso_se_tutti_i_sistemi_sono_noti():
    grezzo = dict(GREZZO_PSS_01, log_avvisi=[])
    pubblico = ValidatoreUfficiale()._esito(grezzo).a_dict()
    assert pubblico["sistemi_non_verificati"] == [] and pubblico["avvisi"] == []


# ------------------------------------------------------------------ 6. data di nascita sconosciuta


def test_data_di_nascita_sconosciuta_rifiutata_prima_della_generazione():
    """Controesempio 6: data_nascita=None generava birthTime nullFlavor=UNK, respinto (ERRORE-17)."""
    base = esempi.pss_completo()
    pss = dataclasses.replace(base, paziente=dataclasses.replace(base.paziente, data_nascita=None))
    assert any("data di nascita obbligatoria" in p for p in pss.problemi_di_forma())
    with pytest.raises(DocumentoNonValido, match="data di nascita"):
        cda_pss.genera_xml(pss)

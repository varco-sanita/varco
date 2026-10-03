# SPDX-License-Identifier: EUPL-1.2
"""FSE 2.0, lato documento: modello, CDA del PSS, validazione locale con schemi e schematron ufficiali, PDF e firma."""

import dataclasses
import datetime as dt
import io
import xml.etree.ElementTree as ET

import pytest

from varco.conformita.motore import cartella_conformita
from varco.fse import cda_pss, esempi
from varco.fse.json import pss_a_dict, pss_da_dict
from varco.fse.modello import Allergia, DocumentoNonValido, Problema, Stato, Terapia
from varco.fse.validazione import ValidatoreLocale, dipendenze_locali_presenti

H = {"h": "urn:hl7-org:v3"}
serve_validatore = pytest.mark.skipif(not dipendenze_locali_presenti(), reason="servono lxml e saxonche (extra fse)")


def xml_di(pss, **kw) -> bytes:
    return cda_pss.genera_xml(pss, **kw)


# ------------------------------------------------------------------ un solo modello dati


def test_il_pss_riusa_gli_oggetti_della_ricetta():
    pss = esempi.pss_completo()
    assert pss.paziente.assistito is esempi.RICETTA.assistito
    assert pss.autore.prescrittore is esempi.RICETTA.prescrittore
    t = Terapia.da_riga(esempi.RICETTA.righe[0])
    assert (t.codice_gruppo_equivalenza, t.descrizione) == ("G3B", "LEVETIRACETAM 500MG 60 UNITA' USO ORALE")
    p = Problema.da_ricetta(esempi.RICETTA)
    assert (p.codice_icd9, p.descrizione) == ("401.9", "Ipertensione essenziale non specificata")
    assert pss.esenzioni[0].codice == esempi.RICETTA.codice_esenzione == "031"


def test_id_documento_dalla_regione_del_prescrittore():
    assert esempi.pss_completo().radice_id == "2.16.840.1.113883.2.9.2.130.4.4"
    cd = ET.fromstring(xml_di(esempi.pss_completo()))
    ident = cd.find("h:id", H)
    assert (ident.get("root"), ident.get("extension")) == ("2.16.840.1.113883.2.9.2.130.4.4", "PSS-TEST-0001")
    assert cd.find("h:setId", H).attrib == ident.attrib and cd.find("h:versionNumber", H).get("value") == "1"


def test_json_andata_e_ritorno():
    for pss in (esempi.pss_completo(), esempi.pss_assenze()):
        assert pss_da_dict(pss_a_dict(pss)) == pss


def test_json_rifiuta_campi_sconosciuti():
    d = pss_a_dict(esempi.pss_assenze())
    d["paziente"]["codice_segreto"] = "x"
    with pytest.raises(ValueError, match="codice_segreto"):
        pss_da_dict(d)


# ------------------------------------------------------------------ controlli di forma (nessun controllo clinico)


@pytest.mark.parametrize(
    "modifica,frammento",
    [
        (dict(allergie=()), "allergie: servono delle voci OPPURE"),
        (dict(allergie_assenti="no-known-allergies"), "allergie: servono delle voci OPPURE"),
        (dict(allergie=(Allergia("X", tipo="DALLERGY"),)), "tipo non ammesso"),
        (dict(allergie=(Allergia("X", "123", "SNOMED"),)), "sistema dell'agente"),
        (dict(terapie=(Terapia("Senza codice"),)), "AIC, ATC o di gruppo"),
        (dict(problemi=(Problema("401.9", "x", stato=Stato.CONCLUSO),)), "richiede la data di fine"),
        (dict(problemi=(Problema("401.9", "x", stato=Stato.ATTIVO, fine=dt.date(2020, 1, 1)),)), "non ammette la data di fine"),
        (dict(riservatezza="X"), "riservatezza"),
    ],
)
def test_problemi_di_forma(modifica, frammento):
    pss = dataclasses.replace(esempi.pss_completo(), **modifica)
    assert any(frammento in p for p in pss.problemi_di_forma())
    with pytest.raises(DocumentoNonValido):
        cda_pss.genera(pss)


def test_medico_senza_recapiti_e_paziente_senza_cf():
    pss = esempi.pss_assenze()
    pss = dataclasses.replace(pss, autore=dataclasses.replace(pss.autore, telefono=None, email=None),
                              paziente=dataclasses.replace(pss.paziente, assistito=dataclasses.replace(pss.paziente.assistito, codice_fiscale="ABC")))
    problemi = pss.problemi_di_forma()
    assert any("recapito" in p for p in problemi) and any("codice fiscale" in p for p in problemi)


def test_esempi_senza_problemi_di_forma():
    assert esempi.pss_completo().problemi_di_forma() == []
    assert esempi.pss_assenze().problemi_di_forma() == []


# ------------------------------------------------------------------ struttura del CDA


def test_riferimenti_narrativi_tutti_risolti():
    """Ogni <reference value="#x"/> deve puntare a un ID del blocco narrativo."""
    cd = ET.fromstring(xml_di(esempi.pss_completo()))
    ids = {e.get("ID") for e in cd.iter() if e.get("ID")}
    rif = {e.get("value")[1:] for e in cd.iter("{urn:hl7-org:v3}reference")}
    assert rif and rif <= ids


def test_sezioni_obbligatorie_e_codici():
    cd = ET.fromstring(xml_di(esempi.pss_completo()))
    codici = [s.find("h:code", H).get("code") for s in cd.iterfind(".//h:section", H)]
    assert codici == ["48765-2", "10160-0", "11450-4", "10157-6", "57827-8"]
    assert cd.find("h:templateId", H).get("root") == "2.16.840.1.113883.2.9.10.1.4.1.1"
    assert cd.find("h:code", H).get("code") == "60591-5"
    assert cd.find(".//h:recordTarget/h:patientRole/h:id", H).get("extension") == "PNIMRA70A01H501P"


def test_assenze_usano_i_value_set_ips():
    cd = ET.fromstring(xml_di(esempi.pss_assenze()))
    sistemi = {v.get("codeSystem") for v in cd.iterfind(".//h:value", H)}
    assert {"2.16.840.1.113883.11.22.9", "2.16.840.1.113883.11.22.17"} <= sistemi
    assert cd.find(".//h:substanceAdministration/h:code", H).get("codeSystem") == "2.16.840.1.113883.11.22.15"


def test_data_con_fuso_italiano():
    assert cda_pss.ts_datetime(dt.datetime(2026, 9, 30, 17, 0, 0)) == "20260930170000+0200"
    assert cda_pss.ts_datetime(dt.datetime(2026, 1, 15, 9, 0, 0)) == "20260115090000+0100"


# ------------------------------------------------------------------ validazione locale (XSD + schematron ufficiali)


@serve_validatore
@pytest.mark.parametrize("pss", [esempi.pss_completo(), esempi.pss_assenze()], ids=["completo", "assenze"])
@pytest.mark.schemi_hl7
def test_pss_generato_valido_per_xsd_e_schematron(pss):
    e = ValidatoreLocale().valida(xml_di(pss))
    assert e.esito == "OK", e.errori
    assert e.schematron == "schematron_PSS_v4.0.sch" and not e.vocabolario_verificato


@serve_validatore
@pytest.mark.parametrize(
    "documento,esito,frammento",
    [
        ("pss_05_ko_senza_allergie.xml", "SEMANTIC_ERROR", "ERRORE-b1|"),
        ("pss_06_ko_cf_paziente.xml", "SEMANTIC_ERROR", "ERRORE-52|"),
        ("pss_07_ko_ordine_xsd.xml", "SYNTAX_ERROR", "title"),
    ],
)
@pytest.mark.schemi_hl7
def test_validatore_locale_boccia(documento, esito, frammento):
    """Gruppo di controllo: il validatore locale deve saper dire di no, con l'errore giusto."""
    e = ValidatoreLocale().valida(cartella_conformita().joinpath("documenti", documento).read_bytes())
    assert e.esito == esito and any(frammento in m for m in e.errori), e.errori


@serve_validatore
@pytest.mark.schemi_hl7
def test_documento_senza_template_noto():
    xml = xml_di(esempi.pss_assenze()).replace(b"2.16.840.1.113883.2.9.10.1.4.1.1", b"1.2.3.4.5")
    e = ValidatoreLocale().valida(xml)
    assert e.esito == "SEMANTIC_ERROR" and "not found" in e.errori[0]


@serve_validatore
def test_xml_malformato():
    assert ValidatoreLocale().valida(b"<ClinicalDocument").esito == "SYNTAX_ERROR"


# ------------------------------------------------------------------ PDF, iniezione, firma di TEST


def test_pdf_con_cda_allegato_stdlib():
    pypdf = pytest.importorskip("pypdf")
    cda = xml_di(esempi.pss_assenze())
    from varco.fse import pdf

    dati = pdf.pdf_con_cda(pdf.righe_leggibili_pss(esempi.pss_assenze()), cda)
    r = pypdf.PdfReader(io.BytesIO(dati))
    assert r.attachments["cda.xml"] == [cda]
    assert "PROFILO SANITARIO SINTETICO" in r.pages[0].extract_text()


def test_pdf_senza_allegato():
    pypdf = pytest.importorskip("pypdf")
    from varco.fse import pdf

    r = pypdf.PdfReader(io.BytesIO(pdf.pdf_con_cda(["solo testo"], None)))
    assert dict(r.attachments) == {}


def test_iniezione_e_firma_pades_di_test(tmp_path):
    pytest.importorskip("pyhanko")
    from pyhanko.keys import load_cert_from_pemder  # noqa: F401
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    from varco.fse import pdf

    cda = xml_di(esempi.pss_completo())
    base = pdf.pdf_con_cda(["PDF del gestionale (TEST)"], None)
    assert pdf.estrai_cda(base) is None
    iniettato = pdf.inietta_cda(base, cda)
    assert pdf.estrai_cda(iniettato) == cda
    assert iniettato.startswith(base)  # aggiornamento incrementale: il PDF originale resta intatto

    p12 = tmp_path / "test.p12"
    pdf.crea_certificato_di_test(str(p12), b"pw")
    firmato = pdf.FirmatarioPKCS12(str(p12), b"pw").firma_pades(iniettato)
    assert pdf.estrai_cda(firmato) == cda

    from cryptography.hazmat.primitives.serialization import pkcs12

    _, cert, _ = pkcs12.load_key_and_certificates(p12.read_bytes(), b"pw")
    assert "CERTIFICATO DI TEST - NON VALIDO" in cert.subject.rfc4514_string()
    from asn1crypto import x509 as asn1x509
    from cryptography.hazmat.primitives import serialization

    radice = asn1x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))
    firma = PdfFileReader(io.BytesIO(firmato)).embedded_signatures[0]
    stato = validate_pdf_signature(firma, ValidationContext(trust_roots=[radice]))
    assert stato.intact and stato.valid and stato.coverage.name == "ENTIRE_FILE"
    assert str(firma.sig_object["/SubFilter"]) == "/ETSI.CAdES.detached"


def test_firma_alterata_non_valida(tmp_path):
    """Gruppo di controllo: un byte cambiato dopo la firma deve rompere la verifica."""
    pytest.importorskip("pyhanko")
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature

    from varco.fse import pdf

    p12 = tmp_path / "test.p12"
    pdf.crea_certificato_di_test(str(p12), b"pw")
    firmato = bytearray(pdf.FirmatarioPKCS12(str(p12), b"pw").firma_pades(pdf.pdf_con_cda(["x"], b"<a/>")))
    i = firmato.index(b"(x) Tj")  # il testo della pagina, dentro l'intervallo firmato
    firmato[i + 1] = ord("y")
    from pyhanko_certvalidator import ValidationContext

    stato = validate_pdf_signature(PdfFileReader(io.BytesIO(bytes(firmato))).embedded_signatures[0], ValidationContext(trust_roots=[]))
    assert not stato.intact

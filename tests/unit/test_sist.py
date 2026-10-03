# SPDX-License-Identifier: EUPL-1.2
"""SIST Regione Puglia: guardia, WS-Security, codifica CVP, CDA2, lettura delle risposte, giro
completo contro il server finto (strumenti/sist_server_finto.py).

Nessuna chiamata alla Regione: il server è su 127.0.0.1. I test che confrontano con lo schema
e gli esempi UFFICIALI si saltano se le specifiche non sono scaricate
(strumenti/scarica_specifiche.py --gruppi sist): non stanno nel repository.

Scritto e verificato sulle specifiche, NON collaudato sul sistema regionale.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as _dt
import hashlib
import importlib.util
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from varco import AmbienteBloccato, ConfigurazioneNonValida, ErroreSOAP, ErroreTrasporto
from varco.ambienti import (
    HOST_SIST_COLLAUDO,
    HOST_SIST_PRODUZIONE,
    e_collaudo_sist,
    e_produzione,
    e_regione_puglia,
    verifica_url_consentito,
)
from varco.errori import RicettaNonValida
from varco.ricetta import (
    Assistito,
    ClassePriorita,
    CriteriNreUtilizzati,
    Prescrittore,
    Ricetta,
    RicettaSIST,
    Riga,
    TipoPrescrizione,
)
from varco.ricetta import cda_sist, xml_sist
from varco.ricetta.sist import FirmatarioCAdESPKCS12
from varco.trasporto import Richiesta, Risposta, TrasportoHTTP
from varco.trasporto.registro import Redattore
from varco.trasporto.sist import (
    AdesioneSIST,
    AmbienteSIST,
    ApplicativoSIST,
    CanaleSIST,
    DatiChiamata,
    OperatoreSIST,
    appl_digest,
    cf_del_certificato,
    url_base,
)
from varco.trasporto.soap import sbusta
from varco.trasporto.wssecurity import ChiavePKCS12, verifica_security

etree = pytest.importorskip("lxml.etree")
pytest.importorskip("pyhanko")
pytest.importorskip("asn1crypto")  # solo il server finto la usa, per verificare la CAdES

RADICE = Path(__file__).resolve().parents[2]
SPEC = RADICE / "specifiche" / "sist" / "specifiche SIST 4.02.27"
XSD_CVP = SPEC / "wsdl-pddasl" / "CVPService.xsd"
ESEMPI = SPEC / "definizione dei CDA" / "esempi CDA"
RISPOSTE = RADICE / "conformita" / "risposte" / "sist"
NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"

TITOLARE, SOSTITUTO, ASSISTITO = "PROVAX00X00X000Y", "PROVAX00X00X000Z", "PNIMRA70A01H501P"
CODICI_REGIONALI = {TITOLARE: "000001", SOSTITUTO: "000002"}
CODICE_APPLICATIVO = "CODICE-APPLICATIVO-DI-PROVA"
APPLICATIVO = ApplicativoSIST("VARCO", "varco", "0.1", CODICE_APPLICATIVO)


def _server_finto():
    spec = importlib.util.spec_from_file_location("sist_server_finto", RADICE / "strumenti" / "sist_server_finto.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("sist_server_finto", mod)
    spec.loader.exec_module(mod)
    return mod


SERVER = _server_finto()

richiede_xsd = pytest.mark.skipif(not XSD_CVP.exists(), reason="specifiche SIST non scaricate (strumenti/scarica_specifiche.py --gruppi sist)")
richiede_esempi = pytest.mark.skipif(not ESEMPI.exists(), reason="specifiche SIST non scaricate (strumenti/scarica_specifiche.py --gruppi sist)")


# ------------------------------------------------------------------ certificati di prova


def _p12(cartella: Path, cf: str) -> Path:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
        x509.NameAttribute(NameOID.COMMON_NAME, f"{cf}/0000000000000000.TEST"),  # come una CNS
    ])
    adesso = _dt.datetime.now(_dt.timezone.utc)
    c = (
        x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(k.public_key()).serial_number(1)
        .not_valid_before(adesso - _dt.timedelta(days=1)).not_valid_after(adesso + _dt.timedelta(days=30))
        .add_extension(x509.KeyUsage(True, True, False, False, False, False, False, False, False), critical=True)
        .sign(k, hashes.SHA256())
    )
    p = cartella / f"{cf}.p12"
    p.write_bytes(pkcs12.serialize_key_and_certificates(b"prova", k, c, None, serialization.BestAvailableEncryption(b"pw")))
    return p


@pytest.fixture(scope="module")
def p12(tmp_path_factory):
    d = tmp_path_factory.mktemp("cns-di-prova")
    return {cf: _p12(d, cf) for cf in (TITOLARE, SOSTITUTO)}


@pytest.fixture(scope="module")
def chiave(p12):
    return ChiavePKCS12(str(p12[TITOLARE]), b"pw")


@pytest.fixture
def rete_vietata(monkeypatch):
    def vietato(*a, **k):
        raise AssertionError("nessuna chiamata di rete doveva partire")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", vietato)


def _ricetta_farm(**kw) -> Ricetta:
    base = dict(
        prescrittore=Prescrittore(TITOLARE, "160", "114", "F"),
        assistito=Assistito(codice_fiscale=ASSISTITO, provincia="BA", asl="114", codice_regione="160"),
        tipo=TipoPrescrizione.FARMACEUTICA,
        righe=(Riga(1, codice="034298051", descrizione="FARMACO DI PROVA", codice_gruppo_equivalenza="02D",
                    descrizione_gruppo_equivalenza="GRUPPO DI PROVA", nota_aifa="1"),),
    )
    base.update(kw)
    return Ricetta(**base)


def _ricetta_spec(**kw) -> Ricetta:
    base = dict(
        prescrittore=Prescrittore(TITOLARE, "160", "114", "F"),
        assistito=Assistito(codice_fiscale=ASSISTITO, provincia="BA", asl="114", codice_regione="160"),
        tipo=TipoPrescrizione.SPECIALISTICA,
        classe_priorita=ClassePriorita.BREVE,
        codice_diagnosi="4254",
        descrizione_diagnosi="CONTROLLO",
        non_esente=False,
        codice_esenzione="048",
        disposizioni_regionali="REG",
        righe=(Riga(1, codice="89.7", descrizione="VISITA DI PROVA", codice_catalogo="51609", tipo_accesso="1",
                    num_sedute=2, note_prestazione="nota di prova"),),
    )
    base.update(kw)
    return Ricetta(**base)


def _dati() -> DatiChiamata:
    return DatiChiamata(OperatoreSIST(TITOLARE, "160114"), "VARCO", "varco", "0.1", "a" * 20,
                        "2026-10-01T11:00:00+0200", "x")


def _codice(cf: str) -> str:
    return CODICI_REGIONALI[cf]


def _lx(el: ET.Element):
    return etree.fromstring(ET.tostring(el))


@pytest.fixture(scope="module")
def xsd_cvp():
    if not XSD_CVP.exists():
        pytest.skip("specifiche SIST non scaricate")
    return etree.XMLSchema(etree.parse(str(XSD_CVP)))


@pytest.fixture(scope="module")
def schema_cda():
    from varco.fse.validazione import _schema_cda

    return _schema_cda()


# ------------------------------------------------------------------ guardia


@pytest.mark.parametrize(
    "url,produzione",
    [
        (f"https://{HOST_SIST_PRODUZIONE}:8181/aslba/CVPService", True),
        (f"https://{HOST_SIST_PRODUZIONE.upper()}:8181/aslle/CVPService", True),
        (f"https://{HOST_SIST_PRODUZIONE}.:8181/aslba", True),  # FQDN col punto finale
        ("http://wsit-virtasl.rmmg.rsr.rupar.puglia.it:8080/x", True),  # l'indirizzo scritto nei WSDL
        ("https://sist.sanita.puglia.it/qualcosa", True),  # prudenza: ogni *.puglia.it
        ("https://puglia.it/", True),
        (f"https://{HOST_SIST_COLLAUDO}:8181/aslba_test", False),  # non produzione, ma bloccato a parte
        ("https://pugliaxit.example/", False),
        ("http://127.0.0.1:8181/aslba_test", False),
    ],
)
def test_host_sist_riconosciuti(url, produzione):
    assert e_produzione(url) is produzione


def test_collaudo_sist_riconosciuto_anche_col_punto_finale():
    assert e_collaudo_sist(f"https://{HOST_SIST_COLLAUDO}.:8181/aslba_test")
    assert e_regione_puglia(f"https://{HOST_SIST_COLLAUDO}/x")
    assert not e_collaudo_sist(f"https://x{HOST_SIST_COLLAUDO}/")


def test_guardia_collaudo_sist_solo_col_suo_flag():
    url = f"https://{HOST_SIST_COLLAUDO}:8181/aslba_test/CVPService"
    with pytest.raises(AmbienteBloccato, match="collaudo SIST"):
        verifica_url_consentito(url)
    with pytest.raises(AmbienteBloccato):
        verifica_url_consentito(url, consenti_produzione=True)  # il flag di produzione non basta
    with pytest.raises(AmbienteBloccato):
        verifica_url_consentito(url, consenti_collaudo_regionale="si")  # solo True esplicito
    verifica_url_consentito(url, consenti_collaudo_regionale=True)


def test_guardia_produzione_sist_non_si_sblocca_col_flag_del_collaudo():
    url = f"https://{HOST_SIST_PRODUZIONE}:8181/aslba/CVPService"
    with pytest.raises(AmbienteBloccato, match="PRODUZIONE"):
        verifica_url_consentito(url, consenti_collaudo_regionale=True)
    verifica_url_consentito(url, consenti_produzione=True)


@pytest.mark.parametrize("ambiente", list(AmbienteSIST))
def test_trasporto_blocca_sist_prima_della_rete(rete_vietata, ambiente):
    t = TrasportoHTTP()
    with pytest.raises(AmbienteBloccato):
        t.invia(Richiesta("sist.chkPrescrizione", url_base(ambiente) + "/CVPService", b"<x/>"))


def test_canale_verso_la_regione_vuole_un_adesione(chiave):
    with pytest.raises(ConfigurazioneNonValida, match="AdesioneSIST"):
        CanaleSIST(OperatoreSIST(TITOLARE, "160114"), chiave)
    with pytest.raises(ConfigurazioneNonValida, match="AdesioneSIST"):
        CanaleSIST(OperatoreSIST(TITOLARE, "160114"), chiave, ambiente=AmbienteSIST.PRODUZIONE)
    with pytest.raises(ConfigurazioneNonValida, match="localhost"):
        CanaleSIST(OperatoreSIST(TITOLARE, "160114"), chiave, adesione=AdesioneSIST("PROT-1", APPLICATIVO),
                   applicativo_di_prova=APPLICATIVO)
    with pytest.raises(ConfigurazioneNonValida):
        AdesioneSIST(" ", APPLICATIVO)
    with pytest.raises(ConfigurazioneNonValida):
        AdesioneSIST("PROT-1", dataclasses.replace(APPLICATIVO, codice_applicativo=""))


@pytest.mark.parametrize("ambiente", [AmbienteSIST.COLLAUDO, AmbienteSIST.PRODUZIONE])
def test_trasporto_proprio_non_aggira_la_guardia(chiave, ambiente):
    """Revisione esterna 02/10/2026: la guardia scatta dove il canale consegna la richiesta
    (`trasporto.http.consegna`), non solo dentro TrasportoHTTP. Prima della correzione il trasporto
    proprio riceveva la richiesta firmata."""

    class Spia:
        def __init__(self):
            self.richieste = []

        def invia(self, richiesta):
            self.richieste.append(richiesta)
            return Risposta(200, b"", {}, 0.0)

    spia = Spia()
    can = CanaleSIST(OperatoreSIST(TITOLARE, "160114"), chiave, adesione=AdesioneSIST("PROT-1", APPLICATIVO),
                     ambiente=ambiente, trasporto=spia)
    with pytest.raises(AmbienteBloccato):
        can.chiama("chkPrescrizione", xml_sist.richiesta_annulla(can.dati_chiamata(), "1600A0000000001"))
    assert spia.richieste == []


def test_con_adesione_resta_comunque_la_guardia(chiave, rete_vietata):
    """Adesione dichiarata ma niente flag sul trasporto: il collaudo regionale resta chiuso."""
    can = CanaleSIST(OperatoreSIST(TITOLARE, "160114"), chiave, adesione=AdesioneSIST("PROT-1", APPLICATIVO))
    assert can.base_url == f"https://{HOST_SIST_COLLAUDO}:8181/aslba_test"
    with pytest.raises(AmbienteBloccato):
        can.chiama("chkPrescrizione", xml_sist.richiesta_annulla(can.dati_chiamata(), "1600A0000000001"))


def test_certificato_di_un_altro_operatore_rifiutato(chiave):
    assert cf_del_certificato(chiave.certificato_der()) == TITOLARE
    with pytest.raises(ConfigurazioneNonValida, match="certificato"):
        CanaleSIST(OperatoreSIST(SOSTITUTO, "160114"), chiave, base_url="http://127.0.0.1:9/x",
                   applicativo_di_prova=APPLICATIVO)


# ------------------------------------------------------------------ datiApplicativo e WS-Security


def test_appl_digest_come_la_javadoc():
    atteso = base64.b64encode(hashlib.sha1(b"N" * 20 + b"2009-08-16T12:07:00+0100" + b"COD").digest()).decode()  # noqa: S324
    assert appl_digest("N" * 20, "2009-08-16T12:07:00+0100", "COD") == atteso


def test_dati_chiamata_nonce_e_created(chiave):
    ora = _dt.datetime(2026, 1, 15, 10, 0, 0, tzinfo=_dt.timezone.utc)
    can = CanaleSIST(OperatoreSIST(TITOLARE, "160114"), chiave, base_url="http://127.0.0.1:9/x",
                     applicativo_di_prova=APPLICATIVO, orologio=lambda: ora)
    d1, d2 = can.dati_chiamata(), can.dati_chiamata()
    assert re.fullmatch(r"[A-Za-z0-9]{20}", d1.nonce) and d1.nonce != d2.nonce
    assert d1.created == "2026-01-15T11:00:00+0100"  # ora di Roma con lo scarto, come l'esempio della javadoc
    assert d1.appl_digest == appl_digest(d1.nonce, d1.created, CODICE_APPLICATIVO)
    assert CODICE_APPLICATIVO not in repr(can.applicativo)
    assert CODICE_APPLICATIVO.encode() not in can.busta(xml_sist.richiesta_annulla(d1, "1600A0000000001"))


def test_ws_security_verificata_da_lxml_e_controllo_negativo(chiave):
    can = CanaleSIST(OperatoreSIST(TITOLARE, "160114"), chiave, base_url="http://127.0.0.1:9/x",
                     applicativo_di_prova=APPLICATIVO)
    busta = can.busta(xml_sist.richiesta_annulla(can.dati_chiamata(), "1600A0000000001"))
    esito = verifica_security(busta)
    assert esito.valida, esito.motivo
    assert cf_del_certificato(esito.certificato_der) == TITOLARE

    # gruppo di controllo: la stessa verifica DEVE fallire se si tocca ciò che è firmato
    creato = re.search(rb"<wsu:Created>([^<]+)</wsu:Created>", busta).group(1)
    alterata = busta.replace(creato, creato[:-3] + (b"01Z" if not creato.endswith(b"01Z") else b"02Z"), 1)
    assert not verifica_security(alterata).valida
    firma = re.search(rb"<ds:SignatureValue>([^<]+)</ds:SignatureValue>", busta).group(1)
    rovinata = base64.b64encode(bytes([base64.b64decode(firma)[0] ^ 1]) + base64.b64decode(firma)[1:])
    assert not verifica_security(busta.replace(firma, rovinata)).valida
    # e fuori dalla finestra di validità del Timestamp
    assert not verifica_security(busta, adesso=_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)).valida


# ------------------------------------------------------------------ formati e controlli locali


def test_formati_sist():
    assert xml_sist.data_ora_sist(_dt.datetime(2026, 3, 4, 5, 6, 7)) == "04/03/2026 05:06:07"
    assert xml_sist.data_sist(_dt.date(2026, 3, 4)) == "04/03/2026"
    assert xml_sist.codice_prestazione_sist("89.7") == "897"
    assert xml_sist.nota_aifa_sist("1") == "001"
    assert xml_sist.codice_nazionale_asl("160", "114") == "160114"
    assert cda_sist.codice_regionale_prestazione("897") == "  897"


def test_problemi_sist():
    r = _ricetta_farm(assistito=Assistito(codice_fiscale=ASSISTITO, asl="114"))
    assert any("codice_regione" in p for p in xml_sist.problemi_sist(r, _codice))
    r = _ricetta_farm(prescrittore=Prescrittore("PROVAX00X00X000W", "160", "114", "F"))
    assert any("codice regionale" in p for p in xml_sist.problemi_sist(r, _codice))
    assert xml_sist.problemi_sist(_ricetta_farm(), _codice) == []
    assert xml_sist.problemi_sist(_ricetta_spec(), _codice) == []


def test_ricerca_senza_nre_puntuale_ne_lotto():
    with pytest.raises(RicettaNonValida, match="NRE puntuale"):
        xml_sist.richiesta_ricerca(_dati(), CriteriNreUtilizzati("160", nre="1600A0000000001"), "000001")
    with pytest.raises(RicettaNonValida, match="000061"):
        xml_sist.richiesta_ricerca(_dati(), CriteriNreUtilizzati("160"), "000001")


def test_sostituto_prescrive_titolare_in_cod_medico_sostituito():
    r = _ricetta_farm(prescrittore=Prescrittore(TITOLARE, "160", "114", "F", codice_fiscale_sostituto=SOSTITUTO))
    el = xml_sist.richiesta_chk(r, _dati(), _codice)
    assert el.findtext(f"{{{xml_sist.NS}}}codMedicoPrescrittore") == "000002"
    assert el.findtext(f"{{{xml_sist.NS}}}codMedicoSostituito") == "000001"
    cda = etree.fromstring(cda_sist.genera_xml(r, "1600A0000000001", "1" * 30, codice_regionale_prescrittore="000002",
                                               codice_regionale_sostituito="000001"))
    h = {"h": "urn:hl7-org:v3"}
    assert cda.find("h:author/h:assignedAuthor/h:id", h).get("extension") == SOSTITUTO
    lic = cda.find("h:participant/h:associatedEntity[@classCode='LIC']", h)
    assert lic is not None and SOSTITUTO not in etree.tostring(lic).decode()


# ------------------------------------------------------------------ schema ufficiale CVPService.xsd


def _richieste_campione():
    from varco.fse.modello import Indirizzo, Paziente

    d = _dati()
    paz = Paziente(_ricetta_farm().assistito, "MARIA", "PROVA", "F", _dt.date(1970, 1, 1),
                   residenza=Indirizzo("BARI", "072006", via="VIA DI PROVA 1"))
    sost = Prescrittore(TITOLARE, "160", "114", "F", codice_fiscale_sostituto=SOSTITUTO)
    return {
        "chk_farmaceutica": xml_sist.richiesta_chk(_ricetta_farm(), d, _codice),
        "chk_farmaceutica_anagrafica": xml_sist.richiesta_chk(_ricetta_farm(), d, _codice, paz),
        "chk_specialistica": xml_sist.richiesta_chk(_ricetta_spec(), d, _codice),
        "chk_sostituto": xml_sist.richiesta_chk(_ricetta_farm(prescrittore=sost), d, _codice),
        "registra": xml_sist.richiesta_registra(d, base64.b64encode(b"p7m").decode(), False, "PCP-1"),
        "annulla": xml_sist.richiesta_annulla(d, "1600A0000000001"),
        "identificata": xml_sist.richiesta_identificata(d, "1600A0000000001", ASSISTITO),
        "ricerca": xml_sist.richiesta_ricerca(
            d, CriteriNreUtilizzati("160", dal=_dt.datetime(2026, 9, 1), al=_dt.datetime(2026, 9, 30)), "000001"),
    }


@richiede_xsd
@pytest.mark.parametrize("nome", list(_richieste_campione()))
def test_richieste_validano_contro_cvpservice_xsd(xsd_cvp, nome):
    el = _lx(_richieste_campione()[nome])
    assert xsd_cvp.validate(el), xsd_cvp.error_log


@richiede_xsd
def test_xsd_cvp_morde(xsd_cvp):
    """Controllo: lo schema è lasco (quasi tutto minOccurs=0), ma un tag sbagliato o fuori ordine lo rifiuta."""
    el = _lx(_richieste_campione()["chk_farmaceutica"])
    estraneo = etree.SubElement(el, f"{{{xml_sist.NS}}}tagCheNonEsiste")
    assert not xsd_cvp.validate(el)
    el.remove(estraneo)
    assert xsd_cvp.validate(el)
    tipologia = el.find(f"{{{xml_sist.NS}}}tipologia")
    el.remove(tipologia)
    el.append(tipologia)
    assert not xsd_cvp.validate(el)


@richiede_xsd
@pytest.mark.parametrize("file", sorted(p.name for p in RISPOSTE.glob("*.xml") if not p.name.startswith("fault")))
def test_risposte_sintetiche_validano_contro_cvpservice_xsd(xsd_cvp, file):
    body = etree.parse(str(RISPOSTE / file)).find(f"{{{NS_SOAP}}}Body")[0]
    assert xsd_cvp.validate(etree.fromstring(etree.tostring(body))), xsd_cvp.error_log


# ------------------------------------------------------------------ confronto con gli esempi ufficiali


def _locale(t) -> str:
    return t.rsplit("}", 1)[-1]


def _percorsi(el, pre="") -> set[str]:
    out: set[str] = set()
    for c in el:
        if isinstance(c.tag, str):
            p = f"{pre}/{_locale(c.tag)}"
            out |= {p} | _percorsi(c, p)
    return out


def _esempio(f: Path):
    """Gli esempi ufficiali non sono tutti XML ben formati: quelli rotti si scartano (docs/SAR_PUGLIA.md)."""
    try:
        return etree.fromstring(f.read_bytes())
    except etree.XMLSyntaxError:
        return None


# Percorsi che emettiamo e che gli esempi ufficiali non mostrano, con il perché.
CHK_IN_PIU = {
    "/elencoPrestazioni/prestazione/numSedute": "Nuovi LEA (4.03.x): numero di sedute; presente in CVPService.xsd",
}
CDA_IN_PIU = {
    "/component/structuredBody/component/section/entry/act/code/originalText": "CDA2_Prescrizione R12: originalText facoltativo (rimando al testo)",
    "/component/structuredBody/component/section/entry/act/code/originalText/reference": "idem",
    "/component/structuredBody/component/section/entry/observation/code/translation": "codice catalogo nazionale accanto a quello regionale (translation facoltativa)",
    "/component/structuredBody/component/section/entry/observation/entryRelationship/observation/text": "descrizione della diagnosi (quesito)",
    "/component/structuredBody/component/section/entry/substanceAdministration/entryRelationship/act": "CDA2_Prescrizione R12: nota AIFA (REFR) e motivazNote (COMP)",
    "/component/structuredBody/component/section/entry/substanceAdministration/entryRelationship/act/code": "idem",
    "/component/structuredBody/component/section/entry/substanceAdministration/entryRelationship/act/text": "CDA2_Prescrizione R12, actMotivazioneNote_IT (text obbligatorio)",
    "/participant/associatedEntity/id": "medico sostituito (classCode LIC) e ASL dell'assistito (GUAR)",
}


@richiede_esempi
def test_chk_ha_la_struttura_degli_esempi_ufficiali():
    ufficiali: set[str] = set()
    letti = 0
    for f in ESEMPI.glob("*/chkPrescrizione.xml"):
        r = _esempio(f)
        if r is None:
            continue
        letti += 1
        ufficiali |= _percorsi(next(e for e in r.iter() if isinstance(e.tag, str) and _locale(e.tag) == "chkPrescrizione"))
    assert letti >= 4
    nostri = set()
    for nome, el in _richieste_campione().items():
        if nome.startswith("chk"):
            nostri |= _percorsi(_lx(el))
    assert nostri - ufficiali <= set(CHK_IN_PIU), sorted(nostri - ufficiali - set(CHK_IN_PIU))
    # gli elementi del nucleo che gli esempi valorizzano ci sono tutti
    nucleo = {"/datiOperatore/codiceFiscale", "/datiApplicativo/applDigest", "/assistito/codIdentificativoAssistito",
              "/assistito/codNazionaleAslResidenza", "/tipologia", "/dataPrescrizione", "/codMedicoPrescrittore",
              "/codEsenzione", "/elencoPrestazioni/prestazione/codPrestazione", "/flagCiclica", "/versione", "/tipoVisita"}
    assert nucleo <= nostri


def _cda_campione() -> list[bytes]:
    from varco.fse.modello import Indirizzo, Medico, Paziente

    paz = Paziente(_ricetta_farm().assistito, "MARIA", "PROVA", "F", _dt.date(1970, 1, 1),
                   residenza=Indirizzo("BARI", "072006", via="VIA DI PROVA 1"))
    med = Medico(Prescrittore(TITOLARE, "160", "114", "F"), "MARIO", "PROVA")
    sost = Prescrittore(TITOLARE, "160", "114", "F", codice_fiscale_sostituto=SOSTITUTO)
    farm_ns = Riga(1, codice="034298051", descrizione="FARMACO DI PROVA", non_sostituibile=True,
                   codice_motivazione_non_sost="4", note="nota di prova")
    return [
        cda_sist.genera_xml(_ricetta_farm(), "1600A0000000001", "1" * 30, codice_regionale_prescrittore="000001",
                            paziente=paz, medico=med),
        cda_sist.genera_xml(_ricetta_farm(righe=(farm_ns,), disposizioni_regionali="REG"), "1600A0000000002", None,
                            codice_regionale_prescrittore="000001", maggior_tutela=True),
        cda_sist.genera_xml(_ricetta_spec(), "1600A0000000003", "1" * 30, codice_regionale_prescrittore="000001",
                            paziente=paz, medico=med),
        cda_sist.genera_xml(_ricetta_spec(prescrittore=sost), "1600A0000000004", "1" * 30,
                            codice_regionale_prescrittore="000002", codice_regionale_sostituito="000001"),
    ]


@richiede_esempi
def test_cda_ha_la_struttura_degli_esempi_ufficiali():
    ufficiali: set[str] = set()
    letti = 0
    for f in list(ESEMPI.glob("*/setRegistraPrescrizione.xml")) + list(ESEMPI.glob("*/cda_numSedute.xml")):
        r = _esempio(f)
        if r is None:
            continue
        letti += 1
        cd = r if _locale(r.tag) == "ClinicalDocument" else next(e for e in r.iter() if _locale(e.tag) == "ClinicalDocument")
        ufficiali |= _percorsi(cd)
    assert letti >= 4
    nostri: set[str] = set()
    for b in _cda_campione():
        nostri |= _percorsi(etree.fromstring(b))
    assert nostri - ufficiali <= set(CDA_IN_PIU), sorted(nostri - ufficiali - set(CDA_IN_PIU))
    intestazione = {p for p in ufficiali if p.count("/") == 1}
    assert intestazione <= {p for p in nostri if p.count("/") == 1}, sorted(intestazione - nostri)


@richiede_esempi
def test_esempi_ufficiali_non_ben_formati_sono_quelli_noti():
    """Se InnovaPuglia corregge gli esempi, questo test lo dice: va aggiornato docs/SAR_PUGLIA.md."""
    rotti = sorted(str(f.relative_to(SPEC / "definizione dei CDA")) for f in (SPEC / "definizione dei CDA").rglob("*.xml")
                   if "coreschemas" not in str(f) and _esempio(f) is None)
    assert len(rotti) >= 1
    assert any("televisita" in r for r in rotti)


# ------------------------------------------------------------------ CDA2 contro lo schema CDA del kit (sempre)


@pytest.mark.parametrize("i", range(4))
@pytest.mark.schemi_hl7
def test_cda_valida_contro_lo_schema_cda(schema_cda, i):
    doc = etree.fromstring(_cda_campione()[i])
    assert schema_cda.validate(doc), schema_cda.error_log


def test_cda_contenuti_principali():
    h = {"h": "urn:hl7-org:v3"}
    farm, _, spec, _ = (etree.fromstring(b) for b in _cda_campione())
    assert farm.find("h:templateId", h).get("extension") == "ITPRF_PRESC_FARMA-001"
    assert farm.find("h:code", h).get("code") == "57833-6"
    assert spec.find("h:code", h).get("code") == "57832-8"
    assert farm.find("h:id", h).get("extension") == "1600A0000000001"
    assert farm.find("h:id", h).get("root") == cda_sist.OID_NRE
    assert farm.find("h:setId", h).get("extension") == "1" * 30
    ids = {i.get("root"): i.get("extension") for i in farm.findall("h:author/h:assignedAuthor/h:id", h)}
    assert ids == {cda_sist.OID_CF: TITOLARE, cda_sist.OID_MEDICO_PUGLIA: "000001"}
    assert etree.fromstring(_cda_campione()[1]).find("h:confidentialityCode", h).get("code") == "V"


def test_righe_dal_cda():
    farm, _, spec, _ = _cda_campione()
    (rf,) = cda_sist.righe_dal_cda(farm)
    assert rf["codice"] == "034298051" and rf["quantita"] == "1"
    (rs,) = cda_sist.righe_dal_cda(spec)
    assert rs["quantita"] == "1"


# ------------------------------------------------------------------ lettura delle risposte (sintetiche, nel repo)


def _risposta(nome: str) -> ET.Element:
    return sbusta((RISPOSTE / nome).read_bytes(), 200)


def test_risposte_sintetiche_dichiarate():
    assert (RISPOSTE / "LEGGIMI.md").exists()
    assert "SINTETICHE" in (RISPOSTE / "LEGGIMI.md").read_text(encoding="utf-8")


def test_leggi_chk():
    e = xml_sist.leggi_chk(_risposta("chk_ok.xml"))
    assert e.ok and e.codice == "0000" and e.nre == "1600A0000000001" and len(e.codice_autenticazione) == 30
    assert e.cognome_medico == "PRO" and e.nome_medico == "VA" and not e.solo_ricetta_rossa
    e = xml_sist.leggi_chk(_risposta("chk_ok_avviso_0023.xml"))
    assert e.ok and e.codice == "0001" and [m.codice for m in e.avvisi] == ["0023"]
    e = xml_sist.leggi_chk(_risposta("chk_rifiuto_0007_0051.xml"))
    assert not e.ok and [m.codice for m in e.errori] == ["0007"] and [m.codice for m in e.avvisi] == ["0051"]
    e = xml_sist.leggi_chk(_risposta("chk_solo_iup.xml"))
    assert e.ok and e.solo_ricetta_rossa
    e = xml_sist.leggi_chk(_risposta("chk_rifiuto_0184_sac.xml"))
    assert not e.ok and e.errori[0].codice == "0184" and "1233" in e.errori[0].testo


def test_leggi_registra_e_annulla():
    assert xml_sist.leggi_registra(_risposta("registra_ok.xml")) is True
    assert xml_sist.leggi_registra(_risposta("registra_esito_false.xml")) is False
    assert xml_sist.leggi_annulla(_risposta("annulla_ok.xml"), "N").ok
    e = xml_sist.leggi_annulla(_risposta("annulla_esito_false.xml"), "N")
    assert not e.ok and e.errori[0].codice == "0184"
    with pytest.raises(ValueError, match="attesa"):
        xml_sist.leggi_annulla(_risposta("registra_ok.xml"))


def test_leggi_identificata():
    e = xml_sist.leggi_identificata(_risposta("identificata_cda.xml"))
    assert e.ok and e.nre == "1600A0000000001" and e.stato_processo == "3" and e.stato_sar == "1"
    assert e.oscurato is False and e.cda and e.cda.lstrip().startswith("<?xml")
    assert e.righe and e.righe[0]["codice"] == "000000017"
    e = xml_sist.leggi_identificata(_risposta("identificata_atomici.xml"))
    assert e.nre == "0300A0000000002" and e.righe[0]["codice"] == "000000017" and e.cda is None
    assert e.codice_autenticazione and e.testata["codRicetta"] == "0300A0000000002"


def test_leggi_ricerca():
    e = xml_sist.leggi_ricerca(_risposta("ricerca_due.xml"))
    assert [r.nre for r in e.ricette] == ["1600A0000000001", "1600A0000000002"]
    assert xml_sist.leggi_ricerca(_risposta("ricerca_vuota.xml")).ricette == ()


@pytest.mark.parametrize("file,codice", [("fault_000004.xml", "000004"), ("fault_000279.xml", "000279"),
                                         ("fault_000231.xml", "000231")])
def test_fault_sist(file, codice):
    with pytest.raises(ErroreSOAP) as e:
        _risposta(file)
    assert xml_sist.codice_fault_sist(e.value) == codice


def test_codice_fault_non_pesca_nel_base64():
    corpo = (RISPOSTE / "fault_000004.xml").read_bytes().replace(b"000004: ", b"").replace(b"<codice>000004</codice>", b"")
    corpo = corpo.replace(b"RklOVE8=", b"MTIzNDU2Nzg5MDEy")  # cifre dentro il KeyIdentifier base64
    with pytest.raises(ErroreSOAP) as e:
        sbusta(corpo, 500)
    assert xml_sist.codice_fault_sist(e.value) is None


# ------------------------------------------------------------------ registro redatto


def test_registro_reda_i_tag_sist(chiave):
    can = CanaleSIST(OperatoreSIST(TITOLARE, "160114"), chiave, base_url="http://127.0.0.1:9/x",
                     applicativo_di_prova=APPLICATIVO)
    from varco.fse.modello import Indirizzo, Paziente

    paz = Paziente(_ricetta_farm().assistito, "MARIA", "PROVANOMEUNICO", "F", _dt.date(1970, 1, 1),
                   residenza=Indirizzo("COMUNEUNICO", "072006", via="VIA UNICA 1"))
    busta = can.busta(xml_sist.richiesta_chk(_ricetta_farm(nre="1600A0000000009"), can.dati_chiamata(), _codice, paz))
    testo = Redattore().corpo(busta).decode()
    for chiaro in (TITOLARE, ASSISTITO, "PROVANOMEUNICO", "COMUNEUNICO", "VIA UNICA", "1600A0000000009",
                   ">000001<", "01/01/1970"):
        assert chiaro not in testo, chiaro
    risposta = Redattore().corpo((RISPOSTE / "identificata_cda.xml").read_bytes()).decode()
    assert ASSISTITO not in risposta and "1600A0000000001" not in risposta and "ClinicalDocument" not in risposta


# ------------------------------------------------------------------ giro completo contro il server finto


class TrasportoLocale:
    """Come TrasportoHTTP (stessa guardia), senza il limitatore a 2 richieste/s: solo verso 127.0.0.1."""

    def __init__(self):
        self.guasti: set[str] = set()  # servizi per cui simulare un timeout
        self.richieste: list[Richiesta] = []

    def invia(self, richiesta: Richiesta) -> Risposta:
        verifica_url_consentito(richiesta.url)
        assert richiesta.url.startswith("http://127.0.0.1:")
        self.richieste.append(richiesta)
        if richiesta.servizio in self.guasti:
            self.guasti.discard(richiesta.servizio)
            raise ErroreTrasporto("timeout simulato")
        req = urllib.request.Request(richiesta.url, data=richiesta.corpo, method="POST", headers=richiesta.intestazioni)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - solo 127.0.0.1
                return Risposta(r.status, r.read(), dict(r.headers.items()), 0.0)
        except urllib.error.HTTPError as e:
            return Risposta(e.code, e.read(), dict(e.headers.items()), 0.0)


@pytest.fixture
def server():
    with SERVER.ServerSIST(CODICE_APPLICATIVO, xsd_cvp=XSD_CVP if XSD_CVP.exists() else None) as s:
        yield s


def _servizio(server, p12, cf=TITOLARE, trasporto=None, **kw) -> RicettaSIST:
    can = CanaleSIST(OperatoreSIST(cf, "160114"), ChiavePKCS12(str(p12[cf]), b"pw"), base_url=server.url,
                     applicativo_di_prova=APPLICATIVO, trasporto=trasporto or TrasportoLocale())
    return RicettaSIST(can, FirmatarioCAdESPKCS12(str(p12[cf]), b"pw"), CODICI_REGIONALI, **kw)


@pytest.mark.schemi_hl7
def test_e2e_con_trasporto_http_vero(server, p12):
    """Una volta col trasporto vero (limitatore, guardia, urllib): due chiamate, ~0,5 s."""
    s = _servizio(server, p12, trasporto=TrasportoHTTP(intervallo_minimo_s=0.5))
    e = s.invia(_ricetta_farm())
    assert e.ok and e.registrato is True, (e, e.errore_registrazione)


@pytest.mark.schemi_hl7
def test_e2e_invia_visualizza_annulla(server, p12):
    s = _servizio(server, p12)
    e = s.invia(_ricetta_farm(), oscurato=True, id_pcp="PCP-PROVA")
    assert e.ok and e.registrato is True and e.errore_registrazione is None
    assert e.nre.startswith("1600A") and len(e.codice_autenticazione) == 30 and not e.solo_ricetta_rossa
    assert e.cda and e.cda_firmato and e.cda not in repr(e).encode()
    assert e.cognome_medico == "PRO"
    # il server ha davvero ricevuto il CDA firmato con la CAdES del medico
    assert server.stato.prescrizioni[e.nre].cda == e.cda

    with pytest.raises(ValueError, match="cf_assistito"):
        s.visualizza(e.nre)
    v = s.visualizza(e.nre, cf_assistito=ASSISTITO)
    assert v.ok and v.nre == e.nre and v.stato_sar == "1" and v.stato_processo == "3"
    assert v.cda and v.righe[0]["codice"] == "034298051"
    altro = s.visualizza(e.nre, cf_assistito="PROVAX00X00X000X")  # identificazione forte: CF sbagliato
    assert not altro.ok and altro.errori[0].codice == "000004"

    a = s.annulla(e.nre)
    assert a.ok and a.nre == e.nre
    di_nuovo = s.annulla(e.nre)
    assert not di_nuovo.ok and di_nuovo.errori[0].codice == "000279"
    assert s.visualizza(e.nre, cf_assistito=ASSISTITO).stato_sar == "0"


@pytest.mark.schemi_hl7
def test_e2e_specialistica_e_ricerca(server, p12):
    s = _servizio(server, p12)
    e1 = s.invia(_ricetta_spec())
    e2 = s.invia(_ricetta_farm())
    assert e1.registrato and e2.registrato
    oggi = _dt.datetime.now()
    r = s.interroga_nre_utilizzati(CriteriNreUtilizzati("160", cf_assistito=ASSISTITO, dal=oggi, al=oggi))
    assert r.ok and {x.nre for x in r.ricette} == {e1.nre, e2.nre}
    with pytest.raises(ValueError, match="CNS"):
        s.annulla(e1.nre, cf_medico=SOSTITUTO)


@pytest.mark.schemi_hl7
def test_e2e_anomalie_bloccanti_niente_registrazione(server, p12):
    s = _servizio(server, p12)
    riga = Riga(1, codice="034298051", descrizione="FARMACO DI PROVA", nota_aifa="999")
    e = s.invia(_ricetta_farm(righe=(riga,)))
    assert not e.ok and e.registrato is None and e.cda is None
    assert {m.codice for m in e.errori} == {"0053"} and {m.codice for m in e.avvisi} == {"0051"}
    assert [op for op, _ in server.stato.richieste] == [f'"{SERVER.AZIONE}chkPrescrizione"']


@pytest.mark.schemi_hl7
def test_e2e_sac_non_disponibile_ricetta_rossa(server, p12):
    """Solo IUP: si stampa la ricetta rossa, ma il CDA (con il solo NRE) si firma e si registra (Appendice A)."""
    s = _servizio(server, p12, valida_localmente=False)
    e = s.invia(_ricetta_farm(assistito=Assistito(codice_fiscale="SAC_GIU_PROVA000", asl="114", codice_regione="160")))
    assert e.ok and e.solo_ricetta_rossa and e.registrato is True
    h = {"h": "urn:hl7-org:v3"}
    # CDA2_Prescrizione, setId_IT: senza codice di autenticazione il setId porta l'NRE
    assert etree.fromstring(e.cda).find("h:setId", h).get("extension") == e.nre


@pytest.mark.schemi_hl7
def test_e2e_registrazione_fallita_si_ripete(server, p12):
    t = TrasportoLocale()
    t.guasti.add("sist.setRegistraPrescrizione")
    s = _servizio(server, p12, trasporto=t)
    e = s.invia(_ricetta_farm())
    assert e.ok and e.nre and e.registrato is False and "timeout" in e.errore_registrazione
    assert e.da_ripetere
    assert server.stato.prescrizioni[e.nre].cda is None  # al SIST manca il CDA, al SAC la ricetta c'è già
    chk_prima = sum(1 for r in t.richieste if r.servizio == "sist.chkPrescrizione")
    e2 = s.ripeti_registrazione(e)
    assert e2.registrato is True and not e2.da_ripetere and e2.nre == e.nre
    assert sum(1 for r in t.richieste if r.servizio == "sist.chkPrescrizione") == chk_prima  # nessun nuovo NRE
    assert server.stato.prescrizioni[e.nre].cda == e.cda


@pytest.mark.schemi_hl7
def test_e2e_sostituto(server, p12):
    pr = Prescrittore(TITOLARE, "160", "114", "F", codice_fiscale_sostituto=SOSTITUTO)
    with pytest.raises(RicettaNonValida, match="000271"):
        _servizio(server, p12, cf=TITOLARE).invia(_ricetta_farm(prescrittore=pr))
    e = _servizio(server, p12, cf=SOSTITUTO).invia(_ricetta_farm(prescrittore=pr))
    assert e.ok and e.registrato is True
    assert server.stato.prescrizioni[e.nre].operatore == SOSTITUTO


@pytest.mark.schemi_hl7
def test_e2e_server_rifiuta_applicativo_e_firmatario_sbagliati(server, p12):
    can = CanaleSIST(OperatoreSIST(TITOLARE, "160114"), ChiavePKCS12(str(p12[TITOLARE]), b"pw"), base_url=server.url,
                     applicativo_di_prova=dataclasses.replace(APPLICATIVO, codice_applicativo="ALTRO"),
                     trasporto=TrasportoLocale())
    with pytest.raises(ErroreSOAP) as e:
        RicettaSIST(can, FirmatarioCAdESPKCS12(str(p12[TITOLARE]), b"pw"), CODICI_REGIONALI).invia(_ricetta_farm())
    assert xml_sist.codice_fault_sist(e.value) == "000220"
    # CDA firmato da un altro medico: il server risponde 000271 e la registrazione resta da ripetere
    s = RicettaSIST(_servizio(server, p12).canale, FirmatarioCAdESPKCS12(str(p12[SOSTITUTO]), b"pw"), CODICI_REGIONALI)
    e = s.invia(_ricetta_farm())
    assert e.ok and e.registrato is False and "000271" in e.errore_registrazione


def test_contratto_comune_visualizza_con_cf_assistito():
    """L'unico punto in cui il contratto si piega: SAC e SIST accettano entrambi cf_assistito."""
    import inspect

    from varco.ricetta.servizio import RicettaSAC, ServizioRicetta

    for cls in (ServizioRicetta, RicettaSAC, RicettaSIST):
        p = inspect.signature(cls.visualizza).parameters
        assert p["cf_assistito"].kind is inspect.Parameter.KEYWORD_ONLY and p["cf_assistito"].default is None


# ------------------------------------------------------------------ revisione esterna 02/10/2026 (3-sar-puglia.md)
# Ogni test riproduce un controesempio del revisore. Fallivano tutti sul codice di prima
# (client e server finto): verificato su una copia, vedi la consegna.


class FirmatarioCheSiStacca:
    """La CNS tolta tra il controllo e la firma: la prima firma fallisce, le altre no."""

    def __init__(self, vero):
        self.vero, self.fallite = vero, 0

    def firma_cades(self, dati: bytes) -> bytes:
        if not self.fallite:
            self.fallite += 1
            raise RuntimeError("CNS rimossa")
        return self.vero.firma_cades(dati)


def _azioni(server) -> list[str]:
    return [a.strip('"').rsplit("#", 1)[-1] for a, _ in server.stato.richieste]


@pytest.mark.schemi_hl7
def test_rev1_ripeti_registrazione_conserva_oscurato_e_pcp(server, p12):
    """Punto 1: dopo un timeout, `ripeti_registrazione(e)` rimandava oscurato=false e niente idPCP."""
    t = TrasportoLocale()
    t.guasti.add("sist.setRegistraPrescrizione")
    s = _servizio(server, p12, trasporto=t)
    e = s.invia(_ricetta_farm(), oscurato=True, id_pcp="PCP-PROVA")
    assert e.da_ripetere and e.oscurato is True and e.id_pcp == "PCP-PROVA"
    e2 = s.ripeti_registrazione(e)
    assert e2.registrato is True
    registra = [r for r in t.richieste if r.servizio == "sist.setRegistraPrescrizione"]
    assert len(registra) == 2
    for r in registra:  # le due richieste dicono la stessa cosa
        assert b"oscurato>true<" in r.corpo and b"idPCP>PCP-PROVA<" in r.corpo
    presc = server.stato.prescrizioni[e.nre]
    assert presc.oscurato is True and presc.id_pcp == "PCP-PROVA"
    assert s.visualizza(e.nre, cf_assistito=ASSISTITO).oscurato is True
    with pytest.raises(ValueError, match="oscurato"):
        s.ripeti_registrazione(e, oscurato=False)  # la volontà dell'assistito non cambia con un timeout


@pytest.mark.schemi_hl7
def test_rev1_server_conserva_il_non_oscurato(server, p12):
    """Gruppo di controllo del punto 1: senza oscuramento il server dice false."""
    s = _servizio(server, p12)
    e = s.invia(_ricetta_farm())
    assert server.stato.prescrizioni[e.nre].oscurato is False
    assert s.visualizza(e.nre, cf_assistito=ASSISTITO).oscurato is False


@pytest.mark.schemi_hl7
def test_rev2_firma_fallita_dopo_il_controllo_da_un_esito_recuperabile(server, p12):
    """Punto 2: CNS tolta dopo chkPrescrizione. Prima usciva RuntimeError e nessun esito."""
    t = TrasportoLocale()
    can = CanaleSIST(OperatoreSIST(TITOLARE, "160114"), ChiavePKCS12(str(p12[TITOLARE]), b"pw"), base_url=server.url,
                     applicativo_di_prova=APPLICATIVO, trasporto=t)
    s = RicettaSIST(can, FirmatarioCheSiStacca(FirmatarioCAdESPKCS12(str(p12[TITOLARE]), b"pw")), CODICI_REGIONALI)
    e = s.invia(_ricetta_farm(), oscurato=True)
    assert e.ok and e.nre and e.registrato is False and e.da_ripetere
    assert "CNS rimossa" in e.errore_registrazione and e.cda_firmato is None
    assert _azioni(server) == ["chkPrescrizione"]
    e2 = s.ripeti_registrazione(e)  # CNS reinserita: CDA e firma si rifanno, nessun nuovo controllo
    assert e2.registrato is True and e2.nre == e.nre and e2.cda_firmato
    assert _azioni(server) == ["chkPrescrizione", "setRegistraPrescrizione"]
    assert server.stato.prescrizioni[e.nre].oscurato is True


@pytest.mark.parametrize("valida_localmente", [True, False])
@pytest.mark.schemi_hl7
def test_rev2_motivo_non_sostituibilita_rifiutato_prima_del_controllo(server, p12, valida_localmente):
    """Punto 2: il motivo 5 era rifiutato DENTRO il CDA, dopo chkPrescrizione."""
    s = _servizio(server, p12, valida_localmente=valida_localmente)
    riga = Riga(1, codice="034298051", descrizione="FARMACO DI PROVA", non_sostituibile=True,
                codice_motivazione_non_sost="5")
    with pytest.raises(RicettaNonValida, match="motivo di non sostituibilità"):
        s.invia(_ricetta_farm(righe=(riga,)))
    assert server.stato.richieste == [] and server.stato.prescrizioni == {}


def test_rev3_iup_regionale_con_oid_e_autorita_della_regione():
    """Punto 3: lo IUP finiva con l'OID dell'NRE e autorità MEF (CDA2_Prescrizione p. 6 e 19)."""
    h = {"h": "urn:hl7-org:v3"}
    d = cda_sist.genera(_ricetta_farm(), "00M6RG0005UDM", None, codice_regionale_prescrittore="000001")
    ident, setid = d.find(f"{{{cda_sist.HL7}}}id"), d.find(f"{{{cda_sist.HL7}}}setId")
    assert (ident.get("root"), ident.get("extension"), ident.get("assigningAuthorityName")) == (
        "2.16.840.1.113883.2.9.4.3.6", "00M6RG0005UDM", "Regione Puglia")
    assert (setid.get("root"), setid.get("extension"), setid.get("assigningAuthorityName")) == (
        "2.16.840.1.113883.2.9.4.3.6", "00M6RG0005UDM", "Regione Puglia")
    # gruppo di controllo: con l'NRE restano OID e autorità del MEF
    n = etree.fromstring(cda_sist.genera_xml(_ricetta_farm(), "1600A0000000001", None, codice_regionale_prescrittore="000001"))
    assert n.find("h:id", h).get("root") == "2.16.840.1.113883.2.9.4.3.8" and n.find("h:id", h).get("assigningAuthorityName") == "MEF"
    assert n.find("h:setId", h).get("root") == "2.16.840.1.113883.2.9.2.4.3.20"
    with pytest.raises(RicettaNonValida, match="né NRE"):
        cda_sist.genera(_ricetta_farm(), "123", None, codice_regionale_prescrittore="000001")


@pytest.mark.schemi_hl7
def test_rev3_server_da_uno_iup_vero_e_rifiuta_lo_iup_codificato_come_nre(server, p12):
    """Punto 3: il server dava un NRE anche nel caso «solo IUP» e non guardava l'OID."""
    s = _servizio(server, p12, valida_localmente=False)
    e = s.invia(_ricetta_farm(assistito=Assistito(codice_fiscale="SAC_GIU_PROVA000", asl="114", codice_regione="160")))
    assert e.ok and e.solo_ricetta_rossa and len(e.nre) == 13 and cda_sist.e_iup(e.nre)
    assert e.registrato is True, e.errore_registrazione
    assert SERVER.iup_online("000001", 1) == SERVER.iup_online("000001", 1) and len(SERVER.iup_online("999999", 5)) == 13
    # il CDA che il client di prima scriveva per lo stesso IUP: OID dell'NRE, autorità MEF
    sbagliato = (e.cda.replace(b'root="2.16.840.1.113883.2.9.4.3.6"', b'root="2.16.840.1.113883.2.9.4.3.8"', 1)
                 .replace(b'assigningAuthorityName="Regione Puglia"', b'assigningAuthorityName="MEF"', 1))
    assert sbagliato != e.cda
    firmato = FirmatarioCAdESPKCS12(str(p12[TITOLARE]), b"pw").firma_cades(sbagliato)
    r = s.ripeti_registrazione(dataclasses.replace(e, cda=sbagliato, cda_firmato=firmato))
    assert r.registrato is False and "000218" in r.errore_registrazione


@pytest.mark.parametrize("tipo", ["ST", "NA"])
@pytest.mark.schemi_hl7
def test_rev4_stp_e_sasn_senza_asl(server, p12, tipo):
    """Punto 4: l'ASL di residenza è obbligatoria solo per gli assistiti SSN (CDA2_Prescrizione p. 2)."""
    cf = "STP1601010000001" if tipo == "ST" else ASSISTITO
    a = Assistito(codice_fiscale=cf, tipo_ricetta=tipo, num_tessera_sasn="12345" if tipo == "NA" else None,
                  societa_navigazione="SOCIETA DI PROVA" if tipo == "NA" else None)
    r = _ricetta_farm(assistito=a)
    assert r.problemi() == [] and xml_sist.problemi_sist(r, _codice) == []
    e = _servizio(server, p12).invia(r)
    assert e.ok and e.registrato is True, e.errore_registrazione
    # gruppo di controllo: l'assistito SSN senza ASL resta un problema, in locale e al server
    ssn = _ricetta_farm(assistito=Assistito(codice_fiscale=ASSISTITO))
    assert any("ASL di residenza" in p for p in xml_sist.problemi_sist(ssn, _codice))
    e = _servizio(server, p12, valida_localmente=False).invia(ssn)
    assert e.ok and e.registrato is False and "000218" in e.errore_registrazione and "ASL" in e.errore_registrazione


@pytest.mark.parametrize("valore,atteso", [("1", True), ("0", False), ("true", True), ("false", False)])
def test_rev5_oscurato_xs_boolean(valore, atteso):
    """Punto 5: `oscurato` è xs:boolean (CVPService.xsd): "1" era letto come falso."""
    corpo = (RISPOSTE / "identificata_cda.xml").read_bytes()
    assert b"<SANITA:oscurato>false</SANITA:oscurato>" in corpo
    corpo = corpo.replace(b"<SANITA:oscurato>false</SANITA:oscurato>", f"<SANITA:oscurato>{valore}</SANITA:oscurato>".encode())
    assert xml_sist.leggi_identificata(sbusta(corpo, 200)).oscurato is atteso


def test_rev5_oscurato_non_booleano_non_si_indovina():
    corpo = (RISPOSTE / "identificata_cda.xml").read_bytes().replace(
        b"<SANITA:oscurato>false</SANITA:oscurato>", b"<SANITA:oscurato>forse</SANITA:oscurato>")
    with pytest.raises(ValueError, match="xs:boolean"):
        xml_sist.leggi_identificata(sbusta(corpo, 200))


@pytest.mark.schemi_hl7
def test_rev5_server_con_booleani_numerici(server, p12):
    server.stato.booleani_numerici = True
    s = _servizio(server, p12)
    e = s.invia(_ricetta_farm(), oscurato=True)
    assert b"<SANITA:oscurato>1</SANITA:oscurato>" in _scambio_identificata(s, e.nre)
    assert s.visualizza(e.nre, cf_assistito=ASSISTITO).oscurato is True


def _scambio_identificata(s, nre) -> bytes:
    s.visualizza(nre, cf_assistito=ASSISTITO)
    return s.ultimo.grezza.xml_risposta


@pytest.mark.schemi_hl7
def test_rev6_firma_di_un_altro_giorno_000303(server, p12):
    """Punto 6: CDA creato due giorni prima della firma. Il server finto rispondeva esito TRUE."""
    due_giorni_fa = lambda: _dt.datetime.now() - _dt.timedelta(days=2)  # noqa: E731
    e = _servizio(server, p12, orologio=due_giorni_fa).invia(_ricetta_farm())
    assert e.ok and e.registrato is False and "000303" in e.errore_registrazione
    assert server.stato.prescrizioni[e.nre].cda is None


def test_rev6_firma_prima_della_creazione_000304():
    h = "urn:hl7-org:v3"
    doc = etree.fromstring(f'<ClinicalDocument xmlns="{h}"><effectiveTime value="20261002101500"/></ClinicalDocument>'.encode())
    roma = _dt.timezone(_dt.timedelta(hours=2))  # 2 ottobre: ora legale
    assert SERVER._difetto_tempi(doc, _dt.datetime(2026, 10, 2, 10, 14, 59, tzinfo=roma))[0] == "000304"
    assert SERVER._difetto_tempi(doc, _dt.datetime(2026, 10, 3, 10, 16, 0, tzinfo=roma))[0] == "000303"
    assert SERVER._difetto_tempi(doc, _dt.datetime(2026, 10, 2, 8, 15, 0, tzinfo=_dt.timezone.utc)) is None  # 10:15 a Roma
    assert SERVER._difetto_tempi(doc, _dt.datetime(2026, 10, 2, 10, 15, 1, tzinfo=roma)) is None


@pytest.mark.schemi_hl7
def test_rev7_ricerca_applica_periodo_tipo_e_prescrittore(server, p12):
    """Punto 7: una farmaceutica del 2020 usciva da una ricerca di ottobre 2026, specialistica, prescrittore 999999."""
    server.stato.prescrizioni["1600A0000000001"] = SERVER.PrescrizioneFinta(
        "1600A0000000001", ASSISTITO, TITOLARE, "1", "01/01/2020 10:00:00", codice_prescrittore="000001")
    can = _servizio(server, p12).canale
    criteri = CriteriNreUtilizzati("160", tipo=TipoPrescrizione.SPECIALISTICA,
                                   dal=_dt.datetime(2026, 10, 1), al=_dt.datetime(2026, 10, 31))
    altro = RicettaSIST(can, FirmatarioCAdESPKCS12(str(p12[TITOLARE]), b"pw"), {TITOLARE: "999999"})
    assert altro.interroga_nre_utilizzati(criteri).ricette == ()
    s = RicettaSIST(can, FirmatarioCAdESPKCS12(str(p12[TITOLARE]), b"pw"), CODICI_REGIONALI)
    assert s.interroga_nre_utilizzati(criteri).ricette == ()  # tipo e periodo sbagliati
    assert s.interroga_nre_utilizzati(dataclasses.replace(criteri, tipo=TipoPrescrizione.FARMACEUTICA)).ricette == ()
    giusti = CriteriNreUtilizzati("160", tipo=TipoPrescrizione.FARMACEUTICA,
                                  dal=_dt.datetime(2020, 1, 1), al=_dt.datetime(2020, 1, 1))
    assert [r.nre for r in s.interroga_nre_utilizzati(giusti).ricette] == ["1600A0000000001"]  # gruppo di controllo
    assert altro.interroga_nre_utilizzati(giusti).ricette == ()
    assert s.interroga_nre_utilizzati(dataclasses.replace(giusti, cf_assistito="PROVAX00X00X000X")).ricette == ()


@pytest.mark.schemi_hl7
def test_rev7_ricerca_senza_periodo_000061(server, p12):
    s = _servizio(server, p12)
    el = xml_sist.richiesta_ricerca(s.canale.dati_chiamata(), CriteriNreUtilizzati(
        "160", dal=_dt.datetime(2026, 10, 1), al=_dt.datetime(2026, 10, 2)), "000001")
    for nome in ("dataEmissioneDal", "dataEmissioneAl"):
        el.remove(el.find(f"{{{xml_sist.NS}}}{nome}"))
    with pytest.raises(ErroreSOAP) as e:
        s.canale.chiama("getPrescrizioniIdentificate", el)
    assert xml_sist.codice_fault_sist(e.value) == "000061"


@pytest.mark.schemi_hl7
def test_rev8_mood_code_documentato_come_emesso(server, p12):
    """Punto 8: la documentazione diceva RQO («il kit segue la tabella»), il codice emette PRMS.
    La tabella ObservationPrestPrescitte_it (CDA2_Prescrizione p. 195) dice PRMS: il codice era giusto."""
    h = {"h": "urn:hl7-org:v3"}
    spec = etree.fromstring(_cda_campione()[2])
    assert {o.get("moodCode") for o in spec.findall("h:component/h:structuredBody/h:component/h:section/h:entry/h:observation", h)} == {"PRMS"}
    doc = (RADICE / "docs" / "SAR_PUGLIA.md").read_text(encoding="utf-8")
    voce = doc[doc.index("**moodCode dell'osservazione specialistica:**"):]
    voce = voce[:voce.index("\n7. ")]
    assert "Il kit emette `PRMS`" in voce and "p. 195" in voce
    # e il server finto rifiuta l'altra lettura
    s = _servizio(server, p12)
    e = s.invia(_ricetta_spec())
    assert e.registrato is True
    rqo = e.cda.replace(b'<observation classCode="OBS" moodCode="PRMS">', b'<observation classCode="OBS" moodCode="RQO">', 1)
    assert rqo != e.cda
    r = s.ripeti_registrazione(dataclasses.replace(e, cda=rqo, cda_firmato=FirmatarioCAdESPKCS12(str(p12[TITOLARE]), b"pw").firma_cades(rqo)))
    assert r.registrato is False and "000218" in r.errore_registrazione and "PRMS" in r.errore_registrazione

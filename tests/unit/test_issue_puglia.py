# SPDX-License-Identifier: EUPL-1.2
"""Issue #3, #4, #5 (limiti noti della 0.1.0, SAR Puglia): controesempi delle issue, rossi prima e
verdi dopo. Fixture e aiuti da test_sist.py (server finto locale, certificati di prova)."""

from __future__ import annotations

import dataclasses
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

import pytest

from test_sist import (  # noqa: F401 - fixture
    APPLICATIVO, TITOLARE, _azioni, _ricetta_farm, _servizio, chiave, p12, server,
)

from varco.errori import RicettaNonValida
from varco.ricetta import Assistito, cda_sist, xml_sist
from varco.trasporto.sist import CanaleSIST, OperatoreSIST
from varco.trasporto.wssecurity import NS_WSSE, verifica_security

etree = pytest.importorskip("lxml.etree")
H = {"h": "urn:hl7-org:v3"}
SOAP = "{http://schemas.xmlsoap.org/soap/envelope/}"
ESTERO = dict(tipo_ricetta="UE", stato_estero="FR", num_ident_tessera="TEAM-PROVA",
              istituzione_competente="ENTE PROVA", data_scadenza_tessera="2027-12-31")


def _presc(server_mod):
    return server_mod.PrescrizioneFinta(nre="1600A4000000001", cf_assistito="PNIMRA70A01H501P",
                                        operatore=TITOLARE, tipologia="F", data="2026-10-03")


def _ids(xml: bytes) -> list[tuple[str, str]]:
    doc = etree.fromstring(xml)
    return [(i.get("root"), i.get("extension")) for i in doc.findall("h:recordTarget/h:patientRole/h:id", H)]


# ------------------------------------------------------------------ #3 assicurati esteri


def test_issue3_senza_identificativo_personale_e_un_problema_prima_del_controllo():
    r = _ricetta_farm(assistito=Assistito(**ESTERO))
    problemi = cda_sist.problemi_righe_cda(r)
    assert any("num_ident_personale" in p for p in problemi), problemi


def test_issue3_con_il_cf_team_e_personale_non_si_perdono():
    a = Assistito(codice_fiscale="PNIMRA70A01H501P", num_ident_personale="PERSONA-PROVA", **ESTERO)
    r = _ricetta_farm(assistito=a)
    assert cda_sist.problemi_righe_cda(r) == []
    ids = _ids(cda_sist.genera_xml(r, "1600A4000000001", None, codice_regionale_prescrittore="000001"))
    assert (cda_sist.OID_TESSERA_TEAM, "FR.TEAM-PROVA") in ids
    assert (cda_sist.OID_IDENT_PERSONALE_ESTERO, "FR.PERSONA-PROVA") in ids
    assert not any(root == cda_sist.OID_CF for root, _ in ids), "accanto all'id principale solo TS e SASN (p. 23)"


@pytest.mark.schemi_hl7
def test_issue3_invio_senza_personale_rifiutato_prima_della_rete(server, p12):
    s = _servizio(server, p12)
    with pytest.raises(RicettaNonValida):
        s.invia(_ricetta_farm(assistito=Assistito(**ESTERO)))
    assert _azioni(server) == []


def test_issue3_server_finto_rifiuta_cda_estero_senza_personale():
    from test_sist import SERVER

    a = Assistito(num_ident_personale="PERSONA-PROVA", **ESTERO)
    r = _ricetta_farm(assistito=a)
    xml = cda_sist.genera_xml(r, "1600A4000000001", None, codice_regionale_prescrittore="000001")
    presc = _presc(SERVER)
    doc = etree.fromstring(xml)
    assert SERVER._difetto_cda(doc, presc) is None
    # gruppo di controllo: tolto l'identificativo personale, il server finto deve rifiutare
    for i in doc.findall("h:recordTarget/h:patientRole/h:id", H):
        if i.get("root") == cda_sist.OID_IDENT_PERSONALE_ESTERO:
            i.getparent().remove(i)
    assert "identificativo personale" in (SERVER._difetto_cda(doc, presc) or "")


# ------------------------------------------------------------------ #4 displayName del motivo


def _codice_motivo(xml: bytes):
    return etree.fromstring(xml).find(f".//h:code[@codeSystem='{cda_sist.OID_MOTIVO_NON_SOST}']", H)


@pytest.mark.parametrize("motivo", ["1", "2", "3", "4"])
def test_issue4_motivo_non_sostituibilita_con_display_name(motivo):
    r = _ricetta_farm()
    riga = dataclasses.replace(r.righe[0], non_sostituibile=True, codice_motivazione_non_sost=motivo)
    c = _codice_motivo(cda_sist.genera_xml(dataclasses.replace(r, righe=(riga,)), "1600A4000000001", None, codice_regionale_prescrittore="000001"))
    assert c is not None and c.get("code") == motivo and (c.get("displayName") or "").strip()
    if motivo == "2":  # l'unica descrizione che la specifica pugliese pubblica (p. 162)
        assert c.get("displayName") == "Obiettive difficoltà di assunzione"


def test_issue4_server_finto_rifiuta_motivo_senza_display_name():
    from test_sist import SERVER

    r = _ricetta_farm()
    riga = dataclasses.replace(r.righe[0], non_sostituibile=True, codice_motivazione_non_sost="2")
    doc = etree.fromstring(cda_sist.genera_xml(dataclasses.replace(r, righe=(riga,)), "1600A4000000001", None, codice_regionale_prescrittore="000001"))
    presc = _presc(SERVER)
    assert SERVER._difetto_cda(doc, presc) is None
    del doc.find(f".//h:code[@codeSystem='{cda_sist.OID_MOTIVO_NON_SOST}']", H).attrib["displayName"]
    assert "displayName" in (SERVER._difetto_cda(doc, presc) or "")


# ------------------------------------------------------------------ #5 WS-Security nel Body


def _busta(chiave) -> bytes:
    can = CanaleSIST(OperatoreSIST(TITOLARE, "160114"), chiave, base_url="http://127.0.0.1:9/x",
                     applicativo_di_prova=APPLICATIVO)
    return can.busta(xml_sist.richiesta_annulla(can.dati_chiamata(), "1600A0000000001"))


def _sposta_nel_body(busta: bytes) -> bytes:
    doc = etree.fromstring(busta)
    sec = doc.find(".//{" + NS_WSSE + "}Security")
    doc.find(SOAP + "Body").append(sec)
    return etree.tostring(doc)


def test_issue5_security_nel_body_rifiutata(chiave):
    busta = _busta(chiave)
    assert verifica_security(busta).valida  # gruppo di controllo
    spostata = _sposta_nel_body(busta)
    assert etree.fromstring(spostata).find(SOAP + "Header/{" + NS_WSSE + "}Security") is None
    assert not verifica_security(spostata).valida


def test_issue5_security_duplicata_nel_body_rifiutata(chiave):
    doc = etree.fromstring(_busta(chiave))
    import copy
    doc.find(SOAP + "Body").append(copy.deepcopy(doc.find(SOAP + "Header/{" + NS_WSSE + "}Security")))
    assert not verifica_security(etree.tostring(doc)).valida


@pytest.mark.schemi_hl7
def test_issue5_server_finto_rifiuta_security_nel_body(server, chiave):
    spostata = _sposta_nel_body(_busta(chiave))
    req = urllib.request.Request(server.url, data=spostata, method="POST",
                                 headers={"Content-Type": "text/xml", "SOAPAction": "x"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            corpo, codice = r.read(), r.status
    except urllib.error.HTTPError as e:
        corpo, codice = e.read(), e.code
    assert codice == 500 and b"000265" in corpo, corpo[:300]

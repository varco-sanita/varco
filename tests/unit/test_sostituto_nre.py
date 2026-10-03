# SPDX-License-Identifier: EUPL-1.2
"""Medico sostituto e InterrogaNreUtilizzati, senza rete (risposte reali registrate il 30/09/2026)."""

import dataclasses
import datetime as dt
import xml.etree.ElementTree as ET

import pytest

from varco import AmbienteBloccato, Credenziali, RicettaNonValida, TrasportoHTTP, kit_mef
from varco.ambienti import e_produzione
from varco.conformita.motore import cartella_conformita
from varco.ricetta import (
    Assistito,
    CriteriNreUtilizzati,
    Messaggio,
    Prescrittore,
    Ricetta,
    RicettaSAC,
    Riga,
    TipoPrescrizione,
)
from varco.ricetta import xml_sac
from varco.schemi import errori_xsd
from varco.trasporto import CanaleSAC, Richiesta, Risposta
from varco.trasporto.soap import sbusta

TITOLARE = Prescrittore("PROVAX00X00X000Y", "130", "201", "F")
CON_SOSTITUTO = dataclasses.replace(TITOLARE, codice_fiscale_sostituto="PROVAX00X00X000Z")
ASS = Assistito(codice_fiscale="PNIMRA70A01H501P", provincia="AQ", asl="201")
RIGA = Riga(1, codice_gruppo_equivalenza="G3B", descrizione_gruppo_equivalenza="LEVETIRACETAM 500MG 60 UNITA' USO ORALE")
OGGI = dt.datetime(2026, 9, 30)


def risposta(nome: str) -> ET.Element:
    return sbusta(cartella_conformita().joinpath("risposte", nome).read_bytes(), 200)


class TrasportoFinto:
    def __init__(self, risposta_xml: bytes):
        self.risposta_xml = risposta_xml
        self.richieste: list[Richiesta] = []

    def invia(self, richiesta: Richiesta) -> Risposta:
        self.richieste.append(richiesta)
        return Risposta(200, self.risposta_xml, {}, 0.01)


def servizio(utente: str, file_risposta: str = "invio_ok.xml", **kw):
    t = TrasportoFinto(cartella_conformita().joinpath("risposte", file_risposta).read_bytes())
    return RicettaSAC(CanaleSAC(Credenziali(utente, "pw", "1234567890"), trasporto=t), **kw), t


# ------------------------------------------------------------------ sostituto


def test_credenziali_sostituto_dal_kit(kit):
    c = kit_mef.credenziali_sostituto(kit)
    assert c.utente == "PROVAX00X00X000Z" and c.cf == "PROVAX00X00X000Z" and len(c.pincode) == 10


def test_sostituto_con_le_sue_credenziali_parte():
    s, t = servizio("PROVAX00X00X000Z")
    s.invia(Ricetta(CON_SOSTITUTO, ASS, TipoPrescrizione.FARMACEUTICA, [RIGA]))
    corpo = t.richieste[0].corpo.decode()
    assert "<inv:cfMedico1>PROVAX00X00X000Y</inv:cfMedico1>" in corpo
    assert "<inv:cfMedico2>PROVAX00X00X000Z</inv:cfMedico2>" in corpo
    assert "PROVAX00X00X000Z-" in t.richieste[0].intestazioni["Authorization2F"]  # sessione del sostituto


def test_sostituto_con_credenziali_del_titolare_rifiuto_locale():
    """Il SAC risponderebbe 1212 (verificato): il kit non manda la richiesta."""
    s, t = servizio("PROVAX00X00X000Y")
    with pytest.raises(RicettaNonValida, match="sostituto"):
        s.invia(Ricetta(CON_SOSTITUTO, ASS, TipoPrescrizione.FARMACEUTICA, [RIGA]))
    assert t.richieste == []


def test_credenziali_di_altri_senza_sostituto_rifiuto_locale():
    s, t = servizio("PROVAX00X00X000Z")
    with pytest.raises(RicettaNonValida, match="titolare"):
        s.invia(Ricetta(TITOLARE, ASS, TipoPrescrizione.FARMACEUTICA, [RIGA]))
    assert t.richieste == []


def test_senza_controlli_locali_la_richiesta_parte():
    s, t = servizio("PROVAX00X00X000Y", valida_localmente=False)
    s.invia(Ricetta(CON_SOSTITUTO, ASS, TipoPrescrizione.FARMACEUTICA, [RIGA]))
    assert len(t.richieste) == 1


def test_avviso_del_sac_non_blocca():
    """Tipo 'Avviso' (visto sul SAC di test per il CF del sostituto): è un avviso, l'esito resta ok."""
    v = xml_sac.leggi_ricevuta_visualizza(risposta("visualizza_sostituto_avviso_1024.xml"))
    assert v.codice == "0001" and v.ok
    assert [m.codice for m in v.avvisi] == ["1024"] and v.errori == ()
    assert v.testata["cfMedico2"] == "PROVAX00X00X000Z"
    assert Messaggio("1024", tipo="Avviso").gravita == "W"


def test_rifiuti_reali_del_sostituto():
    a = xml_sac.leggi_ricevuta_annulla(risposta("annulla_titolare_ricetta_sostituto_1125.xml"))
    assert not a.ok and a.errori[0].codice == "1125" and a.errori[0].bloccante
    i = xml_sac.leggi_ricevuta_invio(risposta("invio_cfmedico2_non_inviante_1212.xml"))
    assert not i.ok and i.errori[0].codice == "1212"


# ------------------------------------------------------------------ InterrogaNreUtilizzati


@pytest.mark.parametrize(
    "criteri,frammento",
    [
        (CriteriNreUtilizzati("130"), "servono entrambe le date"),
        (CriteriNreUtilizzati("130", dal=OGGI, al=OGGI), "tipo di prescrizione"),
        (CriteriNreUtilizzati("130", dal=OGGI, al=OGGI - dt.timedelta(days=1), tipo=TipoPrescrizione.FARMACEUTICA), "rovesciato"),
        (CriteriNreUtilizzati("13", nre="1300A4019294847"), "3 cifre"),
        (CriteriNreUtilizzati("130", nre="123"), "15 caratteri"),
        (CriteriNreUtilizzati("130", nre="1300A4019294847", codice_lotto="123"), "lotto"),
        (CriteriNreUtilizzati("130", nre="1300A4019294847", cf_assistito="ABC"), "CF assistito"),
    ],
)
def test_criteri_nre_non_validi(criteri, frammento):
    assert any(frammento in p for p in criteri.problemi())


def test_criteri_nre_validi():
    assert CriteriNreUtilizzati("130", nre="1300A4019294847").problemi() == []
    assert CriteriNreUtilizzati("130", dal=OGGI, al=OGGI, tipo=TipoPrescrizione.SPECIALISTICA).problemi() == []


def test_richiesta_interroga_nre_valida_contro_xsd_ufficiale():
    c = CriteriNreUtilizzati("130", dal=OGGI, al=OGGI.replace(hour=23, minute=59, second=59), tipo=TipoPrescrizione.FARMACEUTICA,
                             cf_assistito="PNIMRA70A01H501P")
    el = xml_sac.richiesta_interroga_nre(c, "PROVAX00X00X000Y", "1234567890", lambda s: "CIFRATO==")
    assert errori_xsd(el) == []
    tag = [e.tag.rsplit("}", 1)[1] for e in el]
    assert tag == ["pinCode", "codRegione", "cfMedico", "cfAssistito", "tipoPrescr",
                   "dataCompilazioneRicettaDal", "dataCompilazioneRicettaAl"]
    assert el.find(f"{{{xml_sac.NS_NRE_RICH}}}dataCompilazioneRicettaAl").text == "2026-09-30 23:59:59"


def test_richiesta_interroga_nre_con_campo_sbagliato_non_valida():
    """Gruppo di controllo: l'XSD corretto in memoria deve continuare a bocciare."""
    el = xml_sac.richiesta_interroga_nre(CriteriNreUtilizzati("130", nre="1300A4019294847"), "PROVAX00X00X000Y", "1", lambda s: "X")
    el.find(f"{{{xml_sac.NS_NRE_RICH}}}codRegione").text = "13"
    assert errori_xsd(el)


def test_lettura_lista_nre_reale():
    e = xml_sac.leggi_ricevuta_interroga_nre(risposta("interroga_nre_puntuale.xml"))
    assert e.ok and len(e.ricette) == 1
    r = e.ricette[0]
    assert (r.nre, r.cf_medico, r.tipo, r.provenienza, r.lotto) == ("1300A4019294847", "PROVAX00X00X000Y", "F", "0", "1300A40")
    assert r.cf_assistito is None  # il SAC di test non lo restituisce


def test_lettura_lista_vuota_e_rifiuto_1153():
    vuota = xml_sac.leggi_ricevuta_interroga_nre(risposta("interroga_nre_inesistente_vuoto.xml"))
    assert vuota.ok and vuota.ricette == ()
    rif = xml_sac.leggi_ricevuta_interroga_nre(risposta("interroga_nre_senza_tipo_1153.xml"))
    assert not rif.ok and rif.errori[0].codice == "1153"


def test_servizio_interroga_con_trasporto_finto():
    s, t = servizio("PROVAX00X00X000Y", "interroga_nre_puntuale.xml")
    e = s.interroga_nre_utilizzati(CriteriNreUtilizzati("130", nre="1300A4019294847"))
    assert e.ricette[0].nre == "1300A4019294847"
    r = t.richieste[0]
    assert r.url.endswith("/DemRicettaInterrogazioniServicesWeb/services/demInterrogaNreUtilizzati")
    assert r.intestazioni["SOAPAction"] == f'"{xml_sac.SOAP_ACTION_INTERROGA_NRE}"'


def test_servizio_interroga_rifiuta_criteri_prima_di_chiamare():
    s, t = servizio("PROVAX00X00X000Y", "interroga_nre_puntuale.xml")
    with pytest.raises(RicettaNonValida):
        s.interroga_nre_utilizzati(CriteriNreUtilizzati("130", dal=OGGI, al=OGGI))
    assert t.richieste == []


# ------------------------------------------------------------------ guardia anche per il gateway FSE


@pytest.mark.parametrize(
    "url,produzione",
    [
        ("https://modipa.fse.salute.gov.it/govway/rest/in/FSE/gateway/v1/documents", True),
        ("https://MODIPA.fse.salute.gov.it./govway", True),
        ("https://altro-servizio.fse.salute.gov.it/x", True),
        ("https://modipa-val.fse.salute.gov.it/govway/rest/in/FSE/gateway/v1", False),
        ("https://fse.salute.gov.it.esempio.invalid/x", False),
    ],
)
def test_guardia_gateway_fse(url, produzione):
    assert e_produzione(url) is produzione


def test_trasporto_blocca_il_gateway_fse_di_produzione():
    with pytest.raises(AmbienteBloccato):
        TrasportoHTTP().invia(Richiesta("fse.validazione", "https://modipa.fse.salute.gov.it/govway/rest/in/FSE/gateway/v1/documents/validation", b""))

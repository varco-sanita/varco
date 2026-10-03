# SPDX-License-Identifier: EUPL-1.2
"""SAR Regione Friuli-Venezia Giulia (Insiel): guardia, canale, codifica contro gli XSD ufficiali,
lettura delle risposte, registro redatto, giro completo contro il server finto in mutua
autenticazione TLS (strumenti/fvg_server_finto.py).

Nessuna chiamata alla Regione né a Insiel: il server è su 127.0.0.1. I test che confrontano con
gli XSD UFFICIALI si saltano se le specifiche non sono scaricate
(strumenti/scarica_specifiche.py --gruppi fvg): non stanno nel repository.

Scritto e verificato sulle specifiche, NON collaudato sul sistema regionale.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import importlib.util
import inspect
import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from varco import AmbienteBloccato, CifratoreSanitel, ConfigurazioneNonValida, ErroreSOAP, ErroreTrasporto
from varco.ambienti import e_collaudo_fvg, e_produzione, e_regione_fvg, verifica_url_consentito
from varco.errori import RicettaNonValida
from varco.ricetta import (
    Assistito,
    ClassePriorita,
    CriteriNreUtilizzati,
    Prescrittore,
    Ricetta,
    RicettaFVG,
    Riga,
    TipoPrescrizione,
    richiede_downgrade_mir,
)
from varco.ricetta import xml_fvg
from varco.ricetta.fvg import esito_da_fault_invio
from varco.trasporto import RegistratoreFile, Richiesta, TrasportoHTTP
from varco.trasporto.fvg import (
    URL_COLLAUDO,
    AdesioneFVG,
    ApplicativoFVG,
    CanaleFVG,
    ModalitaFVG,
    PostazioneFVG,
    ServizioFVG,
    user_agent,
)
from varco.trasporto.registro import Redattore
from varco.trasporto.soap import sbusta

etree = pytest.importorskip("lxml.etree")

RADICE = Path(__file__).resolve().parents[2]
XSD_DIR = RADICE / "specifiche" / "fvg" / "wsdl" / "sar"
RISPOSTE = RADICE / "conformita" / "risposte" / "fvg"
TITOLARE, SOSTITUTO, ASSISTITO = "PROVAX00X00X000Y", "PROVAX00X00X000Z", "PNIMRA70A01H501P"
PRODOTTO = "VARCO-PROVA"
APPLICATIVO = ApplicativoFVG(PRODOTTO, "0.1", "1.4.4")
POSTAZIONE = PostazioneFVG("MACOS", "15.0", TITOLARE, "LICENZA-0001")


def _modulo(nome: str):
    spec = importlib.util.spec_from_file_location(nome, RADICE / "strumenti" / f"{nome}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(nome, mod)
    spec.loader.exec_module(mod)
    return mod


SERVER = _modulo("fvg_server_finto")
richiede_xsd = pytest.mark.skipif(not XSD_DIR.exists(), reason="specifiche FVG non scaricate (strumenti/scarica_specifiche.py --gruppi fvg)")


@pytest.fixture
def rete_vietata(monkeypatch):
    def vietato(*a, **k):
        raise AssertionError("nessuna chiamata di rete doveva partire")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", vietato)


@pytest.fixture(scope="module")
def tls(tmp_path_factory):
    return SERVER.materiale_tls(tmp_path_factory.mktemp("tls-fvg-di-prova"), [TITOLARE, SOSTITUTO])


def _ricetta_farm(**kw) -> Ricetta:
    base = dict(
        prescrittore=Prescrittore(TITOLARE, "060", "101", "F"),
        assistito=Assistito(ASSISTITO, provincia="UD", asl="101"),
        tipo=TipoPrescrizione.FARMACEUTICA,
        righe=(Riga(1, codice_gruppo_equivalenza="G3B", descrizione_gruppo_equivalenza="GRUPPO DI PROVA"),),
        data_compilazione=_dt.datetime(2026, 10, 1, 11, 0),
    )
    base.update(kw)
    return Ricetta(**base)


def _ricetta_spec(catalogo: str = "2774", **kw) -> Ricetta:
    base = dict(
        prescrittore=Prescrittore(TITOLARE, "060", "101", "F"),
        assistito=Assistito(ASSISTITO),
        tipo=TipoPrescrizione.SPECIALISTICA,
        righe=(Riga(1, codice="88.39.9", descrizione="TC DI CAVIGLIA E PIEDE", codice_catalogo=catalogo, tipo_accesso="1"),),
        descrizione_diagnosi="1-Sospetta frattura dopo esame radiografico negativo.",
        testata2="R066;P1",
        classe_priorita=ClassePriorita.URGENTE,
    )
    base.update(kw)
    return Ricetta(**base)


# ------------------------------------------------------------------ guardia anti-produzione


@pytest.mark.parametrize(
    "url,produzione,collaudo",
    [
        ("https://demtest.sanita.fvg.it/SARWs/InvioPrescrittoSecure", False, True),
        ("https://sartest.sanita.fvg.it/MIRWs/RichiestaLotto", False, True),
        ("https://apiweb-collaudo.sanita.fvg.it:8243/prescrizione-ssn-test/1.0.0/InvioPrescritto", False, True),
        ("https://isweb-collaudo.sanita.fvg.it/oauth2/token", False, True),
        ("https://DEMTEST.sanita.fvg.it./SARWs/x", False, True),  # maiuscole e punto finale: stesso host
        # produzione: non pubblicata, quindi ogni altro host FVG o Insiel
        ("https://dem.sanita.fvg.it/SARWs/InvioPrescrittoSecure", True, False),
        ("https://sar.sanita.fvg.it/x", True, False),
        ("https://apiweb.sanita.fvg.it:8243/prescrizione-ssn/1.0.0/InvioPrescritto", True, False),
        ("https://demtest.regione.fvg.it/x", True, False),
        ("https://medicinrete.insiel.it/allegati/x", True, False),
        ("https://insiel.it/x", True, False),
        ("https://demtest.sanita.fvg.it.example.org/x", False, False),  # non è un host FVG
    ],
)
def test_host_fvg_riconosciuti(url, produzione, collaudo):
    assert e_produzione(url) is produzione
    assert e_collaudo_fvg(url) is collaudo


def test_collaudo_fvg_solo_col_suo_flag_e_produzione_mai_col_flag_del_collaudo():
    collaudo = URL_COLLAUDO[ModalitaFVG.CNS][ServizioFVG.INVIO]
    with pytest.raises(AmbienteBloccato, match="Friuli-Venezia Giulia"):
        verifica_url_consentito(collaudo)
    with pytest.raises(AmbienteBloccato):
        verifica_url_consentito(collaudo, consenti_collaudo_regionale=1)  # proprio True, non «vero»
    verifica_url_consentito(collaudo, consenti_collaudo_regionale=True)
    with pytest.raises(AmbienteBloccato, match="PRODUZIONE"):
        verifica_url_consentito("https://dem.sanita.fvg.it/SARWs/InvioPrescrittoSecure", consenti_collaudo_regionale=True)
    assert e_regione_fvg("https://x.y.insiel.it/") and not e_regione_fvg("https://insiel.it.example.org/")


@pytest.mark.parametrize("modalita", list(ModalitaFVG))
@pytest.mark.parametrize("servizio", [ServizioFVG.INVIO, ServizioFVG.VISUALIZZA, ServizioFVG.ANNULLA, ServizioFVG.GESTORE_AUTORIZZAZIONI])
def test_trasporto_blocca_ogni_endpoint_fvg_prima_della_rete(rete_vietata, modalita, servizio):
    with pytest.raises(AmbienteBloccato):
        TrasportoHTTP().invia(Richiesta("x", URL_COLLAUDO[modalita][servizio], b""))


def test_con_adesione_e_carta_resta_comunque_la_guardia(rete_vietata, tls):
    can = CanaleFVG(TITOLARE, POSTAZIONE, adesione=AdesioneFVG("PROT-PROVA-1", APPLICATIVO),
                    certificato_carta=tls.certificato_client_der(TITOLARE))
    s = RicettaFVG(can, CifratoreSanitel.da_file(tls.certificato_cifratura))
    with pytest.raises(AmbienteBloccato):
        s.invia(_ricetta_farm())


# ------------------------------------------------------------------ canale


def test_canale_verso_la_regione_vuole_un_adesione(tls):
    with pytest.raises(ConfigurazioneNonValida, match="AdesioneFVG"):
        CanaleFVG(TITOLARE, POSTAZIONE, certificato_carta=tls.certificato_client_der(TITOLARE))
    with pytest.raises(ConfigurazioneNonValida, match="localhost"):
        CanaleFVG(TITOLARE, POSTAZIONE, adesione=AdesioneFVG("P", APPLICATIVO), applicativo_di_prova=APPLICATIVO,
                  certificato_carta=tls.certificato_client_der(TITOLARE))
    with pytest.raises(ConfigurazioneNonValida, match="ProdottoCME"):
        AdesioneFVG("PROT-1", ApplicativoFVG("", "0.1"))
    with pytest.raises(ConfigurazioneNonValida, match="riferimento"):
        AdesioneFVG(" ", APPLICATIVO)
    with pytest.raises(ConfigurazioneNonValida, match="localhost"):
        CanaleFVG(TITOLARE, POSTAZIONE, base_url="https://demtest.sanita.fvg.it/SARWs", applicativo_di_prova=APPLICATIVO)


def test_modalita_cns_verso_la_regione_vuole_la_carta_del_medico_che_invia(tls):
    ades = AdesioneFVG("PROT-1", APPLICATIVO)
    with pytest.raises(ConfigurazioneNonValida, match="certificato_carta"):
        CanaleFVG(TITOLARE, POSTAZIONE, adesione=ades)
    with pytest.raises(ConfigurazioneNonValida, match=SOSTITUTO):
        # par. 2.2: inviare solo se il CF della carta nel lettore è quello del medico inviante
        CanaleFVG(TITOLARE, POSTAZIONE, adesione=ades, certificato_carta=tls.certificato_client_der(SOSTITUTO))
    CanaleFVG(TITOLARE, POSTAZIONE, adesione=ades, certificato_carta=tls.certificato_client_der(TITOLARE))


class _Token:
    def access_token(self):
        return "ACCESS-TOKEN-DI-PROVA"

    def id_token(self):
        return "ID-TOKEN-DI-PROVA.CON." + TITOLARE


def test_modalita_federata_trasporta_i_token_non_li_crea():
    ades = AdesioneFVG("PROT-1", APPLICATIVO)
    with pytest.raises(ConfigurazioneNonValida, match="TokenFVG"):
        CanaleFVG(TITOLARE, POSTAZIONE, adesione=ades, modalita=ModalitaFVG.FEDERATA)
    with pytest.raises(ConfigurazioneNonValida, match="federata"):
        CanaleFVG(TITOLARE, POSTAZIONE, base_url="http://127.0.0.1:9", applicativo_di_prova=APPLICATIVO, token=_Token())
    can = CanaleFVG(TITOLARE, POSTAZIONE, adesione=ades, modalita=ModalitaFVG.FEDERATA, token=_Token())
    assert can.url[ServizioFVG.INVIO].startswith("https://apiweb-collaudo.sanita.fvg.it:8243/prescrizione-ssn-test/")
    h = can._intestazioni()
    assert h["Authorization"] == "Bearer ACCESS-TOKEN-DI-PROVA" and h["X-JWT-ASSERTION"].startswith("ID-TOKEN-DI-PROVA")
    assert h["SOAPAction"] == '""'


def test_user_agent_come_il_par_3_1():
    assert user_agent(APPLICATIVO, POSTAZIONE) == f"{PRODOTTO}/0.1 MACOS/15.0 {TITOLARE}/LICENZA-0001"
    # il nome del sistema operativo può avere uno spazio, come nell'esempio ufficiale (WINDOWS NT/10.0)
    assert " WINDOWS NT/10.0 " in user_agent(APPLICATIVO, PostazioneFVG("WINDOWS NT", "10.0", TITOLARE, "L1"))
    for postazione in (PostazioneFVG("MACOS", "15.0", TITOLARE, ""), PostazioneFVG("MAC/OS", "15", TITOLARE, "L1"),
                       PostazioneFVG("MACOS", "15", TITOLARE, "L 1"), PostazioneFVG("MACOS", "15\r\nX: y", TITOLARE, "L1"),
                       PostazioneFVG("MACOS", "10.0 build 22", TITOLARE, "L1")):
        with pytest.raises(ConfigurazioneNonValida):
            user_agent(APPLICATIVO, postazione)


def test_interroga_nre_senza_endpoint_pubblicato_si_ferma_prima(rete_vietata, tls):
    can = CanaleFVG(TITOLARE, POSTAZIONE, adesione=AdesioneFVG("PROT-1", APPLICATIVO),
                    certificato_carta=tls.certificato_client_der(TITOLARE))
    s = RicettaFVG(can, CifratoreSanitel.da_file(tls.certificato_cifratura))
    oggi = _dt.datetime(2026, 10, 1)
    with pytest.raises(ConfigurazioneNonValida, match="nessun endpoint pubblicato"):
        s.interroga_nre_utilizzati(CriteriNreUtilizzati("060", tipo=TipoPrescrizione.FARMACEUTICA, dal=oggi, al=oggi))


def test_cifratore_obbligatorio(tls):
    can = CanaleFVG(TITOLARE, POSTAZIONE, base_url="https://127.0.0.1:9", applicativo_di_prova=APPLICATIVO)
    with pytest.raises(ValueError, match="Insiel"):
        RicettaFVG(can, None)


def test_contratto_comune():
    from varco.ricetta.servizio import ServizioRicetta

    for nome in ("invia", "visualizza", "annulla", "interroga_nre_utilizzati"):
        assert list(inspect.signature(getattr(RicettaFVG, nome)).parameters) == list(
            inspect.signature(getattr(ServizioRicetta, nome)).parameters), nome
    p = inspect.signature(RicettaFVG.visualizza).parameters["cf_assistito"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is None


def test_il_modello_non_e_cambiato():
    """Il FVG sta tutto sotto lo stesso modello: nessun campo nuovo in Ricetta, Riga, Prescrittore, Assistito."""
    import dataclasses

    schema = json.loads((RADICE / "conformita" / "schema" / "ricetta.schema.json").read_text(encoding="utf-8"))
    assert set(schema["properties"]) == {f.name for f in dataclasses.fields(Ricetta)}
    assert "codice_catalogo" in {f.name for f in dataclasses.fields(Riga)}


# ------------------------------------------------------------------ codifica


def test_controlli_locali_fvg():
    assert xml_fvg.problemi_fvg(_ricetta_farm()) == []
    assert xml_fvg.problemi_fvg(_ricetta_spec()) == []
    assert any("numsedute" in p for p in xml_fvg.problemi_fvg(_ricetta_spec(righe=(Riga(1, codice="1", descrizione="d", codice_catalogo="1", num_sedute=2),))))
    assert any("televisita" in p for p in xml_fvg.problemi_fvg(_ricetta_spec(righe=(Riga(1, codice="1", descrizione="d", codice_catalogo="1", prescrizione1="TV;"),))))
    assert any("catalogo" in p for p in xml_fvg.problemi_fvg(_ricetta_spec(catalogo=None)))


def test_codifica_invio_contenuti():
    el = xml_fvg.richiesta_invio(_ricetta_spec(), lambda s: "CIFRATO==", PRODOTTO, "1.4.4")
    figli = {e.tag.rsplit("}", 1)[-1]: e.text for e in el}
    assert figli["pinCode"] is None and figli["codiceAss"] == "CIFRATO==" and figli["testata2"] == "R066;P1"
    assert "tipoRic" not in figli and "nre" not in figli and "classePriorita" in figli
    assert el.get(f"{{{xml_fvg.NS_TIPI}}}prodottoCme") == PRODOTTO
    elenco = el.find(f"{{{xml_fvg.NS_INVIO_RICH}}}ElencoDettagliPrescrizioni")
    assert elenco.get(f"{{{xml_fvg.NS_TIPI}}}versioneCR") == "1.4.4"
    assert [e.tag.rsplit("}", 1)[-1] for e in elenco[0]] == ["codProdPrest", "descrProdPrest", "quantita", "codCatalogoPrescr", "tipoAccesso"]
    # farmaceutica: niente versioneCR se non c'è
    assert xml_fvg.richiesta_invio(_ricetta_farm(), lambda s: "C", PRODOTTO)[-1].attrib == {}
    with pytest.raises(ValueError, match="prodottoCme"):
        xml_fvg.richiesta_visualizza("0600A0000000001", TITOLARE, "")


def test_versione_cr_solo_per_la_specialistica_e_vuoto_vale_assente(server, tls):
    """versioneCR (par. 3.1) solo per la specialistica, anche se il canale ce l'ha; '' come None."""
    farm = xml_fvg.richiesta_invio(_ricetta_farm(assistito=Assistito(ASSISTITO, tipo_ricetta="", provincia="", asl="")),
                                   lambda s: "C", PRODOTTO, "1.4.4")
    assert farm[-1].attrib == {}
    figli = {e.tag.rsplit("}", 1)[-1] for e in farm}
    assert not {"tipoRic", "provAssistito", "aslAssistito"} & figli
    s = _servizio(server, tls)
    s.invia(_ricetta_farm())
    assert b"versioneCR" not in s.ultimo.grezza.xml_richiesta
    s.invia(_ricetta_spec())
    assert b'versioneCR="1.4.4"' in s.ultimo.grezza.xml_richiesta


def test_guardia_con_punti_non_ascii():
    for punto in ("\u3002", "\uff0e", "\uff61"):
        url = f"https://dem.sanita.fvg{punto}it/SARWs/InvioPrescrittoSecure"
        assert e_produzione(url), punto
        with pytest.raises(AmbienteBloccato):
            verifica_url_consentito(url, consenti_collaudo_regionale=True)
    assert e_collaudo_fvg("https://demtest.sanita.fvg\u3002it/x")


def test_codice_lotto_fvg():
    assert xml_fvg.codice_lotto_fvg("0600A01234567") == "1234567"
    assert xml_fvg.codice_lotto_fvg("0600A0123456") == "123456"
    with pytest.raises(ValueError):
        xml_fvg.codice_lotto_fvg("0600A0ABCDEFG")


def _schema(relativo: str):
    return etree.XMLSchema(etree.parse(str(XSD_DIR / relativo)))


def _richieste_campione():
    oggi = _dt.datetime(2026, 10, 1)
    pr_sost = Prescrittore(TITOLARE, "060", "101", "F", codice_fiscale_sostituto=SOSTITUTO)
    cif = lambda s: "CIFRATO==" * 10  # noqa: E731
    return {
        "invio_farmaceutica": ("invioPrescritto/v1.0/InvioPrescrittoRichiesta-v1.0.xsd", xml_fvg.richiesta_invio(_ricetta_farm(), cif, PRODOTTO)),
        "invio_specialistica": ("invioPrescritto/v1.0/InvioPrescrittoRichiesta-v1.0.xsd", xml_fvg.richiesta_invio(_ricetta_spec(), cif, PRODOTTO, "1.4.4")),
        "invio_sostituto_con_nre": ("invioPrescritto/v1.0/InvioPrescrittoRichiesta-v1.0.xsd",
                                    xml_fvg.richiesta_invio(_ricetta_farm(prescrittore=pr_sost, nre="0600A0000000009"), cif, PRODOTTO)),
        "invio_estero": ("invioPrescritto/v1.0/InvioPrescrittoRichiesta-v1.0.xsd", xml_fvg.richiesta_invio(_ricetta_farm(
            assistito=Assistito(tipo_ricetta="UE", stato_estero="DE", istituzione_competente="X", num_ident_personale="1",
                                num_ident_tessera="2", data_nascita_estero="1970-01-01 00:00:00", data_scadenza_tessera="2030-01-01 00:00:00")),
            cif, PRODOTTO)),
        "visualizza": ("visualizzaPrescritto/v1.0/VisualizzaPrescrittoRichiesta-v1.0.xsd", xml_fvg.richiesta_visualizza("0600A0000000001", TITOLARE, PRODOTTO)),
        "annulla": ("annullaPrescritto/v1.0/AnnullaPrescrittoRichiesta-v1.0.xsd", xml_fvg.richiesta_annulla("0600A0000000001", TITOLARE, PRODOTTO)),
        "verifica_sostituto": ("gestoreAutorizzazioni/v1.0/VerificaPosizioneMedicoSostitutoRichiesta-v1.0.xsd",
                               xml_fvg.richiesta_verifica_sostituto(TITOLARE, SOSTITUTO, "101", PRODOTTO)),
        "interroga_nre_puntuale": ("interrogaNreUtilizzati/InterrogaNreUtilRichiesta.xsd",
                                   xml_fvg.richiesta_interroga_nre(CriteriNreUtilizzati("060", nre="0600A0000000001"), TITOLARE)),
        "interroga_nre_periodo": ("interrogaNreUtilizzati/InterrogaNreUtilRichiesta.xsd", xml_fvg.richiesta_interroga_nre(
            CriteriNreUtilizzati("060", codice_lotto="0600A01234567", cf_assistito=ASSISTITO, tipo=TipoPrescrizione.SPECIALISTICA,
                                 dal=oggi, al=oggi), TITOLARE)),
    }


@richiede_xsd
@pytest.mark.parametrize("nome", sorted(_richieste_campione()))
def test_richieste_validano_contro_gli_xsd_fvg(nome):
    xsd, el = _richieste_campione()[nome]
    s = _schema(xsd)
    assert s.validate(etree.fromstring(ET.tostring(el))), [e.message for e in s.error_log]


@richiede_xsd
def test_gli_xsd_fvg_mordono():
    """Gruppo di controllo: lo schema boccia ciò che il kit evita apposta."""
    s = _schema("invioPrescritto/v1.0/InvioPrescrittoRichiesta-v1.0.xsd")

    def valida(muta) -> bool:
        el = xml_fvg.richiesta_invio(_ricetta_farm(), lambda x: "C", PRODOTTO)
        muta(el)
        return s.validate(etree.fromstring(ET.tostring(el)))

    assert valida(lambda el: None)
    # senza prodottoCme (use=required)
    assert not valida(lambda el: el.attrib.clear())
    # un tag facoltativo VUOTO, come lo manda il kit verso il SAC: qui non è valido (tipoRicettaType: 2 caratteri)
    assert not valida(lambda el: el.insert(5, ET.Element(f"{{{xml_fvg.NS_INVIO_RICH}}}tipoRic")))
    # numsedute non esiste nel tracciato FVG
    assert not valida(lambda el: ET.SubElement(el[-1][0], f"{{{xml_fvg.NS_TIPI}}}numsedute").__setattr__("text", "1"))
    # il namespace del SAC non va
    assert not s.validate(etree.fromstring(ET.tostring(ET.Element("{http://invioprescrittorichiesta.xsd.dem.sanita.finanze.it}InvioPrescrittoRichiesta"))))


@richiede_xsd
def test_namespace_del_codec_sono_quelli_degli_xsd_ufficiali():
    attesi = {
        "invioPrescritto/v1.0/InvioPrescrittoRichiesta-v1.0.xsd": xml_fvg.NS_INVIO_RICH,
        "invioPrescritto/v1.0/InvioPrescrittoRicevuta-v1.0.xsd": xml_fvg.NS_INVIO_RIC,
        "visualizzaPrescritto/v1.0/VisualizzaPrescrittoRichiesta-v1.0.xsd": xml_fvg.NS_VIS_RICH,
        "annullaPrescritto/v1.0/AnnullaPrescrittoRichiesta-v1.0.xsd": xml_fvg.NS_ANN_RICH,
        "gestoreAutorizzazioni/v1.0/VerificaPosizioneMedicoSostitutoRichiesta-v1.0.xsd": xml_fvg.NS_SOST_RICH,
        "SARWs/TipiDati-v1.0.xsd": xml_fvg.NS_TIPI,
        "interrogaNreUtilizzati/InterrogaNreUtilRichiesta.xsd": xml_fvg.NS_NRE_RICH,
        "interrogaNreUtilizzati/InterrogaNreUtilRicevuta.xsd": xml_fvg.NS_NRE_RIC,
    }
    for f, ns in attesi.items():
        assert etree.parse(str(XSD_DIR / f)).getroot().get("targetNamespace") == ns, f


@richiede_xsd
def test_difetti_noti_degli_xsd_fvg():
    """Se Insiel li corregge, questi test se ne accorgono (docs/SAR_FVG.md, sez. 7)."""
    ricevuta = (XSD_DIR / "invioPrescritto/v1.0/InvioPrescrittoRicevuta-v1.0.xsd").read_text(encoding="utf-8-sig")
    assert 'name="NewAttribute"' in ricevuta  # attributo globale rimasto dall'editor
    tipi = etree.parse(str(XSD_DIR / "SARWs/TipiDati-v1.0.xsd"))
    pattern = tipi.find(".//{http://www.w3.org/2001/XMLSchema}simpleType[@name='codEsitoType']//{http://www.w3.org/2001/XMLSchema}pattern")
    assert pattern.get("value") == "[0-9]{4}"  # i codici di downgrade 060120-060130 hanno 6 cifre
    wsdl = (XSD_DIR / "invioPrescritto/v1.0/invioPrescritto-v1.0.wsdl").read_text(encoding="utf-8")
    assert "/SARSpecialistiInterni/InvioPrescrittoSecure" in wsdl  # il documento dice /SARWs/
    nre = (XSD_DIR / "interrogaNreUtilizzati/interrogaNreUtil.wsdl").read_text(encoding="utf-8")
    assert "http://localhost:8070/" in nre  # nessun endpoint pubblicato per la lista degli NRE


# ------------------------------------------------------------------ lettura delle risposte (sintetiche)


def _risposta(nome: str) -> ET.Element:
    return sbusta((RISPOSTE / nome).read_bytes(), 200)


def test_risposte_sintetiche_dichiarate_e_rigenerabili():
    assert "SINTETICHE" in (RISPOSTE / "LEGGIMI.md").read_text(encoding="utf-8")
    generate = SERVER.risposte_sintetiche()
    assert set(generate) == {p.name for p in RISPOSTE.glob("*.xml")}
    for nome, dati in generate.items():
        assert (RISPOSTE / nome).read_bytes() == dati, nome


@richiede_xsd
@pytest.mark.parametrize("nome", sorted(p.name for p in RISPOSTE.glob("*.xml")))
def test_risposte_sintetiche_contro_gli_xsd_fvg(nome):
    doc = etree.parse(str(RISPOSTE / nome))
    el = doc.getroot()[-1][0]
    radice = etree.QName(el).localname
    if radice == "Fault":
        return
    s = _schema(SERVER.XSD_RICEVUTE[radice])
    valido = s.validate(el)
    if nome == "invio_downgrade_060120.xml":
        # difetto della specifica: il codice di downgrade (6 cifre) non sta in codEsitoType (4 cifre)
        assert not valido and "060120" in str(s.error_log)
    else:
        assert valido, [e.message for e in s.error_log]


def test_leggi_ricevute():
    e = xml_fvg.leggi_ricevuta_invio(_risposta("invio_ok.xml"))
    assert e.ok and e.nre == "0600A0000000001" and len(e.codice_autenticazione) == 30 and not richiede_downgrade_mir(e)
    assert xml_fvg.leggi_ricevuta_invio(_risposta("invio_ok_dpc_0196.xml")).comunicazione("0196").startswith("CONTIENE FARMACI IN DPC")
    v = xml_fvg.leggi_ricevuta_visualizza(_risposta("visualizza_sostituto.xml"))
    assert v.stato_processo == "3" and v.testata["cfMedico2"] == SOSTITUTO and v.righe[0]["codGruppoEquival"] == "G3B"
    a = xml_fvg.leggi_ricevuta_annulla(_risposta("annulla_rifiuto_1120.xml"))
    assert not a.ok and a.errori[0].codice == "1120"
    vs = xml_fvg.leggi_verifica_sostituto(_risposta("verifica_sostituto_abilitato.xml"))
    assert vs["abilitato"] is True and vs["cf_sostituto"] == SOSTITUTO
    assert xml_fvg.leggi_verifica_sostituto(_risposta("verifica_sostituto_non_abilitato.xml"))["abilitato"] is False
    err = xml_fvg.leggi_verifica_sostituto(_risposta("verifica_sostituto_errore.xml"))  # ElencoErrori, non ElencoErroriRicette
    assert err["abilitato"] is False and [m.codice for m in err["messaggi"]] == ["1212"] and err["messaggi"][0].bloccante
    assert [r.nre for r in xml_fvg.leggi_ricevuta_interroga_nre(_risposta("interroga_nre_ok.xml")).ricette] == ["0600A0000000001"]


def test_downgrade_nelle_due_forme():
    e = xml_fvg.leggi_ricevuta_invio(_risposta("invio_downgrade_060120.xml"))
    assert not e.ok and richiede_downgrade_mir(e)
    with pytest.raises(ErroreSOAP) as f:
        _risposta("invio_downgrade_fault_060125.xml")
    e2 = esito_da_fault_invio(f.value)
    assert e2 is not None and not e2.ok and richiede_downgrade_mir(e2) and e2.errori[0].codice == "060125"
    # un errore qualunque non è un downgrade, e un Fault qualunque resta un'eccezione
    assert not richiede_downgrade_mir(xml_fvg.leggi_ricevuta_invio(_risposta("invio_rifiuto_1020.xml")))
    assert esito_da_fault_invio(ErroreSOAP("soapenv:Server", "errore 0601200 di sistema")) is None
    assert esito_da_fault_invio(ErroreSOAP("soapenv:Server", "codice 060131 fuori intervallo")) is None
    assert [c for c in range(60119, 60132) if xml_fvg.e_codice_downgrade(f"{c:06d}")] == list(range(60120, 60131))
    assert not xml_fvg.e_codice_downgrade("60120")  # le cifre sono sei, lo zero iniziale conta


# ------------------------------------------------------------------ registro redatto


def test_registro_reda_corpo_e_intestazioni_fvg(tmp_path):
    can = CanaleFVG(TITOLARE, POSTAZIONE, base_url="https://127.0.0.1:9", applicativo_di_prova=APPLICATIVO)
    from varco.trasporto.soap import imbusta

    busta = imbusta(xml_fvg.richiesta_invio(_ricetta_farm(nre="0600A0000000009"), lambda s: "Q0lGUkFUTw==" * 8, PRODOTTO))
    reg = RegistratoreFile(tmp_path)
    intest = can._intestazioni() | {"X-JWT-ASSERTION": "eyJ.ID-TOKEN." + TITOLARE, "Authorization": "Bearer SEGRETO"}
    reg(Richiesta("fvg.invio", "https://127.0.0.1:9/SARWs/InvioPrescrittoSecure", busta, intest), None, None)
    testo = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    for chiaro in (TITOLARE, "0600A0000000009", "LICENZA-0001", "SEGRETO", "ID-TOKEN", "Q0lGUkFUTw"):
        assert chiaro not in testo, chiaro
    meta = json.loads(next(tmp_path.glob("*_meta.json")).read_text(encoding="utf-8"))
    assert meta["intestazioni_richiesta"]["User-Agent"].startswith(f"{PRODOTTO}/0.1 MACOS/15.0 [REDATTO:postazione:")
    assert meta["intestazioni_richiesta"]["X-JWT-ASSERTION"] == "***"
    # il tag della verifica del sostituto
    t = Redattore().testo(ET.tostring(xml_fvg.richiesta_verifica_sostituto(TITOLARE, SOSTITUTO, "101", PRODOTTO), encoding="unicode"))
    assert TITOLARE not in t and SOSTITUTO not in t


def test_user_agent_di_sac_e_sist_non_cambia():
    assert Redattore().user_agent("varco/0.1 (+EUPL-1.2)") == "varco/0.1 (+EUPL-1.2)"


def test_user_agent_redatto_anche_con_cf_scritto_male():
    ua = Redattore().user_agent("VARCO/0.1 MACOS/15.0 provax00x00x000y-errato/00E0AABBCCDD")
    assert "00E0AABBCCDD" not in ua and "provax" not in ua.lower() and ua.startswith("VARCO/0.1 MACOS/15.0 [REDATTO:postazione:")


# ------------------------------------------------------------------ giro completo contro il server finto (mTLS)


class TrasportoLocaleTLS(TrasportoHTTP):
    """Il trasporto vero (guardia, HTTPS, nessun redirect, contesto TLS con la «carta»), senza attese:
    il server è su 127.0.0.1."""

    def __init__(self, **kw):
        super().__init__(intervallo_minimo_s=0.5, **kw)
        self.limitatore.intervallo = 0.0


@pytest.fixture
def server(tls):
    with SERVER.ServerFVG(certificato_server=tls.certificato_server, chiave_server=tls.chiave_server, ca_client=tls.ca,
                          chiave_cifratura_pem=tls.chiave_cifratura_pem, prodotto_cme=PRODOTTO,
                          xsd_dir=XSD_DIR if XSD_DIR.exists() else None,
                          sostituti_abilitati={(TITOLARE, SOSTITUTO, "101")}) as s:
        yield s


def _servizio(server, tls, cf=TITOLARE, *, carta: str | None = "stessa", applicativo=APPLICATIVO, registratore=None, **kw) -> RicettaFVG:
    carta = cf if carta == "stessa" else carta
    can = CanaleFVG(cf, PostazioneFVG("MACOS", "15.0", TITOLARE, "LICENZA-0001"), base_url=server.url,
                    applicativo_di_prova=applicativo,
                    certificato_carta=tls.certificato_client_der(cf),
                    trasporto=TrasportoLocaleTLS(contesto_tls=tls.contesto_client(carta), registratore=registratore), **kw)
    return RicettaFVG(can, CifratoreSanitel.da_file(tls.certificato_cifratura))


def test_e2e_invia_visualizza_annulla(server, tls):
    s = _servizio(server, tls)
    e = s.invia(_ricetta_farm())
    assert e.ok and e.nre.startswith("0600A") and len(e.codice_autenticazione) == 30
    # il server ha decifrato il CF dell'assistito con la chiave del certificato di cifratura
    assert server.stato.ricette[e.nre].titolare == TITOLARE
    r = server.stato.richieste[-1]
    assert r["cf_certificato"] == TITOLARE and r["soapaction"] == '""'
    assert r["user_agent"] == f"{PRODOTTO}/0.1 MACOS/15.0 {TITOLARE}/LICENZA-0001"
    v = s.visualizza(e.nre)
    assert v.ok and v.stato_processo == "3" and v.righe[0]["codGruppoEquival"] == "G3B"
    assert not s.visualizza("0600A0000099999").ok
    assert s.annulla(e.nre).ok
    di_nuovo = s.annulla(e.nre)
    assert not di_nuovo.ok and di_nuovo.errori[0].codice == "1120"


def test_e2e_specialistica_dpc_e_downgrade(server, tls):
    s = _servizio(server, tls)
    assert s.invia(_ricetta_spec()).ok
    dpc = s.invia(_ricetta_farm(righe=(Riga(1, codice_gruppo_equivalenza="DPC", descrizione_gruppo_equivalenza="FARMACO IN DPC"),)))
    assert dpc.ok and dpc.comunicazione("0196")
    for catalogo in ("999999", "999998"):  # codice nell'elenco errori / come SOAP Fault
        e = s.invia(_ricetta_spec(catalogo=catalogo))
        assert not e.ok and richiede_downgrade_mir(e), catalogo
    rifiuto = s.invia(_ricetta_farm(righe=(Riga(1, codice="012345678", descrizione="FARMACO", nota_aifa="999"),)))
    assert not rifiuto.ok and not richiede_downgrade_mir(rifiuto) and rifiuto.errori[0].codice == "1020"


def test_e2e_sostituto(server, tls):
    pr = Prescrittore(TITOLARE, "060", "101", "F", codice_fiscale_sostituto=SOSTITUTO)
    with pytest.raises(RicettaNonValida, match="sostituto"):
        _servizio(server, tls, TITOLARE).invia(_ricetta_farm(prescrittore=pr))
    sost = _servizio(server, tls, SOSTITUTO)
    e = sost.invia(_ricetta_farm(prescrittore=pr))
    assert e.ok and server.stato.ricette[e.nre].sostituto == SOSTITUTO
    tit = _servizio(server, tls, TITOLARE)
    assert tit.visualizza(e.nre).ok  # il titolare vede
    assert tit.annulla(e.nre).errori[0].codice == "1125"  # ma non annulla: annulla chi ha prescritto
    assert sost.annulla(e.nre, cf_medico=TITOLARE).ok
    assert sost.verifica_sostituto(TITOLARE, SOSTITUTO, "101").abilitato
    assert not sost.verifica_sostituto(TITOLARE, SOSTITUTO, "102").abilitato
    errore = sost.verifica_sostituto(TITOLARE, TITOLARE, "101")
    assert not errore.abilitato and errore.messaggi[0].codice == "1212"


def test_e2e_lista_nre_solo_con_url_esplicito(server, tls):
    s = _servizio(server, tls)  # verso localhost ci sono i percorsi del server finto
    e = s.invia(_ricetta_farm())
    # la ricetta è compilata il 01/10/2026 (_ricetta_farm). Fino al 02/10/2026 qui si cercava «oggi»
    # e passava solo perché il server finto ignorava le date (revisione esterna, punto 5).
    giorno = _dt.datetime(2026, 10, 1)
    r = s.interroga_nre_utilizzati(CriteriNreUtilizzati("060", tipo=TipoPrescrizione.FARMACEUTICA, dal=giorno,
                                                        al=giorno.replace(hour=23, minute=59, second=59)))
    assert r.ok and e.nre in [x.nre for x in r.ricette]


def test_e2e_mutua_autenticazione_e_accreditamento(server, tls):
    senza_carta = _servizio(server, tls, carta=None)
    with pytest.raises(ErroreTrasporto):  # il server chiede il certificato client: l'handshake non si chiude
        senza_carta.visualizza("0600A0000000001")
    # carta di un altro medico nel contesto TLS: il canale non lo può vedere, il server sì
    con_carta_altrui = _servizio(server, tls, TITOLARE, carta=SOSTITUTO)
    with pytest.raises(ErroreSOAP, match="certificato"):
        con_carta_altrui.invia(_ricetta_farm())
    non_accreditato = _servizio(server, tls, applicativo=ApplicativoFVG("ALTRO-PRODOTTO", "0.1"))
    with pytest.raises(ErroreSOAP, match="non accreditato"):
        non_accreditato.visualizza("0600A0000000001")


def test_e2e_registro_redatto(server, tls, tmp_path):
    s = _servizio(server, tls, registratore=RegistratoreFile(tmp_path))
    e = s.invia(_ricetta_farm())
    s.visualizza(e.nre)
    testo = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    for chiaro in (TITOLARE, e.nre, e.codice_autenticazione, "LICENZA-0001"):
        assert chiaro not in testo, chiaro
    assert "[REDATTO:cf:" in testo and "[REDATTO:nre:" in testo


# ------------------------------------------------------------------ revisione esterna 02/10/2026 (4-sar-fvg.md)
# Ogni test riproduce un controesempio del revisore. Fallivano sul codice di prima (client e
# server finto): verificato su una copia, vedi la consegna. Il punto 1 (guardia nel trasporto
# proprio) è corretto altrove, in trasporto/http.py::consegna.


def test_rev2_specialistica_senza_versione_cr_rifiutata(server, tls):
    """Punto 2: ApplicativoFVG senza versione_cr, specialistica: il client mandava, il server diceva 0000."""
    for valida in (True, False):
        s = _servizio(server, tls, applicativo=ApplicativoFVG(PRODOTTO, "0.1"))
        s.valida_localmente = valida
        with pytest.raises(RicettaNonValida, match="versioneCR"):
            s.invia(_ricetta_spec())
    with pytest.raises(RicettaNonValida, match="patch"):
        _servizio(server, tls, applicativo=ApplicativoFVG(PRODOTTO, "0.1", "1.4")).invia(_ricetta_spec())
    assert server.stato.ricette == {} and server.stato.richieste == []
    # la farmaceutica non la vuole (gruppo di controllo)
    assert _servizio(server, tls, applicativo=ApplicativoFVG(PRODOTTO, "0.1")).invia(_ricetta_farm()).ok


@pytest.mark.parametrize("versione", [None, "1.4"])
def test_rev2_server_rifiuta_specialistica_senza_versione_cr(server, tls, versione):
    s = _servizio(server, tls)
    el = xml_fvg.richiesta_invio(_ricetta_spec(), s.cifratore.cifra, PRODOTTO, "1.4.4")
    elenco = el.find(f"{{{xml_fvg.NS_INVIO_RICH}}}ElencoDettagliPrescrizioni")
    attributo = f"{{{xml_fvg.NS_TIPI}}}versioneCR"
    if versione is None:
        del elenco.attrib[attributo]
    else:
        elenco.set(attributo, versione)
    with pytest.raises(ErroreSOAP, match="versioneCR"):
        s.canale.chiama(ServizioFVG.INVIO, el)
    assert server.stato.ricette == {}


def test_rev3_catalogo_vuoto_vale_assente(server, tls):
    """Punto 3: codice_catalogo="" passava il controllo e il codec toglieva il tag: specialistica senza catalogo, esito 0000."""
    for valida in (True, False):
        s = _servizio(server, tls)
        s.valida_localmente = valida
        with pytest.raises(RicettaNonValida, match="catalogo"):
            s.invia(_ricetta_spec(catalogo=""))
        with pytest.raises(RicettaNonValida, match="catalogo"):
            s.invia(_ricetta_spec(catalogo="  "))
    assert server.stato.richieste == []
    # e il server, a cui arriva una riga senza catalogo, la rifiuta
    s = _servizio(server, tls)
    el = xml_fvg.richiesta_invio(_ricetta_spec(), s.cifratore.cifra, PRODOTTO, "1.4.4")
    dett = el.find(f"{{{xml_fvg.NS_INVIO_RICH}}}ElencoDettagliPrescrizioni")[0]
    dett.remove(dett.find(f"{{{xml_fvg.NS_TIPI}}}codCatalogoPrescr"))
    with pytest.raises(ErroreSOAP, match="codCatalogoPrescr"):
        s.canale.chiama(ServizioFVG.INVIO, el)
    assert server.stato.ricette == {}


RICEVUTA_CON_NOTA = (
    '<r:InvioPrescrittoRicevuta xmlns:r="http://invioprescrittoricevuta.xsd.dem.sanita.fvg.it-v1.0" '
    'xmlns:td="http://tipodati.xsd.dem.sanita.fvg.it-v1.0">'
    "<r:nre>0600A0000000001</r:nre><r:codAutenticazione>000000000000000000000000000001</r:codAutenticazione>"
    "<r:dataInserimento>2026-10-02 10:00:00</r:dataInserimento><r:codEsitoInserimento>0000</r:codEsitoInserimento>"
    "<r:ElencoNota><td:Nota><td:progrPresc>1</td:progrPresc><td:codProdPrest>88.39.9</td:codProdPrest>"
    "<td:tipoAmbulatorio>AMB01</td:tipoAmbulatorio></td:Nota></r:ElencoNota></r:InvioPrescrittoRicevuta>"
)


def test_rev4_esito_porta_elenco_nota_e_tipo_ambulatorio():
    """Punto 4: ElencoNota (tipoAmbulatorio, p. 22) era scartato dal lettore."""
    from varco.ricetta import NotaPrestazione

    el = ET.fromstring(RICEVUTA_CON_NOTA)
    if XSD_DIR.exists():
        s = _schema(SERVER.XSD_RICEVUTE["InvioPrescrittoRicevuta"])
        assert s.validate(etree.fromstring(RICEVUTA_CON_NOTA.encode())), [e.message for e in s.error_log]
    e = xml_fvg.leggi_ricevuta_invio(el)
    assert e.ok and e.note == (NotaPrestazione("1", "88.39.9", "AMB01"),)
    assert xml_fvg.leggi_ricevuta_invio(_risposta("invio_ok.xml")).note == ()  # senza ElencoNota: vuoto


def test_rev4_server_restituisce_la_nota(server, tls):
    s = _servizio(server, tls)
    riga = Riga(1, codice="88.39.9", descrizione="TC DI CAVIGLIA E PIEDE", codice_catalogo="2774", tipo_accesso="1",
                numero_nota="1")
    e = s.invia(_ricetta_spec(righe=(riga,)))
    assert e.ok and [(n.progressivo, n.tipo_ambulatorio) for n in e.note] == [("1", "AMB-PROVA")]
    if XSD_DIR.exists():  # la ricevuta del server con ElencoNota valida contro lo XSD
        busta = etree.fromstring(s.ultimo.grezza.xml_risposta)
        ricevuta = busta[-1][0]
        sch = _schema(SERVER.XSD_RICEVUTE["InvioPrescrittoRicevuta"])
        assert sch.validate(ricevuta), [x.message for x in sch.error_log]


def test_rev5_server_applica_i_filtri_della_lista_nre(server, tls):
    """Punto 5: periodo, CF dell'assistito e lotto erano ignorati: tornava l'NRE estraneo."""
    s = _servizio(server, tls)
    e = s.invia(_ricetta_farm())  # compilata il 01/10/2026
    assert e.nre == "0600A0000000001"
    giorno = dict(dal=_dt.datetime(2026, 10, 1), al=_dt.datetime(2026, 10, 1, 23, 59, 59))
    base = CriteriNreUtilizzati("060", tipo=TipoPrescrizione.FARMACEUTICA, **giorno)

    def trovati(criteri) -> list[str]:
        r = s.interroga_nre_utilizzati(criteri)
        assert r.ok
        return [x.nre for x in r.ricette]

    assert trovati(base) == [e.nre]  # gruppo di controllo
    assert trovati(CriteriNreUtilizzati("060", tipo=TipoPrescrizione.FARMACEUTICA, dal=_dt.datetime(2020, 1, 1),
                                        al=_dt.datetime(2020, 1, 2))) == []
    assert trovati(dataclasses.replace(base, cf_assistito="PROVAX00X00X000X")) == []
    assert trovati(dataclasses.replace(base, cf_assistito=ASSISTITO)) == [e.nre]
    assert trovati(dataclasses.replace(base, codice_lotto="0600A01234567")) == []
    assert trovati(dataclasses.replace(base, codice_lotto="0600A00000000")) == [e.nre]


def test_rev6_assistito_stp_accettato(server, tls):
    """Punto 6: il server pretendeva un CF ordinario; p. 18 e XSD ammettono CF/STP/ENI/altro."""
    s = _servizio(server, tls)
    stp = Assistito("STP0601010000001", tipo_ricetta="ST")
    r = _ricetta_farm(assistito=stp)
    assert r.problemi() == [] and xml_fvg.problemi_fvg(r) == []
    e = s.invia(r)
    assert e.ok, e.messaggi
    assert server.stato.ricette[e.nre].cf_assistito == "STP0601010000001"
    eni = s.invia(_ricetta_farm(assistito=Assistito("ENI0601010000001")))
    assert eni.ok, eni.messaggi
    # coerenza col tipo ricetta (p. 18): STP senza ST, rifiuto locale e del server
    incoerente = _ricetta_farm(assistito=Assistito("STP0601010000001"))
    assert any("ST" in p for p in xml_fvg.problemi_fvg(incoerente))
    s.valida_localmente = False
    rifiuto = s.invia(incoerente)
    assert not rifiuto.ok and rifiuto.errori[0].codice == "1001"


def test_rev_doc_due_serrature_aggiornate():
    """docs/SAR_FVG.md diceva «due serrature» nel trasporto: la guardia sta in trasporto/http.py::consegna."""
    doc = (RADICE / "docs" / "SAR_FVG.md").read_text(encoding="utf-8")
    assert "consegna" in doc and "trasporto proprio" in doc

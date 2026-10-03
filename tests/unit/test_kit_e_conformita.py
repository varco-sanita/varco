# SPDX-License-Identifier: EUPL-1.2
import pytest

from varco import kit_mef
from varco.conformita.esegui import main as esegui_suite
from varco.conformita.motore import Motore, carica_casi


def test_credenziali_dal_kit(kit):
    c = kit_mef.credenziali_medico(kit)
    assert c.utente == "PROVAX00X00X000Y" and len(c.pincode) == 10


def test_posizioni_dal_kit(kit):
    pos = kit_mef.posizioni_medico(kit)
    assert len(pos) > 90
    assert any((p.codice_regione, p.codice_asl, p.codice_specializzazione) == ("130", "201", "F") for p in pos)


def test_assistiti_dal_kit(kit):
    a = kit_mef.assistiti_test(kit)
    assert "PNIMRA70A01H501P" in a["ABRUZZO"] and a["TOSCANA"] == ["CRLCRL81H08F032N"]


def test_kit_assente(tmp_path):
    from varco import ConfigurazioneNonValida

    with pytest.raises(ConfigurazioneNonValida):
        kit_mef.credenziali_medico(tmp_path)


def test_casi_hanno_campi_obbligatori():
    casi = carica_casi("tutte")
    assert len({c["id"] for c in casi}) == len(casi) >= 20
    for c in casi:
        assert c["famiglia"] in ("offline", "online", "fse", "sist", "fvg", "piemonte", "umbria") and c["titolo"] and c["riferimento"] and c["passi"]
        for p in c["passi"]:
            assert "atteso" in p, c["id"]


def test_suite_offline_passa_tutta(capsys):
    assert esegui_suite(["--famiglia", "offline"]) == 0
    assert "falliti 0" in capsys.readouterr().out


def test_casi_online_saltati_senza_credenziali():
    m = Motore()
    assert {m.esegui(c).stato for c in carica_casi("online")} == {"SALTATO"}


@pytest.mark.parametrize(
    "atteso",
    [
        {"codice": ["0000"], "stato_processo": "4"},
        {"codice": ["9999"]},
        {"fault_contiene": "x"},
        {"errore_codice": "1120"},
        {"nre": "1300A4019294834"},
        {"righe": 2},
    ],
)
def test_il_motore_boccia_aspettative_sbagliate(atteso):
    """Gruppo di controllo: un verde che non sa diventare rosso non prova niente."""
    caso = {"id": "T", "titolo": "t", "famiglia": "offline",
            "passi": [{"operazione": "leggi_visualizza", "risposta": "visualizza_stato3.xml", "atteso": atteso}]}
    assert Motore().esegui(caso).stato == "FALLITO"


def test_pulizia_saltata_se_variabile_mancante():
    chiamate = []

    class Finto:
        def annulla(self, nre, cf=None):
            chiamate.append(nre)

    m = Motore(adattatore=lambda c, v: Finto(), credenziali=object())
    # Giro 3 di revisione (conformità n. 2): un passo senza aspettative è FALLITO prima di girare; qui
    # serve un caso conforme al formato per arrivare alla variabile non definita.
    caso = {"id": "SAC-999", "titolo": "t", "famiglia": "online", "riferimento": "test",
            "passi": [{"operazione": "visualizza", "nre": "${nre}", "atteso": {"codice": ["0000"]}}],
            "finale": [{"operazione": "annulla", "nre": "${nre}"}]}
    esito = m.esegui(caso)
    assert esito.stato == "ERRORE", esito.motivo  # variabile non definita
    assert chiamate == []

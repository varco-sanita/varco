# SPDX-License-Identifier: EUPL-1.2
"""Issue #9, #10, #11 (limiti noti della 0.1.0): server finto SIRPED e esecutore Java della suite.
Controesempi delle issue, rossi prima e verdi dopo. Fixture da test_piemonte.py."""

from __future__ import annotations

import base64
import json
import math
import os
import subprocess
from pathlib import Path

import pytest

from test_piemonte import (  # noqa: F401 - server e regione sono fixture
    GESTIONALE, REDIRECT, TITOLARE, TrasportoLocale, _autorizza, _cifratore, _ricetta, regione, server,
)

from varco.conformita.motore import incoerenze, verifica_documento
from varco.ricetta import RicettaPiemonte
from varco.trasporto.piemonte import CanalePiemonte, GestionalePiemonte, ModalitaPiemonte
from varco.trasporto.piemonte_oauth2 import ClientOAuth2Piemonte, ErroreAutorizzazione


RADICE = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ #9 gestionale senza diritto di prescrizione


def test_issue9_gestionale_senza_diritto_di_prescrizione_non_ottiene_il_token(server):
    """A2F-OAU2-TOK-N-03: gestionale censito (SOLOEROG_301, solo «erogazione»), utente abilitato alla
    prescrizione. Prima: scope_token=('prescrizione',) e invio_ok=True."""
    solo_erog = ClientOAuth2Piemonte(server.url_oauth, GestionalePiemonte("SOLOEROG", "301"), REDIRECT,
                                     trasporto=TrasportoLocale())
    ra, ritorno = _autorizza(server, solo_erog)
    with pytest.raises(ErroreAutorizzazione, match="unauthorized_client"):
        solo_erog.leggi_callback(ritorno, ra)
    assert server.stato.codici == {}


def test_issue9_gruppo_di_controllo_gestionale_con_diritto(server, regione):
    client = ClientOAuth2Piemonte(server.url_oauth, GESTIONALE, REDIRECT, trasporto=TrasportoLocale())
    ra, ritorno = _autorizza(server, client)
    token = client.scambia_codice(client.leggi_callback(ritorno, ra), ra)
    assert token.scope == ("prescrizione",)
    can = CanalePiemonte(ModalitaPiemonte.OAUTH2, token_jwt=lambda: token.access_token, cf_medico=TITOLARE,
                         gestionale_di_prova=GESTIONALE, base_url=server.url, trasporto=TrasportoLocale())
    assert RicettaPiemonte(can, _cifratore(regione)).invia(_ricetta()).ok


# ------------------------------------------------------------------ #11 nbf / exp NaN in verify e revoke


def _token(server):
    client = ClientOAuth2Piemonte(server.url_oauth, GESTIONALE, REDIRECT, trasporto=TrasportoLocale())
    ra, ritorno = _autorizza(server, client)
    return client, client.scambia_codice(client.leggi_callback(ritorno, ra), ra).access_token


def _payload(jwt: str) -> dict:
    corpo = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4)))


@pytest.mark.parametrize("campo", ["nbf", "exp"])
@pytest.mark.parametrize("valore", [math.nan, math.inf, -math.inf])
def test_issue11_verify_e_revoke_rifiutano_tempi_non_finiti(server, campo, valore):
    client, buono = _token(server)
    rotto = server._firma(_payload(buono) | {campo: valore})
    assert server._leggi_jwt(rotto) is not None  # firma valida: è proprio il controllo dei tempi a dover mordere
    assert client.verifica_sessione(rotto, TITOLARE).stato_http == 401
    assert client.revoca_sessione(rotto, TITOLARE) == 401
    # gruppo di controllo: il token buono è valido e la sessione non è stata toccata
    info = client.verifica_sessione(buono, TITOLARE)
    assert info.valido and info.stato == 0


# ------------------------------------------------------------------ #10 senza_errori + errori_contengono: []


ATTESO = {"esito": "OK", "senza_errori": True, "errori_contengono": []}
OSSERVATO = {"esito": "OK", "errori": [], "avvisi": []}


def test_issue10_python_accetta_la_lista_vuota():
    assert incoerenze("valida_documento", ATTESO) == [] and verifica_documento(ATTESO, OSSERVATO) == []
    pieno = ATTESO | {"errori_contengono": ["X"]}
    assert incoerenze("valida_documento", pieno)  # gruppo di controllo: lista piena = incoerente


SONDA = """
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

public class SondaListaVuota {
    public static void main(String[] a) throws Exception {
        ObjectMapper m = new ObjectMapper();
        ObjectNode oss = (ObjectNode) m.readTree("{\\"esito\\":\\"OK\\",\\"errori\\":[],\\"avvisi\\":[]}");
        System.out.println("VUOTA=" + EseguiCasiFse.verifica(m.readTree(
            "{\\"esito\\":\\"OK\\",\\"senza_errori\\":true,\\"errori_contengono\\":[]}"), oss));
        System.out.println("PIENA=" + EseguiCasiFse.verifica(m.readTree(
            "{\\"esito\\":\\"OK\\",\\"senza_errori\\":true,\\"errori_contengono\\":[\\"X\\"]}"), oss));
    }
}
"""


@pytest.mark.ufficiale
def test_issue10_java_come_python_sulla_lista_vuota(tmp_path):
    from varco.fse.validazione import cartella_validatore_ufficiale

    qui = cartella_validatore_ufficiale()
    validatore = RADICE / "specifiche" / "fse" / "it-fse-gtw-validator"
    cp_txt = validatore / "target" / "cp.txt"
    if not cp_txt.exists() or not os.environ.get("JAVA_HOME"):
        pytest.skip("validatore ufficiale non preparato (strumenti/validatore-ufficiale/prepara.sh e JAVA_HOME)")
    java = Path(os.environ["JAVA_HOME"]) / "bin"
    classi = tmp_path / "classi"
    (tmp_path / "SondaListaVuota.java").write_text(SONDA, encoding="utf-8")
    cp = f"{validatore / 'target' / 'classes'}:{cp_txt.read_text().strip()}"
    r = subprocess.run([str(java / "javac"), "-nowarn", "-d", str(classi), "-cp", cp,
                        str(qui / "src" / "ValidatoreUfficiale.java"), str(qui / "src" / "EseguiCasiFse.java"),
                        str(tmp_path / "SondaListaVuota.java")], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    r = subprocess.run([str(java / "java"), "-cp", f"{classi}:{cp}", "SondaListaVuota"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    righe = dict(x.split("=", 1) for x in r.stdout.strip().splitlines())
    assert righe["VUOTA"] == "[]", righe["VUOTA"]
    assert "insieme" in righe["PIENA"]  # gruppo di controllo

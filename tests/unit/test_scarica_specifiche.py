# SPDX-License-Identifier: EUPL-1.2
"""Lo script delle specifiche: solo fonti ufficiali, hash fissati, nessuna rete nei test."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
SCRIPT = RADICE / "strumenti" / "scarica_specifiche.py"


@pytest.fixture(scope="module")
def modulo():
    spec = importlib.util.spec_from_file_location("scarica_specifiche", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def manifesto(modulo):
    return json.loads(modulo.MANIFESTO.read_text(encoding="utf-8"))


def test_manifesto_solo_fonti_ufficiali_e_hash_fissati(modulo, manifesto):
    for voce in manifesto["file"]:
        assert modulo._host_ufficiale(voce["url"]), voce["url"]
        assert re.fullmatch(r"[0-9a-f]{64}", voce["sha256"]), voce
        assert voce["gruppo"] in manifesto["gruppi"]
        assert "/main/" not in voce["url"]  # mai un ramo mobile: sempre un commit
    for voce in manifesto["git"]:
        assert modulo._host_ufficiale(voce["repository"]), voce["repository"]
        assert re.fullmatch(r"[0-9a-f]{40}", voce["commit"]), voce


def test_url_non_ufficiali_rifiutati(modulo):
    assert not modulo._host_ufficiale("http://sistemats1.sanita.finanze.it/x")  # non HTTPS
    assert not modulo._host_ufficiale("https://example.org/kit.zip")
    assert not modulo._host_ufficiale("https://sistemats1.sanita.finanze.it.example.org/x")


def test_solo_verifica_boccia_file_mancanti_o_alterati(modulo, manifesto, tmp_path):
    voce = next(v for v in manifesto["file"] if v["gruppo"] == "fse-validatore")
    assert modulo.tratta_file(voce, tmp_path, solo_verifica=True) == "MANCA"
    dest = tmp_path / voce["destinazione"]
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"alterato")
    assert modulo.tratta_file(voce, tmp_path, solo_verifica=True) == "HASH DIVERSO"


def test_destinazione_fuori_cartella_rifiutata(modulo, tmp_path):
    with pytest.raises(SystemExit):
        modulo._sotto(tmp_path, "../fuori.txt")


def test_main_solo_verifica_esce_con_errore_se_manca_tutto(modulo, tmp_path):
    assert modulo.main(["--solo-verifica", "--tutto", "--destinazione", str(tmp_path)]) == 1


def test_redirect_verso_host_non_ufficiale_rifiutato(modulo):
    import urllib.request

    gestore = modulo._SoloRedirectUfficiali()
    req = urllib.request.Request("https://raw.githubusercontent.com/x")
    for verso in ("https://example.org/x", "http://raw.githubusercontent.com/x"):
        with pytest.raises(SystemExit):
            gestore.redirect_request(req, None, 302, "Found", {}, verso)
    assert gestore.redirect_request(req, None, 302, "Found", {}, "https://github.com/y") is not None


def test_zip_scompattato_a_meta_non_e_ok(modulo, manifesto, tmp_path):
    import hashlib
    import zipfile

    zip_path = tmp_path / "k.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("dentro/a.txt", "a")
        z.writestr("dentro/b.txt", "b")
    voce = {"destinazione": "k.zip", "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
            "url": "https://github.com/x", "scompatta_in": "kit"}
    (tmp_path / "kit" / "dentro").mkdir(parents=True)
    (tmp_path / "kit" / "dentro" / "a.txt").write_text("a")
    esito = modulo.tratta_file(voce, tmp_path, solo_verifica=True)
    assert not esito.startswith("ok") and "mancano 1" in esito


def _zip_estratto(tmp_path):
    import hashlib
    import zipfile

    zip_path = tmp_path / "k.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("dentro/a.xsd", "<schema/>")
        z.writestr("dentro/b.txt", "b")
        z.extractall(tmp_path / "kit")
    voce = {"destinazione": "k.zip", "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
            "url": "https://github.com/x", "scompatta_in": "kit"}
    return voce


def test_solo_verifica_boccia_estratti_alterati(modulo, tmp_path):
    """Revisione 02/10/2026, punto 4: lo zip era verificato, gli estratti (quelli che test e strumenti
    leggono) no. Un XSD estratto modificato lasciando intatto lo zip deve essere bocciato."""
    voce = _zip_estratto(tmp_path)
    assert modulo.tratta_file(voce, tmp_path, solo_verifica=True).startswith("ok")  # gruppo di controllo
    (tmp_path / "kit" / "dentro" / "a.xsd").write_text("<schema>alterato</schema>")
    esito = modulo.tratta_file(voce, tmp_path, solo_verifica=True)
    assert not esito.startswith("ok") and "ALTERATI" in esito and "dentro/a.xsd" in esito


def test_solo_verifica_boccia_estratto_sostituito_da_una_cartella(modulo, tmp_path):
    import shutil

    voce = _zip_estratto(tmp_path)
    (tmp_path / "kit" / "dentro" / "b.txt").unlink()
    (tmp_path / "kit" / "dentro" / "b.txt").mkdir()
    assert not modulo.tratta_file(voce, tmp_path, solo_verifica=True).startswith("ok")
    shutil.rmtree(tmp_path / "kit" / "dentro" / "b.txt")


def test_solo_verifica_legge_il_contenuto_del_kit_estratto(modulo, manifesto, monkeypatch):
    """Controesempio esatto della revisione: Path.open patchato in memoria perché assistitoTest.txt
    estratto restituisca contenuto alterato; tratta_file(voce_del_kit, specifiche, solo_verifica=True)
    rispondeva «ok (già presente); scompattato in kit/» senza leggere l'estratto (0 letture)."""
    import io
    from pathlib import Path

    base = RADICE / "specifiche"
    voce = next(v for v in manifesto["file"] if v.get("scompatta_in") == "kit")
    if not (base / voce["destinazione"]).exists() or not list((base / "kit").rglob("assistitoTest.txt")):
        pytest.skip("kit MEF non scaricato (strumenti/scarica_specifiche.py --gruppi mef)")
    assert modulo.tratta_file(voce, base, solo_verifica=True).startswith("ok")  # gruppo di controllo

    letture = []
    originale = Path.open

    def open_alterato(self, *a, **k):
        if self.name == "assistitoTest.txt" and "kit" in self.parts:
            letture.append(self)
            return io.BytesIO(b"FILE ALTERATO DAL CONTROESEMPIO")
        return originale(self, *a, **k)

    monkeypatch.setattr(Path, "open", open_alterato)
    esito = modulo.tratta_file(voce, base, solo_verifica=True)
    assert letture, "il file estratto non è stato letto"
    assert not esito.startswith("ok"), esito

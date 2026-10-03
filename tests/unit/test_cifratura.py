# SPDX-License-Identifier: EUPL-1.2
import base64
import datetime as dt
import shutil
import subprocess

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from varco.cifratura import CifratoreSanitel


@pytest.fixture(scope="module")
def chiave_e_cert():
    """Coppia di chiavi e certificato generati al volo: permettono di DECIFRARE e verificare."""
    chiave = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SanitelCF-prova")])
    adesso = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chiave.public_key())
        .serial_number(1)
        .not_valid_before(adesso)
        .not_valid_after(adesso + dt.timedelta(days=1))
        .sign(chiave, hashes.SHA256())
    )
    return chiave, cert.public_bytes(serialization.Encoding.PEM)


def test_certificato_incluso_e_quello_del_kit():
    c = CifratoreSanitel.incluso()
    assert "CN=SanitelCF" in c.soggetto
    assert c.dimensione_chiave == 1024
    assert c.scadenza.date() == dt.date(2027, 1, 23)


def test_cifrato_ha_forma_attesa_ed_e_casuale():
    c = CifratoreSanitel.incluso()
    a, b = c.cifra("PNIMRA70A01H501P"), c.cifra("PNIMRA70A01H501P")
    assert len(base64.b64decode(a)) == 128  # RSA 1024 bit
    assert a != b  # padding PKCS#1 v1.5 casuale


def test_round_trip_pkcs1v15(chiave_e_cert):
    chiave, pem = chiave_e_cert
    c = CifratoreSanitel(pem)
    for valore in ("PNIMRA70A01H501P", "1234567890"):
        chiaro = chiave.decrypt(base64.b64decode(c.cifra(valore)), padding.PKCS1v15())
        assert chiaro == valore.encode()


def test_certificato_der_accettato(chiave_e_cert):
    chiave, pem = chiave_e_cert
    der = x509.load_pem_x509_certificate(pem).public_bytes(serialization.Encoding.DER)
    assert CifratoreSanitel(der).dimensione_chiave == 1024


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl non disponibile")
def test_equivalente_a_openssl(chiave_e_cert, tmp_path):
    """La specifica chiede un risultato conforme a `openssl rsautl -encrypt -certin -pkcs`:
    il nostro cifrato deve essere decifrabile da openssl con padding PKCS#1 v1.5."""
    chiave, pem = chiave_e_cert
    (tmp_path / "k.pem").write_bytes(
        chiave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    (tmp_path / "cf.enc").write_bytes(base64.b64decode(CifratoreSanitel(pem).cifra("PNIMRA70A01H501P")))
    out = subprocess.run(
        ["openssl", "pkeyutl", "-decrypt", "-inkey", str(tmp_path / "k.pem"), "-in", str(tmp_path / "cf.enc"),
         "-pkeyopt", "rsa_padding_mode:pkcs1"],
        capture_output=True, check=True,
    ).stdout
    assert out == b"PNIMRA70A01H501P"


@pytest.mark.parametrize("valore", ["", "àèì", "X" * 118])
def test_valori_rifiutati(valore):
    with pytest.raises(ValueError):
        CifratoreSanitel.incluso().cifra(valore)


def test_scaduto():
    c = CifratoreSanitel.incluso()
    assert not c.scaduto(dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc))
    assert c.scaduto(dt.datetime(2027, 2, 1, tzinfo=dt.timezone.utc))

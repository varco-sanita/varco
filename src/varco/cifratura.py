# SPDX-License-Identifier: EUPL-1.2
"""Cifratura di codice fiscale e pincode con il certificato SanitelCF.

Specifica (Progetto TS, "Web services ... parte 1: prescrizione", par. 3.5):
  - chiave pubblica RSA del certificato X.509 SanitelCF fornito dal MEF;
  - padding PKCS#1 v1.5;
  - risultato equivalente a `openssl rsautl -encrypt -inkey SanitelCF.cer -certin -pkcs`;
  - poi codifica BASE64.

Il padding PKCS#1 v1.5 è casuale: cifrare due volte lo stesso valore produce due
testi diversi, entrambi validi. Per questo nei test non si confronta il cifrato
con un valore fisso, ma se ne verifica la forma (lunghezza, decodifica).
"""

from __future__ import annotations

import base64
import datetime as _dt
from importlib import resources
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .errori import ConfigurazioneNonValida

NOME_CERTIFICATO_INCLUSO = "SanitelCF-2024-2027.pem"


class CifratoreSanitel:
    """Cifra valori in chiaro (CF assistito, pincode) per i servizi SAC."""

    def __init__(self, certificato_pem: bytes):
        try:
            cert = x509.load_pem_x509_certificate(certificato_pem)
        except ValueError:
            try:
                cert = x509.load_der_x509_certificate(certificato_pem)
            except ValueError as e:  # pragma: no cover - messaggio chiaro
                raise ConfigurazioneNonValida(f"Certificato SanitelCF illeggibile: {e}") from e
        chiave = cert.public_key()
        if not isinstance(chiave, rsa.RSAPublicKey):
            raise ConfigurazioneNonValida("Il certificato SanitelCF non contiene una chiave RSA")
        self._cert = cert
        self._chiave = chiave

    # --- costruttori -----------------------------------------------------
    @classmethod
    def da_file(cls, percorso: str | Path) -> "CifratoreSanitel":
        return cls(Path(percorso).read_bytes())

    @classmethod
    def incluso(cls) -> "CifratoreSanitel":
        """Usa la copia del certificato pubblico SanitelCF distribuita col kit."""
        dati = resources.files("varco.certificati").joinpath(NOME_CERTIFICATO_INCLUSO).read_bytes()
        return cls(dati)

    # --- informazioni ----------------------------------------------------
    @property
    def soggetto(self) -> str:
        return self._cert.subject.rfc4514_string()

    @property
    def scadenza(self) -> _dt.datetime:
        return self._cert.not_valid_after_utc

    @property
    def dimensione_chiave(self) -> int:
        return self._chiave.key_size

    def scaduto(self, adesso: _dt.datetime | None = None) -> bool:
        adesso = adesso or _dt.datetime.now(_dt.timezone.utc)
        return adesso > self.scadenza

    # --- operazione ------------------------------------------------------
    def cifra(self, valore_in_chiaro: str) -> str:
        """Restituisce BASE64(RSA-PKCS1v1.5(valore)). Il valore è codificato in ASCII."""
        if valore_in_chiaro is None or valore_in_chiaro == "":
            raise ValueError("Valore da cifrare vuoto")
        try:
            dati = valore_in_chiaro.encode("ascii")
        except UnicodeEncodeError as e:
            raise ValueError("CF e pincode devono essere ASCII") from e
        massimo = self._chiave.key_size // 8 - 11  # limite di PKCS#1 v1.5
        if len(dati) > massimo:
            raise ValueError(f"Valore troppo lungo per la chiave ({len(dati)} > {massimo} byte)")
        cifrato = self._chiave.encrypt(dati, padding.PKCS1v15())
        return base64.b64encode(cifrato).decode("ascii")

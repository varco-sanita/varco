#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
"""Prove del modulo SIST (Regione Puglia) contro il server FINTO in locale.

Scritto e verificato sulle specifiche, NON collaudato sul sistema regionale: nessuna chiamata
ai sistemi della Regione Puglia. Il server (strumenti/sist_server_finto.py) gira su 127.0.0.1
e controlla ogni richiesta come da specifica: WS-Security (firma del Timestamp), SOAPAction,
schema CVPService.xsd (se indicato), CF del certificato = operatore, applDigest, firma CAdES
del CDA, firmatario = operatore = autore, CDA contro lo schema CDA.

Uso:
    python strumenti/genera_prove_sist.py [--xsd-sist percorso/CVPService.xsd]

Crea prove/<AAAAMMGG-HHMMSS>-sist-server-finto/ con gli XML scambiati (REDATTI: registratore
di default) e riepilogo.json. Identità: solo quelle pubbliche di test del kit MEF; certificati
autofirmati generati al momento e non salvati.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE / "src"))
sys.path.insert(0, str(RADICE / "strumenti"))

import sist_server_finto  # noqa: E402

from varco import ErroreSOAP, RegistratoreFile, TrasportoHTTP  # noqa: E402
from varco.ambiente import leggi  # noqa: E402
from varco.errori import RicettaNonValida  # noqa: E402
from varco.ricetta import (  # noqa: E402
    Assistito,
    ClassePriorita,
    CriteriNreUtilizzati,
    Prescrittore,
    Ricetta,
    RicettaSIST,
    Riga,
    TipoPrescrizione,
)
from varco.ricetta import xml_sist  # noqa: E402
from varco.ricetta.sist import FirmatarioCAdESPKCS12  # noqa: E402
from varco.trasporto.sist import ApplicativoSIST, CanaleSIST, OperatoreSIST  # noqa: E402
from varco.trasporto.wssecurity import ChiavePKCS12, verifica_security  # noqa: E402

DICHIARAZIONE = "Scritto e verificato sulle specifiche, NON collaudato sul sistema regionale."
TITOLARE, SOSTITUTO, ASSISTITO = "PROVAX00X00X000Y", "PROVAX00X00X000Z", "PNIMRA70A01H501P"
CODICI_REGIONALI = {TITOLARE: "000001", SOSTITUTO: "000002"}
CODICE_APPLICATIVO = "CODICE-APPLICATIVO-DI-PROVA"
APPLICATIVO = ApplicativoSIST("VARCO", "varco", "0.1", CODICE_APPLICATIVO)


def p12_di_prova(cartella: Path, cf: str) -> Path:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
                      x509.NameAttribute(NameOID.COMMON_NAME, f"{cf}/0000000000000000.CERTIFICATO-DI-TEST")])
    ora = dt.datetime.now(dt.timezone.utc)
    c = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(k.public_key()).serial_number(1)
         .not_valid_before(ora - dt.timedelta(days=1)).not_valid_after(ora + dt.timedelta(days=1))
         .sign(k, hashes.SHA256()))
    p = cartella / f"{cf}.p12"
    p.write_bytes(pkcs12.serialize_key_and_certificates(b"prova", k, c, None, serialization.BestAvailableEncryption(b"pw")))
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xsd-sist", default=leggi("VARCO_XSD_SIST"))
    args = ap.parse_args()
    xsd = Path(args.xsd_sist) if args.xsd_sist else None

    adesso = dt.datetime.now().astimezone()
    cartella = RADICE / "prove" / (adesso.strftime("%Y%m%d-%H%M%S") + "-sist-server-finto")
    reg = RegistratoreFile(cartella)  # redatto: niente identità di test da riconoscere, niente chiaro
    trasporto = TrasportoHTTP(registratore=reg, intervallo_minimo_s=0.5)
    tmp = Path(tempfile.mkdtemp(prefix="varco-sist-"))
    p12 = {cf: p12_di_prova(tmp, cf) for cf in (TITOLARE, SOSTITUTO)}

    passi: list[dict] = []

    def annota(nome: str, esito=None, errore: Exception | None = None, **extra):
        voce: dict = {"passo": nome}
        if esito is not None:
            voce["codice_esito"] = esito.codice
            voce["messaggi"] = [{"codice": m.codice, "tipo": m.tipo} for m in esito.messaggi]
            for campo in ("nre", "stato_processo", "stato_sar", "registrato", "errore_registrazione", "solo_ricetta_rossa"):
                v = getattr(esito, campo, None)
                if v is not None:
                    voce[campo] = v
            if getattr(esito, "codice_autenticazione", None):
                voce["codice_autenticazione"] = "presente"
            if hasattr(esito, "ricette"):
                voce["ricette"] = len(esito.ricette)
        if errore is not None:
            voce["errore"] = f"{type(errore).__name__}: {errore}"
        voce.update(extra)
        passi.append(voce)
        print(json.dumps(voce, ensure_ascii=False))

    pr = Prescrittore(TITOLARE, "160", "114", "F")
    ass = Assistito(codice_fiscale=ASSISTITO, provincia="BA", asl="114", codice_regione="160")
    farm = Ricetta(prescrittore=pr, assistito=ass, tipo=TipoPrescrizione.FARMACEUTICA, righe=(
        Riga(1, codice="034298051", descrizione="FARMACO DI PROVA", codice_gruppo_equivalenza="02D",
             descrizione_gruppo_equivalenza="GRUPPO DI PROVA", nota_aifa="1"),))
    spec = Ricetta(prescrittore=pr, assistito=ass, tipo=TipoPrescrizione.SPECIALISTICA, classe_priorita=ClassePriorita.BREVE,
                   codice_diagnosi="4254", descrizione_diagnosi="CONTROLLO", non_esente=False, codice_esenzione="048",
                   righe=(Riga(1, codice="89.7", descrizione="VISITA DI PROVA", codice_catalogo="51609", tipo_accesso="1",
                               num_sedute=2),))

    with sist_server_finto.ServerSIST(CODICE_APPLICATIVO, xsd_cvp=xsd) as server:
        def servizio(cf: str, firmatario_cf: str | None = None, **kw) -> RicettaSIST:
            can = CanaleSIST(OperatoreSIST(cf, "160114"), ChiavePKCS12(str(p12[cf]), b"pw"), base_url=server.url,
                             applicativo_di_prova=kw.pop("applicativo", APPLICATIVO), trasporto=trasporto)
            return RicettaSIST(can, FirmatarioCAdESPKCS12(str(p12[firmatario_cf or cf]), b"pw"), CODICI_REGIONALI, **kw)

        titolare = servizio(TITOLARE)
        e1 = titolare.invia(farm, oscurato=False)
        annota("01 invio farmaceutica (chkPrescrizione + setRegistraPrescrizione)", e1)
        v = titolare.visualizza(e1.nre, cf_assistito=ASSISTITO)
        annota("02 visualizza (getPrescrizioneIdentificata, NRE + CF assistito)", v, righe=len(v.righe), cda=bool(v.cda))
        v = titolare.visualizza(e1.nre, cf_assistito="PROVAX00X00X000X")
        annota("03 visualizza con CF assistito sbagliato (rifiuto atteso 000004)", v)
        annota("04 annulla (setAnnullaPrescrizione)", titolare.annulla(e1.nre))
        annota("05 secondo annullamento (rifiuto atteso 000279)", titolare.annulla(e1.nre))
        e2 = titolare.invia(spec)
        annota("06 invio specialistica, catalogo regionale e numSedute", e2)
        riga_ko = Riga(1, codice="034298051", descrizione="FARMACO DI PROVA", nota_aifa="999")
        annota("07 invio con anomalie del server finto (0053 C, 0051 W): niente registrazione",
               titolare.invia(dataclasses.replace(farm, righe=(riga_ko,))))
        senza_controlli = servizio(TITOLARE, valida_localmente=False)
        e3 = senza_controlli.invia(dataclasses.replace(
            farm, assistito=Assistito(codice_fiscale="SAC_GIU_PROVA000", asl="114", codice_regione="160")))
        annota("08 SAC non disponibile (simulato): solo IUP regionale (13 caratteri), ricetta rossa, CDA con id e setId = IUP (OID Regione Puglia)", e3)
        pr_sost = dataclasses.replace(pr, codice_fiscale_sostituto=SOSTITUTO)
        try:
            titolare.invia(dataclasses.replace(farm, righe=farm.righe, prescrittore=pr_sost))
        except RicettaNonValida as e:
            annota("09 ricetta del sostituto con la CNS del titolare: rifiuto locale (000271)", errore=e)
        annota("10 invio del sostituto (codMedicoPrescrittore = sostituto, codMedicoSostituito = titolare)",
               servizio(SOSTITUTO).invia(dataclasses.replace(farm, prescrittore=pr_sost)))
        firma_altrui = servizio(TITOLARE, firmatario_cf=SOSTITUTO)
        e4 = firma_altrui.invia(farm)
        annota("11 CDA firmato da un altro medico: il server risponde 000271, registrazione da ripetere", e4)
        e4b = titolare.ripeti_registrazione(dataclasses.replace(
            e4, cda_firmato=FirmatarioCAdESPKCS12(str(p12[TITOLARE]), b"pw").firma_cades(e4.cda)))
        annota("12 ripeti_registrazione con la firma giusta, stesso NRE, nessun nuovo controllo", e4b)
        oggi = dt.datetime.now()
        annota("13 ricerca per periodo e CF assistito (getPrescrizioniIdentificate)",
               titolare.interroga_nre_utilizzati(CriteriNreUtilizzati("160", cf_assistito=ASSISTITO, dal=oggi, al=oggi)))
        try:
            servizio(TITOLARE, applicativo=dataclasses.replace(APPLICATIVO, codice_applicativo="ALTRO")).invia(farm)
        except ErroreSOAP as e:
            annota("14 codice applicativo sbagliato: Fault 000220", fault=xml_sist.codice_fault_sist(e))

        # verifiche sugli scambi registrati dal server (in chiaro solo in memoria, mai su disco)
        verifiche = []
        for azione, corpo in server.stato.richieste:
            wss = verifica_security(corpo)
            verifiche.append({"azione": azione.strip('"').rsplit("#", 1)[-1], "ws_security_valida": wss.valida,
                              "motivo": wss.motivo})

    riepilogo = {
        "dichiarazione": DICHIARAZIONE,
        "server": "strumenti/sist_server_finto.py su 127.0.0.1 (nessuna chiamata alla Regione Puglia)",
        "xsd_cvp": "CVPService.xsd validato dal server su ogni richiesta" if xsd else "CVPService.xsd NON indicato: schema non controllato",
        "data": adesso.isoformat(timespec="seconds"),
        "passi": passi,
        "ws_security": {"richieste": len(verifiche), "valide": sum(v["ws_security_valida"] for v in verifiche),
                        "dettaglio": verifiche},
    }
    cartella.mkdir(parents=True, exist_ok=True)
    (cartella / "riepilogo.json").write_text(json.dumps(riepilogo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (cartella / "LEGGIMI.txt").write_text(
        DICHIARAZIONE + "\n\nScambi con il server FINTO del kit, non con il SIST. File redatti (registratore di "
        "default): CF, NRE, codici, CDA e firme sostituiti da segnaposto.\n", encoding="utf-8")
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()
    print(f"\n{cartella.relative_to(RADICE)}: {len(passi)} passi, WS-Security valida su "
          f"{riepilogo['ws_security']['valide']}/{len(verifiche)} richieste")
    print(DICHIARAZIONE)
    return 0


if __name__ == "__main__":
    sys.exit(main())

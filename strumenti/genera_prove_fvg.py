#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
"""Prove del modulo SAR FVG (Regione Friuli-Venezia Giulia, Insiel) contro il server FINTO in locale.

Scritto e verificato sulle specifiche, NON collaudato sul sistema regionale: nessuna chiamata
ai sistemi della Regione FVG o di Insiel. Il server (strumenti/fvg_server_finto.py) gira su
127.0.0.1 in HTTPS con mutua autenticazione e controlla ogni richiesta come da specifica:
certificato client (CF = medico che invia), User-Agent del par. 3.1, SOAPAction vuota,
prodottoCme, pinCode vuoto, XSD ufficiali FVG (se indicati), CF dell'assistito cifrato.

Uso:
    python strumenti/genera_prove_fvg.py [--xsd-fvg specifiche/fvg/wsdl/sar]

Crea prove/<AAAAMMGG-HHMMSS>-fvg-server-finto/ con gli XML scambiati (REDATTI: registratore di
default) e riepilogo.json. Identità: solo quelle pubbliche di test del kit MEF; certificati di
prova generati al momento e cancellati alla fine.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE / "src"))
sys.path.insert(0, str(RADICE / "strumenti"))

import fvg_server_finto  # noqa: E402

from varco import CifratoreSanitel, ErroreSOAP, ErroreTrasporto, RegistratoreFile, TrasportoHTTP  # noqa: E402
from varco.ambiente import leggi  # noqa: E402
from varco.errori import RicettaNonValida  # noqa: E402
from varco.ricetta import (  # noqa: E402
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
from varco.trasporto.fvg import ApplicativoFVG, CanaleFVG, PostazioneFVG  # noqa: E402

DICHIARAZIONE = "Scritto e verificato sulle specifiche, NON collaudato sul sistema regionale."
TITOLARE, SOSTITUTO, ASSISTITO = "PROVAX00X00X000Y", "PROVAX00X00X000Z", "PNIMRA70A01H501P"
APPLICATIVO = ApplicativoFVG("VARCO-PROVA", "0.1", "1.4.4")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xsd-fvg", default=leggi("VARCO_XSD_FVG"))
    args = ap.parse_args()
    xsd = Path(args.xsd_fvg) if args.xsd_fvg else None

    adesso = dt.datetime.now().astimezone()
    cartella = RADICE / "prove" / (adesso.strftime("%Y%m%d-%H%M%S") + "-fvg-server-finto")
    reg = RegistratoreFile(cartella)  # redatto: niente identità di test da riconoscere, niente chiaro
    tmp = Path(tempfile.mkdtemp(prefix="varco-fvg-"))
    tls = fvg_server_finto.materiale_tls(tmp, [TITOLARE, SOSTITUTO])
    cifratore = CifratoreSanitel.da_file(tls.certificato_cifratura)

    passi: list[dict] = []

    def annota(nome: str, esito=None, errore: Exception | None = None, **extra):
        voce: dict = {"passo": nome}
        if esito is not None:
            voce["codice_esito"] = esito.codice
            voce["messaggi"] = [{"codice": m.codice, "tipo": m.tipo} for m in esito.messaggi]
            voce["comunicazioni"] = [c.codice for c in esito.comunicazioni]
            for campo in ("nre", "stato_processo"):
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

    pr = Prescrittore(TITOLARE, "060", "101", "F")
    ass = Assistito(codice_fiscale=ASSISTITO, provincia="UD", asl="101")
    farm = Ricetta(prescrittore=pr, assistito=ass, tipo=TipoPrescrizione.FARMACEUTICA, righe=(
        Riga(1, codice_gruppo_equivalenza="G3B", descrizione_gruppo_equivalenza="GRUPPO DI PROVA"),))
    spec = Ricetta(prescrittore=pr, assistito=ass, tipo=TipoPrescrizione.SPECIALISTICA, classe_priorita=ClassePriorita.URGENTE,
                   descrizione_diagnosi="1-Indicazione clinica di prova. Esplicitazione di prova.", testata2="R066;P1",
                   righe=(Riga(1, codice="88.39.9", descrizione="PRESTAZIONE DI PROVA", codice_catalogo="2774", tipo_accesso="1"),))

    with fvg_server_finto.ServerFVG(certificato_server=tls.certificato_server, chiave_server=tls.chiave_server,
                                    ca_client=tls.ca, chiave_cifratura_pem=tls.chiave_cifratura_pem,
                                    prodotto_cme=APPLICATIVO.prodotto_cme, xsd_dir=xsd,
                                    sostituti_abilitati={(TITOLARE, SOSTITUTO, "101")}) as server:
        def servizio(cf: str, carta: str | None = "stessa", applicativo=APPLICATIVO) -> RicettaFVG:
            carta = cf if carta == "stessa" else carta
            can = CanaleFVG(cf, PostazioneFVG("MACOS", "15.0", TITOLARE, "LICENZA-PROVA-0001"), base_url=server.url,
                            applicativo_di_prova=applicativo, certificato_carta=tls.certificato_client_der(cf),
                            trasporto=TrasportoHTTP(registratore=reg, intervallo_minimo_s=0.5,
                                                    contesto_tls=tls.contesto_client(carta)))
            return RicettaFVG(can, cifratore)

        titolare = servizio(TITOLARE)
        e1 = titolare.invia(farm)
        annota("01 invio farmaceutica (InvioPrescritto, mTLS con la carta del titolare)", e1)
        v = titolare.visualizza(e1.nre)
        annota("02 visualizza (VisualizzaPrescritto)", v, righe=len(v.righe))
        annota("03 annulla (AnnullaPrescritto)", titolare.annulla(e1.nre))
        annota("04 secondo annullamento (rifiuto atteso 1120)", titolare.annulla(e1.nre))
        annota("05 invio specialistica: catalogo regionale, versioneCR, RAO in testata2", titolare.invia(spec))
        dpc = dataclasses.replace(farm, righe=(Riga(1, codice_gruppo_equivalenza="DPC", descrizione_gruppo_equivalenza="FARMACO IN DPC"),))
        annota("06 farmaco in DPC: comunicazione 0196", titolare.invia(dpc))
        for n, catalogo, forma in (("07", "999999", "nell'elenco errori"), ("08", "999998", "come SOAP Fault")):
            riga = dataclasses.replace(spec.righe[0], codice_catalogo=catalogo)
            e = titolare.invia(dataclasses.replace(spec, righe=(riga,)))
            annota(f"{n} codice di downgrade {forma}: ricetta rossa MIR (par. 2.3.6)", e, downgrade_mir=richiede_downgrade_mir(e))
        pr_sost = dataclasses.replace(pr, codice_fiscale_sostituto=SOSTITUTO)
        try:
            titolare.invia(dataclasses.replace(farm, prescrittore=pr_sost))
        except RicettaNonValida as e:
            annota("09 ricetta del sostituto con la carta del titolare: rifiuto locale", errore=e)
        sost = servizio(SOSTITUTO)
        e2 = sost.invia(dataclasses.replace(farm, prescrittore=pr_sost))
        annota("10 invio del sostituto (cfMedico1 titolare, cfMedico2 sostituto, carta del sostituto)", e2)
        annota("11 il titolare prova ad annullare la ricetta del sostituto (rifiuto atteso 1125)", titolare.annulla(e2.nre))
        annota("12 il sostituto annulla la sua ricetta", sost.annulla(e2.nre, cf_medico=TITOLARE))
        vs = sost.verifica_sostituto(TITOLARE, SOSTITUTO, "101")
        annota("13 VerificaPosizioneMedicoSostituto (GestoreAutorizzazioni)", abilitato=vs.abilitato)
        # il server finto applica il periodo (estremi compresi): tutta la giornata di oggi
        dal = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        al = dal.replace(hour=23, minute=59, second=59)
        annota("14 lista NRE utilizzati (endpoint del server finto: in FVG non è pubblicato)",
               titolare.interroga_nre_utilizzati(CriteriNreUtilizzati("060", tipo=TipoPrescrizione.FARMACEUTICA, dal=dal, al=al)))
        try:
            servizio(TITOLARE, carta=None).visualizza(e1.nre)
        except ErroreTrasporto as e:
            annota("15 senza certificato client: la mutua autenticazione non si chiude", errore=e)
        try:
            servizio(TITOLARE, carta=SOSTITUTO).invia(farm)
        except ErroreSOAP as e:
            annota("16 carta di un altro medico nel contesto TLS: il server rifiuta", errore=e)
        try:
            servizio(TITOLARE, applicativo=ApplicativoFVG("NON-ACCREDITATO", "0.1")).visualizza(e1.nre)
        except ErroreSOAP as e:
            annota("17 prodottoCme non accreditato: il server rifiuta", errore=e)
        richieste = list(server.stato.richieste)

    riepilogo = {
        "dichiarazione": DICHIARAZIONE,
        "server": "strumenti/fvg_server_finto.py su 127.0.0.1, HTTPS con mutua autenticazione "
                  "(nessuna chiamata alla Regione FVG né a Insiel)",
        "xsd_fvg": "XSD FVG validati dal server su ogni richiesta" if xsd else "XSD FVG NON indicati: schema non controllato",
        "data": adesso.isoformat(timespec="seconds"),
        "passi": passi,
        "richieste_ricevute_dal_server": len(richieste),
        "richieste_con_certificato_client": sum(1 for r in richieste if r["cf_certificato"]),
    }
    cartella.mkdir(parents=True, exist_ok=True)
    (cartella / "riepilogo.json").write_text(json.dumps(riepilogo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (cartella / "LEGGIMI.txt").write_text(
        DICHIARAZIONE + "\n\nScambi con il server FINTO del kit, non con il SAR FVG. File redatti (registratore di "
        "default): CF, NRE, codici, CF cifrato e identificativo della postazione sostituiti da segnaposto.\n",
        encoding="utf-8")
    shutil.rmtree(tmp)
    print(f"\n{cartella.relative_to(RADICE)}: {len(passi)} passi, {len(richieste)} richieste ricevute dal server finto")
    print(DICHIARAZIONE)
    return 0


if __name__ == "__main__":
    sys.exit(main())

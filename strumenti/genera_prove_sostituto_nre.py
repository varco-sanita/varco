#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
"""Prove reali nell'ambiente di TEST del MEF: medico SOSTITUTO e InterrogaNreUtilizzati.

Uso:
    VARCO_KIT_MEF=specifiche/kit/kit-ricetta-dematerializzata-datamatrix \\
        python strumenti/genera_prove_sostituto_nre.py

Crea prove/<AAAAMMGG-HHMMSS>-sostituto-nre/ con gli XML scambiati e riepilogo.json.
Solo utenze di test del kit MEF: titolare PROVAX00X00X000Y, sostituto PROVAX00X00X000Z
(PosizioniTestMedicoSostituto.txt). Nessuna prova di password sbagliata.
Frequenza: 1 richiesta al secondo (limitatore del trasporto). Tutte le ricette create
vengono annullate alla fine.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE / "src"))

from varco import CanaleSAC, ErroreSOAP, RegistratoreFile, RicettaSAC, TrasportoHTTP, kit_mef  # noqa: E402
from varco.errori import RicettaNonValida  # noqa: E402
from varco.ricetta import (  # noqa: E402
    Assistito,
    CriteriNreUtilizzati,
    Ricetta,
    Riga,
    TipoPrescrizione,
)
from varco.ricetta.modello import ora_italiana  # noqa: E402


def main() -> int:
    adesso = dt.datetime.now().astimezone()
    cartella = RADICE / "prove" / (adesso.strftime("%Y%m%d-%H%M%S") + "-sostituto-nre")
    # in chiaro solo se la chiamata usa SOLO identità di test del kit; altrimenti redatta
    reg = RegistratoreFile(cartella, identita_di_test=kit_mef.identita_di_test())
    trasporto = TrasportoHTTP(registratore=reg, intervallo_minimo_s=1.0)

    cred_titolare = kit_mef.credenziali_medico()
    cred_sostituto = kit_mef.credenziali_sostituto()
    titolare = RicettaSAC(CanaleSAC(cred_titolare, trasporto=trasporto))
    sostituto = RicettaSAC(CanaleSAC(cred_sostituto, trasporto=trasporto))
    # stessa utenza del sostituto, ma senza i controlli locali: serve a vedere cosa fa il SAC
    titolare_senza_controlli = RicettaSAC(CanaleSAC(cred_titolare, trasporto=trasporto), valida_localmente=False)

    pos = next(
        p for p in kit_mef.posizioni_medico()
        if (p.codice_regione, p.codice_asl, p.codice_specializzazione, p.codice_struttura) == ("130", "201", "F", None)
    )
    pr_titolare = pos.prescrittore()
    # CASO 2 della specifica: dati di posizione del TITOLARE, cfMedico2 = sostituto
    pr_con_sostituto = dataclasses.replace(pr_titolare, codice_fiscale_sostituto=cred_sostituto.cf)
    cf_assistito = kit_mef.assistiti_test()["ABRUZZO"][1]
    assistito = Assistito(codice_fiscale=cf_assistito, provincia="AQ", asl="201")
    riga = Riga(1, codice_gruppo_equivalenza="G3B", descrizione_gruppo_equivalenza="LEVETIRACETAM 500MG 60 UNITA' USO ORALE")

    passi: list[dict] = []
    da_annullare: list[tuple[RicettaSAC, str, str]] = []  # (servizio, nre, cf che può annullare)

    def annota(nome: str, esito=None, errore: Exception | None = None, **extra):
        voce = {"passo": nome, "ora": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        if esito is not None:
            voce.update(
                codice_esito=esito.codice,
                messaggi=[{"codice": m.codice, "testo": m.testo, "tipo": m.tipo} for m in esito.messaggi],
            )
            for campo in ("nre", "codice_autenticazione", "stato_processo"):
                if getattr(esito, campo, None):
                    voce[campo] = getattr(esito, campo)
            if hasattr(esito, "testata") and esito.testata:
                voce["cfMedico1"] = esito.testata.get("cfMedico1")
                voce["cfMedico2"] = esito.testata.get("cfMedico2")
            if hasattr(esito, "ricette"):
                voce["ricette"] = [dataclasses.asdict(r) for r in esito.ricette]
        if errore is not None:
            voce["errore"] = repr(errore)
        voce.update(extra)
        passi.append(voce)
        print(json.dumps(voce, ensure_ascii=False)[:400])
        return esito

    inizio = ora_italiana().replace(hour=0, minute=0, second=0)

    # ---------------------------------------------------------------- sostituto
    r_sost = Ricetta(pr_con_sostituto, assistito, TipoPrescrizione.FARMACEUTICA, [riga])
    e = annota("S-1 invio del SOSTITUTO (cfMedico1=titolare, cfMedico2=sostituto, credenziali e pincode del sostituto)",
               sostituto.invia(r_sost))
    nre_s = e.nre if e.ok else None
    if nre_s:
        da_annullare.append((sostituto, nre_s, cred_sostituto.cf))
        annota("S-2 visualizza come sostituto (cfMedico=sostituto)", sostituto.visualizza(nre_s, cf_medico=cred_sostituto.cf))
        annota("S-3 visualizza come titolare (cfMedico=titolare, credenziali del titolare)",
               titolare.visualizza(nre_s, cf_medico=cred_titolare.cf))
        annota("S-4 annulla come TITOLARE (la specifica lo vieta: atteso rifiuto)",
               titolare.annulla(nre_s, cf_medico=cred_titolare.cf))

    # controllo locale: sostituto indicato ma credenziali del titolare -> il kit rifiuta prima di chiamare
    try:
        titolare.invia(r_sost)
        annota("S-5 controllo locale sostituto/credenziali", errore=RuntimeError("atteso rifiuto locale, partita la chiamata"))
    except RicettaNonValida as err:
        annota("S-5 controllo locale: cfMedico2 valorizzato con credenziali del titolare (rifiuto locale, nessuna chiamata)",
               errore=err)
    # stessa richiesta senza controlli locali: cosa risponde il SAC?
    e = annota("S-6 stessa richiesta mandata al SAC senza controlli locali (credenziali titolare, cfMedico2=sostituto)",
               titolare_senza_controlli.invia(r_sost))
    if e.ok and e.nre:
        da_annullare.append((titolare, e.nre, cred_titolare.cf))

    # ---------------------------------------------------------------- interroga NRE
    r_tit = Ricetta(pr_titolare, assistito, TipoPrescrizione.FARMACEUTICA, [riga])
    e = annota("N-0 invio del titolare (serve un NRE di oggi da cercare)", titolare.invia(r_tit))
    nre_t = e.nre if e.ok else None
    if nre_t:
        da_annullare.append((titolare, nre_t, cred_titolare.cf))
        annota("N-1 interroga NRE puntuale (titolare)",
               titolare.interroga_nre_utilizzati(CriteriNreUtilizzati(pr_titolare.codice_regione, nre=nre_t)))
    fine = ora_italiana().replace(hour=23, minute=59, second=59)
    annota("N-2 interroga per intervallo di date di oggi, tipo F (titolare)",
           titolare.interroga_nre_utilizzati(CriteriNreUtilizzati(pr_titolare.codice_regione, dal=inizio, al=fine,
                                                                  tipo=TipoPrescrizione.FARMACEUTICA)))
    annota("N-3 interroga per date + CF assistito (titolare)",
           titolare.interroga_nre_utilizzati(CriteriNreUtilizzati(pr_titolare.codice_regione, dal=inizio, al=fine,
                                                                  cf_assistito=cf_assistito)))
    annota("N-4 interroga NRE inesistente (atteso rifiuto o lista vuota)",
           titolare.interroga_nre_utilizzati(CriteriNreUtilizzati(pr_titolare.codice_regione, nre="1300A4099999999")))
    if nre_s:
        annota("N-5 interroga l'NRE del sostituto, come sostituto (cfMedico=sostituto)",
               sostituto.interroga_nre_utilizzati(CriteriNreUtilizzati(pr_titolare.codice_regione, nre=nre_s),
                                                  cf_medico=cred_sostituto.cf))
        annota("N-6 interroga l'NRE del sostituto, come titolare",
               titolare.interroga_nre_utilizzati(CriteriNreUtilizzati(pr_titolare.codice_regione, nre=nre_s)))
    try:
        titolare.interroga_nre_utilizzati(CriteriNreUtilizzati(pr_titolare.codice_regione))
        annota("N-7 controllo locale criteri", errore=RuntimeError("atteso rifiuto locale"))
    except RicettaNonValida as err:
        annota("N-7 controllo locale: né NRE né date (rifiuto locale, nessuna chiamata)", errore=err)

    # ---------------------------------------------------------------- pulizia
    altro = {cred_titolare.cf: (sostituto, cred_sostituto.cf), cred_sostituto.cf: (titolare, cred_titolare.cf)}
    for servizio, nre, cf in da_annullare:
        e = annota(f"Z annulla {nre} (cfMedico={cf})", servizio.annulla(nre, cf_medico=cf))
        if not e.ok:
            s2, cf2 = altro[cf]
            annota(f"Z annulla {nre} con l'altro medico (cfMedico={cf2})", s2.annulla(nre, cf_medico=cf2))
    if nre_s:
        annota("Z visualizza la ricetta del sostituto dopo l'annullamento (atteso stato 4)",
               sostituto.visualizza(nre_s, cf_medico=cred_sostituto.cf))

    riepilogo = {
        "generato": adesso.isoformat(timespec="seconds"),
        "ambiente": titolare.canale.base_url,
        "titolare": cred_titolare.utente,
        "sostituto": cred_sostituto.utente,
        "posizione_titolare": {"regione": pos.codice_regione, "asl": pos.codice_asl, "specializzazione": pos.codice_specializzazione},
        "assistito_test": cf_assistito,
        "passi": passi,
        "file": [p.name for p in reg.file_scritti],
    }
    (cartella / "riepilogo.json").write_text(json.dumps(riepilogo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nProve salvate in {cartella}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ErroreSOAP as e:
        print("SOAP Fault:", e)
        raise

#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
"""Genera le prove: scambi XML reali con l'ambiente di TEST del MEF (demservicetest).

Uso:
    VARCO_KIT_MEF=specifiche/kit/kit-ricetta-dematerializzata-datamatrix \
        python strumenti/genera_prove.py

Crea prove/<AAAAMMGG-HHMMSS>/ con, per ogni chiamata, richiesta.xml, risposta.xml
e meta.json (header di autenticazione mascherati), più riepilogo.json e il PDF
del promemoria restituito dal SAC. Solo utenze e assistiti di test del kit MEF.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE / "src"))

from varco import CanaleSAC, Credenziali, ErroreSOAP, RegistratoreFile, RicettaSAC, TrasportoHTTP, kit_mef  # noqa: E402
from varco.ricetta import (  # noqa: E402
    Assistito,
    ClassePriorita,
    Prescrittore,
    Ricetta,
    Riga,
    TipoPrescrizione,
)


def main() -> int:
    adesso = dt.datetime.now().astimezone()
    cartella = RADICE / "prove" / adesso.strftime("%Y%m%d-%H%M%S")
    # in chiaro solo se la chiamata usa SOLO identità di test del kit; altrimenti redatta
    reg = RegistratoreFile(cartella, identita_di_test=kit_mef.identita_di_test())
    trasporto = TrasportoHTTP(registratore=reg, intervallo_minimo_s=1.0)
    cred = kit_mef.credenziali_medico()
    servizio = RicettaSAC(CanaleSAC(cred, trasporto=trasporto))

    # Posizione di test del kit: Abruzzo (130), ASL 201, medicina generale (F), nessuna struttura.
    pos = next(p for p in kit_mef.posizioni_medico() if (p.codice_regione, p.codice_asl, p.codice_specializzazione, p.codice_struttura) == ("130", "201", "F", None))
    prescrittore: Prescrittore = pos.prescrittore()
    cf_assistito = kit_mef.assistiti_test()["ABRUZZO"][1]  # stesso assistito usato dal progetto SoapUI del kit
    assistito = Assistito(codice_fiscale=cf_assistito, provincia="AQ", asl="201")

    passi: list[dict] = []

    def annota(nome: str, esito=None, errore: Exception | None = None, **extra):
        voce = {"passo": nome, "ora": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        if esito is not None:
            voce.update(
                codice_esito=esito.codice,
                messaggi=[{"codice": m.codice, "testo": m.testo, "tipo": m.tipo} for m in esito.messaggi],
                nre=getattr(esito, "nre", None),
            )
            for campo in ("codice_autenticazione", "stato_processo", "data_inserimento"):
                if getattr(esito, campo, None):
                    voce[campo] = getattr(esito, campo)
        if errore is not None:
            voce["errore"] = repr(errore)
        voce.update(extra)
        passi.append(voce)
        print(json.dumps(voce, ensure_ascii=False))

    # 1) ciclo farmaceutica: invio -> visualizza -> annulla -> visualizza -> annulla (rifiuto atteso)
    farmaco = Ricetta(
        prescrittore=prescrittore,
        assistito=assistito,
        tipo=TipoPrescrizione.FARMACEUTICA,
        righe=[Riga(quantita=1, codice_gruppo_equivalenza="G3B", descrizione_gruppo_equivalenza="LEVETIRACETAM 500MG 60 UNITA' USO ORALE")],
    )
    e = servizio.invia(farmaco)
    annota("F-1 invio farmaceutica", e, cognome_medico=e.cognome_medico, nome_medico=e.nome_medico)
    if e.pdf_promemoria:
        (cartella / f"promemoria_{e.nre}.pdf").write_bytes(e.pdf_promemoria)
    nre_f = e.nre
    if e.ok and nre_f:
        annota("F-2 visualizza (atteso stato 3)", servizio.visualizza(nre_f))
        annota("F-3 annulla", servizio.annulla(nre_f))
        annota("F-4 visualizza (atteso stato 4)", servizio.visualizza(nre_f))
        annota("F-5 annulla di nuovo (atteso rifiuto)", servizio.annulla(nre_f))

    # 2) specialistica con priorità e catalogo, poi annullata
    prestazione = Ricetta(
        prescrittore=prescrittore,
        assistito=assistito,
        tipo=TipoPrescrizione.SPECIALISTICA,
        righe=[Riga(quantita=1, codice="99.97.2", descrizione="TRATTAMENTI PER APPLICAZIONE DI PROTESI RIMOVIBILE", codice_catalogo="A099972")],
        classe_priorita=ClassePriorita.PROGRAMMATA,
        descrizione_diagnosi="CONTROLLO (dato di test)",
    )
    e = servizio.invia(prestazione)
    annota("P-1 invio specialistica", e)
    if e.ok and e.nre:
        annota("P-2 annulla", servizio.annulla(e.nre))

    # 3) NRE inesistente
    annota("X-1 visualizza NRE inesistente", servizio.visualizza("1300A4099999999"))

    # 4) utenza inesistente (NON si usa una password sbagliata sull'utenza condivisa del kit)
    finto = RicettaSAC(CanaleSAC(Credenziali("UTENTEINESISTENT", "x", "0000000000"), trasporto=trasporto))
    try:
        finto.visualizza("1300A4099999999")
        annota("X-2 utenza inesistente", errore=RuntimeError("atteso SOAP Fault, arrivata risposta"))
    except ErroreSOAP as err:
        annota("X-2 utenza inesistente (atteso SOAP Fault)", errore=err)

    riepilogo = {
        "generato": adesso.isoformat(timespec="seconds"),
        "ambiente": servizio.canale.base_url,
        "utenza": cred.utente,
        "posizione": {"regione": pos.codice_regione, "asl": pos.codice_asl, "specializzazione": pos.codice_specializzazione},
        "assistito_test": cf_assistito,
        "passi": passi,
        "file": [p.name for p in reg.file_scritti],
    }
    (cartella / "riepilogo.json").write_text(json.dumps(riepilogo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nProve salvate in {cartella}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

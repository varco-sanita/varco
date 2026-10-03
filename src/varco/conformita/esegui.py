# SPDX-License-Identifier: EUPL-1.2
"""Riga di comando della suite di conformità (esecutore Python dei casi in conformita/).

Esempi:
    # nessuna rete: risposte reali registrate + codifica XSD
    python -m varco.conformita.esegui --famiglia offline

    # documenti FSE (validatore ufficiale se preparato, altrimenti XSD + schematron in locale)
    python -m varco.conformita.esegui --famiglia fse

    # tutto, contro l'ambiente di TEST del MEF, con le utenze del kit pubblico
    python -m varco.conformita.esegui --famiglia tutte --kit percorso/kit-ricetta-dematerializzata-datamatrix

    # SIST Regione Puglia, nessuna rete: codifica CVP e CDA2, risposte SINTETICHE
    # (lo schema CVPService.xsd è della Regione e non sta nel repository: va indicato)
    python -m varco.conformita.esegui --famiglia sist --xsd-sist percorso/wsdl-pddasl/CVPService.xsd

    # SAR Regione FVG (Insiel), nessuna rete: codifica contro gli XSD di wsdl_prescritto.zip, risposte SINTETICHE
    python -m varco.conformita.esegui --famiglia fvg --xsd-fvg percorso/wsdl/sar

    # SIRPED Regione Piemonte, nessuna rete: codifica (XSD del MEF inclusi; XSD A2F del Sistema TS da fuori),
    # intestazioni, PKCE, JWT, risposte SINTETICHE di CreateAuth/CheckToken/RevokeAuth
    python -m varco.conformita.esegui --famiglia piemonte --xsd-a2f "percorso/Kit per lo sviluppo - A2F SistemaTS - ver. 20250902/wsdl"

    # SAR Regione Umbria (PuntoZero), nessuna rete: codifica JSON contro l'OpenAPI ufficiale (da fuori), risposte SINTETICHE
    python -m varco.conformita.esegui --famiglia umbria --openapi-umbria percorso/sar-open-api-prescrittore.yaml

    # collaudo di un'altra implementazione: --adattatore vale SOLO con --famiglia online e
    # --adattatore-fse SOLO con --famiglia fse. Le altre famiglie eseguono i codec di questo kit:
    # con un adattatore la riga di comando si rifiuta, invece di dare verdi che non lo riguardano.
    python -m varco.conformita.esegui --famiglia online --kit ... --adattatore mio_modulo:crea_servizio
    python -m varco.conformita.esegui --famiglia fse --adattatore-fse mio_modulo:genera_pss

Esito (exit): 0 solo se almeno un caso è stato giudicato e nessuno è FALLITO o ERRORE; 1 se ci sono
FALLITO o ERRORE; 2 se la suite non è eseguibile (cartella inesistente, id sconosciuti, nessun caso,
tutti SALTATO) o le opzioni sono incompatibili.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from .. import kit_mef
from ..ricetta.modello import ora_italiana
from ..trasporto import RegistratoreFile, TrasportoHTTP
from .adattatori import adattatore_sac, carica
from .motore import Motore, SuiteNonValida, carica_casi, rapporto_json, validatore_fse_predefinito

# Posizione di test usata di default: Abruzzo, ASL 201, medicina generale, senza struttura
# (è la stessa del progetto SoapUI del kit MEF).
POSIZIONE_DEFAULT = ("130", "201", "F", None)


def contesto_offline() -> dict:
    """I casi offline usano comunque segnaposto: valori fittizi (e di test)."""
    prescrittore = {"codice_fiscale": "PROVAX00X00X000Y", "codice_regione": "130", "codice_asl": "201",
                    "codice_specializzazione": "F"}
    return {
        "prescrittore": prescrittore,
        "prescrittore_con_sostituto": prescrittore | {"codice_fiscale_sostituto": "PROVAX00X00X000Z"},
        "assistito_cf": "AAAAAA00A00A000A",
        "medico_cf": "PROVAX00X00X000Y",
        "sostituto_cf": "PROVAX00X00X000Z",
        "regione": "130",
        "oggi_inizio": "2026-09-30 00:00:00",
        "oggi_fine": "2026-09-30 23:59:59",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="varco-conformita", description="Suite di conformità del kit (ricetta SAC, documenti FSE, SIST Puglia, SAR FVG, SIRPED Piemonte, SAR Umbria)")
    ap.add_argument("--famiglia", choices=["offline", "online", "fse", "sist", "fvg", "piemonte", "umbria", "tutte"], default="offline")
    ap.add_argument("--casi", help="cartella della suite (default: conformita/ del progetto o $VARCO_CONFORMITA)")
    ap.add_argument("--kit", help="cartella del kit MEF (default: $VARCO_KIT_MEF), serve per i casi online")
    ap.add_argument("--solo", nargs="+", help="esegue solo questi id (un id sconosciuto è un errore)")
    ap.add_argument("--adattatore", help="modulo:funzione che crea l'implementazione SAC da collaudare "
                    "(solo con --famiglia online)")
    ap.add_argument("--adattatore-fse", help="modulo:funzione(dati: dict) -> bytes che genera il CDA da collaudare "
                    "(solo con --famiglia fse)")
    ap.add_argument("--validatore-fse", choices=["auto", "ufficiale", "locale"], default="auto")
    ap.add_argument("--xsd-sist", help="CVPService.xsd del SIST (default: $VARCO_XSD_SIST); senza, la parte XSD "
                    "dei casi di codifica SIST è SALTATO")
    ap.add_argument("--xsd-fvg", help="cartella wsdl/sar di wsdl_prescritto.zip (Insiel; default: $VARCO_XSD_FVG); "
                    "senza, la parte XSD dei casi di codifica FVG è SALTATO")
    ap.add_argument("--xsd-a2f", help="cartella wsdl del kit A2F del Sistema TS (default: $VARCO_XSD_A2F); "
                    "senza, la parte XSD dei casi di codifica CreateAuth/CheckToken/RevokeAuth (Piemonte) è SALTATO")
    ap.add_argument("--openapi-umbria", help="sar-open-api-prescrittore.yaml del SAR Umbria (default: $VARCO_OPENAPI_UMBRIA); "
                    "senza, la parte di schema dei casi di codifica Umbria è SALTATO")
    ap.add_argument("--base-url", help="URL base alternativo (default: ambiente di TEST MEF)")
    ap.add_argument(
        "--registra",
        help="cartella dove salvare gli scambi XML: in chiaro solo le chiamate che usano esclusivamente "
        "identità di test del kit MEF, tutte le altre redatte (CF, pincode, NRE, PDF tolti)",
    )
    ap.add_argument(
        "--registra-dati-personali-in-chiaro",
        action="store_true",
        help="PERICOLOSO: con --registra scrive richieste e risposte INTERE anche con dati reali "
        "(CF, promemoria PDF, dati sanitari). Solo con una base giuridica: vedi docs/MINACCE.md",
    )
    ap.add_argument("--rapporto", help="file JSON del rapporto")
    args = ap.parse_args(argv)
    if args.registra_dati_personali_in_chiaro and not args.registra:
        ap.error("--registra-dati-personali-in-chiaro richiede --registra")
    if args.registra and args.famiglia not in ("online", "tutte"):
        ap.error("--registra vale solo con --famiglia online o tutte (le altre non fanno chiamate)")
    # Un adattatore non si ignora in silenzio: le famiglie diverse da online (fse per --adattatore-fse)
    # eseguono i codec di QUESTO kit, e i loro verdi non direbbero niente dell'implementazione indicata.
    if args.adattatore and args.famiglia != "online":
        ap.error(f"--adattatore collauda solo i casi online: usare --famiglia online (con --famiglia {args.famiglia} "
                 "i casi eseguirebbero i codec di questo kit, non l'implementazione indicata)")
    if args.adattatore_fse and args.famiglia != "fse":
        ap.error(f"--adattatore-fse collauda solo i casi FSE: usare --famiglia fse (con --famiglia {args.famiglia} "
                 "il generatore indicato non verrebbe usato da tutti i casi)")

    cartella = Path(args.casi) if args.casi else None
    try:
        casi = carica_casi(args.famiglia, args.solo, cartella)
    except SuiteNonValida as e:
        print(f"ERRORE: {e}. Nessun caso eseguito.", file=sys.stderr)
        return 2
    if not casi:
        print(f"ERRORE: nessun caso della famiglia {args.famiglia} in {cartella or 'conformita/'}. Una suite vuota non "
              "è un verde.", file=sys.stderr)
        return 2
    adattatore = credenziali = sostituto = None
    contesto = contesto_offline()
    if args.famiglia in ("online", "tutte"):
        credenziali = kit_mef.credenziali_medico(args.kit)
        sostituto = kit_mef.credenziali_sostituto(args.kit)
        pos = next(
            p for p in kit_mef.posizioni_medico(args.kit)
            if (p.codice_regione, p.codice_asl, p.codice_specializzazione, p.codice_struttura) == POSIZIONE_DEFAULT
        )
        prescrittore = {k: v for k, v in dataclasses.asdict(pos.prescrittore()).items() if v is not None}
        oggi = ora_italiana()
        contesto.update(
            prescrittore=prescrittore,
            prescrittore_con_sostituto=prescrittore | {"codice_fiscale_sostituto": sostituto.cf},
            assistito_cf=kit_mef.assistiti_test(args.kit)["ABRUZZO"][1],
            medico_cf=pos.codice_fiscale,
            sostituto_cf=sostituto.cf,
            regione=pos.codice_regione,
            oggi_inizio=oggi.strftime("%Y-%m-%d 00:00:00"),
            oggi_fine=oggi.strftime("%Y-%m-%d 23:59:59"),
        )
        registratore = None
        if args.registra and args.registra_dati_personali_in_chiaro:
            registratore = RegistratoreFile(args.registra, registra_dati_personali_in_chiaro=True)
        elif args.registra:
            registratore = RegistratoreFile(args.registra, identita_di_test=kit_mef.identita_di_test(args.kit))
        trasporto = TrasportoHTTP(registratore=registratore, intervallo_minimo_s=1.0)
        adattatore = carica(args.adattatore) if args.adattatore else adattatore_sac(trasporto, args.base_url)

    validatore = generatore = None
    if args.famiglia in ("fse", "tutte"):
        validatore = validatore_fse_predefinito(args.validatore_fse)
        if validatore is not None:
            print(f"Validatore FSE: {validatore.nome}")
        generatore = carica(args.adattatore_fse) if args.adattatore_fse else None

    motore = Motore(adattatore, credenziali, contesto, credenziali_sostituto=sostituto,
                    validatore_fse=validatore, generatore_pss=generatore, cartella=cartella,
                    xsd_sist=Path(args.xsd_sist) if args.xsd_sist else None,
                    xsd_fvg=Path(args.xsd_fvg) if args.xsd_fvg else None,
                    xsd_a2f=Path(args.xsd_a2f) if args.xsd_a2f else None,
                    openapi_umbria=Path(args.openapi_umbria) if args.openapi_umbria else None)
    risultati = []
    for caso in casi:
        r = motore.esegui(caso)
        risultati.append(r)
        print(f"{r.stato:9} {r.id:12} {r.titolo}")
        if r.motivo:
            print(f"          motivo: {r.motivo}")
        for p in r.passi:
            for d in p.dettagli:
                print(f"          [{p.operazione}] {d}")

    rap = rapporto_json(risultati)
    print(
        f"\nTotale {rap['totale']}: superati {rap['superati']}, falliti {rap['falliti']}, "
        f"errori {rap['errori']}, saltati {rap['saltati']}"
    )
    if args.rapporto:
        Path(args.rapporto).write_text(json.dumps(rap, ensure_ascii=False, indent=2), encoding="utf-8")
    if rap["falliti"] or rap["errori"]:
        return 1
    if rap["superati"] == 0:
        print("ERRORE: nessun caso giudicato (tutti SALTATO): non è stato verificato niente.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

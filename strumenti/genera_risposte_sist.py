#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
"""Scrive le risposte SINTETICHE del SIST in conformita/risposte/sist/.

Perché sintetiche: le Specifiche di integrazione SIST v4.03.27 pubblicano esempi di richiesta
(chkPrescrizione, CDA) ma nessuna risposta del componente CVP. Queste buste le costruiamo noi
sullo schema ufficiale (`wsdl-pddasl/CVPService.xsd`) e sulla javadoc; i test controllano che
il Body di ognuna validi contro quello schema. NON sono risposte registrate dal SIST: quando
ci sarà un collaudo vero, andranno affiancate (non sostituite) da quelle reali.

Identità: solo quelle pubbliche di test del kit MEF (PROVAX00X00X000Y, PNIMRA70A01H501P).
Uso: python strumenti/genera_risposte_sist.py
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RADICE / "src"))
sys.path.insert(0, str(RADICE / "strumenti"))

from sist_server_finto import _x, anomalie, fault, risposta  # noqa: E402

DEST = RADICE / "conformita" / "risposte" / "sist"
ORA = _dt.datetime(2026, 10, 1, 9, 0, 0, tzinfo=_dt.timezone.utc)
NRE = "1600A0000000001"
CODICE = "011020261100000000000000000001"
CF_ASSISTITO = "PNIMRA70A01H501P"
COMUNICAZIONI = (
    "<SANITA:elencoComunicazioni>"
    "<SANITA:comunicazione><SANITA:codice>0199</SANITA:codice><SANITA:messaggio>COGNOME_MEDICO=PRO</SANITA:messaggio></SANITA:comunicazione>"
    "<SANITA:comunicazione><SANITA:codice>0198</SANITA:codice><SANITA:messaggio>NOME_MEDICO=VA</SANITA:messaggio></SANITA:comunicazione>"
    "</SANITA:elencoComunicazioni>"
)


def cda_sintetico() -> str:
    from varco.ricetta import Assistito, Prescrittore, Ricetta, Riga, TipoPrescrizione
    from varco.ricetta.cda_sist import genera_xml

    r = Ricetta(
        prescrittore=Prescrittore("PROVAX00X00X000Y", "160", "114", "F"),
        assistito=Assistito(codice_fiscale=CF_ASSISTITO, provincia="BA", asl="114", codice_regione="160"),
        tipo=TipoPrescrizione.FARMACEUTICA,
        righe=(Riga(2, codice="000000017", descrizione="FARMACO DI PROVA 10 COMPRESSE (dato sintetico)",
                    codice_gruppo_equivalenza="ZZZ", descrizione_gruppo_equivalenza="GRUPPO DI PROVA (dato sintetico)"),),
        data_compilazione=_dt.datetime(2026, 10, 1, 11, 0, 0),
    )
    return genera_xml(r, NRE, CODICE, codice_regionale_prescrittore="000001",
                      creato=_dt.datetime(2026, 10, 1, 11, 0, 5)).decode("utf-8")


def risposte() -> dict[str, bytes]:
    b = {}
    b["chk_ok.xml"] = risposta("chkPrescrizione", _x("IUP", NRE) + _x("codAutenticazione", CODICE) + COMUNICAZIONI)
    b["chk_ok_avviso_0023.xml"] = risposta(
        "chkPrescrizione",
        anomalie([("0023", "Esenzione non valida alla data di prescrizione", "W")]) + _x("IUP", NRE) + _x("codAutenticazione", CODICE),
    )
    b["chk_rifiuto_0007_0051.xml"] = risposta(
        "chkPrescrizione",
        anomalie([("0007", "Esenzione non specificata", "C"),
                  ("0051", "Nota AIFA non specificata per farmaco di fascia \"C\"", "W")]),
    )
    b["chk_solo_iup.xml"] = risposta("chkPrescrizione", _x("IUP", NRE))
    b["chk_rifiuto_0184_sac.xml"] = risposta(
        "chkPrescrizione",
        anomalie([("0184", "Elenco delle anomalie bloccanti restituite dai servizi telematici MEF. - ( - 1233 - "
                           "codice prodotto non valido - Progressivo:1", "C")]),
    )
    b["registra_ok.xml"] = risposta("setRegistraPrescrizione", _x("esito", "TRUE"))
    b["registra_esito_false.xml"] = risposta("setRegistraPrescrizione", _x("esito", "FALSE"))
    b["annulla_ok.xml"] = risposta("setAnnullaPrescrizione", _x("esito", "TRUE"))
    b["annulla_esito_false.xml"] = risposta(
        "setAnnullaPrescrizione",
        _x("esito", "FALSE") + anomalie([("0184", "Elenco delle anomalie bloccanti restituite dai servizi telematici MEF.", "C")]),
    )
    b["fault_000004.xml"] = fault("000004", "Prescrizione non identificata")
    b["fault_000279.xml"] = fault("000279", "Non è possibile annullare la prescrizione perché non è in stato di Prescritta.")
    b["fault_000231.xml"] = fault("000231", "Il codice fiscale della smart card non corrisponde a quello dell'operatore.")
    b["identificata_cda.xml"] = risposta(
        "getPrescrizioneIdentificata",
        _x("cdaInstance", cda_sintetico())
        + f"<SANITA:prescrizione>{_x('IUP', NRE)}{_x('statoPrescrizione', '1')}{_x('tipoPrescrizione', '1')}</SANITA:prescrizione>"
        + _x("statoRicetta", "3") + _x("oscurato", "false"),
    )
    b["identificata_oscurata.xml"] = risposta(  # xs:boolean: "1" vale vero
        "getPrescrizioneIdentificata",
        f"<SANITA:prescrizione>{_x('IUP', NRE)}{_x('statoPrescrizione', '1')}{_x('tipoPrescrizione', '1')}"
        f"{_x('codAutenticazioneMedico', CODICE)}</SANITA:prescrizione>"
        + _x("statoRicetta", "3") + _x("oscurato", "1"),
    )
    b["identificata_atomici.xml"] = risposta(
        "getPrescrizioneIdentificata",
        "<SANITA:prescrizione>"
        + f"<SANITA:assistito>{_x('codFiscale', CF_ASSISTITO)}</SANITA:assistito>"
        + _x("codRicetta", "0300A0000000002")
        + "<SANITA:datiFarm>" + _x("codEsenzione", "NES00")
        + "<SANITA:farmaci><SANITA:farmaco>" + _x("codice", "000000017") + _x("descrizione", "FARMACO DI PROVA (dato sintetico)")
        + _x("quantita", "1") + "</SANITA:farmaco></SANITA:farmaci></SANITA:datiFarm>"
        + _x("statoPrescrizione", "1") + _x("tipoPrescrizione", "1") + _x("codAutenticazioneMedico", CODICE)
        + "</SANITA:prescrizione>" + _x("statoRicetta", "3"),
    )
    voci = "".join(
        f"<SANITA:prescrizione>{_x('codAssistito', CF_ASSISTITO)}{_x('codRicetta', n)}{_x('dataEmissione', '01/10/2026')}"
        f"{_x('IUP', n)}</SANITA:prescrizione>"
        for n in (NRE, "1600A0000000002")
    )
    b["ricerca_due.xml"] = risposta("getPrescrizioniIdentificate", f"<SANITA:elenco>{voci}</SANITA:elenco>")
    b["ricerca_vuota.xml"] = risposta("getPrescrizioniIdentificate", "<SANITA:elenco/>")
    return b


def main() -> int:
    import sist_server_finto

    originale = sist_server_finto._dt
    try:
        class _Orologio:  # ora fissa nell'header, così i file non cambiano a ogni rigenerazione
            class datetime(originale.datetime):
                @classmethod
                def now(cls, tz=None):
                    return ORA

            timedelta = originale.timedelta
            timezone = originale.timezone

        sist_server_finto._dt = _Orologio
        DEST.mkdir(parents=True, exist_ok=True)
        for nome, dati in risposte().items():
            (DEST / nome).write_bytes(dati)
            print(f"scritto {DEST.relative_to(RADICE) / nome}")
    finally:
        sist_server_finto._dt = originale
    return 0


if __name__ == "__main__":
    sys.exit(main())

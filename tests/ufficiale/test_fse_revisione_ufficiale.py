# SPDX-License-Identifier: EUPL-1.2
"""Revisione esterna FSE (2026-10-02): le correzioni verificate col codice UFFICIALE.

- validatore it-fse-gtw-validator (strumenti/validatore-ufficiale/valida.sh): i documenti che il kit
  genera ora sono OK; le alternative scartate (nullFlavor sulla via, birthTime UNK, versione 2 senza
  setId distinto) sono respinte, ed è per questo che il kit le rifiuta prima di generarle;
- dispatcher it-fse-gtw-dispatcher (estrai_cda.sh): estrae lo STESSO CDA che rilegge il kit.

Nessuna rete. Si attivano da soli se il banco è pronto (prepara.sh e JAVA_HOME su un JDK 21).
"""

import dataclasses
import datetime as dt
import hashlib
import json
import subprocess

import pytest

from varco.fse import cda_pss, esempi, pdf
from varco.fse.modello import Allergia, Esenzione, Stato
from varco.fse.validazione import ValidatoreUfficiale, cartella_validatore_ufficiale

pytestmark = pytest.mark.ufficiale


def _valida(tmp_path, documenti: dict[str, bytes]):
    percorsi = []
    for nome, xml in documenti.items():
        p = tmp_path / f"{nome}.xml"
        p.write_bytes(xml)
        percorsi.append(p)
    return dict(zip(documenti, ValidatoreUfficiale().valida_file(percorsi)))


def _versione_2():
    return dataclasses.replace(esempi.pss_completo("PSS-TEST-0001-V2"), versione=2, id_set="PSS-TEST-0001",
                               id_documento_precedente="PSS-TEST-0001")


def test_correzioni_validate_dal_validatore_ufficiale(tmp_path):
    completo = esempi.pss_completo()
    senza_tipo = dataclasses.replace(completo, allergie=(
        Allergia("Amoxicillina", "J01CA04", "ATC", inizio=dt.date(2015, 3, 1), stato=Stato.ATTIVO, note="nota di test"),))
    # le alternative NON conformi, generate apposta (valida=False o ritocco del testo) come gruppo di controllo
    via_null = cda_pss.genera_xml(completo).replace(
        b'<routeCode code="PO" codeSystem="2.16.840.1.113883.5.112" codeSystemName="HL7 RouteOfAdministration" />',
        b'<routeCode nullFlavor="UNK" />')
    assert b'routeCode nullFlavor="UNK"' in via_null
    nascita_unk = dataclasses.replace(completo, paziente=dataclasses.replace(completo.paziente, data_nascita=None))
    v2_vecchio_modo = dataclasses.replace(completo, versione=2)

    esiti = _valida(tmp_path, {
        "completo": cda_pss.genera_xml(completo),
        "assenze": cda_pss.genera_xml(esempi.pss_assenze()),
        "allergia_senza_tipo": cda_pss.genera_xml(senza_tipo),
        "versione_2": cda_pss.genera_xml(_versione_2()),
        "ko_via_nullflavor": via_null,
        "ko_nascita_unk": cda_pss.genera_xml(nascita_unk, valida=False),
        "ko_versione_2_senza_setid": cda_pss.genera_xml(v2_vecchio_modo, valida=False),
    })
    for nome in ("completo", "assenze", "allergia_senza_tipo", "versione_2"):
        assert esiti[nome].esito == "OK", (nome, esiti[nome].errori)
        assert esiti[nome].vocabolario_verificato
    assert esiti["ko_via_nullflavor"].esito == "SEMANTIC_ERROR"
    assert any("ERRORE-b112" in e for e in esiti["ko_via_nullflavor"].errori)
    assert esiti["ko_nascita_unk"].esito == "SEMANTIC_ERROR"
    assert any("ERRORE-17" in e for e in esiti["ko_nascita_unk"].errori)
    assert esiti["ko_versione_2_senza_setid"].esito == "SEMANTIC_ERROR"
    errori_v2 = " ".join(esiti["ko_versione_2_senza_setid"].errori)
    assert "ERRORE-8" in errori_v2 and "ERRORE-9" in errori_v2


def test_codici_non_controllati_dichiarati_nell_esito_pubblico(tmp_path):
    """Un codice di esenzione inventato passa il validatore ufficiale (limite del gateway): l'esito
    pubblico deve dirlo."""
    completo = esempi.pss_completo()
    inventato = dataclasses.replace(completo, esenzioni=(Esenzione("INVENTATO", inizio=dt.date(2020, 2, 1),
                                                                   stato=Stato.ATTIVO),))
    esiti = _valida(tmp_path, {"completo": cda_pss.genera_xml(completo), "esenzione_inventata": cda_pss.genera_xml(inventato)})
    for e in esiti.values():
        pubblico = e.a_dict()
        assert pubblico["esito"] == "OK"
        assert "2.16.840.1.113883.2.9.6.1.22" in pubblico["sistemi_non_verificati"]
        assert any("VOCABOLARIO NON VERIFICATO" in a and "esenzioni" in a for a in pubblico["avvisi"])


def _estrai_dispatcher(percorsi) -> list[dict]:
    r = subprocess.run([str(cartella_validatore_ufficiale() / "estrai_cda.sh"), *map(str, percorsi)],
                       capture_output=True, text=True, timeout=600)
    righe = [json.loads(x[len("RISULTATO "):]) for x in r.stdout.splitlines() if x.startswith("RISULTATO ")]
    assert len(righe) == len(percorsi), r.stderr[-2000:]
    return righe


def test_sostituzione_del_cda_kit_e_dispatcher_estraggono_lo_stesso(tmp_path):
    """Controesempio 2 ripetuto con due CDA completi: prima il kit leggeva il vecchio, il dispatcher il nuovo."""
    pytest.importorskip("pyhanko")
    vecchio = cda_pss.genera_xml(esempi.pss_completo())
    nuovo = cda_pss.genera_xml(_versione_2())
    # testo visibile già della v2 con l'allegato vecchio (giro 2, N4: prima il testo era della v1, e il
    # PDF risultante mostrava «versione 1» con dentro il CDA v2; ora inietta_cda lo rifiuta)
    originale = pdf.pdf_con_cda(pdf.righe_leggibili_pss(_versione_2()), vecchio)
    with pytest.raises(pdf.CdaGiaPresente):
        pdf.inietta_cda(originale, nuovo)
    sostituito = pdf.inietta_cda(originale, nuovo, sostituisci=True)
    p = tmp_path / "sostituito.pdf"
    p.write_bytes(sostituito)

    (d,) = _estrai_dispatcher([p])
    kit = pdf.estrai_cda(sostituito)
    assert d["trovato"] and kit == nuovo
    assert d["sha256"] == hashlib.sha256(kit).hexdigest() == hashlib.sha256(nuovo).hexdigest()
    # e il CDA estratto dal dispatcher è un documento valido per il gateway
    assert ValidatoreUfficiale().valida(kit).esito == "OK"


def test_pdf_con_due_cda_il_kit_non_sceglie(tmp_path):
    """Gruppo di controllo: su un PDF con due cda.xml (prodotto dal vecchio inietta_cda) il dispatcher ne
    prende uno; il kit rifiuta invece di verificarne forse un altro."""
    pytest.importorskip("pyhanko")
    import io

    from pyhanko.pdf_utils import embed
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    def _pdf_con_due_cda(primo: bytes, secondo: bytes) -> bytes:  # come faceva il vecchio inietta_cda
        w = IncrementalPdfFileWriter(io.BytesIO(pdf.pdf_con_cda(["test"], primo)))
        ef = embed.EmbeddedFileObject.from_file_data(w, data=secondo, mime_type="text/xml")
        embed.embed_file(w, embed.FileSpec(file_spec_string="cda.xml", file_name="cda.xml", embedded_data=ef))
        out = io.BytesIO()
        w.write(out)
        return out.getvalue()

    vecchio = cda_pss.genera_xml(esempi.pss_completo())
    nuovo = cda_pss.genera_xml(_versione_2())
    p = tmp_path / "due.pdf"
    p.write_bytes(_pdf_con_due_cda(vecchio, nuovo))
    (d,) = _estrai_dispatcher([p])
    assert d["trovato"] and d["sha256"] == hashlib.sha256(nuovo).hexdigest()  # il dispatcher: l'ultimo
    with pytest.raises(pdf.AllegatiCdaAmbigui):
        pdf.estrai_cda(p.read_bytes())

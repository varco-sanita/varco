#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
"""Prove FSE v2 COERENTI: copia di prove/20261002-fse-v2/genera_prove_fse_v2.py con la correzione N4
del giro 2 di revisione (kit-mmg-review/2026-10-02-giro2/2-fse.md).

N4: il generatore vecchio sostituiva l'allegato del PDF v1 con il CDA v2 e firmava il risultato: il PDF
firmato mostrava «versione 1» con dentro il CDA v2. Qui il PDF della versione 2 ha il testo leggibile
della versione 2 (righe_leggibili_pss dello stesso PSS v2 da cui viene il CDA v2); la sostituzione
dell'allegato si prova solo su un PDF il cui testo è già v2 (allegato vecchio del gestionale), e la
vecchia sequenza (testo v1 + CDA v2) è registrata in rifiuti.json: ora inietta_cda la rifiuta.

Uso (dalla radice del progetto):
    JAVA_HOME=/percorso/jdk-21 .venv/bin/python prove/20261002-fse-v2-coerente/genera_prove_fse_v2_coerente.py

Scrive SOLO in questa cartella. Nessuna rete: validatore e dispatcher del gateway girano in locale
(strumenti/validatore-ufficiale). Dati SINTETICI (kit_mmg.fse.esempi). Le prove precedenti
(prove/20260930-172323-fse/, prove/20261002-fse-v2/) non si toccano.

Contenuto:
  - documenti OK e KO (gruppi di controllo), esiti.json con l'esito del validatore locale e di quello
    ufficiale (esito pubblico a_dict() + risultato grezzo);
  - rifiuti.json: i dati che il kit ora rifiuta PRIMA di generare, con il messaggio;
  - PDF: completo v1 (e firmato di TEST), PDF del gestionale con CDA iniettato, PDF v2 con testo v2 e
    CDA v2 (e firmato di TEST), PDF del gestionale con testo v2 e allegato v1 a cui si SOSTITUISCE il
    CDA con il v2 (e firmato di TEST), PDF "pieno" (ogni campo valorizzato) e il testo estratto;
  - verifica_pdf.json: estrazione col codice del dispatcher ufficiale vs estrazione del kit, e per ogni
    PDF il confronto tra id/versione scritti nel testo visibile e id/versione del CDA estratto.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import io
import json
import subprocess
import sys
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

QUI = Path(__file__).resolve().parent
RADICE = QUI.parent.parent
sys.path.insert(0, str(RADICE / "src"))
sys.path.insert(0, str(RADICE / "tests" / "unit"))

from kit_mmg.fse import cda_pss, esempi, pdf  # noqa: E402
from kit_mmg.fse.json import pss_a_dict  # noqa: E402
from kit_mmg.fse.modello import Allergia, DocumentoNonValido, Esenzione, Problema, Stato, Terapia  # noqa: E402
from kit_mmg.fse.validazione import ValidatoreLocale, ValidatoreUfficiale, validatore_ufficiale_pronto  # noqa: E402
from test_fse_revisione_esterna import pss_pieno  # noqa: E402

VU = RADICE / "strumenti" / "validatore-ufficiale"
H = {"h": "urn:hl7-org:v3"}


def documenti():
    completo = esempi.pss_completo()
    assenze = esempi.pss_assenze()
    v2 = dataclasses.replace(esempi.pss_completo("PSS-TEST-0001-V2"), versione=2, id_set="PSS-TEST-0001",
                             id_documento_precedente="PSS-TEST-0001")
    senza_tipo = dataclasses.replace(completo, id_documento="PSS-TEST-0008", allergie=(
        Allergia("Amoxicillina", "J01CA04", "ATC", inizio=dt.date(2015, 3, 1), stato=Stato.ATTIVO,
                 note="tipo non indicato dal medico"),))
    ko_dallergy = dataclasses.replace(completo, id_documento="PSS-TEST-0003", allergie=(
        Allergia("Amoxicillina", "J01CA04", "ATC", "DALLERGY", inizio=dt.date(2015, 3, 1), stato=Stato.ATTIVO),))
    ko_icd = dataclasses.replace(completo, id_documento="PSS-TEST-0004", problemi=(
        Problema("000.00", "Codice inesistente (dato di test)", inizio=dt.date(2020, 1, 10), stato=Stato.ATTIVO),))
    esenzione_inventata = dataclasses.replace(completo, id_documento="PSS-TEST-0013", esenzioni=(
        Esenzione("INVENTATO", inizio=dt.date(2020, 2, 1), stato=Stato.ATTIVO),))
    via_null = cda_pss.genera_xml(dataclasses.replace(completo, id_documento="PSS-TEST-0010")).replace(
        b'<routeCode code="PO" codeSystem="2.16.840.1.113883.5.112" codeSystemName="HL7 RouteOfAdministration" />',
        b'<routeCode nullFlavor="UNK" />')
    assert b'routeCode nullFlavor="UNK"' in via_null
    nascita_unk = dataclasses.replace(completo, id_documento="PSS-TEST-0011",
                                      paziente=dataclasses.replace(completo.paziente, data_nascita=None))
    v2_vecchio = dataclasses.replace(completo, id_documento="PSS-TEST-0012", versione=2)
    return [
        ("pss_01_completo.xml", cda_pss.genera_xml(completo), "OK atteso: PSS completo (via e stato espliciti)", completo),
        ("pss_02_assenze.xml", cda_pss.genera_xml(assenze), "OK atteso: codici di assenza, inizio assenza UNK", assenze),
        ("pss_03_ko_vocabolario_tipo_allergia.xml", cda_pss.genera_xml(ko_dallergy, valida=False),
         "KO atteso: DALLERGY non nel dizionario del gateway", None),
        ("pss_04_ko_vocabolario_icd9.xml", cda_pss.genera_xml(ko_icd), "KO atteso: ICD-9-CM 000.00 inesistente", ko_icd),
        ("pss_08_allergia_senza_tipo.xml", cda_pss.genera_xml(senza_tipo),
         "OK atteso: tipo di allergia non indicato -> value non codificato nullFlavor UNK (ERRORE-b80)", senza_tipo),
        ("pss_09_versione_2.xml", cda_pss.genera_xml(v2),
         "OK atteso: versione 2, setId distinto da id, relatedDocument RPLC", v2),
        ("pss_10_ko_via_nullflavor.xml", via_null,
         "KO atteso (controllo): routeCode nullFlavor=UNK, alternativa scartata per la via mancante", None),
        ("pss_11_ko_nascita_unk.xml", cda_pss.genera_xml(nascita_unk, valida=False),
         "KO atteso (controllo): birthTime nullFlavor=UNK, ERRORE-17", None),
        ("pss_12_ko_versione_2_senza_setid.xml", cda_pss.genera_xml(v2_vecchio, valida=False),
         "KO atteso (controllo): versione 2 come la generava il kit prima (ERRORE-8, ERRORE-9)", None),
        ("pss_13_esenzione_inventata.xml", cda_pss.genera_xml(esenzione_inventata),
         "OK del gateway con codice esenzione INVENTATO: l'esito pubblico deve avvisare", esenzione_inventata),
    ]


def rifiuti() -> list[dict]:
    completo = esempi.pss_completo()
    casi = {
        "terapia senza via (prima: routeCode PO inventato)": dataclasses.replace(
            completo, terapie=(Terapia("Farmaco TEST", codice_atc="B01AB05", stato=Stato.ATTIVO),)),
        "terapia senza stato (prima: active inventato)": dataclasses.replace(
            completo, terapie=(Terapia("Farmaco TEST", codice_atc="B01AB05", via="PO"),)),
        "data di nascita sconosciuta (prima: birthTime UNK, SEMANTIC_ERROR)": dataclasses.replace(
            completo, paziente=dataclasses.replace(completo.paziente, data_nascita=None)),
        "versione 2 senza id_set e id_documento_precedente (prima: SEMANTIC_ERROR)": dataclasses.replace(
            completo, versione=2),
    }
    out = []
    for nome, pss in casi.items():
        try:
            cda_pss.genera_xml(pss)
            out.append({"caso": nome, "rifiutato": False})
        except DocumentoNonValido as e:
            out.append({"caso": nome, "rifiutato": True, "problemi": e.problemi})
    vecchio = pdf.pdf_con_cda(["test"], b"<vecchio/>")
    try:
        pdf.inietta_cda(vecchio, b"<nuovo/>")
        out.append({"caso": "iniezione su PDF che ha già cda.xml", "rifiutato": False})
    except pdf.CdaGiaPresente as e:
        out.append({"caso": "iniezione su PDF che ha già cda.xml", "rifiutato": True, "problemi": [str(e)]})
    # N4 (giro 2): la sequenza del generatore vecchio, testo v1 + CDA v2 sostituito
    v2 = documenti()[5][3]
    caso = "sostituzione del CDA v1 con il v2 sotto un testo visibile v1 (generatore di 20261002-fse-v2)"
    pdf_v1 = pdf.pdf_con_cda(pdf.righe_leggibili_pss(completo), cda_pss.genera_xml(completo))
    try:
        pdf.inietta_cda(pdf_v1, cda_pss.genera_xml(v2), sostituisci=True)
        out.append({"caso": caso, "rifiutato": False})
    except pdf.TestoPdfIncoerente as e:
        out.append({"caso": caso, "rifiutato": True, "problemi": [str(e)]})
    return out


def testo_pdf(dati: bytes) -> str:
    import pypdf

    return "\n".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(dati)).pages)


_INTESTAZIONE = re.compile(r"Documento\s+(\S+)\s+/\s+(\S+)\s+-\s+versione\s+(\d+)")


def coerenza_testo(dati: bytes, cda: bytes | None) -> dict:
    """Id e versione scritti nel testo visibile del PDF contro quelli del CDA estratto."""
    m = _INTESTAZIONE.search(testo_pdf(dati))
    visibile = list(m.groups()) if m else None
    if cda is None:
        return {"testo_visibile": visibile, "cda": None, "coerente": None}
    cd = ET.fromstring(cda)
    i = cd.find("h:id", H)
    nel_cda = [i.get("root"), i.get("extension"), cd.find("h:versionNumber", H).get("value")]
    return {"testo_visibile": visibile, "cda": nel_cda, "coerente": visibile == nel_cda}


def main() -> int:
    if not validatore_ufficiale_pronto():
        print("Validatore ufficiale non pronto: strumenti/validatore-ufficiale/prepara.sh e JAVA_HOME")
        return 2
    adesso = dt.datetime.now().astimezone()
    docs = documenti()
    for nome, xml, _, pss in docs:
        (QUI / nome).write_bytes(xml)
        if pss is not None:
            (QUI / nome.replace(".xml", ".dati.json")).write_text(
                json.dumps(pss_a_dict(pss), ensure_ascii=False, indent=2), encoding="utf-8")
    locale, ufficiale = ValidatoreLocale(), ValidatoreUfficiale()
    esiti = []
    for (nome, xml, descr, _), eu in zip(docs, ufficiale.valida_file([QUI / d[0] for d in docs])):
        el = locale.valida(xml)
        esiti.append({"documento": nome, "descrizione": descr, "sha256": hashlib.sha256(xml).hexdigest(),
                      "locale": el.a_dict(), "ufficiale": eu.a_dict() | {"grezzo": eu.dettagli}})
        print(f"{nome:42} locale={el.esito:15} ufficiale={eu.esito:15} avvisi={len(eu.avvisi)}")
    (QUI / "esiti.json").write_text(json.dumps({"generato": adesso.isoformat(timespec="seconds"), "esiti": esiti},
                                               ensure_ascii=False, indent=2), encoding="utf-8")
    r = rifiuti()
    (QUI / "rifiuti.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    for x in r:
        print(f"rifiuto: {x['caso']:75} rifiutato={x['rifiutato']}")

    # ------------------------------------------------ PDF
    completo = esempi.pss_completo()
    v2 = docs[5][3]
    assert docs[5][0] == "pss_09_versione_2.xml" and v2.versione == 2
    cda_v1 = (QUI / "pss_01_completo.xml").read_bytes()
    cda_v2 = (QUI / "pss_09_versione_2.xml").read_bytes()
    pdf_v1 = pdf.pdf_con_cda(pdf.righe_leggibili_pss(completo), cda_v1, titolo="Profilo Sanitario Sintetico (TEST)")
    (QUI / "pss_01_completo.pdf").write_bytes(pdf_v1)
    gestionale = pdf.pdf_con_cda(pdf.righe_leggibili_pss(completo), None, titolo="PDF del gestionale (TEST)")
    (QUI / "pdf_gestionale_senza_cda.pdf").write_bytes(gestionale)
    iniettato = pdf.inietta_cda(gestionale, cda_v1)
    (QUI / "pdf_gestionale_con_cda_iniettato.pdf").write_bytes(iniettato)
    # N4: versione 2 = testo v2 + CDA v2, dallo stesso PSS
    pdf_v2 = pdf.pdf_con_cda(pdf.righe_leggibili_pss(v2), cda_v2, titolo="Profilo Sanitario Sintetico v2 (TEST)")
    (QUI / "pss_09_v2.pdf").write_bytes(pdf_v2)
    # sostituzione dell'allegato solo dove il testo è già v2 (gestionale con allegato rimasto alla v1)
    v2_allegato_v1 = pdf.pdf_con_cda(pdf.righe_leggibili_pss(v2), cda_v1, titolo="PDF del gestionale v2 con allegato v1 (TEST)")
    (QUI / "pdf_gestionale_v2_con_allegato_v1.pdf").write_bytes(v2_allegato_v1)
    sostituito = pdf.inietta_cda(v2_allegato_v1, cda_v2, sostituisci=True)
    (QUI / "pss_09_v2_sostituito.pdf").write_bytes(sostituito)
    pieno = pss_pieno()
    cda_pieno = cda_pss.genera_xml(pieno)
    (QUI / "pss_pieno.xml").write_bytes(cda_pieno)
    pdf_pieno = pdf.pdf_con_cda(pdf.righe_leggibili_pss(pieno), cda_pieno, titolo="PSS pieno (TEST)")
    (QUI / "pss_pieno.pdf").write_bytes(pdf_pieno)
    (QUI / "pss_pieno.testo_pdf.txt").write_text(testo_pdf(pdf_pieno), encoding="utf-8")
    (QUI / "pss_01_completo.testo_pdf.txt").write_text(testo_pdf(pdf_v1), encoding="utf-8")
    with tempfile.TemporaryDirectory() as d:
        p12 = Path(d) / "test.p12"
        pdf.crea_certificato_di_test(str(p12), b"test")
        firmatario = pdf.FirmatarioPKCS12(str(p12), b"test", motivo="Firma di TEST con certificato autofirmato: nessun valore legale")
        (QUI / "pss_01_completo_firmato_TEST.pdf").write_bytes(firmatario.firma_pades(pdf_v1))
        (QUI / "pss_09_v2_firmato_TEST.pdf").write_bytes(firmatario.firma_pades(pdf_v2))
        (QUI / "pss_09_v2_sostituito_firmato_TEST.pdf").write_bytes(firmatario.firma_pades(sostituito))
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import pkcs12

        _, cert, _ = pkcs12.load_key_and_certificates(p12.read_bytes(), b"test")
        (QUI / "certificato_TEST_autofirmato.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    for nome in ("pss_09_v2_firmato_TEST.pdf", "pss_09_v2_sostituito_firmato_TEST.pdf"):
        (QUI / nome.replace(".pdf", ".testo_pdf.txt")).write_text(testo_pdf((QUI / nome).read_bytes()), encoding="utf-8")

    attesi = {
        "pss_01_completo.pdf": cda_v1,
        "pdf_gestionale_senza_cda.pdf": None,
        "pdf_gestionale_con_cda_iniettato.pdf": cda_v1,
        "pss_09_v2.pdf": cda_v2,
        "pss_09_v2_sostituito.pdf": cda_v2,
        "pss_pieno.pdf": cda_pieno,
        "pss_01_completo_firmato_TEST.pdf": cda_v1,
        "pss_09_v2_firmato_TEST.pdf": cda_v2,
        "pss_09_v2_sostituito_firmato_TEST.pdf": cda_v2,
    }
    estr = subprocess.run([str(VU / "estrai_cda.sh"), *[str(QUI / p) for p in attesi]],
                          capture_output=True, text=True, timeout=600)
    estrazioni = [json.loads(x[len("RISULTATO "):]) for x in estr.stdout.splitlines() if x.startswith("RISULTATO ")]
    assert len(estrazioni) == len(attesi), estr.stderr[-2000:]
    sys.path.insert(0, str(RADICE / "strumenti"))
    from genera_prove_fse import verifica_firma_pyhanko

    verifica = {"pdf": []}
    for (nome, atteso), e in zip(attesi.items(), estrazioni):
        dati = (QUI / nome).read_bytes()
        kit = pdf.estrai_cda(dati)
        voce = {
            "pdf": nome,
            "estrazione_dispatcher_ufficiale": e,
            "sha256_atteso": hashlib.sha256(atteso).hexdigest() if atteso else None,
            "sha256_kit": hashlib.sha256(kit).hexdigest() if kit else None,
        }
        voce["kit_uguale_dispatcher"] = voce["sha256_kit"] == e.get("sha256")
        voce["dispatcher_uguale_atteso"] = e.get("sha256") == voce["sha256_atteso"]
        voce["testo_visibile_e_cda"] = coerenza_testo(dati, kit)
        if "firmato" in nome:
            voce["firma_pyhanko"] = verifica_firma_pyhanko(dati, QUI / "certificato_TEST_autofirmato.pem")
        verifica["pdf"].append(voce)
        print(f"{nome:42} dispatcher trovato={e.get('trovato')} kit=dispatcher {voce['kit_uguale_dispatcher']} "
              f"atteso {voce['dispatcher_uguale_atteso']} testo=CDA {voce['testo_visibile_e_cda']['coerente']}")
    (QUI / "verifica_pdf.json").write_text(json.dumps(verifica, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nProve salvate in {QUI}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

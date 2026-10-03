#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
"""Prove FSE 2.0, lato documento: genera i PSS, li valida con gli strumenti UFFICIALI, li mette nel PDF e firma.

Uso:
    JAVA_HOME=/percorso/jdk-21 python strumenti/genera_prove_fse.py [--conformita]

Serve aver lanciato una volta strumenti/validatore-ufficiale/prepara.sh.
Nessuna chiamata al gateway Sogei: tutto in locale.

Crea prove/<AAAAMMGG-HHMMSS>-fse/ con:
  - i CDA generati (documenti OK e KO), dati SINTETICI (varco.fse.esempi);
  - esiti.json: esito del validatore locale (XSD + schematron) e di quello ufficiale
    (codice it-fse-gtw-validator: XSD + schematron + vocabolari), documento per documento;
  - PDF con il CDA allegato, firmato PAdES con un certificato AUTOFIRMATO DI TEST
    (solo il certificato pubblico viene salvato, non la chiave);
  - verifica_pdf.json: estrazione del CDA col codice del dispatcher ufficiale, verifica
    della firma con pyHanko e con pdfsig (poppler), rivalidazione del CDA estratto.

Con --conformita copia i documenti in conformita/documenti/ (fixture della suite).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE / "src"))

from varco.fse import cda_pss, esempi, pdf  # noqa: E402
from varco.fse.json import pss_a_dict  # noqa: E402
from varco.fse.modello import Allergia, Problema, Stato  # noqa: E402
from varco.fse.validazione import ValidatoreLocale, ValidatoreUfficiale, validatore_ufficiale_pronto  # noqa: E402

VU = RADICE / "strumenti" / "validatore-ufficiale"


def documenti() -> list[tuple[str, bytes, str, dict | None]]:
    """(nome file, xml, descrizione, dati JSON del modello se generato dal modello)."""
    out = []
    completo = esempi.pss_completo()
    out.append(("pss_01_completo.xml", cda_pss.genera_xml(completo),
                "PSS con voci in tutte le sezioni obbligatorie + esenzione; terapia, problema, esenzione dalla ricetta",
                pss_a_dict(completo)))
    assenze = esempi.pss_assenze()
    out.append(("pss_02_assenze.xml", cda_pss.genera_xml(assenze),
                "PSS senza allergie, terapie, problemi, familiarità note (codici di assenza IPS)", pss_a_dict(assenze)))

    # KO: vocabolario. DALLERGY è HL7 valido ma non è nel dizionario del gateway (errore trovato il 30/09/2026)
    ko_voc = dataclasses.replace(completo, id_documento="PSS-TEST-0003",
                                 allergie=(Allergia("Amoxicillina", "J01CA04", "ATC", "DALLERGY", inizio=dt.date(2015, 3, 1), stato=Stato.ATTIVO),))
    out.append(("pss_03_ko_vocabolario_tipo_allergia.xml", cda_pss.genera_xml(ko_voc, valida=False),
                "KO atteso: tipo allergia DALLERGY non censito nel dizionario del gateway", None))
    # KO: vocabolario. Codice ICD-9-CM inesistente
    ko_icd = dataclasses.replace(completo, id_documento="PSS-TEST-0004",
                                 problemi=(Problema("000.00", "Codice inesistente (dato di test)", inizio=dt.date(2020, 1, 10), stato=Stato.ATTIVO),))
    out.append(("pss_04_ko_vocabolario_icd9.xml", cda_pss.genera_xml(ko_icd),
                "KO atteso: codice ICD-9-CM 000.00 inesistente", pss_a_dict(ko_icd)))

    base = cda_pss.genera_xml(assenze).decode("utf-8")
    # KO: schematron. Manca la sezione allergie (obbligatoria)
    senza = re.sub(r'\s*<component typeCode="COMP">\s*<section ID="ALL">.*?</section>\s*</component>', "", base,
                   count=1, flags=re.S)
    assert senza != base
    out.append(("pss_05_ko_senza_allergie.xml", senza.encode(), "KO atteso: sezione Allergie assente (ERRORE-b1)", None))
    # KO: schematron. CF del paziente di 15 caratteri
    cf = base.replace('extension="PNIMRA70A01H501P"', 'extension="PNIMRA70A01H501"', 1)
    assert cf != base
    out.append(("pss_06_ko_cf_paziente.xml", cf.encode(), "KO atteso: CF paziente di 15 caratteri (ERRORE-52)", None))
    # KO: schema. title prima di code (ordine sbagliato per l'XSD)
    m = re.search(r'(\s*<code code="60591-5"[^>]*/>)(\s*<title>[^<]*</title>)', base)
    xsd = base[:m.start()] + m.group(2) + m.group(1) + base[m.end():]
    out.append(("pss_07_ko_ordine_xsd.xml", xsd.encode(), "KO atteso: title prima di code (schema CDA)", None))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conformita", action="store_true", help="copia i documenti in conformita/documenti/")
    args = ap.parse_args()
    if not validatore_ufficiale_pronto():
        print("Validatore ufficiale non pronto: lanciare strumenti/validatore-ufficiale/prepara.sh e impostare JAVA_HOME")
        return 2

    adesso = dt.datetime.now().astimezone()
    cartella = RADICE / "prove" / (adesso.strftime("%Y%m%d-%H%M%S") + "-fse")
    cartella.mkdir(parents=True)
    docs = documenti()
    for nome, xml, _, dati in docs:
        (cartella / nome).write_bytes(xml)
        if dati is not None:
            (cartella / nome.replace(".xml", ".dati.json")).write_text(json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8")

    locale = ValidatoreLocale()
    ufficiale = ValidatoreUfficiale()
    esiti_uff = ufficiale.valida_file([cartella / d[0] for d in docs])
    esiti = []
    for (nome, xml, descr, _), eu in zip(docs, esiti_uff):
        el = locale.valida(xml)
        voce = {
            "documento": nome,
            "descrizione": descr,
            "sha256": hashlib.sha256(xml).hexdigest(),
            "locale": el.a_dict(),
            "ufficiale": eu.a_dict() | {"grezzo": eu.dettagli},
        }
        esiti.append(voce)
        print(f"{nome:45} locale={el.esito:17} ufficiale={eu.esito}")
    (cartella / "esiti.json").write_text(
        json.dumps({"generato": adesso.isoformat(timespec="seconds"), "esiti": esiti}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    # ------------------------------------------------ PDF + firma di TEST
    completo = esempi.pss_completo()
    cda = (cartella / "pss_01_completo.xml").read_bytes()
    pdf_cda = pdf.pdf_con_cda(pdf.righe_leggibili_pss(completo), cda, titolo="Profilo Sanitario Sintetico (TEST)")
    (cartella / "pss_01_completo.pdf").write_bytes(pdf_cda)
    senza_allegato = pdf.pdf_con_cda(pdf.righe_leggibili_pss(completo), None, titolo="PDF del gestionale (TEST)")
    (cartella / "pdf_gestionale_senza_cda.pdf").write_bytes(senza_allegato)
    iniettato = pdf.inietta_cda(senza_allegato, cda)
    (cartella / "pdf_gestionale_con_cda_iniettato.pdf").write_bytes(iniettato)
    with tempfile.TemporaryDirectory() as d:
        p12 = Path(d) / "test.p12"
        pdf.crea_certificato_di_test(str(p12), b"test")
        firmatario = pdf.FirmatarioPKCS12(str(p12), b"test", motivo="Firma di TEST con certificato autofirmato: nessun valore legale")
        firmato = firmatario.firma_pades(pdf_cda)
        (cartella / "pss_01_completo_firmato_TEST.pdf").write_bytes(firmato)
        firmato_iniettato = firmatario.firma_pades(iniettato)
        (cartella / "pdf_gestionale_con_cda_firmato_TEST.pdf").write_bytes(firmato_iniettato)
        # solo il certificato pubblico
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import pkcs12
        _, cert, _ = pkcs12.load_key_and_certificates(p12.read_bytes(), b"test")
        (cartella / "certificato_TEST_autofirmato.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    pdfs = ["pss_01_completo.pdf", "pdf_gestionale_senza_cda.pdf", "pdf_gestionale_con_cda_iniettato.pdf",
            "pss_01_completo_firmato_TEST.pdf", "pdf_gestionale_con_cda_firmato_TEST.pdf"]
    estr = subprocess.run([str(VU / "estrai_cda.sh"), *[str(cartella / p) for p in pdfs]],
                          capture_output=True, text=True, timeout=600)
    estrazioni = [json.loads(r[len("RISULTATO "):]) for r in estr.stdout.splitlines() if r.startswith("RISULTATO ")]
    atteso = hashlib.sha256(cda).hexdigest()
    verifica = {"sha256_cda_originale": atteso, "pdf": []}
    for nome, e in zip(pdfs, estrazioni):
        voce = {"pdf": nome, "estrazione_dispatcher_ufficiale": e, "cda_identico": e.get("sha256") == atteso}
        dati = (cartella / nome).read_bytes()
        if "firmato" in nome:
            voce["firma_pyhanko"] = verifica_firma_pyhanko(dati, cartella / "certificato_TEST_autofirmato.pem")
            ps = subprocess.run(["pdfsig", str(cartella / nome)], capture_output=True, text=True)
            voce["pdfsig_poppler"] = [r.strip() for r in ps.stdout.splitlines() if r.strip()]
            estratto = pdf.estrai_cda(dati)
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / f"estratto_da_{nome}.xml"
                p.write_bytes(estratto)
                voce["rivalidazione_ufficiale_cda_estratto"] = ufficiale.valida_file([p])[0].esito
        verifica["pdf"].append(voce)
        print(f"{nome:45} dispatcher: trovato={e.get('trovato')} identico={voce['cda_identico']}")
    (cartella / "verifica_pdf.json").write_text(json.dumps(verifica, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.conformita:
        dest = RADICE / "conformita" / "documenti"
        dest.mkdir(parents=True, exist_ok=True)
        for nome, _, _, dati in docs:
            shutil.copy(cartella / nome, dest / nome)
        for nome, _, _, dati in docs:
            if dati is not None:
                shutil.copy(cartella / nome.replace(".xml", ".dati.json"), RADICE / "conformita" / "dati" / nome.replace(".xml", ".json"))
    print(f"\nProve salvate in {cartella}")
    return 0


def verifica_firma_pyhanko(dati: bytes, pem: Path) -> dict:
    import io

    from pyhanko.keys import load_cert_from_pemder
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    r = PdfFileReader(io.BytesIO(dati))
    firme = r.embedded_signatures
    ctx = ValidationContext(trust_roots=[load_cert_from_pemder(str(pem))])
    stato = validate_pdf_signature(firme[0], ctx)
    return {
        "firme": len(firme),
        "integra": stato.intact,
        "crittograficamente_valida": stato.valid,
        "attendibile_con_il_certificato_di_test_come_radice": stato.trusted,
        "copre_tutto_il_documento": stato.coverage.name,
        "subfilter": str(firme[0].sig_object.get("/SubFilter")),
        "firmatario": stato.signing_cert.subject.human_friendly,
    }


if __name__ == "__main__":
    raise SystemExit(main())

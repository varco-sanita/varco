# Prove FSE v2 coerenti (02/10/2026)

Sostituisce, per i PDF della versione 2, le prove di `prove/20261002-fse-v2/`
(`pss_01_con_cda_sostituito_da_v2.pdf` e `pss_01_sostituito_v2_firmato_TEST.pdf`). Motivo: rilievo
**N4** del giro 2 di revisione esterna (`kit-mmg-review/2026-10-02-giro2/2-fse.md`). Quei PDF, firma
compresa, mostrano «versione 1» nel testo leggibile e contengono il CDA della versione 2. La cartella
vecchia resta com'era e non si usa come prova della v2.

Qui:

- `pss_09_v2.pdf` e `pss_09_v2_firmato_TEST.pdf` hanno il testo v2 e il CDA v2 dello stesso PSS;
- `pss_09_v2_sostituito.pdf` e `pss_09_v2_sostituito_firmato_TEST.pdf` sono la sostituzione
  dell'allegato fatta su un PDF del gestionale che ha già il testo v2 e l'allegato v1
  (`pdf_gestionale_v2_con_allegato_v1.pdf`);
- `rifiuti.json` riporta la sequenza vecchia (testo v1 + CDA v2): `inietta_cda` ora la rifiuta
  con `TestoPdfIncoerente`;
- `verifica_pdf.json` contiene, per ogni PDF, l'estrazione del dispatcher ufficiale, quella del kit, la
  firma (certificato autofirmato di TEST, senza valore legale) e il confronto tra id e versione del
  testo visibile e quelli del CDA (`testo_visibile_e_cda`);
- `esiti.json` contiene gli esiti dei validatori, locale e ufficiale, rigenerati.

Rigenerare (scrive solo qui, niente rete):

    JAVA_HOME=/percorso/jdk-21 .venv/bin/python prove/20261002-fse-v2-coerente/genera_prove_fse_v2_coerente.py

Test: `tests/unit/test_revisione_giro2_fse.py::test_g2_n4_prova_coerente_testo_visibile_uguale_al_cda`.
Dati sintetici.

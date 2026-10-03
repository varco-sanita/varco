// SPDX-License-Identifier: EUPL-1.2
//
// Esecutore JAVA dei casi di conformità FSE (conformita/casi/*.json, famiglia "fse").
//
// È la prova che il formato dei casi non dipende dal Python del kit: questo programma
// legge gli stessi file JSON, segue la stessa specifica (conformita/schema/caso.schema.json)
// e usa come implementazione il validatore UFFICIALE del gateway (ValidatoreUfficiale.Banco).
// Esegue i passi "valida_documento"; i passi "genera_pss" li dà SALTATO (qui non c'è
// un generatore di CDA: quello lo porta chi si collauda).
//
// Uso: java -cp ... EseguiCasiFse <cartella-dump> <cartella-conformita> [rapporto.json]

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.stream.Stream;

public class EseguiCasiFse {

    static String normalizza(String t) {
        t = String.join(" ", t.trim().split("\\s+"));
        return t.startsWith("[") && t.endsWith("]") ? t.substring(1, t.length() - 1) : t;
    }

    /** L'oggetto "osservato" della specifica (osservatoFse) dal risultato del validatore ufficiale. */
    static ObjectNode osserva(ObjectNode r) {
        ObjectNode oss = ValidatoreUfficiale.JSON.createObjectNode();
        String esito = r.path("esito").asText();
        oss.put("esito", esito);
        ArrayNode errori = oss.putArray("errori");
        ArrayNode avvisi = oss.putArray("avvisi");
        if (esito.equals("SYNTAX_ERROR") || esito.equals("VOCABULARY_ERROR")) {
            r.path("messaggi").forEach(m -> errori.add(normalizza(m.asText())));
        } else {
            r.path("schematron").path("errori").forEach(e -> errori.add(normalizza(e.path("testo").asText())));
        }
        r.path("schematron").path("avvisi").forEach(e -> avvisi.add(normalizza(e.path("testo").asText())));
        oss.put("vocabolario_verificato", r.has("vocabolario"));
        return oss;
    }

    static boolean esitoAmmesso(JsonNode atteso, String esito) {
        if (atteso.isArray()) {
            for (JsonNode a : atteso) if (a.asText().equals(esito)) return true;
            return false;
        }
        return atteso.asText().equals(esito);
    }

    /** Le chiavi di $defs/attesoFse in conformita/schema/caso.schema.json (le stesse di CHIAVI_ATTESO del Python). */
    static final Set<String> CHIAVI_ATTESO_FSE = Set.of("esito", "valido", "errori_contengono", "senza_errori");

    /** Difetti del caso in sé: lo schema vuole almeno un passo ("passi": minItems 1). Un caso senza passi
     *  non verifica niente e non può essere SUPERATO (revisione esterna giro 2, 6-conformita N1). */
    static List<String> difettiCaso(JsonNode caso) {
        List<String> ko = new ArrayList<>();
        JsonNode passi = caso.path("passi");
        if (!passi.isArray() || passi.isEmpty()) ko.add("caso senza passi: lo schema ne vuole almeno uno (passi, minItems 1)");
        // Ogni passo vuole le sue aspettative, non vuote: senza 'atteso' (o con {}) non si confronta niente
        // e il caso risultava SUPERATO (revisione esterna giro 3, conformità n. 2). Come il motore Python.
        int i = 0;
        for (JsonNode passo : passi) {
            i++;
            JsonNode atteso = passo.path("atteso");
            if (!atteso.isObject() || atteso.isEmpty())
                ko.add("passo " + i + " (" + passo.path("operazione").asText() + "): "
                        + (atteso.isMissingNode() ? "manca 'atteso'" : "'atteso' vuoto o non è un oggetto")
                        + ": un passo senza aspettative non verifica niente");
        }
        return ko;
    }

    /** Aspettative non rispettate (attesoFse). Come dice la descrizione di caso.schema.json, un'aspettativa
     *  sconosciuta o incoerente rende il passo FALLITO, mai ignorata (revisione esterna giro 2, N2). */
    static List<String> verifica(JsonNode atteso, ObjectNode oss) {
        List<String> ko = new ArrayList<>();
        atteso.fieldNames().forEachRemaining(k -> {
            if (!CHIAVI_ATTESO_FSE.contains(k)) ko.add("aspettativa sconosciuta '" + k + "': l'esecutore non sa verificarla");
        });
        if (atteso.has("valido") && atteso.has("esito")) {
            boolean compatibile = false;
            for (String e : List.of("OK", "SEMANTIC_WARNING", "SEMANTIC_ERROR", "SYNTAX_ERROR", "VOCABULARY_ERROR"))
                if (esitoAmmesso(atteso.get("esito"), e)
                        && (e.equals("OK") || e.equals("SEMANTIC_WARNING")) == atteso.get("valido").asBoolean()) compatibile = true;
            if (!compatibile) ko.add("valido=" + atteso.get("valido") + " incompatibile con esito " + atteso.get("esito"));
        }
        if (atteso.path("senza_errori").asBoolean(false) && atteso.has("errori_contengono"))
            ko.add("senza_errori ed errori_contengono insieme");
        String esito = oss.get("esito").asText();
        if (atteso.has("esito") && !esitoAmmesso(atteso.get("esito"), esito))
            ko.add("esito " + esito + " invece di " + atteso.get("esito"));
        if (atteso.has("valido")) {
            boolean valido = esito.equals("OK") || esito.equals("SEMANTIC_WARNING");
            if (valido != atteso.get("valido").asBoolean()) ko.add("esito " + esito + ": validità diversa dall'attesa");
        }
        for (JsonNode f : atteso.path("errori_contengono")) {
            boolean trovato = false;
            for (JsonNode e : oss.get("errori")) if (e.asText().contains(f.asText())) { trovato = true; break; }
            if (!trovato) ko.add("nessun errore contiene '" + f.asText() + "'");
        }
        if (atteso.path("senza_errori").asBoolean(false) && oss.get("errori").size() > 0)
            ko.add("errori presenti: " + oss.get("errori"));
        return ko;
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("uso: EseguiCasiFse <cartella-dump> <cartella-conformita> [rapporto.json]");
            System.exit(2);
        }
        ValidatoreUfficiale.Banco banco = ValidatoreUfficiale.prepara(Path.of(args[0]));
        Path conf = Path.of(args[1]);
        List<Path> file;
        try (Stream<Path> s = Files.list(conf.resolve("casi"))) {
            file = s.filter(p -> p.toString().endsWith(".json")).sorted().toList();
        }
        ObjectNode rapporto = ValidatoreUfficiale.JSON.createObjectNode();
        ArrayNode casiOut = ValidatoreUfficiale.JSON.createArrayNode();
        int superati = 0, falliti = 0, saltati = 0, errori = 0;
        for (Path f : file) {
            JsonNode caso = ValidatoreUfficiale.JSON.readTree(f.toFile());
            if (!"fse".equals(caso.path("famiglia").asText())) continue;
            String stato = "SUPERATO";
            String motivo = null;
            ArrayNode passiOut = ValidatoreUfficiale.JSON.createArrayNode();
            List<String> difetti = difettiCaso(caso);
            if (!difetti.isEmpty()) { stato = "FALLITO"; motivo = String.join("; ", difetti); }
            else try {
                for (JsonNode passo : caso.path("passi")) {
                    String op = passo.path("operazione").asText();
                    ObjectNode po = passiOut.addObject();
                    po.put("operazione", op);
                    if (!op.equals("valida_documento")) {
                        stato = "SALTATO";
                        motivo = "operazione " + op + ": questo esecutore non ha un generatore di CDA";
                        po.put("superato", false);
                        break;
                    }
                    String doc = passo.path("documento").asText();
                    String cda = Files.readString(conf.resolve(doc), StandardCharsets.UTF_8);
                    ObjectNode oss = osserva(banco.valida(doc, cda));
                    List<String> ko = verifica(passo.path("atteso"), oss);
                    po.put("superato", ko.isEmpty());
                    po.set("osservato", oss);
                    ArrayNode dettagli = po.putArray("dettagli");
                    ko.forEach(dettagli::add);
                    if (!ko.isEmpty()) { stato = "FALLITO"; break; }
                }
            } catch (Exception e) {
                stato = "ERRORE";
                motivo = e.toString();
            }
            switch (stato) {
                case "SUPERATO" -> superati++;
                case "FALLITO" -> falliti++;
                case "SALTATO" -> saltati++;
                default -> errori++;
            }
            System.out.printf("%-9s %-8s %s%n", stato, caso.path("id").asText(), caso.path("titolo").asText());
            if (motivo != null) System.out.println("          motivo: " + motivo);
            ObjectNode co = casiOut.addObject();
            co.put("id", caso.path("id").asText());
            co.put("titolo", caso.path("titolo").asText());
            co.put("stato", stato);
            co.put("motivo", motivo);
            co.set("passi", passiOut);
        }
        int totale = superati + falliti + saltati + errori;
        rapporto.put("totale", totale).put("superati", superati).put("falliti", falliti).put("errori", errori).put("saltati", saltati);
        rapporto.put("esecutore", "Java: EseguiCasiFse + codice it-fse-gtw-validator");
        rapporto.set("casi", casiOut);
        System.out.printf("%nTotale %d: superati %d, falliti %d, errori %d, saltati %d%n", totale, superati, falliti, errori, saltati);
        if (args.length > 2) {
            ValidatoreUfficiale.JSON.writerWithDefaultPrettyPrinter().writeValue(Path.of(args[2]).toFile(), rapporto);
        }
        System.exit(falliti == 0 && errori == 0 ? 0 : 1);
    }
}

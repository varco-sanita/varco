// SPDX-License-Identifier: EUPL-1.2
//
// Banco di prova del validatore UFFICIALE del gateway FSE 2.0, senza Docker e senza rete.
//
// Non reimplementa niente: chiama le classi del microservizio
// ministero-salute/it-fse-gtw-validator (compilate dal sorgente pubblico, commit
// fissato in prepara.sh), nello stesso ordine del suo controller ValidationCTL:
//
//   1. CDAHelper.extractInfo            (templateId e typeId del documento)
//   2. ValidationSRV.validateSyntactic  (XSD, con il ResourceResolver ufficiale)
//   3. ValidationSRV.validateSemantic   (schematron: ph-schematron + Saxon, come in produzione)
//   4. ValidationSRV.validateVocabularies -> TerminologySRV (dizionari)
//
// L'unica cosa sostituita è MongoDB: al suo posto ci sono repository in memoria
// che leggono i dump PUBBLICI con cui il gateway riempie il database
// (it-fse-catalogs/mongo-dump: schema, schematron, dictionary, terminology).
// Le query sono riprodotte da repository/mongo/impl/*.java del validatore.
//
// Non fa: la chiamata al dispatcher (JWT, estrazione dal PDF, hash), che sta in un
// altro microservizio, né la scelta della mappa FHIR (getStructureObjectID).
//
// Uso: java -cp ... ValidatoreUfficiale <cartella-dump> <file.xml>...
// Stampa, per ogni file, una riga "RISULTATO {json}" (le altre righe sono log del validatore).

import com.fasterxml.jackson.core.JsonFactory;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.JsonToken;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.helger.schematron.ISchematronResource;
import com.helger.schematron.svrl.jaxb.FailedAssert;
import com.helger.schematron.svrl.jaxb.SchematronOutputType;
import com.helger.schematron.svrl.jaxb.SuccessfulReport;
import it.finanze.sanita.fse2.ms.gtw.validator.cda.CDAHelper;
import it.finanze.sanita.fse2.ms.gtw.validator.dto.CDAValidationDTO;
import it.finanze.sanita.fse2.ms.gtw.validator.dto.ExtractedInfoDTO;
import it.finanze.sanita.fse2.ms.gtw.validator.dto.SchematronFailedAssertionDTO;
import it.finanze.sanita.fse2.ms.gtw.validator.dto.SchematronValidationResultDTO;
import it.finanze.sanita.fse2.ms.gtw.validator.dto.VocabularyResultDTO;
import it.finanze.sanita.fse2.ms.gtw.validator.enums.CDASeverityViolationEnum;
import it.finanze.sanita.fse2.ms.gtw.validator.enums.CDAValidationStatusEnum;
import it.finanze.sanita.fse2.ms.gtw.validator.enums.ErrorLogEnum;
import it.finanze.sanita.fse2.ms.gtw.validator.enums.OperationLogEnum;
import it.finanze.sanita.fse2.ms.gtw.validator.enums.ResultLogEnum;
import it.finanze.sanita.fse2.ms.gtw.validator.enums.SystemTypeEnum;
import it.finanze.sanita.fse2.ms.gtw.validator.enums.WarnLogEnum;
import it.finanze.sanita.fse2.ms.gtw.validator.logging.LoggerHelper;
import it.finanze.sanita.fse2.ms.gtw.validator.repository.entity.DictionaryETY;
import it.finanze.sanita.fse2.ms.gtw.validator.repository.entity.SchemaETY;
import it.finanze.sanita.fse2.ms.gtw.validator.repository.entity.SchematronETY;
import it.finanze.sanita.fse2.ms.gtw.validator.repository.mongo.IDictionaryRepo;
import it.finanze.sanita.fse2.ms.gtw.validator.repository.mongo.ISchemaRepo;
import it.finanze.sanita.fse2.ms.gtw.validator.repository.mongo.ISchematronRepo;
import it.finanze.sanita.fse2.ms.gtw.validator.repository.mongo.ITerminologyRepo;
import it.finanze.sanita.fse2.ms.gtw.validator.service.impl.SchemaSRV;
import it.finanze.sanita.fse2.ms.gtw.validator.service.impl.TerminologySRV;
import it.finanze.sanita.fse2.ms.gtw.validator.service.impl.ValidationSRV;
import it.finanze.sanita.fse2.ms.gtw.validator.singleton.SchematronValidatorSingleton;
import org.bson.types.Binary;

import javax.xml.transform.stream.StreamSource;
import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Comparator;
import java.util.Date;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.zip.GZIPInputStream;

public class ValidatoreUfficiale {

    static final ObjectMapper JSON = new ObjectMapper();

    // ------------------------------------------------------------ dump -> entità
    static JsonNode leggiDump(Path p) throws Exception {
        try (InputStream in = new GZIPInputStream(Files.newInputStream(p))) {
            return JSON.readTree(in);
        }
    }

    static byte[] binario(JsonNode n) {
        JsonNode b = n.get("$binary");
        String b64 = b.isObject() ? b.get("base64").asText() : b.asText();
        return Base64.getDecoder().decode(b64);
    }

    static Date data(JsonNode n) {
        if (n == null || n.isNull()) return null;
        JsonNode d = n.get("$date");
        return d == null ? null : Date.from(Instant.parse(d.asText()));
    }

    static String testo(JsonNode n, String campo) {
        JsonNode v = n.get(campo);
        return v == null || v.isNull() ? null : v.asText();
    }

    // ------------------------------------------------------------ repository in memoria
    static class SchemaRepoMemoria implements ISchemaRepo {
        final List<SchemaETY> righe = new ArrayList<>();

        SchemaRepoMemoria(JsonNode dump) {
            for (JsonNode n : dump) {
                SchemaETY e = new SchemaETY();
                e.setId(n.get("_id").get("$oid").asText());
                e.setNameSchema(testo(n, "name_schema"));
                e.setContentSchema(new Binary(binario(n.get("content_schema"))));
                e.setTypeIdExtension(testo(n, "type_id_extension"));
                e.setRootSchema(n.get("root_schema").asBoolean());
                e.setLastUpdateDate(data(n.get("last_update_date")));
                e.setDeleted(n.get("deleted").asBoolean());
                righe.add(e);
            }
        }

        List<SchemaETY> vivi() { return righe.stream().filter(r -> !r.getDeleted()).collect(Collectors.toList()); }

        public SchemaETY findFatherLastVersionXsd() {
            return vivi().stream().filter(SchemaETY::getRootSchema).max(Comparator.comparing(SchemaETY::getLastUpdateDate)).orElse(null);
        }
        public SchemaETY findFatherXsd(String v) {
            return vivi().stream().filter(r -> r.getRootSchema() && v.equals(r.getTypeIdExtension())).findFirst().orElse(null);
        }
        public List<SchemaETY> findChildrenXsd(String v) {
            return vivi().stream().filter(r -> !r.getRootSchema() && v.equals(r.getTypeIdExtension())).collect(Collectors.toList());
        }
        public SchemaETY findByNameAndVersion(String nome, String v) {
            return vivi().stream().filter(r -> nome.equals(r.getNameSchema()) && v.equals(r.getTypeIdExtension())).findFirst().orElse(null);
        }
        public List<SchemaETY> findByVersion(String v) {
            return vivi().stream().filter(r -> v.equals(r.getTypeIdExtension())).collect(Collectors.toList());
        }
        public List<SchemaETY> findByExtensionAndLastUpdateDate(String v, Date d) {
            return vivi().stream().filter(r -> v.equals(r.getTypeIdExtension()) && r.getLastUpdateDate().after(d)).collect(Collectors.toList());
        }
        public SchemaETY findGtLastUpdate(String v) {
            return vivi().stream().filter(r -> v.equals(r.getTypeIdExtension())).max(Comparator.comparing(SchemaETY::getLastUpdateDate)).orElse(null);
        }
    }

    static class SchematronRepoMemoria implements ISchematronRepo {
        final List<SchematronETY> righe = new ArrayList<>();

        SchematronRepoMemoria(JsonNode dump) {
            for (JsonNode n : dump) {
                SchematronETY e = new SchematronETY();
                e.setId(n.get("_id").get("$oid").asText());
                e.setContentSchematron(new Binary(binario(n.get("content_schematron"))));
                e.setNameSchematron(testo(n, "name_schematron"));
                e.setTemplateIdRoot(testo(n, "template_id_root"));
                e.setVersion(testo(n, "version"));
                e.setSystem(testo(n, "system"));
                e.setLastUpdateDate(data(n.get("last_update_date")));
                e.setDeleted(n.get("deleted").asBoolean());
                righe.add(e);
            }
        }

        static boolean stesso(String a, String b) { return a == null ? b == null : a.equals(b); }

        // Mongo: where template_id_root = root and system = system and deleted = false, sort version DESC (stringhe)
        public SchematronETY findByRootAndSystem(String root, String system) {
            return righe.stream()
                .filter(r -> !r.getDeleted() && root.equals(r.getTemplateIdRoot()) && stesso(system, r.getSystem()))
                .max(Comparator.comparing(SchematronETY::getVersion)).orElse(null);
        }
        public SchematronETY findGreaterOne(String root, String system, String version) {
            return righe.stream()
                .filter(r -> !r.getDeleted() && root.equals(r.getTemplateIdRoot()) && stesso(system, r.getSystem())
                        && r.getVersion().compareTo(version) > 0)
                .max(Comparator.comparing(SchematronETY::getVersion)).orElse(null);
        }
    }

    static class DizionariMemoria implements IDictionaryRepo {
        final List<DictionaryETY> righe = new ArrayList<>();

        DizionariMemoria(JsonNode dump) {
            for (JsonNode n : dump) {
                if (n.get("deleted").asBoolean()) continue;
                righe.add(new DictionaryETY(n.get("_id").get("$oid").asText(), testo(n, "system"), testo(n, "version"),
                        data(n.get("creation_date")), data(n.get("release_date")), n.get("whitelist").asBoolean()));
            }
        }
        public List<DictionaryETY> getCodeSystems() { return righe; }
    }

    /** terminology.json è grande (~150 MB): lo si legge in streaming e si tengono solo system/version/code. */
    static class TerminologiaMemoria implements ITerminologyRepo {
        final Map<String, Set<String>> perVersione = new HashMap<>();   // system|version -> codici
        final Map<String, Set<String>> perSistema = new HashMap<>();    // system -> codici

        TerminologiaMemoria(Path p) throws Exception {
            JsonFactory f = JSON.getFactory();
            try (InputStream in = new GZIPInputStream(Files.newInputStream(p)); JsonParser jp = f.createParser(in)) {
                if (jp.nextToken() != JsonToken.START_ARRAY) throw new IllegalStateException("terminology: atteso un array");
                while (jp.nextToken() == JsonToken.START_OBJECT) {
                    String system = null, version = null, code = null;
                    boolean deleted = false;
                    while (jp.nextToken() != JsonToken.END_OBJECT) {
                        String nome = jp.getCurrentName();
                        JsonToken t = jp.nextToken();
                        if (t == JsonToken.START_OBJECT || t == JsonToken.START_ARRAY) { jp.skipChildren(); continue; }
                        switch (nome) {
                            case "system": system = jp.getValueAsString(); break;
                            case "version": version = t == JsonToken.VALUE_NULL ? null : jp.getValueAsString(); break;
                            case "code": code = jp.getValueAsString(); break;
                            case "deleted": deleted = jp.getValueAsBoolean(); break;
                            default: break;
                        }
                    }
                    if (deleted || system == null || code == null) continue;
                    perVersione.computeIfAbsent(system + "|" + version, k -> new HashSet<>()).add(code);
                    perSistema.computeIfAbsent(system, k -> new HashSet<>()).add(code);
                }
            }
        }

        public boolean allCodesExists(String system, List<String> codes) {
            return perSistema.getOrDefault(system, Set.of()).containsAll(codes);
        }
        public List<String> findAllCodesExists(String system, List<String> codes) {
            Set<String> s = perSistema.getOrDefault(system, Set.of());
            return codes.stream().filter(s::contains).collect(Collectors.toList());
        }
        // query ufficiale: system = ? and code in (?) and deleted = false [and version = ? se version != null]
        public List<String> findAllCodesExistsForVersion(String system, String version, List<String> codes) {
            Set<String> s = version == null ? perSistema.getOrDefault(system, Set.of())
                    : perVersione.getOrDefault(system + "|" + version, Set.of());
            return codes.stream().filter(s::contains).collect(Collectors.toList());
        }
        public boolean existBySystemAndCode(String system, String code) {
            return perSistema.getOrDefault(system, Set.of()).contains(code);
        }
        public boolean existBySystemAndNotCodes(String system, List<String> codes) {
            Set<String> s = perSistema.get(system);
            return s != null && !codes.stream().allMatch(s::contains);
        }
    }

    /** Il LoggerHelper ufficiale scrive su Kafka: qui si raccolgono solo i messaggi. */
    static class LogRaccolti extends LoggerHelper {
        final List<String> avvisi = new ArrayList<>();
        final List<String> errori = new ArrayList<>();

        @Override
        public void warn(String w, String message, OperationLogEnum op, ResultLogEnum r, Date d, WarnLogEnum warning) {
            avvisi.add(message);
        }
        @Override
        public void error(String w, String message, OperationLogEnum op, ResultLogEnum r, Date d, ErrorLogEnum error) {
            errori.add(message);
        }
    }

    static void inietta(Object bersaglio, String campo, Object valore) throws Exception {
        Field f = bersaglio.getClass().getDeclaredField(campo);
        f.setAccessible(true);
        f.set(bersaglio, valore);
    }

    // ------------------------------------------------------------ banco
    /** Il servizio ufficiale con i repository in memoria. */
    static final class Banco {
        final ValidationSRV srv;
        final LogRaccolti log;

        Banco(ValidationSRV srv, LogRaccolti log) { this.srv = srv; this.log = log; }

        /** Valida un CDA e restituisce l'oggetto risultato (stessi campi della riga RISULTATO). */
        ObjectNode valida(String nome, String cda) {
            log.avvisi.clear();
            log.errori.clear();
            ObjectNode out = JSON.createObjectNode();
            out.put("file", nome);
            try {
                ValidatoreUfficiale.valida(srv, cda, out, log);
            } catch (Exception e) {
                out.put("esito", "ECCEZIONE");
                out.put("eccezione", e.toString());
            }
            return out;
        }
    }

    static Banco prepara(Path dump) throws Exception {
        SchemaRepoMemoria schemi = new SchemaRepoMemoria(leggiDump(dump.resolve("schema.json.gzip")));
        SchematronRepoMemoria schematron = new SchematronRepoMemoria(leggiDump(dump.resolve("schematron.json.gzip")));
        DizionariMemoria dizionari = new DizionariMemoria(leggiDump(dump.resolve("dictionary.json.gzip")));
        TerminologiaMemoria terminologia = new TerminologiaMemoria(dump.resolve("terminology.json.gzip"));

        LogRaccolti log = new LogRaccolti();
        TerminologySRV terminologySRV = new TerminologySRV();
        inietta(terminologySRV, "terminologyRepo", terminologia);
        inietta(terminologySRV, "codeSystemRepo", dizionari);
        inietta(terminologySRV, "logger", log);

        ValidationSRV srv = new ValidationSRV();
        inietta(srv, "terminologySRV", terminologySRV);
        inietta(srv, "schematronRepo", schematron);
        inietta(srv, "schemaRepo", schemi);
        inietta(srv, "schemaSRV", new SchemaSRV());
        return new Banco(srv, log);
    }

    // ------------------------------------------------------------ main
    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("uso: ValidatoreUfficiale <cartella-dump> <file.xml>...");
            System.exit(2);
        }
        Banco banco = prepara(Path.of(args[0]));
        for (int i = 1; i < args.length; i++) {
            Path file = Path.of(args[i]);
            String cda = Files.readString(file, StandardCharsets.UTF_8);
            System.out.println("RISULTATO " + JSON.writeValueAsString(banco.valida(file.getFileName().toString(), cda)));
        }
    }

    /** Stessa sequenza di ValidationCTL.validation (it-fse-gtw-validator). */
    static void valida(ValidationSRV srv, String cda, ObjectNode out, LogRaccolti log) throws Exception {
        List<String> messages = new ArrayList<>();
        String outcome = "OK";
        ExtractedInfoDTO info = CDAHelper.extractInfo(cda, null);   // header di sistema assente -> NONE (non TS)
        out.put("typeIdExtension", info.getTypeIdExtension());

        CDAValidationDTO sintassi = srv.validateSyntactic(cda, info.getTypeIdExtension());
        if (CDAValidationStatusEnum.NOT_VALID.equals(sintassi.getStatus())) {
            if (sintassi.getMessage() == null || sintassi.getMessage().isEmpty()) {
                for (Map.Entry<CDASeverityViolationEnum, List<String>> v : sintassi.getViolations().entrySet())
                    for (String s : v.getValue()) messages.add(v.getKey() + ": " + s);
            } else {
                messages.add(sintassi.getMessage());
            }
            outcome = "SYNTAX_ERROR";
        }

        if ("OK".equals(outcome)) {
            SchematronValidationResultDTO sem = srv.validateSemantic(cda, info);
            if (sem.getMessage() == null || sem.getMessage().isEmpty()) {
                if (Boolean.FALSE.equals(sem.getValidSchematron())) {
                    messages.add("Invalid schematron");
                    outcome = "SEMANTIC_ERROR";
                } else if (sem.getFailedAssertions() != null && !sem.getFailedAssertions().isEmpty()) {
                    for (SchematronFailedAssertionDTO v : sem.getFailedAssertions()) messages.add(v.getText());
                    outcome = Boolean.FALSE.equals(sem.getValidXML()) ? "SEMANTIC_ERROR" : "SEMANTIC_WARNING";
                }
            } else {
                messages.add(sem.getMessage());
                outcome = "SEMANTIC_ERROR";
            }
            dettaglioSchematron(cda, info, out);

            if (info.getSystem() != SystemTypeEnum.TS && ("OK".equals(outcome) || "SEMANTIC_WARNING".equals(outcome))) {
                VocabularyResultDTO voc = srv.validateVocabularies(cda, "varco-prova-locale");
                ObjectNode v = out.putObject("vocabolario");
                v.put("valido", voc.getValid());
                v.put("messaggio", voc.getMessage());
                if (!Boolean.TRUE.equals(voc.getValid())) {
                    outcome = "VOCABULARY_ERROR";
                    messages = new ArrayList<>();
                    messages.add(voc.getMessage());
                }
            }
        }
        out.put("esito", outcome);
        ArrayNode m = out.putArray("messaggi");
        messages.forEach(m::add);
        ArrayNode la = out.putArray("log_avvisi");
        log.avvisi.forEach(la::add);
        ArrayNode le = out.putArray("log_errori");
        log.errori.forEach(le::add);
    }

    /** Separa assert falliti (errori) e report (avvisi) usando la stessa risorsa schematron scelta dal servizio. */
    static void dettaglioSchematron(String cda, ExtractedInfoDTO info, ObjectNode out) throws Exception {
        Map<String, SchematronValidatorSingleton> mappa = SchematronValidatorSingleton.getMapInstance();
        if (mappa == null) return;
        for (String root : info.getTemplateIdSchematron()) {
            SchematronValidatorSingleton s = mappa.get(SchematronValidatorSingleton.identifier(root, info.getSystem().value()));
            if (s == null) continue;
            ObjectNode sch = out.putObject("schematron");
            sch.put("templateIdRoot", s.getTemplateIdRoot());
            sch.put("versione", s.getVersion());
            ISchematronResource r = s.getSchematronResource();
            SchematronOutputType svrl;
            try (ByteArrayInputStream in = new ByteArrayInputStream(cda.getBytes(StandardCharsets.UTF_8))) {
                svrl = r.applySchematronValidationToSVRL(new StreamSource(in));
            }
            ArrayNode errori = sch.putArray("errori");
            ArrayNode avvisi = sch.putArray("avvisi");
            for (Object o : svrl.getActivePatternAndFiredRuleAndFailedAssert()) {
                if (o instanceof FailedAssert fa) {
                    errori.addObject().put("testo", fa.getText().getContent().toString()).put("posizione", fa.getLocation());
                } else if (o instanceof SuccessfulReport sr) {
                    avvisi.addObject().put("testo", sr.getText().getContent().toString()).put("posizione", sr.getLocation());
                }
            }
            return;
        }
    }
}

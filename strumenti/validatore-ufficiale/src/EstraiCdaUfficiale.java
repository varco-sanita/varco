// SPDX-License-Identifier: EUPL-1.2
//
// Estrae il CDA da un PDF con il codice UFFICIALE del dispatcher del gateway FSE 2.0
// (it-fse-gtw-dispatcher, utility/PDFUtility.extractContentFromAttachments, modalità ATTACHMENT,
// nome allegato "cda.xml" come in application.properties: cda.attachment.name=cda.xml).
//
// Uso: java -cp ... EstraiCdaUfficiale <file.pdf>...
// Stampa, per ogni PDF, una riga "RISULTATO {json}" con: trovato, sha256 e lunghezza del CDA estratto.

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import it.finanze.sanita.fse2.ms.gtw.dispatcher.utility.PDFUtility;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;

public class EstraiCdaUfficiale {
    public static void main(String[] args) throws Exception {
        ObjectMapper json = new ObjectMapper();
        for (String a : args) {
            Path p = Path.of(a);
            byte[] pdf = Files.readAllBytes(p);
            ObjectNode out = json.createObjectNode();
            out.put("file", p.getFileName().toString());
            out.put("is_pdf", PDFUtility.isPdf(pdf));
            String cda = PDFUtility.extractContentFromAttachments(pdf, "cda.xml");
            out.put("trovato", cda != null);
            if (cda != null) {
                byte[] b = cda.getBytes(StandardCharsets.UTF_8);
                out.put("lunghezza", b.length);
                out.put("sha256", HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(b)));
            }
            System.out.println("RISULTATO " + json.writeValueAsString(out));
        }
    }
}

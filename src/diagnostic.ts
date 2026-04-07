import * as fs from 'fs';
import * as path from 'path';
const pdfParse = require('pdf-parse');

const PDF_DIR =
  (process.env.PDF_DIR && process.env.PDF_DIR.trim()) ||
  "C:\\Users\\ivanam.PSSJotace\\nags\\pdfs";

async function diagnosticPDF() {
  if (!fs.existsSync(PDF_DIR)) {
    console.error(`❌ PDF_DIR no existe: ${PDF_DIR} (define PDF_DIR en .env)`);
    return;
  }
  const files = fs.readdirSync(PDF_DIR).filter(f => f.endsWith('.pdf'));
  
  if (files.length === 0) {
    console.log("❌ No se encontraron PDFs");
    return;
  }

  // Analizar el primer PDF
  const testFile = files[0];
  const filePath = path.join(PDF_DIR, testFile);
  
  console.log(`\n🔍 DIAGNÓSTICO DE: ${testFile}\n`);
  console.log("=".repeat(60));

  try {
    const dataBuffer = fs.readFileSync(filePath);
    const pdfData = await pdfParse(dataBuffer);
    
    const pages = pdfData.text.split(/\f/g);
    
    console.log(`📄 Total de páginas detectadas: ${pdfData.numpages}`);
    console.log(`📝 Páginas con texto extraído: ${pages.length}`);
    console.log(`📊 Total de caracteres extraídos: ${pdfData.text.length}`);
    console.log("\n" + "=".repeat(60));
    
    // Analizar cada página
    for (let i = 0; i < Math.min(pages.length, 5); i++) {
      const pageText = pages[i].trim();
      console.log(`\n📄 Página ${i + 1}:`);
      console.log(`   Caracteres: ${pageText.length}`);
      
      if (pageText.length > 0) {
        console.log(`   Preview: ${pageText.substring(0, 100)}...`);
      } else {
        console.log(`   ⚠️  VACÍA - Probablemente imagen escaneada`);
      }
    }
    
    console.log("\n" + "=".repeat(60));
    
    // Diagnóstico final
    const avgCharsPerPage = pdfData.text.length / pdfData.numpages;
    console.log(`\n📊 DIAGNÓSTICO:`);
    console.log(`   Promedio de caracteres por página: ${avgCharsPerPage.toFixed(0)}`);
    
    if (avgCharsPerPage < 100) {
      console.log(`   ⚠️  PROBLEMA DETECTADO: PDF contiene imágenes escaneadas`);
      console.log(`   ✅ SOLUCIÓN: Necesitas implementar OCR (Tesseract)`);
    } else {
      console.log(`   ✅ PDF tiene texto seleccionable, no necesitas OCR`);
    }
    
  } catch (error) {
    console.error(`❌ Error:`, error);
  }
}

diagnosticPDF();
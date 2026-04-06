// src/test-ocr.ts
import { fromPath } from 'pdf2pic';
import { createWorker } from 'tesseract.js';
import * as fs from 'fs';
import * as path from 'path';

async function testSinglePage() {
  const pdfPath = "C:\\Users\\ivanam.PSSJotace\\nags\\pdfs\\NAGS 2020.pdf";
  
  console.log('🧪 Prueba de OCR en página 2...\n');
  
  // Crear carpeta temp si no existe
  if (!fs.existsSync('./temp')) {
    fs.mkdirSync('./temp');
  }
  
  try {
    // Paso 1: Convertir PDF a imagen
    console.log('📄 Paso 1: Convirtiendo página 2 a imagen...');
    const options = {
      density: 150,
      saveFilename: `test_page_2`,
      savePath: "./temp",
      format: "png",
      width: 1600,
      height: 1600
    };
    
    const convert = fromPath(pdfPath, options);
    const pageImage = await convert(2, { responseType: "image" });
    
    if (!pageImage || !pageImage.path) {
      console.log('❌ No se pudo convertir la página a imagen');
      return;
    }
    
    console.log(`✅ Imagen creada: ${pageImage.path}`);
    console.log(`📊 Tamaño del archivo: ${fs.statSync(pageImage.path).size} bytes`);
    
    // Paso 2: Aplicar OCR
    console.log('\n🔍 Paso 2: Aplicando OCR...');
    const worker = await createWorker('eng');
    
    const { data: { text } } = await worker.recognize(pageImage.path);
    
    await worker.terminate();
    
    console.log(`✅ OCR completado`);
    console.log(`📊 Caracteres extraídos: ${text.length}`);
    console.log(`\n📝 Primeros 200 caracteres:\n${text.substring(0, 200)}`);
    
    // Limpiar
    if (fs.existsSync(pageImage.path)) {
      fs.unlinkSync(pageImage.path);
      console.log('\n🧹 Imagen temporal eliminada');
    }
    
  } catch (error) {
    console.error('❌ Error:', error);
  }
}

testSinglePage();
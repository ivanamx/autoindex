// src/verify-tesseract.ts
import { createWorker } from 'tesseract.js';

async function verifyTesseract() {
  console.log('🔍 Verificando instalación de Tesseract...\n');
  
  try {
    const worker = await createWorker('eng');
    console.log('✅ Tesseract.js inicializado correctamente');
    console.log('✅ Motor OCR listo para usar');
    await worker.terminate();
    
    console.log('\n📋 Siguiente paso: Configurar extracción con OCR');
  } catch (error) {
    console.error('❌ Error al inicializar Tesseract:', error);
    console.log('\n⚠️  Asegúrate de haber instalado Tesseract correctamente');
  }
}

verifyTesseract();
import 'dotenv/config';
import * as fs from 'fs';
import * as path from 'path';
import { execSync } from 'child_process';
import { Pool } from 'pg';
const pdfParse = require('pdf-parse');

const PDF_DIR =
  (process.env.PDF_DIR && process.env.PDF_DIR.trim()) ||
  "C:\\Users\\ivanam.PSSJotace\\nags\\pdfs";

function resolveVenvPython(projectRoot: string): string {
  const unix = path.join(projectRoot, "venv", "bin", "python");
  const win = path.join(projectRoot, "venv", "Scripts", "python.exe");
  if (fs.existsSync(unix)) return `"${unix}"`;
  if (fs.existsSync(win)) return `"${win}"`;
  return "python";
}

// Usar connection string del .env
const pool = new Pool({
  connectionString: process.env.DB_CONNECTION_STRING
});

interface PageData {
  catalogo_nombre: string;
  pagina: number;
  texto: string;
  pdf_path: string;
}

async function extractTextWithOCR(pdfPath: string, pageNumber: number): Promise<string> {
  try {
    console.log(`   🔍 Aplicando OCR a página ${pageNumber}...`);
    
    const projectRoot = path.resolve(__dirname, '..');
    const pythonScript = path.join(projectRoot, 'ocr_extractor.py');
    const pythonCmd = resolveVenvPython(projectRoot);
    
    if (!fs.existsSync(pythonScript)) {
      console.error(`   ❌ Script Python no encontrado`);
      return '';
    }
    
    const command = `${pythonCmd} "${pythonScript}" "${pdfPath}" ${pageNumber}`;
    const output = execSync(command, {
      encoding: 'utf-8',
      maxBuffer: 50 * 1024 * 1024,
      timeout: 120000
    });
    
    const result = JSON.parse(output);
    console.log(`   ✅ OCR completado: ${result.text.length} caracteres extraídos`);
    
    return result.text;
    
  } catch (error) {
    console.error(`   ❌ Error en OCR página ${pageNumber}:`, error instanceof Error ? error.message : error);
    return '';
  }
}

async function extractPDFText(filePath: string): Promise<PageData[]> {
  const dataBuffer = fs.readFileSync(filePath);
  const pdfData = await pdfParse(dataBuffer);
  const fileName = path.basename(filePath);
  
  console.log(`\n📄 Procesando: ${fileName}`);
  console.log(`   Total de páginas: ${pdfData.numpages}`);
  
  const pages: PageData[] = [];
  const totalPages = pdfData.numpages;
  const textPages = pdfData.text.split(/\f/g);
  
  if (textPages.length === 1 && totalPages > 1) {
    console.log(`   ⚠️  Detectadas ${totalPages} páginas pero solo 1 con texto`);
    console.log(`   🔄 Iniciando extracción con OCR (TODAS las páginas)...`);
    
    // ← CAMBIO: Procesar TODAS las páginas con OCR (incluyendo la 1)
    let successCount = 0;
    let failCount = 0;
    
    for (let i = 1; i <= totalPages; i++) {
      console.log(`\n   📄 Procesando página ${i}/${totalPages}`);
      const pageText = await extractTextWithOCR(filePath, i);
      
      if (pageText.trim().length > 0) {
        pages.push({
          catalogo_nombre: fileName,
          pagina: i,
          texto: pageText,
          pdf_path: filePath
        });
        successCount++;
      } else {
        failCount++;
      }
      
      if (i % 10 === 0) {
        console.log(`   ⏸️  Progreso: ${i}/${totalPages} (${successCount} OK, ${failCount} fallos)`);
        await new Promise(resolve => setTimeout(resolve, 500));
      }
    }
    
    console.log(`\n   📊 Resumen OCR: ${successCount} exitosas, ${failCount} fallidas`);
  } else {
    // Todas las páginas tienen texto nativo
    console.log(`   ✅ Texto extraído directamente (sin OCR necesario)`);
    textPages.forEach((text: string, index: number) => {
      if (text.trim().length > 0) {
        pages.push({
          catalogo_nombre: fileName,
          pagina: index + 1,
          texto: text,
          pdf_path: filePath
        });
      }
    });
  }
  
  console.log(`\n   📊 Total de páginas extraídas: ${pages.length}`);
  return pages;
}

function limpiarTexto(text: string): string {
  return text
    .replace(/\uFFFD/g, '')
    .replace(/[^\x00-\x7F\u00A0-\uFFFF]/g, '') 
    .trim();
}

async function saveToDatabase(pages: PageData[], catalogoNombre: string) {
  const client = await pool.connect();
  
  try {
    await client.query('BEGIN');
    
    // Eliminar registros anteriores de este catálogo
    await client.query(
      'DELETE FROM catalogos WHERE catalogo_nombre = $1',
      [catalogoNombre]
    );
    console.log(`   🗑️  Registros anteriores eliminados`);
    
    // Insertar nuevos registros
    let insertedCount = 0;
    for (const page of pages) {
      await client.query(
        'INSERT INTO catalogos (catalogo_nombre, pagina, texto, pdf_path) VALUES ($1, $2, $3, $4)',
        [page.catalogo_nombre, page.pagina, limpiarTexto(page.texto), page.pdf_path]
      );
      insertedCount++;
    }
    
    await client.query('COMMIT');
    console.log(`   ✅ ${insertedCount} páginas guardadas en PostgreSQL`);
    
  } catch (error) {
    await client.query('ROLLBACK');
    console.error(`   ❌ Error guardando en base de datos:`, error);
    throw error;
  } finally {
    client.release();
  }
}

async function processPDFs() {
  if (!fs.existsSync(PDF_DIR)) {
    console.error(`❌ PDF_DIR no existe: ${PDF_DIR} (define PDF_DIR en .env)`);
    process.exit(1);
  }
  const files = fs.readdirSync(PDF_DIR).filter(f => f.endsWith('.pdf'));
  
  console.log(`\n🚀 Procesando ${files.length} PDFs...\n`);
  console.log("=".repeat(60));

  try {
    // Verificar conexión a PostgreSQL
    const client = await pool.connect();
    console.log('✅ Conexión a PostgreSQL establecida\n');
    client.release();
    
    for (const file of files) {
      const filePath = path.join(PDF_DIR, file);

      try {
        console.log(`\n🔄 Procesando: ${file}`);
        
        // Extraer texto de todas las páginas
        const pages = await extractPDFText(filePath);
        
        // Guardar en PostgreSQL
        console.log(`\n   💾 Guardando en base de datos...`);
        await saveToDatabase(pages, file);
        
        console.log(`✅ ${file} - Completado\n`);

      } catch (error) {
        console.error(`❌ Error procesando ${file}:`, error);
      }
    }

    console.log("\n" + "=".repeat(60));
    console.log("✅ Procesamiento completado");
    
    // Mostrar estadísticas
    const result = await pool.query('SELECT catalogo_nombre, COUNT(*) as paginas FROM catalogos GROUP BY catalogo_nombre');
    console.log("\n📊 Estadísticas en base de datos:");
    result.rows.forEach(row => {
      console.log(`   ${row.catalogo_nombre}: ${row.paginas} páginas`);
    });
    
  } catch (error) {
    console.error('❌ Error general:', error);
  } finally {
    await pool.end();
  }
}

processPDFs();
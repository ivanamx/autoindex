import json
import os
import shutil
import sys
from typing import Optional

from dotenv import load_dotenv
from pdf2image import convert_from_path
import pytesseract

load_dotenv()

# En servidor (Linux) suele bastar tener `tesseract` y `pdftoppm` en el PATH.
# En Windows, define rutas explícitas en .env, por ejemplo:
#   TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
#   POPPLER_PATH=C:\ruta\a\poppler\Library\bin


def _tesseract_executable() -> Optional[str]:
    """Ruta al binario de Tesseract, o None para usar el `tesseract` del PATH."""
    cmd = (os.getenv("TESSERACT_CMD") or "").strip()
    if cmd:
        return cmd
    return None


def _poppler_bin_dir() -> Optional[str]:
    """Directorio con pdftoppm (.exe en Windows), o None si Poppler está en el PATH."""
    path = (os.getenv("POPPLER_PATH") or "").strip()
    return path if path else None


_tess = _tesseract_executable()
if _tess:
    pytesseract.pytesseract.tesseract_cmd = _tess


def extract_text_from_page(pdf_path, page_number):
    """Extrae texto de una página específica usando OCR"""
    try:
        if _tess:
            if not os.path.isfile(_tess):
                raise FileNotFoundError(f"Tesseract no encontrado en TESSERACT_CMD={_tess!r}")
        elif not shutil.which("tesseract"):
            raise FileNotFoundError(
                "Tesseract no está en el PATH. Instálalo o define TESSERACT_CMD en .env (ruta al ejecutable)."
            )

        poppler_path = _poppler_bin_dir()
        if poppler_path and not os.path.isdir(poppler_path):
            raise FileNotFoundError(f"POPPLER_PATH no es un directorio válido: {poppler_path!r}")

        convert_kw = {
            "pdf_path": pdf_path,
            "first_page": page_number,
            "last_page": page_number,
            "dpi": 150,
        }
        if poppler_path:
            convert_kw["poppler_path"] = poppler_path

        images = convert_from_path(**convert_kw)

        if not images:
            return ""

        return pytesseract.image_to_string(images[0], lang="eng")

    except Exception as e:
        print(f"Error en página {page_number}: {str(e)}", file=sys.stderr)
        return ""


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Uso: python ocr_extractor.py <pdf_path> <page_number>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    page_number = int(sys.argv[2])

    text = extract_text_from_page(pdf_path, page_number)

    result = {
        "page": page_number,
        "text": text,
        "length": len(text),
    }

    print(json.dumps(result, ensure_ascii=False))

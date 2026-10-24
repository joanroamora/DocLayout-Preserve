# DocLayout-Preserve

Document Layout Preservation Engine & Toolkit

## 1. Visión y Objetivo

**DocLayout-Preserve** es un proyecto enfocado en la extracción, procesamiento y reconstrucción de documentos (PDF, DOCX, imágenes escaneadas, etc.) garantizando la **preservación estricta de la estructura visual y de maquetación** (posicionamiento de bloques, tipografía, tablas, columnas, figuras y jerarquía visual) durante transformaciones de contenido (traducción, reescritura, OCR, redacción asistida por IA).

---

## 2. Alcance del Proyecto

1. **Ingesta y Parsing**:
   - Detección de elementos de maquetación (bounding boxes, párrafos, encabezados, tablas, listas, imágenes).
   - Extracción de metadatos de estilo (fuentes, tamaños, colores, espaciados, alineación).

2. **Procesamiento de Contenido**:
   - Soporte para modificación, traducción o enriquecimiento del texto sin alterar el esquema espacial del documento.
   - Ajuste adaptativo de texto (font-scaling / reflow controlado) para evitar desbordamientos en áreas delimitadas.

3. **Reconstrucción y Exportación**:
   - Generación de documentos finales conservando el formato original con alta fidelidad.
   - Exportación a formatos estándar (PDF, DOCX, Markdown estructurado / HTML).

---

## 3. Arquitectura Propuesta

```
┌────────────────┐     ┌──────────────────────┐     ┌──────────────────────┐
│ Document Ingest│ ──> │ Layout Decomposition │ ──> │ Content Processing   │
│ (PDF/DOCX/Img) │     │ & Structure Analysis │     │ (Translation/AI Ref) │
└────────────────┘     └──────────────────────┘     └──────────────────────┘
                                                               │
                                                               ▼
┌────────────────┐     ┌──────────────────────┐     ┌──────────────────────┐
│ Output Export  │ <── │ Quality Verification │ <── │ Layout Reconstruction│
│  (Fidelity OK) │     │ (Diff & BBox Check)  │     │   & Text Reflow      │
└────────────────┘     └──────────────────────┘     └──────────────────────┘
```

---

## 4. Pila Tecnológica Preliminar

- **Lenguaje Base**: Python 3.11+
- **Parsing y Manipulación de Documentos**:
  - `PyMuPDF` (fitz) / `pdfplumber` / `pypdf` para análisis y renderizado PDF.
  - `python-docx` para documentos Word.
  - Modelos de Vision-Language / Document Layout Analysis (LayoutLM, DocLayout-YOLO, o Gemini Multimodal) según requerimientos.
- **Entorno y Aislamiento**:
  - Entornos virtuales locales (`.venv`) sin dependencias globales.
- **Pruebas y Verificación**:
  - `pytest` para pruebas unitarias y de integración.
  - Comparadores visuales de fidelidad de maquetación (Pixel Diff / SSIM / IoU de Bounding Boxes).

---

## 5. Estructura de Directorios (Propuesta)

```plaintext
DocLayout-Preserve/
├── .gitignore
├── project.md
├── README.md
├── src/
│   └── doclayout/
│       ├── __init__.py
│       ├── ingestion/
│       ├── layout/
│       ├── processor/
│       └── render/
├── tests/
└── samples/
```

---

## 6. Siguientes Pasos

1. [ ] Definición de especificaciones detalladas de los formatos de entrada/salida.
2. [ ] Selección del motor de análisis de maquetación (heurístico vs. basado en modelos de ML/visión).
3. [ ] Implementación del pipeline inicial de extracción y reconstrucción básica.
4. [ ] Validación de fidelidad de diseño y tolerancias de ajuste tipográfico.

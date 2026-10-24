# DocLayout-Preserve

**Motor de Preservación de Estructura y Maquetación de Documentos con Gemini Multimodal**

`DocLayout-Preserve` extrae, transforma (mediante `google-genai` y modelos multimodales de Gemini) y reconstruye documentos complejos (`PDF`, `DOCX`), preservando su geometría espacial, cuadros delimitadores (*bounding boxes*), tipografías, tablas e imágenes.

---

## Arquitectura del Pipeline (4 Etapas)

1. **Etapa 1 — Ingesta y Descomposición de Layout (`src/doclayout/ingestion/`)**:
   Extrae bloques semánticos, fuentes, tamaños, estilos, celdas de tabla y coordenadas exactas `(x0, y0, x1, y1)`.
2. **Etapa 2 — Transformación Semántica (`src/doclayout/llm/`)**:
   Procesa el contenido con Gemini API bajo salidas estructuradas (JSON Schema), manteniendo longitud controlada y tratando documentos como *untrusted input*.
3. **Etapa 3 — Reconstrucción Tipográfica & Fitting (`src/doclayout/reconstruction/`)**:
   Aplica *text-fitting* adaptativo (micro-tracking, escalado proporcional de fuente hasta 75% mínimo e interlineado) y renderiza sobre la geometría original.
4. **Etapa 4 — Verificación Visual y Calidad (`src/doclayout/verification/`)**:
   Calcula métricas objetivas de fidelidad espacial (**IoU**, detección de colisiones, **SSIM** y reporte de desbordamientos).

---

## Requisitos y Aislamiento Obligatorio

- Todo el procesamiento e instalación se ejecuta estrictamente dentro de `.venv` en la raíz del proyecto.
- Las cachés de instalación y pruebas residen en `.cache/` y los archivos temporales en `.tmp/`.
- Prohibido instalar paquetes globales o usar `--user`.

### Configuración Inicial en Windows PowerShell

```powershell
# 1. Crear entorno virtual y descargar dependencias con cache local
.\scripts\dev_setup.ps1

# 2. Configurar variables de entorno
Copy-Item .env.example .env
# Edita .env con tu GEMINI_API_KEY
```

### Ejecución de Validaciones de Calidad y Pruebas

```powershell
.\scripts\run_checks.ps1
```

O individualmente con el intérprete de `.venv`:

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m black --check src tests
.\.venv\Scripts\python.exe -m isort --check-only src tests
.\.venv\Scripts\python.exe -m mypy src tests
.\.venv\Scripts\python.exe -m pytest
```

---

## Documentación de Gobernanza

- [`project.md`](./project.md): Especificación técnica maestra, estrategia de ramas (`main`, `dev`, `Feature<N><Nombre>`), CI/CD, Kubernetes, Terraform, Observabilidad y Regla de Costo/Tokens Cero en Teardown.
- [`stages.md`](./stages.md): Cronograma paso a paso y estado de avance por Feature.

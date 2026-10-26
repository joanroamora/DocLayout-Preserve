# Cronograma de Actividades y Plan de Implementación por Features: DocLayout-Preserve

Este documento define la hoja de ruta secuencial, el paso a paso detallado y el estado de avance del proyecto **DocLayout-Preserve**.
Cada fase se desarrolla obligatoriamente bajo una rama dedicada siguiendo la convención estricta: `Feature<Número><NombreDelDesarrollo>`.

---

## Convenciones de Estado

- `[ ]` **Pendiente**: Tarea planificada aún no iniciada.
- `[/]` **En progreso**: Tarea actualmente en desarrollo.
- `[x]` **Completado**: Tarea implementada, probada localmente y validada con pruebas unitarias/de integración.

---

## Mapa General de Features

| Feature | Rama Asignada | Objetivo Central | Estado |
| :--- | :--- | :--- | :--- |
| **Fase 0** | `main` | Especificación técnica, gobernanza y repositorio base | `[x] Completado` |
| **Feature 1** | `Feature1EntornoYScaffolding` | Aislamiento `.venv`, estructura modular, tooling y contratos de datos | `[x] Completado` |
| **Feature 2** | `Feature2IngestaYParsingLayout` | Motor de parsing y extracción de Bounding Boxes (PDF/DOCX) | `[x] Completado` |
| **Feature 3** | `Feature3IntegracionGeminiIA` | Cliente `google-genai`, esquemas estructurados y mock testing | `[x] Completado` |
| **Feature 4** | `Feature4MotorReconstruccionYFitting` | Algoritmo de text-fitting adaptativo y renderizado de salida | `[x] Completado` |
| **Feature 5** | `Feature5MotorVerificacionFidelidad` | Engine de calidad visual (IoU, colisiones, SSIM y reportes JSON) | `[ ] Pendiente` |
| **Feature 6** | `Feature6InterfazStreamlitComparativa` | UI con vista lado a lado, capas de bounding boxes e historial | `[ ] Pendiente` |
| **Feature 7** | `Feature7PipelineCICDSeguridad` | GitHub Actions (validación de ramas, tests con mocks, SAST, Trivy) | `[ ] Pendiente` |
| **Feature 8** | `Feature8ArquitecturaAsincronaK8s` | Desacoplamiento (API + Workers + Cola), manifests K8s y HPA | `[ ] Pendiente` |
| **Feature 9** | `Feature9InfraestructuraTerraform` | Módulos Terraform, backend remoto con locking y multi-entorno | `[ ] Pendiente` |
| **Feature 10** | `Feature10ObservabilidadGrafanaRunbooks` | Dashboards Grafana, métricas Prometheus, OTel y Runbooks | `[ ] Pendiente` |
| **Feature 11** | `Feature11PipelinePromocionYRollback` | CD con firma Cosign, SBOM, smoke tests y sincronización de `dev` | `[ ] Pendiente` |

---

## Detalle Paso a Paso por Feature

### Fase 0: Fundaciones y Gobernanza (Completada)
- [x] Inicialización del repositorio Git y vinculación con el remoto en GitHub (`DocLayout-Preserve.git`).
- [x] Configuración de `.gitignore` con exclusiones de entornos virtuales, cachés y credenciales.
- [x] Creación y evolución de [`project.md`](file:///C:/Users/joanr/ProjectsJRRM/WebProjects/DocLayout-Preserve/project.md) como especificación técnica maestra (estrategia de ramas, aislamiento, arquitectura K8s, CI/CD, IaC, observabilidad y DoD).
- [x] Creación de [`stages.md`](file:///C:/Users/joanr/ProjectsJRRM/WebProjects/DocLayout-Preserve/stages.md) como cronograma de actividades trazable.

---

### Feature 1: Entorno de Desarrollo, Aislamiento y Scaffolding Base
**Rama**: `Feature1EntornoYScaffolding`  
**Objetivo**: Crear la estructura de directorios, el entorno virtual estrictamente aislado y los contratos de datos base.

- [x] Creación de `.venv` local mediante `python -m venv .venv` y configuración de cachés en `.cache/pip`.
- [x] Definición de `requirements.txt` y `requirements-dev.txt` con versiones congeladas (`pytest`, `black`, `isort`, `ruff`, `mypy`).
- [x] Implementación de la estructura modular en `src/doclayout/` (`core`, `ingestion`, `llm`, `reconstruction`, `verification`, `api`, `ui`).
- [x] Definición de modelos de datos Pydantic en `src/doclayout/core/models.py`:
  - `BoundingBox(x0, y0, x1, y1, page_index)`
  - `LayoutBlock(block_id, type, bbox, text, font_family, font_size, line_height, color, z_index)`
  - `DocumentLayout(document_id, pages, blocks, metadata)`
  - `TransformationResult(block_id, original_text, transformed_text, status, warnings)`
- [x] Creación de scripts de arranque y verificación en PowerShell (`scripts/dev_setup.ps1` y `scripts/run_checks.ps1`).
- [x] Configuración de pruebas unitarias iniciales para validación de modelos y contratos.

---

### Feature 2: Ingesta y Parsing de Maquetación Documental
**Rama**: `Feature2IngestaYParsingLayout`  
**Objetivo**: Extraer la estructura visual completa de PDFs y DOCX sin pérdida geométrica.

- [x] Implementación de `PDFLayoutExtractor` en `src/doclayout/ingestion/pdf.py` usando `PyMuPDF` (`fitz`) y `pdfplumber`.
  - Extracción de bloques de texto con coordenadas espaciales exactas `(x0, y0, x1, y1)`.
  - Detección de propiedades tipográficas: fuente, tamaño en puntos, color RGBA e interlineado.
  - Identificación de bloques estructurales: Encabezados, Párrafos, Tablas, Listas e Imágenes.
- [x] Implementación de `DocxLayoutExtractor` en `src/doclayout/ingestion/docx.py` para soporte nativo de Word.
- [x] Soporte para extracción de celdas y filas en tablas preservando anchos de columna relativos.
- [x] Manejo de PDFs protegidos, vacíos o corruptos con excepciones controladas y mensajes comprensibles.
- [x] Pruebas unitarias con documentos muestra en `tests/unit/test_ingestion.py`.

---

### Feature 3: Integración con Gemini API y Transformación Semántica
**Rama**: `Feature3IntegracionGeminiIA`  
**Objetivo**: Conectar con Gemini Developer API mediante el SDK oficial y transformar texto preservando restricciones espaciales.

- [x] Implementación del cliente `GeminiClient` en `src/doclayout/llm/client.py` usando `google-genai`.
- [x] Configuración segura mediante `GEMINI_API_KEY` y `GEMINI_MODEL` (con `.env.example`).
- [x] Diseño de prompts estructurados en `src/doclayout/llm/prompts.py`:
  - Inyección de bloques con sus IDs y límites de longitud de caracteres/tokens.
  - Instrucción estricta para evitar desbordamientos tipográficos en el idioma destino.
  - Tratamiento del contenido del documento como *untrusted input*.
- [x] Respuestas estructuradas (Structured Outputs / JSON Schema) mapeando salidas directamente a cada `block_id`.
- [x] Implementación de cliente simulado (`MockGeminiClient` en `tests/mocks/`) para pruebas sin costo de cuota.
- [x] Reintentos exponenciales con *jitter* ante errores de tasa (`429`) y cuota (`RESOURCE_EXHAUSTED`).

---

### Feature 4: Motor de Reconstrucción Tipográfica y Layout Fitting
**Rama**: `Feature4MotorReconstruccionYFitting`  
**Objetivo**: Renderizar el documento transformado encajando el texto en la geometría original.

- [x] Implementación de algoritmo de ajuste adaptativo (*Adaptive Text-Fitting*) en `src/doclayout/reconstruction/fitting.py`:
  - Cálculo de ancho y alto requerido para el texto transformado según la fuente y tamaño base.
  - Ajuste 1: Micro-tracking (espaciado entre caracteres sutil).
  - Ajuste 2: Escalado tipográfico proporcional controlado (umbral mínimo del 75% del tamaño original).
  - Ajuste 3: Ajuste proporcional de interlineado.
  - Emisión de alertas de desbordamiento si el texto supera la capacidad física del contenedor.
- [x] Renderizador PDF en `src/doclayout/reconstruction/pdf_builder.py`:
  - Reemplazo y maquetación de bloques de texto sobre la geometría original.
  - Preservación exacta de imágenes, bordes de tabla, logotipos y elementos vectoriales.
- [x] Generador de documentos Word en `src/doclayout/reconstruction/docx_builder.py`.
- [x] Pruebas de renderizado y ajuste tipográfico en `tests/unit/test_reconstruction.py`.

---

### Feature 5: Motor de Verificación Visual y Control de Calidad
**Rama**: `Feature5MotorVerificacionFidelidad`  
**Objetivo**: Medir cuantitativamente la fidelidad geométrica y visual entre el documento original y el reconstruido.

- [ ] Implementación de métricas de calidad en `src/doclayout/verification/metrics.py`:
  - Cálculo de **IoU (Intersection over Union)** para cajas delimitadoras de texto.
  - Detección de colisiones o solapamientos entre bloques de texto e imágenes.
  - Detección de truncamientos y textos fuera de los márgenes imprimibles.
- [ ] Implementación de comparación visual SSIM (Structural Similarity Index) en elementos gráficos/fondos.
- [ ] Generador de reporte de calidad en formato JSON y resumen estructurado de incidentes visuales.
- [ ] Pruebas automatizadas en `tests/unit/test_verification.py`.

---

### Feature 6: Interfaz de Usuario Local en Streamlit
**Rama**: `Feature6InterfazStreamlitComparativa`  
**Objetivo**: Proporcionar una pantalla interactiva completa para inspección, comparación y descargas.

- [ ] Implementación de la aplicación en `src/doclayout/ui/app.py`:
  - Carga y validación de documentos (PDF / DOCX).
  - Configuración de transformación (idioma destino, estilo, instrucciones de diseño).
  - Visualizador comparativo lado a lado (*Side-by-Side*): documento original vs documento reconstruido.
  - Capas interactivas de inspección: visualización de *bounding boxes* coloreadas por tipo de elemento.
  - Panel de métricas de calidad visual (semáforo de desbordamientos, IoU promedio).
  - Descarga de archivos resultantes (`.pdf`, `.docx`, `.json`).
  - Historial de trabajos de la sesión y acciones «Vaciar historial» y «Limpiar toda la sesión».
- [ ] Ejecución local restringida a `127.0.0.1:8501`.
- [ ] Manejo visual amigable de errores de API o documentos ilegibles.

---

### Feature 7: Pipeline de CI/CD y DevSecOps en GitHub Actions
**Rama**: `Feature7PipelineCICDSeguridad`  
**Objetivo**: Automatizar la validación de código, pruebas, seguridad e integridad del repositorio.

- [ ] Workflow de validación de ramas: comprueba que las ramas sigan la nomenclatura `Feature<Número><NombreDelDesarrollo>`.
- [ ] Workflow `ci-validation.yml`:
  - Instalación reproducible en runner efímero.
  - Linters y formato: `black`, `isort`, `flake8`, `ruff`.
  - Tipado estático con `mypy`.
  - Ejecución de pruebas unitarias e integración con mocks de Gemini (cero consumo de cuota).
- [ ] Workflow `ci-security.yml`:
  - SAST (análisis estático de seguridad).
  - SCA: auditoría de dependencias conocidas con `pip-audit`.
  - Secret scanning con Gitleaks para prevenir filtración de credenciales.
  - Escaneo de contenedores y manifiestos con Trivy.

---

### Feature 8: Arquitectura Asíncrona y Manifiestos de Kubernetes
**Rama**: `Feature8ArquitecturaAsincronaK8s`  
**Objetivo**: Desacoplar la aplicación en API, Workers y Cola para escalamiento horizontal en Kubernetes.

- [ ] API REST en `src/doclayout/api/main.py` (FastAPI) para recepción de documentos y consulta de trabajos asíncronos.
- [ ] Worker asíncrono en `src/doclayout/api/worker.py` consumiendo tareas desde cola (Redis / RabbitMQ).
- [ ] Idempotencia en trabajos y persistencia de estado con TTL y cifrado en almacenamiento de objetos (S3/MinIO).
- [ ] Manifiestos base y overlays en `k8s/`:
  - Deployments separados para UI, API Gateway y Processing Workers.
  - Configuración de `HPA (Horizontal Pod Autoscaler)` basado en profundidad de cola y uso de CPU/RAM.
  - Probes configuradas: `startupProbe`, `readinessProbe` y `livenessProbe`.
  - Políticas de interrupción `PodDisruptionBudget` (PDB).
  - `NetworkPolicies` en modo *deny-all* por defecto, con tráfico permitido únicamente entre componentes necesarios.
  - Contenedores ejecutándose como usuario `non-root` con `read-only root filesystem`.

---

### Feature 9: Infraestructura como Código (IaC) con Terraform
**Rama**: `Feature9InfraestructuraTerraform`  
**Objetivo**: Aprovisionar la infraestructura de forma reproducible, declarativa y segura.

- [ ] Estructura modular en `infra/terraform/modules/`:
  - Módulo de red (VPC, subredes públicas/privadas, NAT Gateways).
  - Módulo de clúster Kubernetes gestionado (EKS/GKE/AKS).
  - Módulo de almacenamiento seguro (Buckets con cifrado KMS y políticas de retención TTL).
  - Módulo de identidades y permisos (IAM / OIDC).
- [ ] Entornos aislados en `infra/terraform/environments/validation/` y `production/`.
- [ ] Configuración de backend remoto con cifrado y bloqueo de estado (*state locking*).
- [ ] Fijación estricta de versiones de proveedores en `.terraform.lock.hcl`.
- [ ] Workflow en GitHub Actions para `terraform fmt`, `tflint`, escaneo con `tfsec`/`checkov` y generación de `terraform plan`.
- [ ] Definición de etiquetas obligatorias para control y alertas de presupuesto en GCP.
- [ ] Implementación y prueba de teardown total (`terraform destroy` con vaciado previo de buckets de Cloud Storage) que garantice el apagado total de infraestructura y cero consumo residual de tokens en GCP ($0 / 0 tokens).

---

### Feature 10: Observabilidad con Grafana, Prometheus y Runbooks
**Rama**: `Feature10ObservabilidadGrafanaRunbooks`  
**Objetivo**: Monitorear en tiempo real la salud de la aplicación, métricas de negocio y procedimientos de operación.

- [ ] Instrumentación de métricas Prometheus:
  - Duración por etapa (ingesta, llamada a Gemini, text-fitting, verificación).
  - Profundidad y latencia de la cola de procesamiento.
  - Conteo de desbordamientos tipográficos y promedio de IoU por documento.
  - Tokens y tiempos de respuesta de Gemini API (asociados a correlaciones anónimas).
- [ ] Trazabilidad distribuida con OpenTelemetry (sin registrar contenido de documentos o PII).
- [ ] Dashboards declarativos de Grafana en `monitoring/dashboards/`:
  - Panel operativo de salud de Kubernetes y workers.
  - Panel de rendimiento del pipeline documental y cuotas de API.
- [ ] Reglas de alerta en `monitoring/alerts/` (cola atascada, errores 5xx, fallos en workers).
- [ ] Creación de runbooks operativos asociados a cada alerta en `docs/runbooks/`.

---

### Feature 11: Pipeline de Promoción Inmutable y Rollback Auditado
**Rama**: `Feature11PipelinePromocionYRollback`  
**Objetivo**: Garantizar despliegues continuos seguros con firma criptográfica, verificación y sincronización con `dev`.

- [ ] Construcción de imágenes OCI con bases mínimas y fijadas por digest SHA inmutable.
- [ ] Generación de SBOM (Software Bill of Materials) y atestación de procedencia SLSA.
- [ ] Firma digital de imágenes mediante Cosign.
- [ ] Despliegue continuo hacia entornos mediante **GitHub Environments** con puertas de aprobación manual para producción.
- [ ] Ejecución de Smoke Tests automáticos post-despliegue con mocks de Gemini.
- [ ] Automatización de sincronización: tras certificar producción, el workflow de CD actualiza automáticamente la rama `dev` hacia el commit desplegado.
- [ ] Procedimiento de rollback automático auditado que revierte producción y sincroniza `dev` ante cualquier anomalía post-despliegue.

---

## Registro de Cambios y Progreso

| Fecha | Feature / Hito | Responsable | Descripción del Cambio |
| :--- | :--- | :--- | :--- |
| 23/10/2026 | **Fase 0** | Antigravity AI / Joan Roa | Repositorio vinculado a remoto, especificación técnica en `project.md` y creación del cronograma `stages.md`. |
| 23/10/2026 | **Feature 1** | Antigravity AI / Joan Roa | Rama `Feature1EntornoYScaffolding`: entorno `.venv`, cachés locales, modelos Pydantic, configuración de tooling y tests unitarios. |
| 23/10/2026 | **Feature 2** | Antigravity AI / Joan Roa | Rama `Feature2IngestaYParsingLayout`: motores `PDFLayoutExtractor` y `DocxLayoutExtractor`, soporte de tablas, validaciones y tests unitarios. |
| 24/10/2026 | **Feature 3** | Antigravity AI / Joan Roa | Rama `Feature3IntegracionGeminiIA`: `GeminiClient` con `google-genai`, prompts anti-inyección, Structured Outputs, backoff con jitter y `MockGeminiClient`. |
| 25/10/2026 | **Feature 4** | Antigravity AI / Joan Roa | Rama `Feature4MotorReconstruccionYFitting`: `AdaptiveTextFitter`, `PDFDocumentBuilder` (overlay + standalone), `DocxDocumentBuilder` y tests unitarios. |

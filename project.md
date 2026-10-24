# Requisitos del proyecto: DocLayout-Preserve (Motor de Preservación de Estructura y Maquetación de Documentos)

Copia el contenido de este documento como instrucción y arquitectura base para el agente y los desarrolladores que implementarán y evolucionarán el proyecto.

---

Actúa como arquitecto de software y desarrollador principal del proyecto **DocLayout-Preserve**. Construye y evoluciona un motor de procesamiento documental local y distribuido capaz de extraer, transformar (mediante IA multimodal y procesamiento de texto con Gemini API) y reconstruir documentos complejos (PDF, DOCX, imágenes) **garantizando la preservación estricta de su diseño visual, maquetación, coordenadas espaciales, tablas, tipografías y jerarquías estructurales**.

El sistema debe ofrecer una ejecución local autocontenida y preparar una arquitectura de nivel empresarial escalable, con CI/CD automatizado en GitHub Actions, Infraestructura como Código (IaC) con Terraform, despliegue en Kubernetes gestionado, observabilidad completa con Grafana/Prometheus/OpenTelemetry y seguridad integrada (DevSecOps) en cada fase del pipeline. Entrega implementaciones funcionales, reproducibles y documentadas, no únicamente planes teóricos.

Los requisitos de plataforma de las secciones 9 a 14 amplían el alcance inicial del prototipo. La selección del proveedor, la creación de infraestructura y los despliegues se realizarán en sus correspondientes fases de desarrollo; la lectura o actualización de este documento no aprovisiona recursos de infraestructura directamente.

---

## 1. Objetivo y alcance del proyecto

El objetivo primordial de **DocLayout-Preserve** es resolver el problema de la pérdida de formato, desbordamiento de texto y desalineación visual que ocurre comúnmente al traducir, resumir, anonimizar o transformar documentos formateados.

El usuario proporciona un documento de origen (PDF nativo, PDF escaneado o DOCX) y parámetros de transformación (por ejemplo, traducción de idioma con nivel de estilo, anonimización de datos o reescritura asistida por IA). El sistema procesa el documento y genera:

1. **Documento Reconstruido de Alta Fidelidad**: Documento final en el mismo formato de origen (o exportable a PDF/DOCX) con el contenido transformado, donde los textos se adaptan dinámicamente a sus cuadros delimitadores (*bounding boxes*) originales sin desbordamientos ni superposiciones.
2. **Preservación Integral de Elementos**: Tablas, imágenes, diagramas, encabezados, pies de página, firmas, alineaciones, fuentes y sangrías permanecen en su posición exacta.
3. **Reporte de Calidad de Maquetación**: Métricas visuales de fidelidad espacial (IoU de bounding boxes, detección de colisiones, análisis de similitud estructural SSIM y alertas de desbordamiento tipográfico).

El servicio debe poder desplegarse localmente para desarrollo inmediato y horizontalmente en Kubernetes gestionado para cargas de producción asíncronas, con análisis de costos y decisiones de arquitectura formalmente documentadas.

---

## 2. Tecnología y aislamiento obligatorio

- **Lenguaje y Ecosistema**: Prioriza **Python 3.11+** para la lógica de procesamiento documental y visión. Utiliza **Streamlit** como interfaz inicial de usuario e inspección visual. Separa interfaz, API REST/gRPC y trabajadores (*workers*) de procesamiento cuando lo demande la arquitectura escalable de la sección 11. No uses Node.js a menos que sea imprescindible para renderizado de layouts basados en web; si se usa, justifícalo formalmente.
- **Entorno Virtual Estricto**:
  - Crea el entorno virtual en la raíz del proyecto en `<raíz-del-proyecto>/.venv` mediante `python -m venv .venv`. Prohibido habilitar paquetes del sistema (`--system-site-packages`).
  - Toda ejecución, prueba e instalación debe realizarse exclusivamente a través del intérprete local del entorno:
    - En Windows PowerShell: `.\.venv\Scripts\python.exe -m pip install ...` y `.\.venv\Scripts\python.exe ...`
    - En Linux/macOS: `./.venv/bin/python -m pip install ...` y `./.venv/bin/python ...`
  - Prohibido depender de la activación de variables de entorno global o scripts de activación volátiles.
- **Cachés y Aislamiento de Instalación**:
  - Mantén las cachés de herramientas dentro del proyecto (ej. `.cache/pip`, `.cache/pytest`). Configura `PIP_CACHE_DIR` hacia esta ruta local.
  - Prohibido instalar paquetes globalmente, usar el flag `--user`, modificar el `PATH` persistente del sistema o escribir configuraciones fuera del workspace.
  - Redirige temporales de instalación a una carpeta local `.tmp/`.
  - Esta regla aplica rigurosamente a Terraform, clientes de Kubernetes (`kubectl`, `helm`), utilidades de contenedor y analizadores de seguridad en la estación del desarrollador: se emplean binarios locales o runners de CI con versiones estrictamente fijadas.
- **Versionado Reproducible**: Registra versiones exactas de dependencias en archivos de requerimientos o bloqueos (`requirements.txt`, `requirements-dev.txt` con hashes SHA si aplica).
- **Red y Puertos Locales**: En modo local, los servicios deben escuchar únicamente en `127.0.0.1` (puerto predeterminado 8501 para Streamlit, 8000 para API local). En Kubernetes, la exposición será exclusivamente a través de Services, Ingress y NetworkPolicies con TLS obligatorio.

---

## 3. Conexión con Gemini y Modelos Multimodales

- **SDK Oficial**: Emplea el SDK oficial de Python `google-genai` conectado a Gemini Developer API. Consulta la documentación vigente de Google AI Studio / Gemini API. Prohibido usar librerías obsoletas (`google-generativeai`) o inventar nombres de modelos.
- **Gestión Segura de Credenciales**:
  - Lee `GEMINI_API_KEY` desde variables de entorno o archivos `.env` locales.
  - En producción, inyéctala dinámicamente mediante un gestor de secretos (Secrets Store CSI Driver, HashiCorp Vault, AWS Secrets Manager o GCP Secret Manager) con permisos mínimos de lectura en tiempo de ejecución.
  - El repositorio debe incluir un archivo `.env.example` sin valores sensibles. Prohibido commitear credenciales, almacenarlas en logs, mostrarlas en la interfaz o incluirlas en imágenes de contenedor o planes de Terraform.
- **Selección de Modelos**: Permite configurar el modelo principal a través de `GEMINI_MODEL` (ej. `gemini-2.5-flash` o `gemini-2.5-pro` según requerimientos de visión y ventana de contexto). Los cambios de modelo deben realizarse mediante configuración externa sin alterar el código fuente.
- **Procesamiento de Documentos e Imágenes**:
  - Utiliza las capacidades nativas multimodales de Gemini para el análisis visual de maquetación compleja, diagramas y transcripción de tablas difíciles.
  - Para documentos sensibles, informa explícitamente en la interfaz que el contenido o las extracciones visuales se transmitirán al endpoint seguro de Google para su procesamiento de IA.
- **Pruebas sin Consumo de Cuota**: Durante pruebas unitarias, de integración o comprobaciones periódicas en CI/CD, utiliza *mocks* y clientes simulados de Gemini. Prohibido realizar llamadas reales a la API durante pipelines automatizados de CI para evitar cobros innecesarios o fallos por límites de tasa.

---

## 4. Interfaz de Usuario y Experiencia (Streamlit & Visualización)

Desarrolla una interfaz en español, funcional y moderna en Streamlit que proporcione:

1. **Panel de Ingesta y Carga**:
   - Selector y zona de arrastre para archivos PDF nativos, PDF escaneados o documentos DOCX.
   - Parámetros de transformación: selección de tarea (traducción a idioma destino, reescritura de estilo, resumen estructurado o anonimización) e instrucciones complementarias de diseño.
2. **Visualizador Comparativo de Diseño (Side-by-Side)**:
   - Panel izquierdo: Vista previa del documento original con opción de alternar capas de detección de diseño (bounding boxes de títulos, párrafos, tablas e imágenes identificadas por colores).
   - Panel derecho: Vista previa en tiempo real del documento reconstruido, permitiendo verificar visualmente la fidelidad de maquetación y la adaptación tipográfica.
3. **Métricas de Control de Calidad Visual**:
   - Semáforo de advertencias: desbordamientos de texto detectados, truncamientos o solapamientos.
   - Puntuación de fidelidad de maquetación (IoU promedio de cajas delimitadoras y fidelidad de fuentes).
4. **Descargas y Exportación**:
   - Descarga directa del documento reconstruido final (`.pdf`, `.docx`).
   - Descarga opcional del esquema de maquetación estructurado en formato `.json` (coordenadas, jerarquías y contenido).
5. **Gestión de Sesión e Historial**:
   - Botón «Nuevo Documento»: limpia el editor y resultados activos, pero preserva el historial de la sesión de trabajo.
   - Historial de trabajos en la parte inferior: listado ordenado cronológicamente con vistas previas, estado, parámetros y acceso a descargas sin reejecutar llamadas de IA.
   - Acciones claras y separadas: «Vaciar historial» (elimina el registro histórico) y «Limpiar toda la sesión» (purga historial, documentos en memoria y archivos temporales de disco).
   - Prevención de ejecuciones concurrentes o llamadas duplicadas mientras una transformación esté en curso.

---

## 5. Comportamiento del Flujo: Pipeline de Preservación de Maquetación

El flujo de procesamiento se ejecuta en cuatro etapas bien definidas, desacopladas e independientes:

```
┌─────────────────────────────────┐
│ Etapa 1: Ingesta y Parsing      │ ──> Extrae texto, fuentes, tamaños, imágenes,
│ (Layout Decomposition)          │     tablas y Bounding Boxes (PDF/DOCX).
└─────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│ Etapa 2: Transformación         │ ──> Procesa el contenido textual con Gemini
│ Semántica (Gemini API)          │     respetando restricciones de longitud/tono.
└─────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│ Etapa 3: Reconstrucción         │ ──> Aplica text-fitting, ajuste tipográfico
│ Tipográfica & Layout Fitting    │     adaptativo y ensamblado espacial estricto.
└─────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│ Etapa 4: Verificación Visual &  │ ──> Mide IoU, solapamientos, desbordes y
│ Control de Calidad (Diff Engine)│     genera reporte de fidelidad visual.
└─────────────────────────────────┘
```

### Etapa 1: Ingesta y Descomposición de Layout (Layout Decomposition)
- Extrae la estructura del documento localmente mediante bibliotecas especializadas instaladas en `.venv` (`PyMuPDF/fitz`, `pdfplumber`, `python-docx`).
- Mapea cada elemento a un bloque con: identificador único, coordenadas espaciales exactas `(x0, y0, x1, y1)`, índice de página, familia tipográfica, tamaño, interlineado, color, alineación y orden de lectura z-index.
- Clasifica bloques semánticos: Encabezado, Párrafo, Tabla (con estructura de celdas), Lista, Imagen, Gráfico o Pie de página.
- En PDFs escaneados, activa extracción visual o OCR condicional estructurado, mapeando las palabras a cajas delimitadoras precisas.

### Etapa 2: Transformación Semántica (Gemini API)
- Envía el texto de los bloques estructurados a Gemini junto con las instrucciones del usuario y metadatos de restricción de longitud.
- Emplea esquemas estructurados (JSON Schema / Structured Outputs) para asegurar que el modelo devuelva el contenido transformado asociado unívocamente al identificador de bloque de origen.
- Instruye al modelo sobre la importancia del *espacio físico*: si el texto resultante en el idioma o estilo destino es un 30% más largo, el modelo debe priorizar la concisión para minimizar la necesidad de reducción tipográfica agresiva.
- Trata todo el texto del documento de entrada como datos no confiables (*untrusted input*): las directivas incrustadas en el documento jamás deben alterar el comportamiento del sistema ni escapar las reglas de seguridad.

### Etapa 3: Reconstrucción Tipográfica y Ajuste Espacial (Layout Reconstruction & Text Fitting)
- Reconstruye el documento en el lienzo de salida utilizando la geometría original como plantilla maestra.
- **Algoritmo de Text-Fitting Adaptativo**:
  - Si el texto transformado excede el bounding box original, el motor aplica de forma secuencial y controlada:
    1. Ajuste sutil de interletraje (*micro-tracking*).
    2. Reducción proporcional del tamaño de fuente hasta un límite mínimo configurable (ej. no menos del 75% del tamaño original).
    3. Ajuste de interlineado (*line-height*).
  - Si tras agotar la tolerancia tipográfica el texto aún desborda, emite una advertencia de desbordamiento en los metadatos del bloque para inspección.
- Reinserta imágenes, vectores y tablas en sus posiciones absolutas exactas, garantizando que bordes y celdas preserven su escala original.

### Etapa 4: Verificación Automatizada de Fidelidad (Layout Quality Engine)
- Compara la máscara visual del documento original con el renderizado final.
- Calcula métricas objetivas:
  - **IoU (Intersection over Union)** de los contenedores de texto.
  - **Detección de Colisiones**: ningún bloque de texto debe colisionar con imágenes, bordes de página u otros bloques.
  - **SSIM (Structural Similarity Index)** en elementos estáticos (gráficos, logotipos, fondos).
- Genera un reporte JSON y visual que se expone en la interfaz y en los metadatos del trabajo.

---

## 6. Documentos, Privacidad y Resiliencia

- **Validación de Archivos**: Valida tamaño máximo, número de páginas admitidas y MIME types antes de iniciar el procesamiento. Detecta archivos corruptos o protegidos por contraseña e informa al usuario con mensajes claros.
- **Privacidad y Retención Cero por Defecto**:
  - En ejecución local, los archivos temporales se limpian al cerrar la sesión o pulsar «Limpiar toda la sesión».
  - En producción, los documentos cargados y procesados se almacenan temporalmente en almacenamiento compartido (S3/GCS/MinIO) bajo cifrado en reposo (KMS) y en tránsito (TLS 1.3), asignando un TTL estricto (ej. 2 horas). Transcurrido el TTL, los objetos se purgan automáticamente mediante reglas de ciclo de vida del bucket.
- **Gestión de Errores y Límites**:
  - Manejo robusto de errores de Gemini API: cuotas agotadas (`RESOURCE_EXHAUSTED`), límites de tasa (`429`), timeouts y respuestas bloqueadas por filtros de seguridad.
  - Implementa reintentos exponenciales con *jitter* para errores transitorios, evitando reintentar errores fatales de autenticación o validación.
  - Si una etapa falla en un trabajo asíncrono, se preservan los artefactos de las etapas anteriores para permitir el reintento específico sin reprocesar etapas completadas.

---

## 7. Estructura del Repositorio y Documentación

El repositorio debe organizarse modularmente separando lógica de dominio, conectores externos, infraestructura y flujos de CI/CD:

```plaintext
DocLayout-Preserve/
├── .github/
│   ├── workflows/               # Pipelines de CI/CD, seguridad e IaC
│   └── pull_request_template.md
├── infra/
│   └── terraform/               # Módulos y entornos de Terraform
│       ├── modules/
│       │   ├── networking/
│       │   ├── k8s_cluster/
│       │   ├── storage/
│       │   └── observability/
│       └── environments/
│           ├── validation/
│           └── production/
├── k8s/                         # Manifiestos o Helm charts de Kubernetes
│   ├── base/
│   └── overlays/
│       ├── validation/
│       └── production/
├── monitoring/                  # Dashboards de Grafana, alertas y reglas Prometheus
│   ├── dashboards/
│   └── alerts/
├── src/
│   └── doclayout/
│       ├── __init__.py
│       ├── core/                # Modelos de datos, configuración y contratos
│       ├── ingestion/           # Extracción y parsing de PDF/DOCX
│       ├── llm/                 # Cliente Gemini (google-genai) y prompts
│       ├── reconstruction/      # Motores de renderizado y text-fitting
│       ├── verification/        # Motores de cálculo de IoU, SSIM y reportes
│       ├── api/                 # API REST/gRPC para orquestación asíncrona
│       └── ui/                  # Interfaz de usuario con Streamlit
├── tests/
│   ├── unit/
│   ├── integration/
│   └── mocks/                   # Respuestas simuladas de Gemini
├── .cache/                      # Cachés locales ignoradas en Git
├── .gitignore
├── .env.example
├── project.md                   # Requisitos maestros y especificación técnica
└── README.md                    # Guía completa de uso, instalación y arquitectura
```

### Documentación Requerida
- `README.md` en español con: propósito, arquitectura global, guía de inicio rápido en Windows PowerShell (usando `.venv`), configuración de variables, ejecución de pruebas, solución de incidencias y guía de contribución.
- Instrucciones precisas de comandos de PowerShell para desarrolladores en Windows, acompañadas de comandos equivalentes para Linux/macOS.

---

## 8. Validación, Pruebas y Entrega

- **Estrategia de Testing**:
  - Pruebas unitarias de parsing, extracción geométrica, algoritmos de text-fitting y validación de esquemas.
  - Pruebas de integración con un cliente Gemini completamente simulado (`tests/mocks/`) que verifique la comunicación con la API sin incurrir en costos ni depender de conectividad externa.
  - Pruebas de fidelidad visual automatizadas con documentos de muestra (`samples/`).
- **Verificación Local**:
  - El servidor local debe iniciar limpiamente con `.\.venv\Scripts\python.exe -m streamlit run src/doclayout/ui/app.py` en `127.0.0.1:8501`.
  - Si falta `GEMINI_API_KEY`, la UI debe cargar con normalidad y advertir visualmente del requisito antes de permitir el procesamiento real.
- **Entregables**: Al completar cada fase o feature, se debe documentar el estado, las pruebas ejecutadas, la evidencia de éxito y las dependencias resueltas.

---

## 9. Repositorio y Estrategia de Ramas

- **Control de Versiones y Alojamiento**: Repositorio central alojado en GitHub con GitHub Actions como orquestador de CI/CD.
- **Definición de Ramas Maestras**:
  - `main`: Rama troncal del proyecto. Contiene el código fuente probado y validado por revisiones de pares y pipelines de CI. Puede contener funcionalidades listas aún no liberadas a producción.
  - `dev`: **Refleja exclusivamente el commit efectivamente desplegado y validado en producción**. Queda terminantemente prohibido utilizar `dev` como rama de integración previa, rama de staging o rama de pruebas inestables.
- **Estrategia de Ramas Feature**:
  - Las ramas de trabajo deben crearse a partir de `main` actualizado bajo la convención estricta:
    `Feature<Número><NombreDelDesarrollo>`
    Ejemplos: `Feature1ParserPDF`, `Feature2PipelineCICD`, `Feature3InfraestructuraTerraform`, `Feature4MotorAjusteTipografico`.
  - El nombre debe utilizar números enteros consecutivos y PascalCase sin espacios, guiones ni tildes.
  - El pipeline de CI validará mediante expresiones regulares que toda rama o Pull Request cumpla obligatoriamente con esta nomenclatura.
- **Flujo de Integración y Promoción**:
  1. La rama `Feature` se desarrolla, prueba localmente y se envía como Pull Request hacia `main`.
  2. El Pull Request ejecuta la batería exhaustiva de validaciones de CI (linting, tests, análisis estático, escaneo de seguridad).
  3. Tras revisión obligatoria de código y superación de checks automáticos, se realiza merge hacia `main`. La rama feature se elimina automáticamente.
  4. La publicación a producción se activa sobre un commit específico de `main` a través del workflow autorizado de CD.
  5. Una vez desplegado y superadas las comprobaciones de salud en producción, el workflow de CD actualiza automáticamente la rama `dev` hacia ese mismo commit exacto mediante automatización auditada.
- **Rollback y Sincronización**: Si una publicación falla o se ordena un rollback, el sistema revierte la versión en producción y sincroniza `dev` con el commit restaurado, conservando trazabilidad auditable en los registros de despliegue.

---

## 10. CI/CD con GitHub Actions

- **Estructura de Workflows**:
  - `ci-validation.yml`: Ejecuta en cada PR hacia `main` la verificación de formato de rama, instalación reproducible en runner efímero, formateo (`black`, `isort`), linters (`flake8`, `ruff`), análisis de tipos (`mypy`), pruebas con mocks y linters de IaC (`terraform fmt`, `tflint`).
  - `ci-security.yml`: Análisis SAST, detección de secretos, auditoría de dependencias (`pip-audit`) y escaneo de vulnerabilidades en IaC y contenedores (`trivy`).
  - `cd-publish-image.yml`: Construye imágenes de contenedor Open Container Initiative (OCI) con tags inmutables (SHA de commit), genera SBOM (Software Bill of Materials en formato SPDX/CycloneDX), evidencia de procedencia (SLSA) y firma la imagen con Cosign.
  - `cd-deploy.yml`: Orquesta el despliegue hacia entornos gestionados utilizando **GitHub Environments** separados (`validation` y `production`), con puertas de aprobación manual para producción.
- **Autenticación sin Claves Persistentes (OIDC)**:
  - Utiliza OpenID Connect (OIDC) para autenticar los runners de GitHub Actions contra los proveedores de nube (AWS/GCP/Azure). Prohibido almacenar claves estáticas de larga duración en los secretos del repositorio.
  - Restringe permisos al mínimo indispensable (`least privilege`) en el token `GITHUB_TOKEN`.
- **Inmutabilidad y Promoción**:
  - La misma imagen OCI validada y firmada en el pipeline es la que se promueve a producción. Prohibido reconstruir imágenes entre entornos.
- **Post-Deploy Smoke Tests**: Inmediatamente tras el despliegue en producción, se ejecutan verificaciones sintéticas de salud y pruebas de humo con mocks de Gemini para certificar la estabilidad del servicio antes de actualizar la rama `dev`.

---

## 11. Arquitectura y Escalabilidad con Kubernetes

- **Plataforma Objetivo**: Clúster de Kubernetes gestionado (EKS, GKE o AKS) configurado mediante decisiones de arquitectura que balanceen disponibilidad, seguridad y presupuesto operativo.
- **Desacoplamiento de Servicios**:
  - **Servicio Web / UI (Streamlit)**: Pods encargados de la interacción visual, autenticación de usuarios y visualización de comparativas.
  - **Job API Gateway (FastAPI)**: Punto de entrada RESTful para recepción de documentos, validación y consulta de estado de trabajos.
  - **Async Processing Workers**: Pods dedicados exclusivamente al cómputo intensivo de layout, invocación a Gemini y reconstrucción documental.
  - **Cola de Mensajes & Broker**: Cola asíncrona (Redis Streams, RabbitMQ o Cloud Pub/Sub) para distribución de trabajos pesados sin bloquear la interfaz.
- **Externalización y Persistencia de Estado**:
  - Almacenamiento de objetos compatible con S3 para documentos fuente, artefactos intermedios y resultados finales, protegido con cifrado y ciclo de vida (TTL).
  - Estado de trabajos y metadatos persistido en almacenamiento en memoria o base de datos externa con aislamiento estricto por usuario y sesión.
- **Autoscaling y Resiliencia**:
  - Configura **HPA (Horizontal Pod Autoscaler)** en los workers basado en la longitud y tiempo de espera de la cola de procesamiento, además de métricas estándar de CPU y memoria.
  - Configuración de `requests` y `limits` explícitos en todos los pods, asegurando compatibilidad con el autoscaler de nodos del clúster.
  - `PodDisruptionBudgets` (PDB) para garantizar que las actualizaciones del clúster no interrumpan el servicio.
  - Sondeos de salud configurados rigurosamente: `startupProbe`, `readinessProbe` y `livenessProbe`, con terminación ordenada (*graceful shutdown*) para permitir que los workers finalicen trabajos en curso antes de apagarse.
- **Protección de Cuotas**: Lógica de limitación de tasa (*rate limiting*) distribuida para garantizar que el escalado de workers nunca sobrepase los límites de cuota de Gemini Developer API.

---

## 12. Infraestructura como Código (IaC) con Terraform

- **Definición Declarativa de Recursos**:
  - Todos los recursos en la nube (VPC, subredes públicas/privadas, clúster de Kubernetes, registros de contenedores ECR/GAR, buckets de almacenamiento, identidades IAM y roles RBAC) se definen formalmente en Terraform.
- **Diseño Modular y Multientorno**:
  - Módulos reutilizables en `infra/terraform/modules/`.
  - Entornos separados e independientes en `infra/terraform/environments/validation/` y `infra/terraform/environments/production/`.
  - Versiones de Terraform y proveedores estrictamente fijadas con archivo `.terraform.lock.hcl` versionado en el repositorio.
- **Gestión de Estado Remoto Seguro**:
  - Backend remoto con cifrado en reposo, versionado y control de bloqueo concurrente (*state locking* mediante DynamoDB o equivalente de almacenamiento en la nube).
  - Procedimientos de recuperación y control de acceso restringido al estado. Prohibido almacenar el estado o planes con datos sensibles en el control de versiones Git.
- **Pipeline de Terraform en CI**:
  - Los Pull Requests ejecutan únicamente `terraform fmt -check`, `terraform validate` y `terraform plan` con fines de revisión y análisis de seguridad (`tfsec`/`checkov`).
  - Prohibido ejecutar `terraform apply` o `destroy` desde ramas de características o Pull Requests no aprobados. El `apply` solo se ejecuta en la rama troncal con aprobación previa.
- **Detección de Drift, Control de Costos y Política de Destrucción Total (Cero Recursos / Cero Tokens)**:
  - Tareas programadas periódicas de detección de desvíos (*drift detection*).
  - Etiquetas (*tags*) obligatorias en todos los recursos: `Project = DocLayout-Preserve`, `Environment`, `ManagedBy = Terraform`, `CostCenter`.
  - Alertas presupuestarias configuradas en la cuenta de GCP antes de aprovisionar infraestructura activa.
  - **Regla Mandatoria de Teardown Total (Costo Cero / Tokens Cero)**: El comando o workflow de `destroy` debe desaprovisionar y purgar absolutamente todos los recursos creados en Google Cloud Platform (GKE, VPC, subredes, balanceadores, discos persistentes, IPs estáticas, secretos y buckets de Cloud Storage —forzando el vaciado previo de objetos y versiones—).
  - La destrucción debe garantizar que no queden recursos residuales o huérfanos facturando y que el consumo de tokens y llamadas a Gemini API quede **estrictamente en CERO (0)**.

---

## 13. Observabilidad, Monitoreo y Ciberseguridad

### Observabilidad y Monitoreo (Grafana Stack)
- **Monitoreo Unificado**:
  - Dashboards centralizados en **Grafana** alimentados por métricas de **Prometheus**.
  - Logs centralizados y estructurados en formato JSON (Loki o FluentBit).
  - Trazabilidad distribuida con instrumentación compatible con **OpenTelemetry** para seguir el ciclo de vida de un documento a través de la UI, la API, la cola y los workers.
- **Métricas Clave de Rendimiento (SLIs / SLOs)**:
  - Latencia y duración por etapa (tiempo de parsing, tiempo de llamada a Gemini, tiempo de ajuste tipográfico y tiempo de verificación visual).
  - Profundidad, tasa de encolamiento y antigüedad de mensajes en la cola de trabajos.
  - Tasa de errores por bloque y frecuencia de desbordamientos tipográficos detectados.
  - Consumo de tokens, latencia promedio y distribución de códigos de respuesta de Gemini API.
  - Métricas de infraestructura: uso de CPU/memoria por contenedor, réplicas activas de HPA y estado de nodos de Kubernetes.
- **Reglas de Alerta y Runbooks**:
  - Alertas configuradas para: saturación de colas, agotamiento de cuota de API, tasa elevada de errores 5xx, caída de workers y fallos de comprobación de salud.
  - Cada alerta versionada debe enlazar a su correspondiente *runbook* operativo con los pasos precisos de remediación.
- **Privacidad Estricta en Telemetría**:
  - Prohibido registrar contenido de texto extraído de documentos, nombres personales, credenciales o imágenes en logs, trazas o métricas.
  - Emplea identificadores de correlación anónimos (*Job ID* / *Trace ID*) para el seguimiento de extremo a extremo sin exponer PII.

### Ciberseguridad y DevSecOps
- **Hardening de Contenedores**:
  - Las imágenes de contenedor deben ejecutarse obligatoriamente bajo un usuario sin privilegios (`non-root`, UID > 10000).
  - Sistema de archivos raíz de solo lectura (`read-only root filesystem`), montando directorios temporales `.tmp/` como `emptyDir` en memoria.
  - Eliminación de todas las capacidades innecesarias de Linux (`drop: ALL`).
- **Seguridad en Red y Clúster**:
  - `NetworkPolicies` de Kubernetes por defecto en modo denegar todo (`deny-all`), habilitando únicamente el tráfico explícito entre UI, API, workers y almacenes de datos.
  - Todo tráfico HTTP público debe redireccionarse a HTTPS mediante TLS 1.3 gestionado en el Ingress controller.
  - Roles de Kubernetes (RBAC) y roles de servicio cloud acotados estrictamente mediante ServiceAccounts individuales por componente.
- **Escaneos Automatizados en el Pipeline**:
  - **SAST**: Análisis estático de vulnerabilidades en código fuente.
  - **SCA**: Análisis de vulnerabilidades en dependencias externas.
  - **Secret Scanning**: Detección continua de claves o credenciales filtradas (Gitleaks).
  - **Container Scanning**: Escaneo de imágenes antes de su publicación hacia el registro para bloquear vulnerabilidades críticas (CVEs).

---

## 14. Criterios de Aceptación de la Plataforma (Definition of Done)

Para considerar completada y validada cualquier entrega del proyecto, se deben cumplir rigurosamente los siguientes criterios:

1. **Estrategia de Ramas Cumplida**: Todo cambio arriba a `main` mediante Pull Request originado en una rama con formato válido `Feature<Número><NombreDelDesarrollo>`, con revisión aprobada y todos los checks de CI en verde.
2. **Sincronización Auditable de `dev`**: El pipeline de CD actualiza `dev` única y automáticamente tras certificar que el despliegue en producción superó las pruebas de humo post-despliegue.
3. **Reproducibilidad y Aislamiento Local**: El entorno local se construye en Windows PowerShell y Linux exclusivamente con `.venv` y cachés locales, sin requerir privilegios de administrador ni dependencias globales en la máquina del desarrollador.
4. **Infraestructura como Código Íntegra**: Terraform reproduce los entornos de validación y producción sin pasos manuales no documentados, con estado remoto bloqueado y análisis de seguridad sin fallos críticos.
5. **Procesamiento de Documentos Funcional y Resiliente**: El pipeline procesa documentos de prueba conservando tablas, fuentes y cajas delimitadoras, aplicando text-fitting adaptativo y reportando métricas de fidelidad visual.
6. **Manejo Seguro de la API**: Las pruebas automatizadas utilizan mocks de Gemini sin consumir cuotas de API. En producción, las claves se inyectan mediante gestores de secretos y nunca se imprimen en logs ni pantallas.
7. **Alta Disponibilidad y Autoscaling**: Múltiples réplicas de workers procesan documentos asíncronamente desde la cola sin duplicar trabajos ni mezclar sesiones de usuarios. El HPA escala adecuadamente ante incrementos de carga.
8. **Observabilidad Operativa**: Los dashboards de Grafana muestran en tiempo real la salud de la aplicación, estado de la cola y métricas de procesamiento; una falla simulada dispara la alerta correspondiente.
9. **Garantía de Destrucción Total y Costo Cero**: El ciclo de vida de infraestructura cuenta con un proceso de desaprovisionamiento (`destroy`) formalmente probado y verificado que elimina el 100% de los recursos en GCP (compute, redes, almacenamiento, balanceadores e IPs) y detiene de inmediato cualquier tráfico o consumo de tokens en Gemini API, garantizando que el costo recurrente quede estrictamente en cero ($0 / 0 tokens).

---

## 15. Referencias oficiales para el implementador

- **Google GenAI SDK para Python**: https://github.com/googleapis/python-genai
- **Document Processing con Gemini**: https://ai.google.dev/gemini-api/docs/document-processing
- **PyMuPDF (fitz) Documentation**: https://pymupdf.readthedocs.io/
- **python-docx Documentation**: https://python-docx.readthedocs.io/
- **GitHub Actions Documentation**: https://docs.github.com/actions
- **Terraform Language Documentation**: https://developer.hashicorp.com/terraform/docs
- **Kubernetes Official Documentation**: https://kubernetes.io/docs/
- **Grafana Observability Documentation**: https://grafana.com/docs/
- **Prometheus Monitoring Documentation**: https://prometheus.io/docs/
- **OpenTelemetry Python SDK**: https://opentelemetry.io/docs/languages/python/

*Consulta siempre la documentación oficial vigente antes de implementar interfaces, esquemas de llamadas o módulos de infraestructura.*

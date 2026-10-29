# Política de Seguridad, Severidades y Remediación — DocLayout-Preserve

Este documento versiona los umbrales obligatorios de bloqueo en CI/CD, endurecimiento de contenedores y gestión de excepciones conforme a la Sección 13 de [`project.md`](./project.md).

---

## 1. Umbrales de Bloqueo en Pipeline (`ci-security.yml`)

| Control | Herramienta | Umbral que Bloquea PR / Release | Alcance |
| :--- | :--- | :--- | :--- |
| **Detección de Secretos** | `Gitleaks` | **Cualquier hallazgo (1+)** | Historial Git completo y árbol de trabajo |
| **SAST (Código Python)** | `Bandit` | Severidad **MEDIUM, HIGH o CRITICAL** (`-ll -ii`) | `src/doclayout/` |
| **SCA (Dependencias)** | `pip-audit` | Cualquier CVE conocida con parche (`--strict`) | `requirements.txt` |
| **IaC & Manifiestos** | `Trivy Config` | Severidad **HIGH o CRITICAL** | `Dockerfile`, `k8s/`, `infra/terraform/` |
| **Imagen OCI** | `Trivy Image` | Severidad **HIGH o CRITICAL** (`ignore-unfixed: true`) | Imagen de runtime multi-stage |

---

## 2. Reglas de Endurecimiento de Workflows e Imágenes

1. **Pinning Inmutable de GitHub Actions**: Toda acción externa (`uses:`) debe fijarse a un hash SHA completo de 40 caracteres hexadecimales.
2. **Privilegio Mínimo en `GITHUB_TOKEN`**: Por defecto `permissions: contents: read`. Ningún Pull Request no confiable recibe credenciales OIDC ni secretos de producción.
3. **Cero Consumo de Cuota en CI/Seguridad**: Los workflows fuerzan `GEMINI_API_KEY=""` y ejecutan exclusivamente clientes simulados (`MockGeminiClient`).
4. **Contenedores Non-Root**: La imagen OCI ejecuta obligatoriamente bajo UID/GID `10001:10001` sin shell interactiva (`/usr/sbin/nologin`) y excluye `.env`, `private/` y credenciales mediante `.dockerignore`.

---

## 3. Gestión Auditada de Excepciones

Cualquier excepción temporal ante un falso positivo o vulnerabilidad sin parche upstream requiere Pull Request aprobado por `CODEOWNERS` documentando:
- **Identificador CVE / Regla**
- **Justificación técnica y mitigación compensatoria**
- **Responsable asignado**
- **Fecha máxima de vencimiento (máximo 30 días calendario)**

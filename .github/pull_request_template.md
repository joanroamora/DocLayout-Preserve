## Resumen del Cambio

- **Rama Feature:** `Feature<Número><NombreDelDesarrollo>`
- **Objetivo funcional / técnico:**

---

## Lista de Verificación Obligatoria (DoD)

- [ ] La rama cumple estrictamente la nomenclatura `Feature<Número><NombreEnPascalCase>`.
- [ ] Aislamiento verificado: todas las dependencias y pruebas corren en `.venv` con cachés en `.cache/`.
- [ ] `ruff`, `flake8`, `black --check`, `isort --check-only` y `mypy` pasan con **0 errores**.
- [ ] Las pruebas automatizadas (`pytest`) usan `MockGeminiClient` con **0 consumo de tokens**.
- [ ] Ningún secreto, clave API ni JSON de Service Account fue incluido en commits, logs o artefactos.
- [ ] `stages.md` fue actualizado reflejando el progreso correspondiente.

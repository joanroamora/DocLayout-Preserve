"""Repository governance validators (Feature branch naming convention)."""

from __future__ import annotations

import re
import sys

FEATURE_BRANCH_PATTERN = re.compile(r"^Feature[1-9][0-9]*[A-Z][a-zA-Z0-9]*$")
PROTECTED_BRANCHES = frozenset({"main", "dev"})


def validate_feature_branch_name(branch_name: str, allow_protected: bool = False) -> bool:
    """Return True if branch_name matches Feature<PositiveInt><PascalCaseName> without diacritics."""
    cleaned = branch_name.strip()
    if cleaned.startswith("refs/heads/"):
        cleaned = cleaned[len("refs/heads/") :]

    if allow_protected and cleaned in PROTECTED_BRANCHES:
        return True

    # Ensure strictly ASCII alphanumeric (no spaces, hyphens, underscores, or accents/tildes)
    if not cleaned.isascii():
        return False
    return bool(FEATURE_BRANCH_PATTERN.fullmatch(cleaned))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point invoked by GitHub Actions to enforce Feature branch nomenclature."""
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        sys.stderr.write("Uso: python -m doclayout.core.governance <nombre_de_rama>\n")
        return 2

    branch = args[0]
    allow_prot = "--allow-protected" in args[1:]
    if validate_feature_branch_name(branch, allow_protected=allow_prot):
        sys.stdout.write(f"[OK] Rama válida conforme a la convención: {branch}\n")
        return 0

    sys.stderr.write(
        f"[ERROR] Nombre de rama inválido: '{branch}'. "
        "Debe cumplir estrictamente el patrón Feature<Número><NombreEnPascalCase> "
        "(sin espacios, guiones ni tildes; ej. Feature1EntornoYScaffolding).\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

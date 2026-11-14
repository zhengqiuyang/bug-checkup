"""Consistency probe for instructor's provider/mode registry (v2/core/registry.py + provider_specs.py).

The spec tables are the single source of truth: a mode a provider advertises must resolve to a
registered handler, otherwise the first caller of that combination gets a KeyError even though
the API says the mode is supported. These invariants are cheap to check and need no provider SDK.

1. every ``supported_modes`` entry resolves through the registry (handler or lazy loader);
2. no mode is both supported and unsupported;
3. every legacy alias normalizes to a mode the same provider supports;
4. every provider with a handler module points at a module that exists;
5. the public factory bindings name attributes that the client module can expose (module exists).

Standalone script (no pytest); exit 0 PASS / 1 FAIL / 2 INCONCLUSIVE / 3 CHECKER BUG.
"""

from __future__ import annotations

from pathlib import Path

import instructor as _instructor

_ROOT = Path(_instructor.__file__).parent.parent
from instructor.v2.core.provider_specs import (
    ALIAS_TO_PROVIDER,
    HANDLER_SPECS,
    PROVIDER_SPECS,
    PUBLIC_FACTORY_ATTRS,
)
from instructor.v2.core.registry import mode_registry, normalize_mode


def check_supported_modes_resolve(failures: list[str]) -> None:
    for provider, spec in PROVIDER_SPECS.items():
        for mode in spec.supported_modes:
            if not mode_registry.is_registered(provider, mode):
                failures.append(f"{provider.name}.{mode.name} is advertised but not registered")
    for provider, (module_path, modes) in HANDLER_SPECS.items():
        spec = PROVIDER_SPECS.get(provider)
        if spec is None:
            failures.append(f"HANDLER_SPECS has {provider.name} with no provider spec")
            continue
        for mode in modes:
            if mode not in spec.supported_modes:
                failures.append(
                    f"HANDLER_SPECS registers {provider.name}.{mode.name} but the spec does not support it"
                )


def check_supported_disjoint(failures: list[str]) -> None:
    for provider, spec in PROVIDER_SPECS.items():
        overlap = set(spec.supported_modes) & set(spec.unsupported_modes)
        if overlap:
            names = sorted(mode.name for mode in overlap)
            failures.append(f"{provider.name}: modes both supported and unsupported: {names}")


def check_legacy_aliases(failures: list[str]) -> None:
    for provider, spec in PROVIDER_SPECS.items():
        # Alias providers (canonical_provider != provider) are legacy spellings: their modes are
        # validated against the canonical provider's spec, which owns the handlers.
        owner = PROVIDER_SPECS.get(spec.canonical_provider, spec)
        supported = set(owner.supported_modes)
        for legacy, canonical in spec.legacy_modes.items():
            target = normalize_mode(provider, legacy)
            if target != canonical:
                failures.append(f"{provider.name}: normalize_mode({legacy.name}) = {target.name}, spec says {canonical.name}")
            if canonical not in supported:
                modes = sorted(mode.name for mode in supported)
                failures.append(f"{provider.name}: alias {legacy.name} -> {canonical.name}, which is not in supported_modes {modes}")


def _module_exists(module_path: str) -> bool:
    """Filesystem check: find_spec would import the parent package (and its SDK)."""
    relative = module_path.replace(".", "/")
    return (_ROOT / f"{relative}.py").is_file() or (_ROOT / relative / "__init__.py").is_file()


def check_handler_modules_exist(failures: list[str]) -> None:
    for provider, spec in PROVIDER_SPECS.items():
        if spec.handler_module is None:
            continue
        if not _module_exists(spec.handler_module):
            failures.append(f"{provider.name}: handler module {spec.handler_module!r} cannot be found")


def check_public_factories(failures: list[str]) -> None:
    for attr, (module_path, function_name) in PUBLIC_FACTORY_ATTRS.items():
        if not _module_exists(module_path):
            failures.append(f"public factory {attr!r} points at missing module {module_path!r}")
    for alias, provider in ALIAS_TO_PROVIDER.items():
        if PROVIDER_SPECS.get(provider) is None:
            failures.append(f"alias {alias!r} maps to {provider.name} with no provider spec")


def main() -> int:
    if not PROVIDER_SPECS:
        print("CHECKER BUG: provider specs are empty")
        return 3
    failures: list[str] = []
    for check in (
        check_supported_modes_resolve,
        check_supported_disjoint,
        check_legacy_aliases,
        check_handler_modules_exist,
        check_public_factories,
    ):
        try:
            check(failures)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    if failures:
        print(f"FAIL ({len(failures)} issue(s))")
        for failure in failures[:20]:
            print(" -", failure)
        return 1
    print(
        f"PASS: {len(PROVIDER_SPECS)} provider specs, "
        f"{sum(len(s.supported_modes) for s in PROVIDER_SPECS.values())} mode bindings consistent"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

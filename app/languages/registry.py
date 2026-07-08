"""Language definitions: how to stage and invoke each language.

M1: Python only, using the host toolchain bind-mounted read-only.
M3 will extend this with Piston-package toolchains (C/C++, Node).
"""
from dataclasses import dataclass, replace

from ..executor.limits import Limits


@dataclass(frozen=True)
class LanguageDef:
    name: str
    main_file: str
    run_args: list[str]           # argv; {main} replaced with main file name
    compile_args: tuple[str, ...] | None = None  # if set, two-phase compile+run
    run_memory: str | None = None      # override run-step memory.max
    compile_memory: str = "512M"       # compile-step memory.max (looser)
    compile_timeout_s: float | None = None  # override compile-step wall timeout
    compile_cache: str | None = None         # persistent build-cache name (at /cache)
    run_env: tuple[tuple[str, str], ...] = ()      # extra env for the run step
    compile_env: tuple[tuple[str, str], ...] = ()  # extra env for the compile step


@dataclass(frozen=True)
class Prepared:
    run_args: list[str]
    compile_args: list[str] | None
    run_limits: Limits
    compile_limits: Limits | None
    run_env: dict[str, str]
    compile_env: dict[str, str]
    compile_cache: str | None = None


_LANGUAGES = {
    "python": LanguageDef(
        name="python",
        main_file="main.py",
        # -E -s (not -I): ignore PYTHON* env vars and user site-packages, but keep
        # the script dir (/src) on sys.path so multi-file programs can import
        # sibling modules. Full isolation still comes from the gVisor sandbox.
        run_args=["/usr/bin/python3", "-E", "-s", "/src/{main}"],
    ),
    "javascript": LanguageDef(
        name="javascript",
        main_file="main.js",
        run_args=["/usr/bin/node", "/src/{main}"],
    ),
    "ruby": LanguageDef(
        name="ruby",
        main_file="main.rb",
        run_args=["/usr/bin/ruby", "/src/{main}"],
    ),
    "cpp": LanguageDef(
        name="cpp",
        main_file="main.cpp",
        compile_args=("/usr/bin/g++", "/src/{main}", "-O2", "-std=c++17",
                      "-o", "/out/prog"),
        run_args=["/out/prog"],
    ),
    "rust": LanguageDef(
        name="rust",
        main_file="main.rs",
        compile_args=("/usr/bin/rustc", "/src/{main}", "-O", "-o", "/out/prog"),
        run_args=["/out/prog"],
    ),
    "go": LanguageDef(
        name="go",
        main_file="main.go",
        # -C /src builds the whole /src package (all .go files) so multi-file Go
        # works; "." is the package in that dir. Single-file is just main.go.
        compile_args=("/usr/bin/go", "build", "-C", "/src", "-o", "/out/prog", "."),
        run_args=["/out/prog"],
        compile_memory="2G",
        compile_timeout_s=30.0,
        compile_cache="go",   # persistent GOCACHE at /cache, warm after 1st build
        compile_env=(
            ("GOROOT", "/usr/lib/go-1.22"), ("GOCACHE", "/cache"),
            ("GOPATH", "/tmp/go"), ("GO111MODULE", "off"),
            ("CGO_ENABLED", "0"), ("GOTOOLCHAIN", "local"),
            ("GOMAXPROCS", "1"), ("GOFLAGS", "-p=1"),
        ),
    ),
}


def get_language(name: str) -> LanguageDef:
    if name not in _LANGUAGES:
        raise KeyError(f"unsupported language: {name!r} "
                       f"(available: {', '.join(sorted(_LANGUAGES))})")
    return _LANGUAGES[name]


def list_languages() -> list[str]:
    return sorted(_LANGUAGES)


def resolve(lang: LanguageDef, base: Limits) -> Prepared:
    """Map a LanguageDef to a concrete execution, applying its declarative
    per-language limit and env overrides. compile_* are None for interpreted
    languages. Single place that maps a LanguageDef to an execution."""
    run_args = [a.replace("{main}", lang.main_file) for a in lang.run_args]
    run_limits = replace(base, memory_max=lang.run_memory) if lang.run_memory else base
    if not lang.compile_args:
        return Prepared(run_args, None, run_limits, None,
                        dict(lang.run_env), {})
    compile_args = [a.replace("{main}", lang.main_file) for a in lang.compile_args]
    overrides = {"memory_max": lang.compile_memory}
    if lang.compile_timeout_s:
        overrides["compile_timeout_s"] = lang.compile_timeout_s
    compile_limits = replace(base, **overrides)
    return Prepared(run_args, compile_args, run_limits, compile_limits,
                    dict(lang.run_env), dict(lang.compile_env),
                    lang.compile_cache)

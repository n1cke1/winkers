"""Language profiles for tree-sitter parsing."""

from winkers.languages.base import LanguageProfile
from winkers.languages.csharp import CSharpProfile
from winkers.languages.go import GoProfile
from winkers.languages.java import JavaProfile
from winkers.languages.javascript import JavaScriptProfile
from winkers.languages.python import PythonProfile
from winkers.languages.rust import RustProfile
from winkers.languages.typescript import TypeScriptProfile

PROFILES: dict[str, LanguageProfile] = {
    p.language: p()
    for p in [
        PythonProfile,
        TypeScriptProfile,
        JavaScriptProfile,
        JavaProfile,
        GoProfile,
        RustProfile,
        CSharpProfile,
    ]
}

EXTENSION_MAP: dict[str, str] = {
    ext: profile.language
    for profile in PROFILES.values()
    for ext in profile.extensions
}


def get_profile(language: str) -> LanguageProfile | None:
    return PROFILES.get(language)


def get_profile_for_file(filename: str) -> LanguageProfile | None:
    from pathlib import Path
    ext = Path(filename).suffix
    lang = EXTENSION_MAP.get(ext)
    return PROFILES.get(lang) if lang else None

"""Java language profile."""

from winkers.languages.base import LanguageProfile


class JavaProfile(LanguageProfile):
    language = "java"
    extensions = [".java"]
    tree_sitter_language = "java"

    function_query = """
    [
      (method_declaration
        name: (identifier) @fn.name
        parameters: (formal_parameters) @fn.params
        body: (block) @fn.body) @fn.def

      (constructor_declaration
        name: (identifier) @fn.name
        parameters: (formal_parameters) @fn.params
        body: (constructor_body) @fn.body) @fn.def
    ]
    """

    call_query = """
    (method_invocation
      name: (identifier) @call.name)
    """

    import_query = """
    (import_declaration
      (scoped_identifier) @imp.source)
    """

    export_query = ""

    def is_exported(self, function_name: str, file_imports: list[dict]) -> bool:
        return any(
            imp.get("type") == "export" and imp.get("name") == function_name
            for imp in file_imports
        )

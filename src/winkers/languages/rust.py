"""Rust language profile."""

from winkers.languages.base import LanguageProfile


class RustProfile(LanguageProfile):
    language = "rust"
    extensions = [".rs"]
    tree_sitter_language = "rust"

    function_query = """
    [
      (function_item
        name: (identifier) @fn.name
        parameters: (parameters) @fn.params
        return_type: (_)? @fn.return_type
        body: (block) @fn.body) @fn.def

      (impl_item
        body: (declaration_list
          (function_item
            name: (identifier) @fn.name
            parameters: (parameters) @fn.params
            return_type: (_)? @fn.return_type
            body: (block) @fn.body) @fn.def))
    ]
    """

    call_query = """
    [
      (call_expression
        function: (identifier) @call.name)
      (call_expression
        function: (scoped_identifier
          name: (identifier) @call.name))
      (call_expression
        function: (field_expression
          value: (_) @call.object
          field: (field_identifier) @call.attr))
    ]
    """

    import_query = """
    (use_declaration
      argument: (_) @imp.source)
    """

    export_query = ""

    def is_exported(self, function_name: str, file_imports: list[dict]) -> bool:
        return any(
            imp.get("type") == "export" and imp.get("name") == function_name
            for imp in file_imports
        )

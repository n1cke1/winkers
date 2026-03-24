"""Go language profile."""

from winkers.languages.base import LanguageProfile


class GoProfile(LanguageProfile):
    language = "go"
    extensions = [".go"]
    tree_sitter_language = "go"

    function_query = """
    [
      (function_declaration
        name: (identifier) @fn.name
        parameters: (parameter_list) @fn.params
        result: (_)? @fn.return_type
        body: (block) @fn.body) @fn.def

      (method_declaration
        name: (field_identifier) @fn.name
        parameters: (parameter_list) @fn.params
        result: (_)? @fn.return_type
        body: (block) @fn.body) @fn.def
    ]
    """

    call_query = """
    (call_expression
      function: [
        (identifier) @call.name
        (selector_expression
          operand: (_) @call.object
          field: (field_identifier) @call.attr)
      ])
    """

    import_query = """
    (import_spec
      path: (interpreted_string_literal) @imp.source)
    """

    export_query = ""

    def is_exported(self, function_name: str, file_imports: list[dict]) -> bool:
        # Go: exported if name starts with uppercase
        return bool(function_name) and function_name[0].isupper()

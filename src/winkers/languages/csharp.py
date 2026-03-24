"""C# language profile."""

from winkers.languages.base import LanguageProfile


class CSharpProfile(LanguageProfile):
    language = "csharp"
    extensions = [".cs"]
    tree_sitter_language = "csharp"

    function_query = """
    (method_declaration
      returns: (_) @fn.return_type
      name: (identifier) @fn.name
      parameters: (parameter_list) @fn.params
      body: (block) @fn.body) @fn.def
    """

    call_query = """
    (invocation_expression
      function: [
        (identifier) @call.name
        (member_access_expression
          name: (identifier) @call.attr)
      ])
    """

    import_query = """
    (using_directive
      [(identifier) (qualified_name)] @imp.source)
    """

    export_query = ""

    def is_exported(self, function_name: str, file_imports: list[dict]) -> bool:
        return any(
            imp.get("type") == "export" and imp.get("name") == function_name
            for imp in file_imports
        )

"""TypeScript language profile."""

from winkers.languages.base import LanguageProfile


class TypeScriptProfile(LanguageProfile):
    language = "typescript"
    extensions = [".ts", ".tsx"]
    tree_sitter_language = "typescript"

    function_query = """
    [
      (function_declaration
        name: (identifier) @fn.name
        parameters: (formal_parameters) @fn.params
        return_type: (type_annotation)? @fn.return_type
        body: (statement_block) @fn.body) @fn.def

      (export_statement
        (function_declaration
          name: (identifier) @fn.name
          parameters: (formal_parameters) @fn.params
          return_type: (type_annotation)? @fn.return_type
          body: (statement_block) @fn.body)) @fn.def

      (method_definition
        name: (property_identifier) @fn.name
        parameters: (formal_parameters) @fn.params
        return_type: (type_annotation)? @fn.return_type
        body: (statement_block) @fn.body) @fn.def

      (arrow_function
        parameters: (formal_parameters) @fn.params
        body: (_) @fn.body) @fn.def
    ]
    """

    call_query = """
    (call_expression
      function: [
        (identifier) @call.name
        (member_expression
          object: (_) @call.object
          property: (property_identifier) @call.attr)
      ])
    """

    import_query = """
    (import_declaration
      source: (string) @imp.source
      (import_clause [
        (identifier) @imp.default
        (named_imports (import_specifier name: (identifier) @imp.name))
        (namespace_import (identifier) @imp.namespace)
      ]))
    """

    export_query = """
    [
      (export_statement declaration:
        (function_declaration name: (identifier) @export.name))
      (export_statement declaration:
        (lexical_declaration
          (variable_declarator name: (identifier) @export.name)))
    ]
    """

    def is_exported(self, function_name: str, file_imports: list[dict]) -> bool:
        # Simplified: check if function appears in export list
        return any(
            imp.get("type") == "export" and imp.get("name") == function_name
            for imp in file_imports
        )

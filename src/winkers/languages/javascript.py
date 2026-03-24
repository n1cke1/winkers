"""JavaScript language profile."""

from winkers.languages.base import LanguageProfile


class JavaScriptProfile(LanguageProfile):
    language = "javascript"
    extensions = [".js", ".jsx", ".mjs", ".cjs"]
    tree_sitter_language = "javascript"

    function_query = """
    [
      (function_declaration
        name: (identifier) @fn.name
        parameters: (formal_parameters) @fn.params
        body: (statement_block) @fn.body) @fn.def

      (method_definition
        name: (property_identifier) @fn.name
        parameters: (formal_parameters) @fn.params
        body: (statement_block) @fn.body) @fn.def

      (arrow_function
        parameters: [(identifier) (formal_parameters)] @fn.params
        body: [(_) (statement_block)] @fn.body) @fn.def
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
    [
      (import_declaration
        source: (string) @imp.source)
      (call_expression
        function: (identifier) @require.fn
        arguments: (arguments (string) @imp.source)
        (#eq? @require.fn "require"))
    ]
    """

    export_query = """
    [
      (export_statement declaration: (function_declaration name: (identifier) @export.name))
      (expression_statement
        (assignment_expression
          left: (member_expression
            object: (identifier) @obj
            property: (property_identifier) @export.name)
          (#eq? @obj "module")))
    ]
    """

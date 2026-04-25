"""JavaScript language profile."""

from winkers.languages.base import LanguageProfile


class JavaScriptProfile(LanguageProfile):
    language = "javascript"
    extensions = [".js", ".jsx", ".mjs", ".cjs"]
    tree_sitter_language = "javascript"

    # Note on arrow_function: tree-sitter-javascript uses two different field
    # names depending on form. `(x) => ...` and `(a, b) => ...` use the
    # `parameters` field with `formal_parameters` value, while bare-identifier
    # `x => ...` uses the SINGULAR `parameter` field with `identifier` value.
    # Combining these into one pattern via [(identifier) (formal_parameters)]
    # is rejected by the grammar — must be two patterns. Bodies can be any
    # expression or statement_block, so `(_)` covers both.
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
        parameters: (formal_parameters) @fn.params
        body: (_) @fn.body) @fn.def

      (arrow_function
        parameter: (identifier) @fn.params
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

    # Note: tree-sitter-javascript names the ES module import node
    # `import_statement`, not `import_declaration`. The latter is a Python
    # tree-sitter convention. Source field on `import_statement` holds the
    # module string for `import x from 'foo'` / `import 'foo'`.
    import_query = """
    [
      (import_statement
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

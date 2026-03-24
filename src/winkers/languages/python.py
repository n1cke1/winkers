"""Python language profile."""

from winkers.languages.base import LanguageProfile


class PythonProfile(LanguageProfile):
    language = "python"
    extensions = [".py"]
    tree_sitter_language = "python"

    function_query = """
    (function_definition
      name: (identifier) @fn.name
      parameters: (parameters) @fn.params
      return_type: (type)? @fn.return_type
      body: (block) @fn.body) @fn.def
    """

    call_query = """
    [
      (call function: (identifier) @call.name)
      (call function: (attribute
        object: (_) @call.object
        attribute: (identifier) @call.attr))
    ]
    """

    import_query = """
    [
      (import_from_statement
        module_name: (dotted_name) @imp.module
        name: [(dotted_name) (aliased_import)] @imp.name)
      (import_statement
        name: (dotted_name) @imp.module)
    ]
    """

    class_query = """
    (class_definition
      name: (identifier) @class.name
      body: (block
        (function_definition
          name: (identifier) @method.name
          parameters: (parameters) @method.params) @method.def))
    """

    def resolve_import(self, import_node: dict, project_files: list[str]) -> str | None:
        module = import_node.get("source", "")
        # Convert dotted module path to file path candidates
        candidates = [
            module.replace(".", "/") + ".py",
            module.replace(".", "/") + "/__init__.py",
        ]
        for candidate in candidates:
            for pf in project_files:
                if pf.endswith(candidate):
                    return pf
        return None

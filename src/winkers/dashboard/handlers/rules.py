"""Rules API handlers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from aiohttp import web


def make_handlers(root: Path) -> SimpleNamespace:
    async def handle_rules(request: web.Request) -> web.Response:
        from winkers.conventions import RulesStore
        rules_file = RulesStore(root).load()
        return web.json_response([
            {
                "id": r.id,
                "category": r.category,
                "title": r.title,
                "content": r.content,
                "wrong_approach": r.wrong_approach,
                "source": r.source,
                "created": r.created,
                "stats": r.stats.model_dump(),
            }
            for r in rules_file.rules
        ])

    async def handle_rules_dismiss(request: web.Request) -> web.Response:
        from winkers.conventions import DismissedStore, RuleAdd, RulesStore
        try:
            rule_id = int(request.match_info["id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "invalid id"}, status=400)
        rules_store = RulesStore(root)
        rules_file = rules_store.load()
        rule = next((r for r in rules_file.rules if r.id == rule_id), None)
        if rule is None:
            return web.json_response({"error": "not found"}, status=404)
        DismissedStore(root).merge(
            [RuleAdd(category=rule.category, title=rule.title, content=rule.content)],
            [], []
        )
        rules_file.rules = [r for r in rules_file.rules if r.id != rule_id]
        rules_store.save(rules_file)
        return web.json_response({"ok": True})

    async def handle_rules_add(request: web.Request) -> web.Response:
        from datetime import date

        from winkers.conventions import ConventionRule, RulesStore
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        category = data.get("category", "").strip()
        title = data.get("title", "").strip()
        content = data.get("content", "").strip()
        if not category or not title or not content:
            return web.json_response({"error": "category, title, content required"}, status=400)
        rules_store = RulesStore(root)
        rules_file = rules_store.load()
        rule = ConventionRule(
            id=rules_store.next_id(rules_file),
            category=category,
            title=title,
            content=content,
            wrong_approach=data.get("wrong_approach", ""),
            source="manual",
            created=date.today().isoformat(),
        )
        rules_file.rules.append(rule)
        rules_store.save(rules_file)
        return web.json_response(rule.model_dump())

    return SimpleNamespace(
        list=handle_rules,
        dismiss=handle_rules_dismiss,
        add=handle_rules_add,
    )

#!/usr/bin/env python3
"""Non-destructive dependency-boundary diagnostic, NOT a complete audit."""
import ast
import pathlib
import sys

root = pathlib.Path.cwd()
source = root / 'src' / 'email_agent'
if not source.is_dir():
    raise SystemExit('Run from email-agent repository root (src/email_agent not found).')

violations = []
parse_errors = []
files = list(source.rglob('*.py'))
for f in files:
    rel = f.relative_to(source)
    layer = rel.parts[0] if len(rel.parts) > 1 else ''
    try:
        tree = ast.parse(f.read_text(encoding='utf-8'))
    except (SyntaxError, UnicodeDecodeError) as exc:
        parse_errors.append((str(rel), str(exc)))
        continue
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    if layer == 'domain':
        forbidden_prefixes = ('email_agent.application', 'email_agent.infrastructure', 'email_agent.api', 'email_agent.workers', 'fastapi', 'sqlalchemy', 'google', 'sqlite3')
    elif layer == 'application':
        forbidden_prefixes = ('email_agent.infrastructure', 'email_agent.api', 'email_agent.workers', 'fastapi', 'google', 'sqlite3')
    else:
        forbidden_prefixes = ()
    for mod in modules:
        if any(mod == prefix or mod.startswith(prefix + '.') for prefix in forbidden_prefixes):
            violations.append((str(rel), mod))

print(f'Checked {len(files)} Python modules for selected import-boundary violations.')
for f, mod in violations:
    print(f'BOUNDARY: {f}: imports {mod}')
for f, issue in parse_errors:
    print(f'PARSE ERROR: {f}: {issue}')
print(f'Findings: {len(violations)} boundary imports; {len(parse_errors)} parse errors.')
print('This does NOT verify runtime behavior, authentication, prompt safety, database integrity, or all architecture rules.')
if violations or parse_errors:
    sys.exit(1)

from collections import defaultdict
import re


SERIAL_PATTERN = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")


def normalize_serial(value):
    return "".join(str(value or "").upper().split())


def _case_sort_key(case):
    return (case.received_at or case.date_only, case.id)


def build_lineages(cases, seed_serials):
    """Build forward-only replacement trees rooted in the requested S/Ns."""
    pair_cases = defaultdict(list)
    children = defaultdict(set)
    for case in cases:
        faulty = normalize_serial(case.serial_no)
        replacement = normalize_serial(case.replacement_serial_no)
        if not SERIAL_PATTERN.fullmatch(faulty) or not SERIAL_PATTERN.fullmatch(replacement):
            continue
        pair_cases[(faulty, replacement)].append(case)
        children[faulty].add(replacement)

    seeds = {normalize_serial(value) for value in seed_serials if normalize_serial(value)}
    descendants_of_other_seed = set()
    for seed in seeds:
        stack = list(children.get(seed, ()))
        seen = set()
        while stack:
            serial = stack.pop()
            if serial in seen:
                continue
            seen.add(serial)
            if serial in seeds:
                descendants_of_other_seed.add(serial)
            stack.extend(children.get(serial, ()))
    roots = sorted(seeds - descendants_of_other_seed) or sorted(seeds)

    lineages = []
    for root in roots:
        serials = {root}
        steps = []
        active_path = set()
        has_cycle = False

        def visit(parent, depth):
            nonlocal has_cycle
            if parent in active_path:
                has_cycle = True
                return
            active_path.add(parent)
            for child in sorted(children.get(parent, ())):
                histories = sorted(pair_cases[(parent, child)], key=_case_sort_key)
                steps.append(
                    {
                        "from_serial": parent,
                        "to_serial": child,
                        "cases": histories,
                        "case": histories[-1],
                        "depth": depth,
                    }
                )
                serials.add(child)
                if child in active_path:
                    has_cycle = True
                    continue
                visit(child, depth + 1)
            active_path.remove(parent)

        visit(root, 0)
        lineages.append(
            {
                "root_serial": root,
                "serials": serials,
                "steps": steps,
                "has_branch": any(len(children.get(serial, ())) > 1 for serial in serials),
                "has_cycle": has_cycle,
            }
        )
    return lineages
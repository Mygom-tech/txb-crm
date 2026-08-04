"""The transition matrix, rendered for QA.

Generated from the registry so it cannot drift from what the code enforces. Run with:

	bench --site localhost execute crm.txb.transition_matrix.write --kwargs "{'path': 'docs/transition-matrix.md'}"
"""

from crm.txb.pipelines.actions import PIPELINE_ACTIONS, find_action
from crm.txb.pipelines.transitions import get_transitions


def render_matrix() -> str:
	out = ["# Opportunity transition matrix", ""]
	out.append("Generated from `crm.txb.pipelines.actions`. Do not edit by hand.")
	out.append("")

	for pipeline in PIPELINE_ACTIONS:
		out.append(f"## {pipeline}")
		out.append("")
		out.append("| From | To | Action | Admin only |")
		out.append("| --- | --- | --- | --- |")

		graph = get_transitions(pipeline)
		for source in sorted(graph):
			for target in sorted(graph[source]):
				for name in graph[source][target]:
					spec = find_action(pipeline, name)
					admin = "yes" if spec.get("admin_only") else "no"
					out.append(f"| {source} | {target} | {spec['label']} | {admin} |")

		out.append("")

	return "\n".join(out)


def write(path: str = "transition-matrix.md"):
	with open(path, "w") as handle:
		handle.write(render_matrix())

	return path

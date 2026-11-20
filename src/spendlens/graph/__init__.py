"""The resource graph: how AWS resources connect, with cost, findings and owners on every node.

One read-only query per resource type (sources.py) gives the nodes and, from their own columns, the edges. The
relationships follow the Steampipe AWS Insights mod (turbot/steampipe-mod-aws-insights, Apache-2.0), collected for
the whole account at once instead of one resource at a time, so whole groups can be found:

  model.py    Node, Edge and Graph (neighbours, paths, impact)
  sources.py  the queries and how each row becomes nodes and edges, with a monthly cost estimate
  collect.py  runs them through Steampipe (a missing permission skips one source, never the run)
  analyze.py  cleanup groups (resources that can be removed together) and what deleting one affects
  render.py   terminal tree, Mermaid, Graphviz DOT, JSON and a self-contained SVG/HTML picture

Edge kinds say who depends on whom:
  uses      from needs to         (instance -> volume it mounts: deleting the volume breaks the instance)
  contains  to needs from         (ECS cluster -> service: the service must go before the cluster)
  ref       informational only    (volume -> snapshot taken from it: each lives on without the other)
"""

from .model import Edge, Graph, Node

__all__ = ["Edge", "Graph", "Node"]

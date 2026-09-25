"""Crewquarters runtime daemon (PLAN.md sections 4.1, 4.2, 5.2).

The only process allowed to talk to Docker. It listens on a Unix socket, accepts
validated platform records (never raw ``docker run`` payloads), and starts hardened
agent containers and allowlisted model-serving containers.

Standard library only; compatible with Python 3.10 (DGX OS / Ubuntu 22.04).
"""

__version__ = "0.1.0"

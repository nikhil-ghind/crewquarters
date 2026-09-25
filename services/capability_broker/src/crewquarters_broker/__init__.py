"""Capability broker (PLAN.md sections 4.1, 10.3, 10.4, 12.1).

The only service that handles Google and Twilio credentials. Agents reach it with a
run capability token and get narrow operations, never secrets or a generic proxy.
"""
